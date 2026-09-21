// Command tandemsync is a long-running service that mirrors health-sync's
// shape (login once per cycle, poll on a ticker, upsert what's new): every
// SYNC_INTERVAL_SECONDS (default 1 hour) it logs into Tandem Source, fetches
// bolus/basal/CGM pump-log events, and upserts them into the tandem_bolus /
// tandem_basal / tandem_cgm tables in health-db. Those tables' schema is
// owned by health-api's goose migrations (see
// health-api/database/migrations/00003_add_tandem_insulin_tables.sql and
// 00004_add_tandem_cgm_table.sql), not by anything in this module.
//
// The fetch window starts from a watermark — the latest timestamp already
// stored in health-db across those three tables (insulin.LastSyncedAt) —
// minus SYNC_OVERLAP_MINUTES, rather than a fixed lookback from now. This
// makes each cycle self-healing: if tandemsync is down for longer than one
// interval, the next successful cycle picks up from wherever the data
// actually left off instead of leaving a silent gap. There is no cap on how
// far back the watermark can pull from, so a very long outage means one
// larger-than-usual cycle rather than a permanent hole (tandemload remains
// the tool for backfilling a genuinely empty or very stale database, but a
// gap left by downtime no longer needs a manual tandemload run to fix).
// SYNC_LOOKBACK_HOURS is used only as a fallback window when all three
// tables are empty (e.g. before any tandemload has ever run).
//
// This is the incremental counterpart to cmd/tandemload, which does a
// one-time full-history backfill; the two share the insulin package's
// extraction/upsert logic and this module's db package's connection to
// health-db.
//
// If BACKUP_POSTGRESQL_* is set (see db.BackupURLFromEnv), every cycle also
// writes the same data to that second, off-site Postgres instance — e.g. an
// online/managed database kept as a backup independent of the local
// health-db container. The backup write is best-effort: a failure there is
// logged but doesn't fail the cycle, since health-db (what the rest of the
// app depends on) already has the data. The backup database needs the same
// schema as health-db; nothing here creates it — see db.BackupURLFromEnv's
// doc comment.
//
// Credentials and config are read from the environment (loaded from a .env
// file in the working directory if present):
//
//	TANDEM_USERNAME / TANDEM_PASSWORD  - Tandem Source login (required)
//	POSTGRES_HOST/PORT/DB/USER/PASSWORD - health-db connection (required;
//	                                      see db.URLFromEnv)
//	DB_SSL_MODE                        - health-db SSL mode (default "require")
//	BACKUP_POSTGRESQL_HOST/PORT/DB/USER/PASSWORD - optional backup Postgres
//	                                      connection (see db.BackupURLFromEnv)
//	BACKUP_DB_SSL_MODE                 - backup Postgres SSL mode (default "require")
//	SYNC_INTERVAL_SECONDS              - poll interval in seconds (default 3600)
//	SYNC_OVERLAP_MINUTES               - minutes subtracted from the DB watermark
//	                                      each cycle, so a very recently written
//	                                      but not-yet-settled row is still picked
//	                                      up (default 15)
//	SYNC_LOOKBACK_HOURS                - fallback window in hours, used only when
//	                                      health-db has no tandem data yet (default 6)
package main

import (
	"database/sql"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	tandemdb "tandemsync/db"
	"tandemsync/insulin"
	"tandemsync/pumplog"
	"tandemsync/tandem"
)

const (
	defaultSyncIntervalSeconds = 3600 // 1 hour
	defaultOverlapMinutes      = 15
	defaultLookbackHours       = 6
)

func main() {
	// Load health-tandem-sync/.env first, then the root .env as a fallback
	// for anything not already set (POSTGRES_*) — see cmd/tandemload's
	// identical comment for why this order and why both files. Under
	// docker-compose this is a no-op (POSTGRES_* is already in the
	// container's environment via env_file:); it only matters when running
	// this binary standalone from inside health-tandem-sync/.
	if err := tandem.LoadDotEnv(".env"); err != nil {
		log.Println("Warning: could not read .env:", err)
	}
	if err := tandem.LoadDotEnv("../.env"); err != nil {
		log.Println("Warning: could not read ../.env:", err)
	}

	username := requireEnv("TANDEM_USERNAME")
	password := requireEnv("TANDEM_PASSWORD")

	healthDBURL, err := tandemdb.URLFromEnv()
	if err != nil {
		log.Fatal("Failed to build health-db connection URL: ", err)
	}

	syncInterval := envDuration("SYNC_INTERVAL_SECONDS", defaultSyncIntervalSeconds, time.Second)
	overlap := envDuration("SYNC_OVERLAP_MINUTES", defaultOverlapMinutes, time.Minute)
	fallbackLookback := envDuration("SYNC_LOOKBACK_HOURS", defaultLookbackHours, time.Hour)

	sqlDB, err := tandemdb.Open(healthDBURL)
	if err != nil {
		log.Fatal("Failed to connect to health-db: ", err)
	}
	defer sqlDB.Close()

	backupDB := openBackupDB()
	if backupDB != nil {
		defer backupDB.Close()
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	syncOnce(sqlDB, backupDB, username, password, overlap, fallbackLookback)

	ticker := time.NewTicker(syncInterval)
	defer ticker.Stop()

	log.Printf("tandemsync started. Polling every %s (%s overlap, %s fallback window when empty).\n",
		syncInterval, overlap, fallbackLookback)

	for {
		select {
		case <-ticker.C:
			syncOnce(sqlDB, backupDB, username, password, overlap, fallbackLookback)
		case sig := <-sigChan:
			log.Println("Received signal:", sig, ". Shutting down gracefully.")
			return
		}
	}
}

// openBackupDB connects to the optional backup Postgres instance if
// BACKUP_POSTGRESQL_* is configured, or returns nil if it isn't. Unlike
// health-db, a connection failure here is fatal at startup (rather than
// silently skipped) so a typo'd/unreachable backup config is caught
// immediately instead of failing quietly on every sync cycle thereafter.
func openBackupDB() *sql.DB {
	backupURL, enabled, err := tandemdb.BackupURLFromEnv()
	if err != nil {
		log.Fatal("Invalid BACKUP_POSTGRESQL_* configuration: ", err)
	}
	if !enabled {
		return nil
	}
	backupDB, err := tandemdb.Open(backupURL)
	if err != nil {
		log.Fatal("Failed to connect to backup Postgres: ", err)
	}
	log.Println("Backup Postgres configured: cycles will also write there")
	return backupDB
}

// syncOnce logs into Tandem Source, fetches bolus/basal/CGM pump-log events
// from a watermark-derived window, and upserts them into health-db, then
// (best-effort, if backupDB is non-nil) into the backup Postgres too.
// Logging in fresh every cycle (rather than trying to keep a session alive
// across SYNC_INTERVAL_SECONDS, which defaults to an hour) sidesteps having
// to reverse-engineer the Tandem Source session's actual lifetime/refresh
// behavior — Login's full PKCE flow is cheap enough to redo hourly.
//
// The window start is insulin.LastSyncedAt(sqlDB) minus overlap, so a cycle
// always resumes from wherever the data actually left off; if health-db has
// no tandem data yet, it falls back to now-fallbackLookback instead.
func syncOnce(sqlDB, backupDB *sql.DB, username, password string, overlap, fallbackLookback time.Duration) {
	start := time.Now()
	log.Println("Starting sync cycle")

	now := time.Now().UTC()

	windowStart, ok, err := insulin.LastSyncedAt(sqlDB)
	if err != nil {
		log.Println("Failed to determine last synced timestamp, using fallback window:", err)
		windowStart = now.Add(-fallbackLookback)
	} else if !ok {
		log.Println("No tandem data in health-db yet, using fallback window")
		windowStart = now.Add(-fallbackLookback)
	} else {
		windowStart = windowStart.Add(-overlap)
	}

	auth, err := tandem.Login(username, password)
	if err != nil {
		log.Println("Login failed:", err)
		return
	}

	meta, err := tandem.FetchPumperReportMeta(auth.Client, auth.AccessToken, auth.PumperID)
	if err != nil {
		log.Println("Fetching pumper report meta failed:", err)
		return
	}
	reportID, err := tandem.PumperAssignmentID(meta)
	if err != nil {
		log.Println("Could not determine report ID:", err)
		return
	}

	raw, err := tandem.FetchPumpLogsRawFiltered(auth.Client, auth.AccessToken, reportID, auth.PumperID, windowStart, now, insulin.EventIDs())
	if err != nil {
		log.Println("Fetching pump-logs failed:", err)
		return
	}

	logs, err := pumplog.Parse(raw)
	if err != nil {
		log.Println("Parsing pump-logs failed:", err)
		return
	}

	result, err := insulin.Sync(sqlDB, logs.Events)
	if err != nil {
		log.Println("Upserting insulin data failed:", err)
		return
	}

	log.Printf("Sync cycle complete in %s: %d boluses, %d basal changes, %d CGM readings upserted (window %s to %s)\n",
		time.Since(start).Round(time.Millisecond), result.BolusesUpserted, result.BasalUpserted, result.CGMReadingsUpserted,
		windowStart.Format(time.RFC3339), now.Format(time.RFC3339))

	if backupDB != nil {
		if backupResult, err := insulin.Sync(backupDB, logs.Events); err != nil {
			log.Println("Backup Postgres upsert failed (health-db already has this data):", err)
		} else {
			log.Printf("Backup Postgres: %d boluses, %d basal changes, %d CGM readings upserted\n",
				backupResult.BolusesUpserted, backupResult.BasalUpserted, backupResult.CGMReadingsUpserted)
		}
	}
}

func requireEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("Environment variable %s must be set", key)
	}
	return v
}

// envDuration reads key as an integer count of unit (e.g. time.Second,
// time.Minute), falling back to def*unit if unset or invalid.
func envDuration(key string, def int, unit time.Duration) time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return time.Duration(def) * unit
	}
	parsed, err := strconv.Atoi(v)
	if err != nil || parsed <= 0 {
		log.Printf("Invalid %s=%q, using default %d\n", key, v, def)
		return time.Duration(def) * unit
	}
	return time.Duration(parsed) * unit
}

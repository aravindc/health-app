// Command tandemsync is a long-running service that mirrors health-sync's
// shape (login once, poll on a ticker, upsert what's new) but for Tandem
// pump insulin data instead of Dexcom glucose readings: every
// SYNC_INTERVAL_SECONDS (default 1 hour) it logs into Tandem Source, fetches
// recent bolus/basal pump-log events, and upserts them into the
// tandem_bolus / tandem_basal tables (health-db/04-add-tandem-insulin-tables.sql)
// in health-db — a different database than tandemdata's own `events` store
// (tandemdb), which -d/-l continue to use unchanged.
//
// Credentials and config are read from the environment (loaded from a .env
// file in the working directory if present, same as the other tandemdata
// commands):
//
//	TANDEM_USERNAME / TANDEM_PASSWORD  - Tandem Source login (required)
//	HEALTHDB_URL                       - postgres:// URL for health-db (required)
//	SYNC_INTERVAL_SECONDS              - poll interval in seconds (default 3600)
//	SYNC_LOOKBACK_HOURS                - hours of history to re-fetch each cycle,
//	                                      so a missed cycle or late-arriving
//	                                      event is still picked up (default 6)
package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"

	"tandemdata/insulin"
	"tandemdata/pumplog"
	"tandemdata/tandem"
)

const (
	defaultSyncIntervalSeconds = 3600 // 1 hour
	defaultLookbackHours       = 6
	pingTimeout                = 10 * time.Second
)

func main() {
	if err := tandem.LoadDotEnv(".env"); err != nil {
		log.Println("Warning: could not read .env:", err)
	}

	username := requireEnv("TANDEM_USERNAME")
	password := requireEnv("TANDEM_PASSWORD")
	healthDBURL := requireEnv("HEALTHDB_URL")

	syncInterval := envDuration("SYNC_INTERVAL_SECONDS", defaultSyncIntervalSeconds)
	lookback := envDuration("SYNC_LOOKBACK_HOURS", defaultLookbackHours) * time.Hour / time.Second * time.Second

	db, err := openHealthDB(healthDBURL)
	if err != nil {
		log.Fatal("Failed to connect to health-db: ", err)
	}
	defer db.Close()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	syncOnce(db, username, password, lookback)

	ticker := time.NewTicker(syncInterval)
	defer ticker.Stop()

	log.Printf("tandemsync started. Polling every %s, %s lookback window.\n", syncInterval, lookback)

	for {
		select {
		case <-ticker.C:
			syncOnce(db, username, password, lookback)
		case sig := <-sigChan:
			log.Println("Received signal:", sig, ". Shutting down gracefully.")
			return
		}
	}
}

// syncOnce logs into Tandem Source, fetches the lookback window of
// insulin-relevant pump-log events, and upserts them into health-db. Logging
// in fresh every cycle (rather than trying to keep a session alive across
// SYNC_INTERVAL_SECONDS, which defaults to an hour) sidesteps having to
// reverse-engineer the Tandem Source session's actual lifetime/refresh
// behavior — Login's full PKCE flow is cheap enough to redo hourly.
func syncOnce(db *sql.DB, username, password string, lookback time.Duration) {
	start := time.Now()
	log.Println("Starting sync cycle")

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

	now := time.Now().UTC()
	windowStart := now.Add(-lookback)

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

	result, err := insulin.Sync(db, logs.Events)
	if err != nil {
		log.Println("Upserting insulin data failed:", err)
		return
	}

	log.Printf("Sync cycle complete in %s: %d boluses, %d basal changes upserted (window %s to %s)\n",
		time.Since(start).Round(time.Millisecond), result.BolusesUpserted, result.BasalChangesUpserted,
		windowStart.Format(time.RFC3339), now.Format(time.RFC3339))
}

// openHealthDB connects to health-db. Unlike tandemdb.Open (tandemdata's own
// database), no migrations are run here — health-db's schema is managed by
// health-db/*.sql, not tandemdata's embedded goose migrations.
func openHealthDB(connStr string) (*sql.DB, error) {
	config, err := pgx.ParseConfig(connStr)
	if err != nil {
		return nil, fmt.Errorf("parsing HEALTHDB_URL: %w", err)
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec

	db := stdlib.OpenDB(*config)
	ctx, cancel := context.WithTimeout(context.Background(), pingTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("connecting to health-db: %w", err)
	}
	return db, nil
}

func requireEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("Environment variable %s must be set", key)
	}
	return v
}

func envDuration(key string, def int) time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return time.Duration(def) * time.Second
	}
	parsed, err := strconv.Atoi(v)
	if err != nil || parsed <= 0 {
		log.Printf("Invalid %s=%q, using default %d\n", key, v, def)
		return time.Duration(def) * time.Second
	}
	return time.Duration(parsed) * time.Second
}

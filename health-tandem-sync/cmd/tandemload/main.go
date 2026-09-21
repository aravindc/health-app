// Command tandemload does a one-time historical backfill: it logs into
// Tandem Source, fetches the account's full available pump-log history (or
// an explicit date range) in chunkDays windows, and upserts bolus/basal/CGM
// data into health-db's tandem_bolus / tandem_basal / tandem_cgm tables (see
// health-db/04-add-tandem-insulin-tables.sql and
// health-db/05-add-tandem-cgm-table.sql) as each chunk is fetched.
//
// This is the one-shot counterpart to cmd/tandemsync, which polls for
// incremental data every SYNC_INTERVAL_SECONDS; the two share the insulin
// package's extraction/upsert logic and this module's db package's
// connection to health-db.
//
// If BACKUP_POSTGRESQL_* is set (see db.BackupURLFromEnv), each chunk is
// also upserted into that second, off-site Postgres instance after the
// primary health-db upsert succeeds. The backup write is best-effort: a
// failure there is logged but doesn't stop the load, since health-db
// already has the data. The backup database needs the same schema as
// health-db; nothing here creates it — see db.BackupURLFromEnv's doc comment.
//
// Each chunk's raw response is cached to -chunk-dir/<start>_<end>.json (same
// naming as tandemdata's -d -init) before being upserted, so a run that
// fails partway through (network issue, rate limit, DB error) can simply be
// re-run: completed chunks are read from cache instead of re-fetched, and
// upserts are idempotent (ON CONFLICT), so already-loaded chunks are safely
// upserted again rather than duplicated.
//
//	go run ./cmd/tandemload                      # full available history, 30-day chunks
//	go run ./cmd/tandemload -chunk-days 14        # full history, 14-day chunks
//	go run ./cmd/tandemload -start 2024-01-01     # explicit range through now
//	go run ./cmd/tandemload -start 2024-01-01 -end 2024-06-01
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
package main

import (
	"database/sql"
	"flag"
	"fmt"
	"os"
	"time"

	tandemdb "tandemsync/db"
	"tandemsync/insulin"
	"tandemsync/pumplog"
	"tandemsync/tandem"
)

func main() {
	chunkDays := flag.Int("chunk-days", 30, "window size in days per request")
	chunkDir := flag.String("chunk-dir", "./data/chunks", "permanent directory holding one cached JSON file per date-range chunk")
	startDate := flag.String("start", "", "start date (YYYY-MM-DD) of an explicit range to load; overrides the account's full available history")
	endDate := flag.String("end", "", "end date (YYYY-MM-DD) of an explicit range; defaults to now if -start is set")
	flag.Parse()

	if err := tandem.LoadDotEnv(".env"); err != nil {
		fmt.Fprintln(os.Stderr, "Warning: could not read .env:", err)
	}

	username := requireEnv("TANDEM_USERNAME")
	password := requireEnv("TANDEM_PASSWORD")

	healthDBURL, err := tandemdb.URLFromEnv()
	if err != nil {
		fmt.Fprintln(os.Stderr, "Failed to build health-db connection URL:", err)
		os.Exit(1)
	}

	sqlDB, err := tandemdb.Open(healthDBURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Failed to connect to health-db:", err)
		os.Exit(1)
	}
	defer sqlDB.Close()

	backupDB := openBackupDB()
	if backupDB != nil {
		defer backupDB.Close()
	}

	auth, err := tandem.Login(username, password)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Login failed:", err)
		os.Exit(1)
	}
	fmt.Println("Login OK. pumperId =", auth.PumperID)

	meta, err := tandem.FetchPumperReportMeta(auth.Client, auth.AccessToken, auth.PumperID)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Fetching pumper report meta failed:", err)
		os.Exit(1)
	}
	reportID, err := tandem.PumperAssignmentID(meta)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Could not determine report ID:", err)
		os.Exit(1)
	}

	now := time.Now().UTC()

	var start, end time.Time
	if *startDate != "" {
		start, end, err = parseDateRange(*startDate, *endDate, now)
		if err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
	} else {
		start, err = tandem.PumperAvailableDataStart(meta)
		if err != nil {
			fmt.Fprintln(os.Stderr, "Could not determine available data start:", err)
			os.Exit(1)
		}
		end = now
	}

	fmt.Printf("Loading history from %s to %s in %d-day chunks (cached in %s)\n",
		start.Format("2006-01-02"), end.Format("2006-01-02"), *chunkDays, *chunkDir)

	var totals insulin.UpsertResult
	err = tandem.FetchChunksFiltered(auth, reportID, start, end, *chunkDays, *chunkDir, insulin.EventIDs(),
		logChunkProgress,
		func(chunkStart, chunkEnd time.Time, raw []byte) error {
			result, err := loadChunk(sqlDB, raw)
			if err != nil {
				return err
			}
			totals.BolusesUpserted += result.BolusesUpserted
			totals.BasalUpserted += result.BasalUpserted
			totals.CGMReadingsUpserted += result.CGMReadingsUpserted
			fmt.Printf("    upserted %d boluses, %d basal changes, %d CGM readings\n",
				result.BolusesUpserted, result.BasalUpserted, result.CGMReadingsUpserted)

			if backupDB != nil {
				if backupResult, err := loadChunk(backupDB, raw); err != nil {
					fmt.Fprintln(os.Stderr, "    backup Postgres upsert failed (health-db already has this chunk):", err)
				} else {
					fmt.Printf("    backup Postgres: upserted %d boluses, %d basal changes, %d CGM readings\n",
						backupResult.BolusesUpserted, backupResult.BasalUpserted, backupResult.CGMReadingsUpserted)
				}
			}
			return nil
		},
	)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}

	fmt.Printf("Done: upserted %d boluses, %d basal changes, %d CGM readings total\n",
		totals.BolusesUpserted, totals.BasalUpserted, totals.CGMReadingsUpserted)
}

// openBackupDB connects to the optional backup Postgres instance if
// BACKUP_POSTGRESQL_* is configured, or returns nil if it isn't. Unlike
// health-db, a connection failure here is fatal at startup (rather than
// silently skipped) so a typo'd/unreachable backup config is caught
// immediately instead of failing quietly on every chunk thereafter.
func openBackupDB() *sql.DB {
	backupURL, enabled, err := tandemdb.BackupURLFromEnv()
	if err != nil {
		fmt.Fprintln(os.Stderr, "Invalid BACKUP_POSTGRESQL_* configuration:", err)
		os.Exit(1)
	}
	if !enabled {
		return nil
	}
	backupDB, err := tandemdb.Open(backupURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Failed to connect to backup Postgres:", err)
		os.Exit(1)
	}
	fmt.Println("Backup Postgres configured: chunks will also be upserted there")
	return backupDB
}

// loadChunk parses one chunk's raw pump-logs JSON and upserts it into db
// (either health-db or the backup Postgres).
func loadChunk(sqlDB *sql.DB, raw []byte) (insulin.UpsertResult, error) {
	logs, err := pumplog.Parse(raw)
	if err != nil {
		return insulin.UpsertResult{}, fmt.Errorf("parsing chunk: %w", err)
	}
	return insulin.Sync(sqlDB, logs.Events)
}

func logChunkProgress(i, total int, chunkStart, chunkEnd time.Time, cached bool) {
	status := "fetching..."
	if cached {
		status = "using cache"
	}
	fmt.Printf("  [%d/%d] %s to %s: %s\n",
		i, total, chunkStart.Format("2006-01-02"), chunkEnd.Format("2006-01-02"), status)
}

// parseDateRange parses -start and -end (both YYYY-MM-DD). end defaults to
// now (already UTC) when omitted. It rejects an end date that isn't after
// start.
func parseDateRange(startDate, endDate string, now time.Time) (start, end time.Time, err error) {
	const layout = "2006-01-02"

	start, err = time.Parse(layout, startDate)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("parsing -start %q (want YYYY-MM-DD): %w", startDate, err)
	}

	if endDate == "" {
		end = now
	} else {
		end, err = time.Parse(layout, endDate)
		if err != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("parsing -end %q (want YYYY-MM-DD): %w", endDate, err)
		}
	}

	if !end.After(start) {
		return time.Time{}, time.Time{}, fmt.Errorf("-end (%s) must be after -start (%s)", end.Format(layout), start.Format(layout))
	}

	return start, end, nil
}

func requireEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		fmt.Fprintf(os.Stderr, "Environment variable %s must be set\n", key)
		os.Exit(1)
	}
	return v
}

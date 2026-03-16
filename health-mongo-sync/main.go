package main

import (
	"context"
	"health-mongo-sync/db"
	mongoclient "health-mongo-sync/mongo"
	"os"
	"strconv"
	"time"

	log "github.com/sirupsen/logrus"
	easy "github.com/t-tomalak/logrus-easy-formatter"
)

func init() {
	log.SetOutput(os.Stdout)
	log.SetFormatter(&easy.Formatter{
		TimestampFormat: "2006-01-02 15:04:05",
		LogFormat:       "[%lvl%]: %time% - %msg%\n",
	})
	lvl, err := log.ParseLevel(os.Getenv("LOG_LEVEL"))
	if err != nil {
		lvl = log.InfoLevel
	}
	log.SetLevel(lvl)

	required := []string{
		"MONGO_URI", "MONGO_DB", "MONGO_COLLECTION",
		"POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
	}
	for _, k := range required {
		if os.Getenv(k) == "" {
			log.Fatalf("Required environment variable %s is not set", k)
		}
	}
}

func main() {
	// How far back to look for gaps (default: all data — use 0 to mean "no limit").
	// Set MONGO_SYNC_LOOKBACK_DAYS to limit the window, e.g. 90.
	lookbackDays := 0
	if v := os.Getenv("MONGO_SYNC_LOOKBACK_DAYS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			lookbackDays = n
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()

	// --- Connect to Postgres ---
	pgDB, err := db.PgClient(
		os.Getenv("POSTGRES_HOST"),
		os.Getenv("POSTGRES_PORT"),
		os.Getenv("POSTGRES_USER"),
		os.Getenv("POSTGRES_PASSWORD"),
		os.Getenv("POSTGRES_DB"),
	)
	if err != nil {
		log.Fatal("Postgres connection failed: ", err)
	}
	defer pgDB.Close()

	// --- Connect to MongoDB ---
	mongoClient, err := mongoclient.Connect(ctx, os.Getenv("MONGO_URI"))
	if err != nil {
		log.Fatal("MongoDB connection failed: ", err)
	}
	defer mongoClient.Disconnect(ctx)

	// --- Determine the time window to sync ---
	to := time.Now().UTC()
	var from time.Time
	if lookbackDays > 0 {
		from = to.AddDate(0, 0, -lookbackDays)
	} else {
		// Default: start of Dexcom CGM era or a sensible far-back date.
		from = time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)
	}
	log.Infof("Sync window: %s → %s", from.Format(time.DateOnly), to.Format(time.DateOnly))

	// --- Step 1: Fetch all entries from MongoDB in ONE query ---
	mongoEntries, err := mongoclient.FetchRange(
		ctx, mongoClient,
		os.Getenv("MONGO_DB"),
		os.Getenv("MONGO_COLLECTION"),
		from, to,
	)
	if err != nil {
		log.Fatal("MongoDB fetch failed: ", err)
	}
	if len(mongoEntries) == 0 {
		log.Info("No entries found in MongoDB for the given window. Nothing to do.")
		return
	}

	// --- Step 2: Fetch existing ns_time values from Postgres for the same window ---
	// This is one query that returns all timestamps we already have — fast because
	// ns_datetime is indexed and the result is just a list of int64s.
	existing, err := db.ExistingNsTimes(pgDB, from, to)
	if err != nil {
		log.Fatal("Failed to query existing Postgres entries: ", err)
	}
	log.Infof("Postgres already has %d records in this window", len(existing))

	// --- Step 3: Bulk-insert only missing records ---
	inserted, err := db.BulkInsert(pgDB, mongoEntries, existing)
	if err != nil {
		log.Fatal("Bulk insert failed: ", err)
	}

	skipped := len(mongoEntries) - inserted
	log.Infof("Sync complete — inserted: %d, skipped (already existed): %d", inserted, skipped)
}

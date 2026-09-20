// Command tandemdata is a single entry point for the download / load / parse
// workflow around Tandem Source pump data:
//
//	go run ./cmd/tandemdata -d [-days 28] [-chunk-dir ./data/chunks] [-meta-out ...]
//	go run ./cmd/tandemdata -d -init [-chunk-days 30] [-chunk-dir ./data/chunks] [-meta-out ...]
//	go run ./cmd/tandemdata -d -start 2025-01-01 -end 2025-02-01 [-chunk-dir ./data/chunks] [-meta-out ...]
//	go run ./cmd/tandemdata -l [-file pump-logs-raw.json]
//	go run ./cmd/tandemdata -l -init [-chunk-dir ./data/chunks]
//	go run ./cmd/tandemdata -p [-file pump-logs-raw.json] [-code 399] [-limit 20]
//
// -d downloads the pumper report metadata and raw pump logs from Tandem
// Source, -l loads pump-logs data into a PostgreSQL database, and -p prints
// a summary (or, with -code, matching events) from a raw pump-logs file.
// Exactly one of -d, -l, -p must be given.
//
// -d always writes the downloaded pump-logs to
// -chunk-dir/<start>_<end>.json (the same naming -l -init reads), using
// whichever of the following determines [start, end):
//
//   - by default, the last -days days (default 28), ending now;
//   - with -start (and, optionally, -end), that explicit range instead, as
//     a single request (-days and -init are ignored when -start is set);
//     dates are given as YYYY-MM-DD and -end defaults to now if omitted;
//   - with -init, the entire available history, fetched in -chunk-days
//     windows and cached as one file per window.
//
// -d -init and -l -init share -chunk-dir, a permanent directory (not a
// scratch/tmp path) holding one cached JSON response per date-range chunk.
// -d -init fetches missing chunks into it; -l -init loads every chunk file
// in it into the database, each in its own transaction, so a failure on one
// chunk doesn't roll back chunks already loaded and a re-run only needs to
// retry the chunk(s) that failed. A plain -d or -d -start/-end run writes
// its single [start, end) file into -chunk-dir the same way, so it too can
// be picked up by a later -l -init.
//
// Credentials are read from TANDEM_USERNAME / TANDEM_PASSWORD (for -d) and
// DATABASE_URL (for -l), loaded from a .env file in the working directory if
// present. DATABASE_URL is a standard postgres:// connection string, e.g.
// postgres://user:password@host:5432/dbname?sslmode=require.
package main

import (
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"tandemdata/pumplog"
	"tandemdata/tandem"
	"tandemdata/tandemdb"
)

func main() {
	doDownload := flag.Bool("d", false, "download pumper metadata and raw pump logs from Tandem Source")
	doLoad := flag.Bool("l", false, "load a raw pump-logs file into the database")
	doParse := flag.Bool("p", false, "parse/summarize a raw pump-logs file")

	// -d flags
	days := flag.Int("days", 28, "[-d] number of days of pump logs to fetch, ending now (ignored with -init or -start)")
	chunkDays := flag.Int("chunk-days", 30, "[-d] window size in days per request when -init is set")
	metaOut := flag.String("meta-out", "pumper-report-meta.json", "[-d] path to write pumper report metadata")
	startDate := flag.String("start", "", "[-d] start date (YYYY-MM-DD) of an explicit range to fetch; overrides -days and -init")
	endDate := flag.String("end", "", "[-d] end date (YYYY-MM-DD) of an explicit range to fetch; defaults to now if -start is set")

	// -d / -l shared flags
	initFlag := flag.Bool("init", false, "[-d] fetch full history in chunks instead of -days; [-l] load every cached chunk file instead of -file")
	chunkDir := flag.String("chunk-dir", "./data/chunks", "[-d, -l] permanent directory holding one cached JSON file per date-range chunk")

	// -l / -p shared flag
	file := flag.String("file", "pump-logs-raw.json", "[-l, -p] path to the raw pump-logs file")

	// -p flags
	code := flag.Int("code", -1, "[-p] if set, show events matching this eventCode instead of the summary")
	limit := flag.Int("limit", 20, "[-p] max events to print when -code is set (0 = no limit)")

	flag.Parse()

	switch numSet(*doDownload, *doLoad, *doParse) {
	case 0:
		fmt.Fprintln(os.Stderr, "exactly one of -d, -l, -p is required")
		flag.Usage()
		os.Exit(1)
	case 1:
		// exactly one set, proceed
	default:
		fmt.Fprintln(os.Stderr, "only one of -d, -l, -p may be given at a time")
		os.Exit(1)
	}

	if err := tandem.LoadDotEnv(".env"); err != nil {
		fmt.Fprintln(os.Stderr, "Warning: could not read .env:", err)
	}

	switch {
	case *doDownload:
		runDownload(*initFlag, *days, *chunkDays, *chunkDir, *metaOut, *startDate, *endDate)
	case *doLoad:
		if *initFlag {
			runLoadInit(*chunkDir)
		} else {
			runLoad(*file)
		}
	case *doParse:
		runParse(*file, *code, *limit)
	}
}

func numSet(flags ...bool) int {
	n := 0
	for _, f := range flags {
		if f {
			n++
		}
	}
	return n
}

// runDownload mirrors the former cmd/download.
func runDownload(initFlag bool, days, chunkDays int, chunkDir, metaOut, startDate, endDate string) {
	username := os.Getenv("TANDEM_USERNAME")
	password := os.Getenv("TANDEM_PASSWORD")

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
	metaBytes, err := json.MarshalIndent(meta, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "Marshaling pumper report meta failed:", err)
		os.Exit(1)
	}
	if err := os.WriteFile(metaOut, metaBytes, 0600); err != nil {
		fmt.Fprintln(os.Stderr, "Writing", metaOut, "failed:", err)
		os.Exit(1)
	}
	fmt.Println("Saved pumper report metadata to", metaOut)

	reportID, err := tandem.PumperAssignmentID(meta)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Could not determine report ID:", err)
		fmt.Println("Inspect", metaOut, "to find the right field manually.")
		os.Exit(1)
	}

	now := time.Now().UTC()

	if initFlag {
		start, err := tandem.PumperAvailableDataStart(meta)
		if err != nil {
			fmt.Fprintln(os.Stderr, "Could not determine available data start:", err)
			os.Exit(1)
		}
		fmt.Printf("Fetching full history from %s to %s in %d-day chunks\n", start, now, chunkDays)
		if _, err := tandem.FetchWithChunkCache(auth, reportID, start, now, chunkDays, chunkDir, logChunkProgress); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		fmt.Println("Saved chunk files to", chunkDir)
		return
	}

	// Single-request download: either an explicit -start/-end range, or the
	// last -days days ending now.
	var start, end time.Time
	if startDate != "" {
		var err error
		start, end, err = parseDateRange(startDate, endDate, now)
		if err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
	} else {
		start, end = now.AddDate(0, 0, -days), now
	}

	fmt.Printf("Fetching pump logs from %s to %s\n", start.Format("2006-01-02"), end.Format("2006-01-02"))
	raw, err := tandem.FetchPumpLogsRaw(auth.Client, auth.AccessToken, reportID, auth.PumperID, start, end)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Fetching pump-logs failed:", err)
		os.Exit(1)
	}

	if err := os.MkdirAll(chunkDir, 0700); err != nil {
		fmt.Fprintln(os.Stderr, "Creating chunk directory", chunkDir, "failed:", err)
		os.Exit(1)
	}
	logsOut := chunkFilePath(chunkDir, start, end)
	if err := os.WriteFile(logsOut, raw, 0600); err != nil {
		fmt.Fprintln(os.Stderr, "Writing", logsOut, "failed:", err)
		os.Exit(1)
	}
	fmt.Printf("Saved raw pump-logs response to %s (%d bytes)\n", logsOut, len(raw))
}

// chunkFilePath returns the cache path for a [start, end) range, matching
// the <start>_<end>.json naming used by -d -init / -l -init.
func chunkFilePath(dir string, start, end time.Time) string {
	const layout = "2006-01-02"
	return filepath.Join(dir, fmt.Sprintf("%s_%s.json", start.Format(layout), end.Format(layout)))
}

func logChunkProgress(i, total int, chunkStart, chunkEnd time.Time, cached bool) {
	status := "fetching..."
	if cached {
		status = "using cache"
	}
	fmt.Printf("  [%d/%d] %s to %s: %s\n",
		i, total, chunkStart.Format("2006-01-02"), chunkEnd.Format("2006-01-02"), status)
}

// parseDateRange parses -start and -end (both YYYY-MM-DD) for an explicit
// download range. end defaults to now (already UTC) when omitted. It
// rejects an end date that isn't after start.
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

// openAndMigrateDB connects to DATABASE_URL and brings the schema up to date.
func openAndMigrateDB() *sql.DB {
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		fmt.Fprintln(os.Stderr, "DATABASE_URL is not set")
		os.Exit(1)
	}

	db, err := tandemdb.Open(dbURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}

	if err := tandemdb.Migrate(db); err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}
	return db
}

// runLoad mirrors the former cmd/loaddb: it loads a single pump-logs file.
func runLoad(file string) {
	logs, err := pumplog.Load(file)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "Loaded %d events, %d clock changes from %s\n",
		len(logs.Events), len(logs.ClockChanges), file)

	db := openAndMigrateDB()
	defer db.Close()

	result, err := tandemdb.Load(db, logs)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}

	fmt.Printf("Upserted %d events and %d clock changes into the database\n",
		result.EventsUpserted, result.ClockChangesUpserted)
}

// runLoadInit loads every cached chunk file in chunkDir (as populated by
// `-d -init`) into the database, each chunk in its own transaction: a
// failure partway through leaves already-loaded chunks committed, and a
// re-run only needs to fix and retry whichever chunk failed.
func runLoadInit(chunkDir string) {
	entries, err := os.ReadDir(chunkDir)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error reading chunk directory:", err)
		os.Exit(1)
	}

	var files []string
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		files = append(files, e.Name())
	}
	sort.Strings(files) // filenames are <start>_<end>.json, so this is chronological

	if len(files) == 0 {
		fmt.Println("No chunk files found in", chunkDir)
		return
	}

	db := openAndMigrateDB()
	defer db.Close()

	var totalEvents, totalClockChanges int
	for i, name := range files {
		path := filepath.Join(chunkDir, name)

		logs, err := pumplog.Load(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error loading chunk %d/%d (%s): %v\n", i+1, len(files), name, err)
			os.Exit(1)
		}

		result, err := tandemdb.Load(db, logs)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error upserting chunk %d/%d (%s): %v\n", i+1, len(files), name, err)
			os.Exit(1)
		}

		totalEvents += result.EventsUpserted
		totalClockChanges += result.ClockChangesUpserted
		fmt.Printf("  [%d/%d] %s: upserted %d events, %d clock changes\n",
			i+1, len(files), name, result.EventsUpserted, result.ClockChangesUpserted)
	}

	fmt.Printf("Upserted %d events and %d clock changes total from %d chunk file(s)\n",
		totalEvents, totalClockChanges, len(files))
}

// runParse mirrors the former cmd/parselogs.
func runParse(file string, code, limit int) {
	logs, err := pumplog.Load(file)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "Loaded %d events, %d clock changes from %s\n\n",
		len(logs.Events), len(logs.ClockChanges), file)

	if code < 0 {
		printSummary(logs)
		return
	}
	printEvents(logs, code, limit)
}

func printSummary(logs *pumplog.Logs) {
	fmt.Printf("%-8s %-24s %s\n", "Code", "Name", "Count")
	for _, c := range logs.EventCounts() {
		fmt.Printf("%-8d %-24s %d\n", c.Code, c.Name, c.Count)
	}
}

func printEvents(logs *pumplog.Logs, code, limit int) {
	matches := logs.FilterByCode(code)
	if len(matches) == 0 {
		fmt.Printf("No events found for eventCode %d (%s)\n", code, pumplog.EventName(code))
		return
	}

	name := pumplog.EventName(code)
	fmt.Printf("%d event(s) for code %d (%s)", len(matches), code, name)
	if limit > 0 && limit < len(matches) {
		fmt.Printf(", showing first %d by pump time", limit)
	}
	fmt.Println()

	sort.Slice(matches, func(i, j int) bool { return matches[i].PumpDateTime < matches[j].PumpDateTime })

	if limit > 0 && limit < len(matches) {
		matches = matches[:limit]
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	for _, e := range matches {
		if err := enc.Encode(e); err != nil {
			fmt.Fprintln(os.Stderr, "encode error:", err)
			return
		}
	}
}

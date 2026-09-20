// Package tandemdb loads parsed pump-logs-raw.json events into a PostgreSQL
// database for analysis.
package tandemdb

import (
	"context"
	"database/sql"
	"embed"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"

	"tandemdata/pumplog"
)

//go:embed migrations/*.sql
var migrationsFS embed.FS

const migrationsDir = "migrations"

// pingTimeout bounds the initial connectivity check so a network problem
// (bad host, unreachable endpoint, hung TLS handshake) fails fast instead of
// blocking forever, since database/sql's Ping with no context has no deadline.
const pingTimeout = 10 * time.Second

// Open connects to a PostgreSQL database using connStr, a standard
// postgres:// connection URL (or libpq keyword/value string), e.g.
// postgres://user:password@host:5432/dbname?sslmode=require.
//
// Prepared statement caching is disabled (QueryExecModeExec): many managed
// Postgres providers front connections with a transaction-mode connection
// pooler (e.g. PgBouncer), which multiplexes one logical connection across
// several backend connections. pgx's default extended-protocol mode
// prepares and caches statements per backend connection, which under a
// pooler leads to "prepared statement already exists" or "prepared
// statement is already in use" errors once a query is routed to a
// connection that already has a same-named statement from a different
// client. QueryExecModeExec skips server-side statement preparation, which
// pgx's own docs recommend over the simple protocol whenever the target
// supports the extended protocol at all, as connection poolers do.
//
// QueryExecModeExec (like the simple protocol) infers each parameter's
// PostgreSQL type from its Go type rather than the target column, so a
// []byte argument is always sent as bytea. Callers writing to a json/jsonb
// column must pass the JSON as a string (which defaults to text and
// implicitly casts), not []byte — see buildBatchInsert.
func Open(connStr string) (*sql.DB, error) {
	config, err := pgx.ParseConfig(connStr)
	if err != nil {
		return nil, fmt.Errorf("parsing connection string: %w", err)
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec

	db := stdlib.OpenDB(*config)
	ctx, cancel := context.WithTimeout(context.Background(), pingTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("connecting to database: %w", err)
	}
	return db, nil
}

// Migrate brings the database schema up to date by running any pending
// goose migrations embedded from the migrations/ directory.
func Migrate(db *sql.DB) error {
	goose.SetBaseFS(migrationsFS)
	defer goose.SetBaseFS(nil)

	if err := goose.SetDialect("postgres"); err != nil {
		return fmt.Errorf("setting goose dialect: %w", err)
	}
	if err := goose.Up(db, migrationsDir); err != nil {
		return fmt.Errorf("running migrations: %w", err)
	}
	return nil
}

// pumpDateTimeLayout matches how pumplog.Event.PumpDateTime strings are
// formatted (no timezone suffix, see reports.go's captured event shape).
const pumpDateTimeLayout = "2006-01-02T15:04:05"

// LastPumpDateTime returns the most recent pump_date_time stored in the
// events table, or the zero time with ok=false if the table is empty.
func LastPumpDateTime(db *sql.DB) (t time.Time, ok bool, err error) {
	var maxStr sql.NullString
	if err := db.QueryRow(`SELECT MAX(pump_date_time) FROM events`).Scan(&maxStr); err != nil {
		return time.Time{}, false, fmt.Errorf("querying last pump_date_time: %w", err)
	}
	if !maxStr.Valid || maxStr.String == "" {
		return time.Time{}, false, nil
	}
	parsed, err := time.Parse(pumpDateTimeLayout, maxStr.String)
	if err != nil {
		return time.Time{}, false, fmt.Errorf("parsing last pump_date_time %q: %w", maxStr.String, err)
	}
	return parsed, true, nil
}

// batchSize is how many event rows go into a single multi-row INSERT. Each
// row binds 9 parameters; PostgreSQL allows up to 65535 parameters per
// statement, so a large batch keeps a ~57k-event load from taking one
// round trip per row.
const batchSize = 200

const insertColumns = `
	device_assignment_id, sequence_group, sequence_number,
	event_code, event_name, pump_date_time, estimated_date_time,
	event_properties, is_clock_change
`

const onConflictUpdate = `
	ON CONFLICT (device_assignment_id, sequence_group, sequence_number, is_clock_change)
	DO UPDATE SET
		event_code = excluded.event_code,
		event_name = excluded.event_name,
		pump_date_time = excluded.pump_date_time,
		estimated_date_time = excluded.estimated_date_time,
		event_properties = excluded.event_properties
`

// LoadResult summarizes a Load call.
type LoadResult struct {
	EventsUpserted       int
	ClockChangesUpserted int
}

// Load upserts every event and clock change from logs into the events table,
// keyed on (device_assignment_id, sequence_group, sequence_number,
// is_clock_change) so re-running Load with overlapping data is idempotent.
// Rows are sent in batches of batchSize per Exec call to keep round trips low.
func Load(db *sql.DB, logs *pumplog.Logs) (LoadResult, error) {
	tx, err := db.Begin()
	if err != nil {
		return LoadResult{}, fmt.Errorf("starting transaction: %w", err)
	}
	defer tx.Rollback()

	var result LoadResult
	if err := upsertAll(tx, logs.Events, false, &result.EventsUpserted); err != nil {
		return LoadResult{}, err
	}
	if err := upsertAll(tx, logs.ClockChanges, true, &result.ClockChangesUpserted); err != nil {
		return LoadResult{}, err
	}

	if err := tx.Commit(); err != nil {
		return LoadResult{}, fmt.Errorf("committing transaction: %w", err)
	}
	return result, nil
}

func upsertAll(tx *sql.Tx, events []pumplog.Event, isClockChange bool, count *int) error {
	for start := 0; start < len(events); start += batchSize {
		end := start + batchSize
		if end > len(events) {
			end = len(events)
		}
		batch := events[start:end]

		query, args, err := buildBatchInsert(batch, isClockChange)
		if err != nil {
			return fmt.Errorf("building batch insert (rows %d-%d): %w", start, end, err)
		}
		if _, err := tx.Exec(query, args...); err != nil {
			return fmt.Errorf("upserting batch (rows %d-%d): %w", start, end, err)
		}
		*count += len(batch)
	}
	return nil
}

const colsPerRow = 9

func buildBatchInsert(events []pumplog.Event, isClockChange bool) (string, []interface{}, error) {
	placeholders := make([]string, 0, len(events))
	args := make([]interface{}, 0, len(events)*colsPerRow)
	for i, e := range events {
		propsJSON, err := json.Marshal(e.EventProperties)
		if err != nil {
			return "", nil, fmt.Errorf("marshaling event properties: %w", err)
		}

		base := i * colsPerRow
		ph := make([]string, colsPerRow)
		for j := range ph {
			ph[j] = fmt.Sprintf("$%d", base+j+1)
		}
		placeholders = append(placeholders, "("+strings.Join(ph, ", ")+")")

		args = append(args,
			e.DeviceAssignmentID,
			e.SequenceGroup,
			e.SequenceNumber,
			e.EventCode,
			pumplog.EventName(e.EventCode),
			e.PumpDateTime,
			e.EstimatedDateTime,
			string(propsJSON),
			isClockChange,
		)
	}

	query := fmt.Sprintf("INSERT INTO events (%s) VALUES %s %s",
		insertColumns, strings.Join(placeholders, ", "), onConflictUpdate)
	return query, args, nil
}

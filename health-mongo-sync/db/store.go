package db

import (
	"context"
	"crypto/tls"
	"database/sql"
	"fmt"
	"health-mongo-sync/model"
	"os"
	"time"

	log "github.com/sirupsen/logrus"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/dialect/pgdialect"
	"github.com/uptrace/bun/driver/pgdriver"
)

// PgClient opens and verifies a Postgres connection.
func PgClient(host, port, user, pass, dbname string) (*sql.DB, error) {
	sslMode := os.Getenv("DB_SSL_MODE")
	if sslMode == "" {
		sslMode = "disable"
	}

	opts := []pgdriver.Option{
		pgdriver.WithNetwork("tcp"),
		pgdriver.WithAddr(fmt.Sprintf("%s:%s", host, port)),
		pgdriver.WithUser(user),
		pgdriver.WithPassword(pass),
		pgdriver.WithDatabase(dbname),
		pgdriver.WithTimeout(10 * time.Second),
		pgdriver.WithDialTimeout(10 * time.Second),
		pgdriver.WithReadTimeout(30 * time.Second),
		pgdriver.WithWriteTimeout(30 * time.Second),
	}
	if sslMode == "disable" {
		opts = append(opts, pgdriver.WithInsecure(true))
	} else {
		opts = append(opts, pgdriver.WithTLSConfig(&tls.Config{ServerName: host}))
	}

	conn := pgdriver.NewConnector(opts...)
	db := sql.OpenDB(conn)
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("postgres ping failed: %w", err)
	}
	log.Info("Postgres connection established")
	return db, nil
}

// ExistingNsTimes returns the set of all ns_time values stored in Postgres
// between from and to (inclusive). Used to skip records we already have.
func ExistingNsTimes(sqlDB *sql.DB, from, to time.Time) (map[int64]struct{}, error) {
	db := bun.NewDB(sqlDB, pgdialect.New())
	ctx := context.Background()

	var times []int64
	err := db.NewSelect().
		TableExpr("ns_part").
		ColumnExpr("ns_time").
		Where("ns_datetime >= ? AND ns_datetime <= ?", from, to).
		Scan(ctx, &times)
	if err != nil {
		return nil, fmt.Errorf("ExistingNsTimes query: %w", err)
	}

	set := make(map[int64]struct{}, len(times))
	for _, t := range times {
		set[t] = struct{}{}
	}
	return set, nil
}

// nightscoutRow mirrors the nightscoutdb table for bun inserts.
type nightscoutRow struct {
	bun.BaseModel `bun:"table:nightscoutdb,alias:ns"`

	Sgv         int       `bun:"sgv"`
	NsTime      int64     `bun:"ns_time,type:bigint"`
	NsDatetime  time.Time `bun:"ns_datetime,type:timestamptz"`
	Trend       int       `bun:"trend"`
	Utcoffset   int       `bun:"utcoffset"`
	Systime     time.Time `bun:"systime,type:timestamptz"`
}

// BulkInsert inserts entries into nightscoutdb in a single statement.
// The existing DB trigger will propagate each row into ns_part automatically.
// Entries whose ns_time is in the skip set are silently dropped.
func BulkInsert(sqlDB *sql.DB, entries []model.NsEntry, skip map[int64]struct{}) (int, error) {
	if len(entries) == 0 {
		return 0, nil
	}

	rows := make([]nightscoutRow, 0, len(entries))
	for _, e := range entries {
		if _, exists := skip[e.NsTime]; exists {
			continue
		}
		rows = append(rows, nightscoutRow{
			Sgv:        e.Sgv,
			NsTime:     e.NsTime,
			NsDatetime: e.NsDatetime,
			Trend:      e.Trend,
			Utcoffset:  e.Utcoffset,
			Systime:    e.Systime,
		})
	}

	if len(rows) == 0 {
		return 0, nil
	}

	db := bun.NewDB(sqlDB, pgdialect.New())
	ctx := context.Background()

	// ON CONFLICT DO NOTHING: safe to re-run if the service restarts mid-batch.
	_, err := db.NewInsert().
		Model(&rows).
		On("CONFLICT (ns_time) DO NOTHING").
		Exec(ctx)
	if err != nil {
		return 0, fmt.Errorf("BulkInsert error: %w", err)
	}

	log.Infof("Inserted %d records into Postgres", len(rows))
	return len(rows), nil
}

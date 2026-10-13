package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
)

// checkConnections connects to both databases and verifies the privileges
// the sync needs. Both sides are always checked so one run reports every
// problem. Connection details from .env are deliberately not logged.
func checkConnections(ctx context.Context, cfg *Config) error {
	srcErr := checkDB(ctx, "source", cfg.Source, func(conn *pgx.Conn) ([]any, error) {
		var tables int
		var unreadable []string
		err := conn.QueryRow(ctx, `
			SELECT count(*),
			       coalesce(array_agg(n.nspname || '.' || c.relname ORDER BY n.nspname, c.relname)
			                FILTER (WHERE NOT has_table_privilege(c.oid, 'SELECT')), '{}')
			FROM pg_class c
			JOIN pg_namespace n ON n.oid = c.relnamespace
			WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
			  AND n.nspname = ANY($1)`, cfg.Schemas).Scan(&tables, &unreadable)
		if err != nil {
			return nil, err
		}
		attrs := []any{"schemas", cfg.Schemas, "tables", tables}
		switch {
		case tables == 0:
			return attrs, fmt.Errorf("no tables found in schemas %v", cfg.Schemas)
		case len(unreadable) > 0:
			return attrs, fmt.Errorf("%d of %d tables lack SELECT privilege: %v", len(unreadable), tables, unreadable)
		}
		return attrs, nil
	})

	tgtErr := checkDB(ctx, "target", cfg.Target, func(conn *pgx.Conn) ([]any, error) {
		var canCreate, inRecovery bool
		var readOnly string
		err := conn.QueryRow(ctx, `
			SELECT has_database_privilege(current_database(), 'CREATE'),
			       pg_is_in_recovery(),
			       current_setting('default_transaction_read_only')`).Scan(&canCreate, &inRecovery, &readOnly)
		if err != nil {
			return nil, err
		}
		attrs := []any{"can_create_schema", canCreate}
		switch {
		case inRecovery:
			return attrs, errors.New("target is a read-only standby")
		case readOnly == "on":
			return attrs, errors.New("target sessions default to read-only")
		case !canCreate:
			// Not fatal if the schemas and tables already exist.
			slog.Warn("target user lacks CREATE on database; missing schemas cannot be created")
		}
		return attrs, nil
	})

	if srcErr != nil || tgtErr != nil {
		return errors.New("connection check failed")
	}
	return nil
}

func checkDB(ctx context.Context, name string, cc *pgx.ConnConfig, extra func(*pgx.Conn) ([]any, error)) error {
	log := slog.With("db", name)
	t0 := time.Now()
	conn, err := connect(ctx, name, cc)
	if err != nil {
		log.Error("connection check failed", "err", err)
		return err
	}
	defer conn.Close(context.Background())

	var version string
	if err := conn.QueryRow(ctx, "SHOW server_version").Scan(&version); err != nil {
		log.Error("connection check failed", "err", err)
		return err
	}
	attrs, err := extra(conn)
	attrs = append([]any{"server_version", version, "latency", time.Since(t0).Round(time.Millisecond)}, attrs...)
	if err != nil {
		log.Error("connection check failed", append(attrs, "err", err)...)
		return err
	}
	log.Info("connection OK", attrs...)
	return nil
}

// connect opens a connection, keeping connection details out of the error.
func connect(ctx context.Context, name string, cc *pgx.ConnConfig) (*pgx.Conn, error) {
	conn, err := pgx.ConnectConfig(ctx, cc)
	if err != nil {
		return nil, fmt.Errorf("cannot connect to %s database: %s", name, safeErr(err, cc))
	}
	return conn, nil
}

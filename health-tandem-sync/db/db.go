// Package db opens the connection to health-db that both cmd/tandemload and
// cmd/tandemsync write into.
package db

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

// pingTimeout bounds the initial connectivity check so a network problem
// (bad host, unreachable endpoint, hung TLS handshake) fails fast instead of
// blocking forever, since database/sql's Ping with no context has no deadline.
const pingTimeout = 10 * time.Second

// Open connects to health-db using connStr, a standard postgres:// URL
// (or libpq keyword/value string).
//
// Prepared statement caching is disabled (QueryExecModeExec): many managed
// Postgres providers front connections with a transaction-mode connection
// pooler (e.g. PgBouncer), which multiplexes one logical connection across
// several backend connections. pgx's default extended-protocol mode
// prepares and caches statements per backend connection, which under a
// pooler leads to "prepared statement already exists" errors once a query
// is routed to a connection that already has a same-named statement from a
// different client. QueryExecModeExec skips server-side statement
// preparation, which pgx's own docs recommend over the simple protocol
// whenever the target supports the extended protocol at all, as connection
// poolers do.
//
// QueryExecModeExec (like the simple protocol) infers each parameter's
// PostgreSQL type from its Go type rather than the target column, so a
// []byte argument is always sent as bytea. Callers writing to a json/jsonb
// column must pass the JSON as a string (which defaults to text and
// implicitly casts), not []byte.
func Open(connStr string) (*sql.DB, error) {
	config, err := pgx.ParseConfig(connStr)
	if err != nil {
		return nil, fmt.Errorf("parsing connection string: %w", err)
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec

	sqlDB := stdlib.OpenDB(*config)
	ctx, cancel := context.WithTimeout(context.Background(), pingTimeout)
	defer cancel()
	if err := sqlDB.PingContext(ctx); err != nil {
		sqlDB.Close()
		return nil, fmt.Errorf("connecting to health-db: %w", err)
	}
	return sqlDB, nil
}

// URLFromEnv builds a postgres:// connection URL for health-db from
// discrete environment variables, matching the POSTGRES_* variables
// health-sync/health-mongo-sync/health-api already read from the shared
// root .env (see docker-compose.yml's env_file: lists) — so this module
// needs no docker-compose-level variable interpolation to reach health-db.
//
//	POSTGRES_HOST      (required)
//	POSTGRES_PORT      (required)
//	POSTGRES_DB        (required)
//	POSTGRES_USER      (required)
//	POSTGRES_PASSWORD  (required)
//	DB_SSL_MODE        (optional, default "require"; set to "disable" for
//	                    the internal docker-compose network, same as the
//	                    other health-db-connecting services)
func URLFromEnv() (string, error) {
	return urlFromEnvPrefix("POSTGRES", "DB_SSL_MODE")
}

// BackupURLFromEnv builds a postgres:// connection URL for an optional
// second, off-site Postgres instance that tandemsync/tandemload also write
// to as a backup, from BACKUP_POSTGRESQL_* variables in
// health-tandem-sync/.env (never the shared root .env — this is meant for
// an external/online database, unrelated to health-db):
//
//	BACKUP_POSTGRESQL_HOST      (required for backup to be enabled)
//	BACKUP_POSTGRESQL_PORT      (required for backup to be enabled)
//	BACKUP_POSTGRESQL_DB        (required for backup to be enabled)
//	BACKUP_POSTGRESQL_USER      (required for backup to be enabled)
//	BACKUP_POSTGRESQL_PASSWORD  (required for backup to be enabled)
//	BACKUP_DB_SSL_MODE          (optional, default "require")
//
// If none of the BACKUP_POSTGRESQL_* variables are set at all, backup is
// simply not configured: it returns ("", false, nil), not an error — the
// backup write is opt-in. If some but not all are set, that's a
// configuration mistake and returns an error, so a typo doesn't silently
// disable the backup.
//
// The backup database needs the same schema as health-db (tandem_bolus /
// tandem_basal / tandem_cgm); nothing here creates it. Schema is owned by
// health-api's goose migrations (health-api/database/migrations) — point
// health-api at the backup instance once to apply them there, or run goose
// directly against it.
func BackupURLFromEnv() (url string, enabled bool, err error) {
	const prefix = "BACKUP_POSTGRESQL"
	if !anyEnvSet(prefix) {
		return "", false, nil
	}
	u, err := urlFromEnvPrefix(prefix, "BACKUP_DB_SSL_MODE")
	if err != nil {
		return "", false, fmt.Errorf("BACKUP_POSTGRESQL_* is partially set: %w", err)
	}
	return u, true, nil
}

// anyEnvSet reports whether any of <prefix>_HOST/PORT/DB/USER/PASSWORD is
// set in the environment.
func anyEnvSet(prefix string) bool {
	for _, suffix := range []string{"HOST", "PORT", "DB", "USER", "PASSWORD"} {
		if os.Getenv(prefix+"_"+suffix) != "" {
			return true
		}
	}
	return false
}

// urlFromEnvPrefix builds a postgres:// connection URL from
// <prefix>_HOST/PORT/DB/USER/PASSWORD, with SSL mode from sslModeVar
// (default "require" if unset).
func urlFromEnvPrefix(prefix, sslModeVar string) (string, error) {
	host := os.Getenv(prefix + "_HOST")
	port := os.Getenv(prefix + "_PORT")
	dbName := os.Getenv(prefix + "_DB")
	user := os.Getenv(prefix + "_USER")
	password := os.Getenv(prefix + "_PASSWORD")

	var missing []string
	for _, kv := range []struct{ name, val string }{
		{prefix + "_HOST", host}, {prefix + "_PORT", port}, {prefix + "_DB", dbName},
		{prefix + "_USER", user}, {prefix + "_PASSWORD", password},
	} {
		if kv.val == "" {
			missing = append(missing, kv.name)
		}
	}
	if len(missing) > 0 {
		return "", fmt.Errorf("missing required environment variable(s): %v", missing)
	}

	sslMode := os.Getenv(sslModeVar)
	if sslMode == "" {
		sslMode = "require"
	}

	u := url.URL{
		Scheme:   "postgres",
		User:     url.UserPassword(user, password),
		Host:     fmt.Sprintf("%s:%s", host, port),
		Path:     "/" + dbName,
		RawQuery: "sslmode=" + sslMode,
	}
	return u.String(), nil
}

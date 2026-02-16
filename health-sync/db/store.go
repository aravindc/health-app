package db

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"fmt"
	"health-sync/model"
	"os"
	"time"

	log "github.com/sirupsen/logrus"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/dialect/pgdialect"
	"github.com/uptrace/bun/driver/pgdriver"
)

// DbClient returns a *sql.DB object for connecting to a PostgreSQL database.
func DbClient(postgres_host string, postgres_port string, pg_user string, pg_pass string, pg_db string) (*sql.DB, error) {

	// Check if NS_ENV is set
	_, nsEnvExists := os.LookupEnv("NS_ENV")
	if !nsEnvExists {
		os.Setenv("NS_ENV", "development")
	}

	sslMode := os.Getenv("DB_SSL_MODE")
	if sslMode == "" {
		sslMode = "require"
	}

	connOpts := []pgdriver.Option{
		pgdriver.WithNetwork("tcp"),
		pgdriver.WithAddr(fmt.Sprintf("%s:%s", postgres_host, postgres_port)),
		pgdriver.WithUser(pg_user),
		pgdriver.WithPassword(pg_pass),
		pgdriver.WithDatabase(pg_db),
		pgdriver.WithTimeout(5 * time.Second),
		pgdriver.WithDialTimeout(5 * time.Second),
		pgdriver.WithReadTimeout(5 * time.Second),
		pgdriver.WithWriteTimeout(5 * time.Second),
	}

	switch sslMode {
	case "disable":
		connOpts = append(connOpts, pgdriver.WithInsecure(true))
	case "require":
		connOpts = append(connOpts, pgdriver.WithTLSConfig(&tls.Config{
			ServerName: postgres_host,
		}))
	case "verify-ca", "verify-full":
		caCertPath := os.Getenv("DB_CA_CERT_PATH")
		if caCertPath == "" {
			return nil, fmt.Errorf("DB_CA_CERT_PATH must be set when DB_SSL_MODE is %s", sslMode)
		}
		caCert, err := os.ReadFile(caCertPath)
		if err != nil {
			return nil, fmt.Errorf("failed to read CA certificate: %w", err)
		}
		caCertPool := x509.NewCertPool()
		if !caCertPool.AppendCertsFromPEM(caCert) {
			return nil, fmt.Errorf("failed to parse CA certificate")
		}
		connOpts = append(connOpts, pgdriver.WithTLSConfig(&tls.Config{
			RootCAs:    caCertPool,
			ServerName: postgres_host,
		}))
	default:
		return nil, fmt.Errorf("invalid DB_SSL_MODE: %s. Must be one of: disable, require, verify-ca, verify-full", sslMode)
	}

	pgconn := pgdriver.NewConnector(connOpts...)
	db := sql.OpenDB(pgconn)

	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("failed to connect to database: %w", err)
	}
	log.Info("Database connection established")

	return db, nil
}

// EntriesExist checks if a Nightscoutdb entry with the specified ns_time exists in the database.
func EntriesExist(db_client *sql.DB, ns_time int64) (bool, error) {
	db := bun.NewDB(db_client, pgdialect.New())
	ctx := context.Background()
	exists, err := db.NewSelect().Table("nightscoutdb").Where("ns_time = ?", ns_time).Exists(ctx)
	if err != nil {
		return false, fmt.Errorf("exists check error: %w", err)
	}
	return exists, nil
}

// InsertEntries inserts a Nightscoutdb entry into the database.
func InsertEntries(db_client *sql.DB, nsItem model.Nightscoutdb) error {
	db := bun.NewDB(db_client, pgdialect.New())
	ctx := context.Background()
	newNsItem := &model.Nightscoutdb{
		Sgv:         nsItem.Sgv,
		Ns_time:     nsItem.Ns_time,
		Ns_datetime: nsItem.Ns_datetime,
		Trend:       nsItem.Trend,
		Utcoffset:   nsItem.Utcoffset,
		Systime:     nsItem.Systime,
	}
	log.Info("Trying to insert: ", nsItem.Sgv, nsItem.Ns_time, nsItem.Ns_datetime, nsItem.Trend, nsItem.Utcoffset, nsItem.Systime)
	_, err := db.NewInsert().Model(newNsItem).Exec(ctx)
	if err != nil {
		return fmt.Errorf("insert error: %w", err)
	}
	log.Info("Insert successful for ns_time: ", nsItem.Ns_time)
	return nil
}

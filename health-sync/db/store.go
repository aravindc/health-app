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
func DbClient(postgres_host string, postgres_port string, pg_user string, pg_pass string, pg_db string) *sql.DB {

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
			log.Fatal("DB_CA_CERT_PATH must be set when DB_SSL_MODE is ", sslMode)
		}
		caCert, err := os.ReadFile(caCertPath)
		if err != nil {
			log.Fatal("Failed to read CA certificate: ", err)
		}
		caCertPool := x509.NewCertPool()
		if !caCertPool.AppendCertsFromPEM(caCert) {
			log.Fatal("Failed to parse CA certificate")
		}
		connOpts = append(connOpts, pgdriver.WithTLSConfig(&tls.Config{
			RootCAs:    caCertPool,
			ServerName: postgres_host,
		}))
	default:
		log.Fatal("Invalid DB_SSL_MODE: ", sslMode, ". Must be one of: disable, require, verify-ca, verify-full")
	}

	pgconn := pgdriver.NewConnector(connOpts...)
	return sql.OpenDB(pgconn)
}

// SelectEntries retrieves a list of Nightscoutdb entries from the database.
func SelectEntries(db_client *sql.DB) []model.Nightscoutdb {
	db := bun.NewDB(db_client, pgdialect.New())
	ctx := context.Background()
	var nsentries []model.Nightscoutdb
	err := db.NewSelect().Table("nightscoutdb").Model(&nsentries).Limit(5).Scan(ctx)
	if err != nil {
		log.Fatal("Select error: ", err)
	}
	return nsentries
}

// EntriesExist checks if a Nightscoutdb entry with the specified ns_time exists in the database.
func EntriesExist(db_client *sql.DB, ns_time int64) bool {
	db := bun.NewDB(db_client, pgdialect.New())
	ctx := context.Background()
	exists, err := db.NewSelect().Table("nightscoutdb").Where("ns_time = ?", ns_time).Exists(ctx)
	if err != nil {
		log.Fatal("Exists error: ", err)
	}
	return exists
}

// InsertEntries inserts a Nightscoutdb entry into the database.
func InsertEntries(db_client *sql.DB, nsItem model.Nightscoutdb) {
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
	res, err := db.NewInsert().Model(newNsItem).Exec(ctx)
	log.Info("Insert result: ", res)
	if err != nil {
		log.Fatal("Insert error: ", err)
	}
}

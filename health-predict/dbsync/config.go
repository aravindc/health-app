package main

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/joho/godotenv"
)

// Config holds runtime settings. Credentials live only inside the pgx
// configs and are never logged.
type Config struct {
	Source *pgx.ConnConfig
	Target *pgx.ConnConfig

	Schemas          []string          // source schemas to sync
	Exclude          map[string]bool   // "schema.table" entries to skip
	WatermarkColumns []string          // candidate column names, in priority order
	TableWatermarks  map[string]string // "schema.table" -> explicit watermark column
}

// loadEnv loads variables from the given .env file. When path is empty it
// tries ./.env and then ../.env. Existing environment variables win.
func loadEnv(path string) error {
	if path != "" {
		return godotenv.Load(path)
	}
	for _, p := range []string{".env", "../.env"} {
		if _, err := os.Stat(p); err == nil {
			return godotenv.Load(p)
		}
	}
	return errors.New("no .env file found in . or ..")
}

func loadConfig() (*Config, error) {
	src, err := dbConfig("SOURCE")
	if err != nil {
		return nil, err
	}
	tgt, err := dbConfig("TARGET")
	if err != nil {
		return nil, err
	}

	cfg := &Config{
		Source:           src,
		Target:           tgt,
		Schemas:          splitList(getenv("SYNC_SCHEMAS", "public")),
		Exclude:          map[string]bool{},
		WatermarkColumns: splitList(getenv("SYNC_WATERMARK_COLUMNS", "updated_at,modified_at,last_modified,created_at,inserted_at")),
		TableWatermarks:  map[string]string{},
	}
	for _, t := range splitList(os.Getenv("SYNC_EXCLUDE_TABLES")) {
		cfg.Exclude[qualify(t)] = true
	}
	for _, pair := range splitList(os.Getenv("SYNC_TABLE_WATERMARKS")) {
		tbl, col, ok := strings.Cut(pair, ":")
		if !ok || tbl == "" || col == "" {
			return nil, fmt.Errorf("SYNC_TABLE_WATERMARKS: invalid entry %q (want schema.table:column)", pair)
		}
		cfg.TableWatermarks[qualify(tbl)] = strings.TrimSpace(col)
	}
	return cfg, nil
}

// dbConfig builds a pgx config from <prefix>_DB_* variables.
func dbConfig(prefix string) (*pgx.ConnConfig, error) {
	need := func(name string) (string, error) {
		v := os.Getenv(prefix + "_DB_" + name)
		if v == "" {
			return "", fmt.Errorf("%s_DB_%s is not set", prefix, name)
		}
		return v, nil
	}
	var vals [5]string
	for i, n := range []string{"HOST", "PORT", "NAME", "USER", "PASSWORD"} {
		v, err := need(n)
		if err != nil {
			return nil, err
		}
		vals[i] = v
	}
	host, port, name, user, pass := vals[0], vals[1], vals[2], vals[3], vals[4]

	q := url.Values{}
	q.Set("sslmode", getenv(prefix+"_DB_SSLMODE", "prefer"))
	q.Set("application_name", "dbsync")
	u := url.URL{
		Scheme:   "postgres",
		User:     url.UserPassword(user, pass),
		Host:     net.JoinHostPort(host, port),
		Path:     "/" + name,
		RawQuery: q.Encode(),
	}
	cc, err := pgx.ParseConfig(u.String())
	if err != nil {
		// Do not wrap err: keep the message free of connection details.
		return nil, fmt.Errorf("%s database settings are invalid", strings.ToLower(prefix))
	}
	cc.ConnectTimeout = 10 * time.Second
	// Fixed session settings so COPY text output from the source parses
	// identically on the target regardless of server defaults.
	cc.RuntimeParams["datestyle"] = "ISO, MDY"
	cc.RuntimeParams["intervalstyle"] = "postgres"
	cc.RuntimeParams["timezone"] = "UTC"
	cc.RuntimeParams["extra_float_digits"] = "3"
	cc.RuntimeParams["bytea_output"] = "hex"
	return cc, nil
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// qualify defaults unqualified table names to the public schema.
func qualify(t string) string {
	t = strings.TrimSpace(t)
	if !strings.Contains(t, ".") {
		return "public." + t
	}
	return t
}

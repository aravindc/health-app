package config

import (
	"fmt"
	"log"
	"net/netip"
	"os"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
)

// Config holds all configuration for the application
type Config struct {
	DSN            string // Data Source Name
	MinMmol        string
	StrictMaxMmol  string
	MedicalMaxMmol string
	AppEnv         string
	APIKeys        []string
	// CORSAllowedOrigins is the browser origins allowed to call the API
	// cross-origin: CORS_ALLOWED_ORIGINS if set, else the defaults for AppEnv.
	CORSAllowedOrigins []string
	// TrustedProxies is the IPs/CIDRs whose X-Forwarded-For entries the API
	// believes when working out a request's client IP (gin's ClientIP, used
	// by the rate limiter): TRUSTED_PROXIES if set, else
	// DefaultTrustedProxies.
	TrustedProxies []string
	// MaxHistoryDays is how far back the API serves data: MAX_HISTORY_DAYS
	// if set, else DefaultMaxHistoryDays. It can only be raised above the
	// default, since the dashboard's fixed 90-day views (GMI, heatmaps, the
	// 90d period) need at least that much.
	MaxHistoryDays int
}

// DefaultMaxHistoryDays is the API's history window unless an admin
// extends it with MAX_HISTORY_DAYS.
const DefaultMaxHistoryDays = 90

// DefaultTrustedProxies covers loopback and the private ranges Docker
// networks use, i.e. the Caddy → health-fe nginx → health-api chain.
// gin reads X-Forwarded-For right to left and stops at the first address
// outside these, so a public client can't pass off a spoofed address:
// Caddy replaces the header for untrusted clients, nginx appends Caddy's
// address, and the client's own public IP is the first untrusted entry.
// Clients on these private ranges (LAN, Docker) can still spoof; narrow
// TRUSTED_PROXIES to the actual proxy addresses to rule that out.
var DefaultTrustedProxies = []string{
	"127.0.0.0/8",
	"::1/128",
	"10.0.0.0/8",
	"172.16.0.0/12",
	"192.168.0.0/16",
	"fc00::/7",
}

// Default CORS origins, used when CORS_ALLOWED_ORIGINS is unset.
var (
	defaultProdCORSOrigins = []string{"https://ui.health.pers.dev"}
	defaultDevCORSOrigins  = []string{
		"http://localhost:5173",
		"http://localhost:4000",
		"http://health-ui:9093",
		"http://localhost:9083",
	}
)

// splitCSV splits a comma-separated list, trimming spaces and dropping
// empty entries. Returns nil for an empty string.
func splitCSV(s string) []string {
	var out []string
	for _, part := range strings.Split(s, ",") {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

// LoadConfig loads configuration from .env.local
func LoadConfig() (*Config, error) {
	// find_dotenv logic
	if err := godotenv.Load(".env.local"); err != nil {
		log.Println("No .env.local file found, reading from environment")
	}

	postgresUser := os.Getenv("POSTGRES_USER")
	postgresPass := os.Getenv("POSTGRES_PASSWORD")
	postgresHost := os.Getenv("POSTGRES_HOST")
	postgresPort := os.Getenv("POSTGRES_PORT")
	postgresDB := os.Getenv("POSTGRES_DB")

	// Create the DSN string
	// "postgresql+psycopg2://..." -> "host=... user=... password=... dbname=... port=...
	dsn := fmt.Sprintf("host=%s user=%s password=%s dbname=%s port=%s sslmode=disable",
		postgresHost, postgresUser, postgresPass, postgresDB, postgresPort)

	apiKeys := splitCSV(os.Getenv("API_KEYS"))

	appEnv := os.Getenv("NS_ENV")
	corsOrigins := splitCSV(os.Getenv("CORS_ALLOWED_ORIGINS"))
	for _, o := range corsOrigins {
		if !strings.HasPrefix(o, "http://") && !strings.HasPrefix(o, "https://") {
			return nil, fmt.Errorf("CORS_ALLOWED_ORIGINS: %q must start with http:// or https://", o)
		}
	}
	trustedProxies := splitCSV(os.Getenv("TRUSTED_PROXIES"))
	for _, p := range trustedProxies {
		if _, err := netip.ParsePrefix(p); err == nil {
			continue
		}
		if _, err := netip.ParseAddr(p); err == nil {
			continue
		}
		return nil, fmt.Errorf("TRUSTED_PROXIES: %q is not an IP address or CIDR", p)
	}
	if len(trustedProxies) == 0 {
		trustedProxies = DefaultTrustedProxies
	}

	maxHistoryDays := DefaultMaxHistoryDays
	if v := strings.TrimSpace(os.Getenv("MAX_HISTORY_DAYS")); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < DefaultMaxHistoryDays {
			return nil, fmt.Errorf("MAX_HISTORY_DAYS: %q must be a whole number of days, at least %d", v, DefaultMaxHistoryDays)
		}
		maxHistoryDays = n
	}

	if len(corsOrigins) == 0 {
		if appEnv == "production" {
			corsOrigins = defaultProdCORSOrigins
		} else {
			corsOrigins = defaultDevCORSOrigins
		}
	}

	return &Config{
		DSN:                dsn,
		MinMmol:            os.Getenv("MIN_MMOL"),
		StrictMaxMmol:      os.Getenv("STRICT_MAX_MMOL"),
		MedicalMaxMmol:     os.Getenv("MEDICAL_MAX_MMOL"),
		AppEnv:             appEnv,
		APIKeys:            apiKeys,
		CORSAllowedOrigins: corsOrigins,
		TrustedProxies:     trustedProxies,
		MaxHistoryDays:     maxHistoryDays,
	}, nil
}

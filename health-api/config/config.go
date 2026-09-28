package config

import (
	"fmt"
	"log"
	"os"
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
	}, nil
}

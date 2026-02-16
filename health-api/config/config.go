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

	var apiKeys []string
	if keys := os.Getenv("API_KEYS"); keys != "" {
		for _, k := range strings.Split(keys, ",") {
			if trimmed := strings.TrimSpace(k); trimmed != "" {
				apiKeys = append(apiKeys, trimmed)
			}
		}
	}

	return &Config{
		DSN:            dsn,
		MinMmol:        os.Getenv("MIN_MMOL"),
		StrictMaxMmol:  os.Getenv("STRICT_MAX_MMOL"),
		MedicalMaxMmol: os.Getenv("MEDICAL_MAX_MMOL"),
		AppEnv:         os.Getenv("NS_ENV"),
		APIKeys:        apiKeys,
	}, nil
}

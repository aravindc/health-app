package database

import (
	"fmt"
	"log/slog"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

// ConnectDB initializes the GORM database connection
func ConnectDB(dsn string) (*gorm.DB, error) {
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil {
		return nil, fmt.Errorf("failed to connect to database: %w", err)
	}
	slog.Info("Database connection established")
	return db, nil
}

// CreateDbAndTables corresponds to your create_db_and_tables()
// It takes a list of all your models
func CreateDbAndTables(db *gorm.DB, models ...interface{}) {
	slog.Info("Running auto-migration...")
	err := db.AutoMigrate(models...)
	if err != nil {
		slog.Error("Failed to auto-migrate tables", "error", err)
		// In production, you might want to panic or exit
	}
	slog.Info("Auto-migration complete")
}

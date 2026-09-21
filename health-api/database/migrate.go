package database

import (
	"database/sql"
	"embed"
	"fmt"

	"github.com/pressly/goose/v3"
)

//go:embed migrations/*.sql
var migrationsFS embed.FS

const migrationsDir = "migrations"

// Migrate brings the database schema up to date by running any pending
// goose migrations embedded from the migrations/ directory. It takes a
// *sql.DB rather than the *gorm.DB ConnectDB returns, since goose drives
// migrations directly over database/sql — pass the result of calling DB()
// on the *gorm.DB from ConnectDB, which shares the same underlying
// connection pool rather than opening a second one.
func Migrate(db *sql.DB) error {
	goose.SetBaseFS(migrationsFS)
	defer goose.SetBaseFS(nil)

	if err := goose.SetDialect("postgres"); err != nil {
		return fmt.Errorf("setting goose dialect: %w", err)
	}
	if err := goose.Up(db, migrationsDir); err != nil {
		return fmt.Errorf("running migrations: %w", err)
	}
	return nil
}

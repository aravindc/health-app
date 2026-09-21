package database

import (
	"testing"

	"github.com/pressly/goose/v3"
)

func TestMigrationsParse(t *testing.T) {
	goose.SetBaseFS(migrationsFS)
	defer goose.SetBaseFS(nil)

	migrations, err := goose.CollectMigrations(migrationsDir, 0, goose.MaxVersion)
	if err != nil {
		t.Fatalf("CollectMigrations failed: %v", err)
	}
	if len(migrations) == 0 {
		t.Fatal("expected at least one migration")
	}
	for _, m := range migrations {
		if m.Source == "" {
			t.Errorf("migration %d has no source file", m.Version)
		}
	}
}

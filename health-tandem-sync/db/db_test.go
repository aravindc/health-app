package db

import (
	"strings"
	"testing"
)

func setEnv(t *testing.T, vars map[string]string) {
	t.Helper()
	for k, v := range vars {
		t.Setenv(k, v)
	}
}

func TestURLFromEnv(t *testing.T) {
	setEnv(t, map[string]string{
		"POSTGRES_HOST":     "health-db",
		"POSTGRES_PORT":     "5432",
		"POSTGRES_DB":       "health",
		"POSTGRES_USER":     "admin",
		"POSTGRES_PASSWORD": "s3cret",
	})

	got, err := URLFromEnv()
	if err != nil {
		t.Fatalf("URLFromEnv failed: %v", err)
	}
	want := "postgres://admin:s3cret@health-db:5432/health?sslmode=require"
	if got != want {
		t.Errorf("expected %q, got %q", want, got)
	}
}

func TestURLFromEnvCustomSSLMode(t *testing.T) {
	setEnv(t, map[string]string{
		"POSTGRES_HOST":     "health-db",
		"POSTGRES_PORT":     "5432",
		"POSTGRES_DB":       "health",
		"POSTGRES_USER":     "admin",
		"POSTGRES_PASSWORD": "s3cret",
		"DB_SSL_MODE":       "disable",
	})

	got, err := URLFromEnv()
	if err != nil {
		t.Fatalf("URLFromEnv failed: %v", err)
	}
	if !strings.HasSuffix(got, "sslmode=disable") {
		t.Errorf("expected sslmode=disable in URL, got %q", got)
	}
}

func TestURLFromEnvMissingVars(t *testing.T) {
	setEnv(t, map[string]string{
		"POSTGRES_HOST": "health-db",
		// POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD unset
	})

	_, err := URLFromEnv()
	if err == nil {
		t.Fatal("expected an error for missing required vars, got nil")
	}
}

func TestURLFromEnvSpecialCharsInPassword(t *testing.T) {
	// Passwords containing URL-special characters must be escaped so
	// pgx.ParseConfig doesn't misparse the connection string.
	setEnv(t, map[string]string{
		"POSTGRES_HOST":     "health-db",
		"POSTGRES_PORT":     "5432",
		"POSTGRES_DB":       "health",
		"POSTGRES_USER":     "admin",
		"POSTGRES_PASSWORD": "p@ss:word/with?special&chars",
	})

	got, err := URLFromEnv()
	if err != nil {
		t.Fatalf("URLFromEnv failed: %v", err)
	}
	if !strings.Contains(got, "%40") { // '@' escaped
		t.Errorf("expected password to be URL-escaped, got %q", got)
	}
}

func TestBackupURLFromEnvNotConfigured(t *testing.T) {
	// None of BACKUP_POSTGRESQL_* set at all: backup is simply disabled,
	// not an error.
	_, enabled, err := BackupURLFromEnv()
	if err != nil {
		t.Fatalf("BackupURLFromEnv failed: %v", err)
	}
	if enabled {
		t.Error("expected backup to be disabled when no BACKUP_POSTGRESQL_* vars are set")
	}
}

func TestBackupURLFromEnvConfigured(t *testing.T) {
	setEnv(t, map[string]string{
		"BACKUP_POSTGRESQL_HOST":     "backup.example.com",
		"BACKUP_POSTGRESQL_PORT":     "5432",
		"BACKUP_POSTGRESQL_DB":       "tandem_backup",
		"BACKUP_POSTGRESQL_USER":     "backupuser",
		"BACKUP_POSTGRESQL_PASSWORD": "s3cret",
	})

	got, enabled, err := BackupURLFromEnv()
	if err != nil {
		t.Fatalf("BackupURLFromEnv failed: %v", err)
	}
	if !enabled {
		t.Fatal("expected backup to be enabled when BACKUP_POSTGRESQL_* vars are set")
	}
	want := "postgres://backupuser:s3cret@backup.example.com:5432/tandem_backup?sslmode=require"
	if got != want {
		t.Errorf("expected %q, got %q", want, got)
	}
}

func TestBackupURLFromEnvCustomSSLMode(t *testing.T) {
	setEnv(t, map[string]string{
		"BACKUP_POSTGRESQL_HOST":     "backup.example.com",
		"BACKUP_POSTGRESQL_PORT":     "5432",
		"BACKUP_POSTGRESQL_DB":       "tandem_backup",
		"BACKUP_POSTGRESQL_USER":     "backupuser",
		"BACKUP_POSTGRESQL_PASSWORD": "s3cret",
		"BACKUP_DB_SSL_MODE":         "verify-full",
	})

	got, enabled, err := BackupURLFromEnv()
	if err != nil {
		t.Fatalf("BackupURLFromEnv failed: %v", err)
	}
	if !enabled {
		t.Fatal("expected backup to be enabled")
	}
	if !strings.HasSuffix(got, "sslmode=verify-full") {
		t.Errorf("expected sslmode=verify-full in URL, got %q", got)
	}
}

func TestBackupURLFromEnvPartiallyConfigured(t *testing.T) {
	// Only some BACKUP_POSTGRESQL_* vars set: a likely typo/mistake, so
	// this should error rather than silently disable the backup or connect
	// with a wrong/incomplete config.
	setEnv(t, map[string]string{
		"BACKUP_POSTGRESQL_HOST": "backup.example.com",
		// BACKUP_POSTGRESQL_PORT/DB/USER/PASSWORD unset
	})

	_, enabled, err := BackupURLFromEnv()
	if err == nil {
		t.Fatal("expected an error for a partially-configured backup, got nil")
	}
	if enabled {
		t.Error("expected enabled=false when BackupURLFromEnv returns an error")
	}
}

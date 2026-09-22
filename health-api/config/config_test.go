package config

import (
	"testing"
)

// setEnv sets the given env vars for the duration of the test via
// t.Setenv, which restores their prior value automatically on cleanup.
func setEnv(t *testing.T, vars map[string]string) {
	t.Helper()
	for k, v := range vars {
		t.Setenv(k, v)
	}
}

func TestLoadConfig_BuildsDSN(t *testing.T) {
	setEnv(t, map[string]string{
		"POSTGRES_USER":     "admin",
		"POSTGRES_PASSWORD": "s3cret",
		"POSTGRES_HOST":     "health-db",
		"POSTGRES_PORT":     "5432",
		"POSTGRES_DB":       "health",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	want := "host=health-db user=admin password=s3cret dbname=health port=5432 sslmode=disable"
	if cfg.DSN != want {
		t.Errorf("expected DSN %q, got %q", want, cfg.DSN)
	}
}

func TestLoadConfig_ThresholdsAndEnv(t *testing.T) {
	setEnv(t, map[string]string{
		"MIN_MMOL":         "4.0",
		"STRICT_MAX_MMOL":  "8.0",
		"MEDICAL_MAX_MMOL": "10.0",
		"NS_ENV":           "production",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	if cfg.MinMmol != "4.0" {
		t.Errorf("expected MinMmol=4.0, got %q", cfg.MinMmol)
	}
	if cfg.StrictMaxMmol != "8.0" {
		t.Errorf("expected StrictMaxMmol=8.0, got %q", cfg.StrictMaxMmol)
	}
	if cfg.MedicalMaxMmol != "10.0" {
		t.Errorf("expected MedicalMaxMmol=10.0, got %q", cfg.MedicalMaxMmol)
	}
	if cfg.AppEnv != "production" {
		t.Errorf("expected AppEnv=production, got %q", cfg.AppEnv)
	}
}

func TestLoadConfig_APIKeysParsedAndTrimmed(t *testing.T) {
	setEnv(t, map[string]string{
		"API_KEYS": " key1, key2 ,key3",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	want := []string{"key1", "key2", "key3"}
	if len(cfg.APIKeys) != len(want) {
		t.Fatalf("expected %d API keys, got %d (%v)", len(want), len(cfg.APIKeys), cfg.APIKeys)
	}
	for i, k := range want {
		if cfg.APIKeys[i] != k {
			t.Errorf("APIKeys[%d] = %q, want %q", i, cfg.APIKeys[i], k)
		}
	}
}

func TestLoadConfig_APIKeysEmpty(t *testing.T) {
	setEnv(t, map[string]string{
		"API_KEYS": "",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	if cfg.APIKeys != nil {
		t.Errorf("expected nil APIKeys for empty API_KEYS, got %v", cfg.APIKeys)
	}
}

func TestLoadConfig_APIKeysSkipsEmptyEntries(t *testing.T) {
	// A trailing comma or doubled comma shouldn't produce a blank key.
	setEnv(t, map[string]string{
		"API_KEYS": "key1,,key2,",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	want := []string{"key1", "key2"}
	if len(cfg.APIKeys) != len(want) {
		t.Fatalf("expected %d API keys, got %d (%v)", len(want), len(cfg.APIKeys), cfg.APIKeys)
	}
	for i, k := range want {
		if cfg.APIKeys[i] != k {
			t.Errorf("APIKeys[%d] = %q, want %q", i, cfg.APIKeys[i], k)
		}
	}
}

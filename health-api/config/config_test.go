package config

import (
	"slices"
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

func TestLoadConfig_CORSAllowedOriginsParsedAndTrimmed(t *testing.T) {
	setEnv(t, map[string]string{
		"NS_ENV":               "production",
		"CORS_ALLOWED_ORIGINS": " https://health.example.com, ,http://localhost:3000 ",
	})

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	want := []string{"https://health.example.com", "http://localhost:3000"}
	if !slices.Equal(cfg.CORSAllowedOrigins, want) {
		t.Errorf("CORSAllowedOrigins = %v, want %v", cfg.CORSAllowedOrigins, want)
	}
}

func TestLoadConfig_CORSAllowedOriginsDefaults(t *testing.T) {
	tests := []struct {
		appEnv string
		want   []string
	}{
		{"production", defaultProdCORSOrigins},
		{"development", defaultDevCORSOrigins},
		{"", defaultDevCORSOrigins},
	}
	for _, tt := range tests {
		t.Run("NS_ENV="+tt.appEnv, func(t *testing.T) {
			setEnv(t, map[string]string{
				"NS_ENV":               tt.appEnv,
				"CORS_ALLOWED_ORIGINS": "",
			})

			cfg, err := LoadConfig()
			if err != nil {
				t.Fatalf("LoadConfig failed: %v", err)
			}
			if !slices.Equal(cfg.CORSAllowedOrigins, tt.want) {
				t.Errorf("CORSAllowedOrigins = %v, want %v", cfg.CORSAllowedOrigins, tt.want)
			}
		})
	}
}

func TestLoadConfig_CORSAllowedOriginsRejectsMissingScheme(t *testing.T) {
	setEnv(t, map[string]string{
		"CORS_ALLOWED_ORIGINS": "https://ok.example.com,health.example.com",
	})

	if _, err := LoadConfig(); err == nil {
		t.Fatal("expected an error for an origin without http:// or https://")
	}
}

func TestLoadConfig_TrustedProxiesDefault(t *testing.T) {
	setEnv(t, map[string]string{"TRUSTED_PROXIES": ""})
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	if !slices.Equal(cfg.TrustedProxies, DefaultTrustedProxies) {
		t.Errorf("expected defaults %v, got %v", DefaultTrustedProxies, cfg.TrustedProxies)
	}
}

func TestLoadConfig_TrustedProxiesParsedAndTrimmed(t *testing.T) {
	setEnv(t, map[string]string{"TRUSTED_PROXIES": " 172.18.0.0/16 , 10.0.0.5,,fd00::/8 "})
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	want := []string{"172.18.0.0/16", "10.0.0.5", "fd00::/8"}
	if !slices.Equal(cfg.TrustedProxies, want) {
		t.Errorf("expected %v, got %v", want, cfg.TrustedProxies)
	}
}

func TestLoadConfig_TrustedProxiesRejectsInvalid(t *testing.T) {
	for _, v := range []string{"caddy", "172.18.0.0/99", "10.0.0.0/8,not-an-ip"} {
		t.Run(v, func(t *testing.T) {
			setEnv(t, map[string]string{"TRUSTED_PROXIES": v})
			if _, err := LoadConfig(); err == nil {
				t.Fatalf("expected an error for TRUSTED_PROXIES=%q", v)
			}
		})
	}
}

func TestLoadConfig_MaxHistoryDaysDefault(t *testing.T) {
	setEnv(t, map[string]string{"MAX_HISTORY_DAYS": ""})
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	if cfg.MaxHistoryDays != DefaultMaxHistoryDays {
		t.Errorf("expected %d, got %d", DefaultMaxHistoryDays, cfg.MaxHistoryDays)
	}
}

func TestLoadConfig_MaxHistoryDaysExtended(t *testing.T) {
	setEnv(t, map[string]string{"MAX_HISTORY_DAYS": " 365 "})
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}
	if cfg.MaxHistoryDays != 365 {
		t.Errorf("expected 365, got %d", cfg.MaxHistoryDays)
	}
}

func TestLoadConfig_MaxHistoryDaysRejectsInvalidOrShorter(t *testing.T) {
	for _, v := range []string{"30", "89", "0", "-5", "ninety", "90.5"} {
		t.Run(v, func(t *testing.T) {
			setEnv(t, map[string]string{"MAX_HISTORY_DAYS": v})
			if _, err := LoadConfig(); err == nil {
				t.Fatalf("expected an error for MAX_HISTORY_DAYS=%q", v)
			}
		})
	}
}

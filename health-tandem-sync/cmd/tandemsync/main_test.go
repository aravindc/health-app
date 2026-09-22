package main

import (
	"os"
	"testing"
	"time"
)

func TestEnvDuration_UsesDefaultWhenUnset(t *testing.T) {
	unsetEnv(t, "SOME_DURATION_VAR")
	got := envDuration("SOME_DURATION_VAR", 5, time.Second)
	want := 5 * time.Second
	if got != want {
		t.Errorf("envDuration = %v, want %v", got, want)
	}
}

func TestEnvDuration_ParsesValidValue(t *testing.T) {
	t.Setenv("SOME_DURATION_VAR", "10")
	got := envDuration("SOME_DURATION_VAR", 5, time.Minute)
	want := 10 * time.Minute
	if got != want {
		t.Errorf("envDuration = %v, want %v", got, want)
	}
}

func TestEnvDuration_InvalidValueFallsBackToDefault(t *testing.T) {
	t.Setenv("SOME_DURATION_VAR", "not-a-number")
	got := envDuration("SOME_DURATION_VAR", 5, time.Second)
	want := 5 * time.Second
	if got != want {
		t.Errorf("envDuration = %v, want %v (default on parse failure)", got, want)
	}
}

func TestEnvDuration_NonPositiveValueFallsBackToDefault(t *testing.T) {
	t.Setenv("SOME_DURATION_VAR", "0")
	got := envDuration("SOME_DURATION_VAR", 5, time.Second)
	want := 5 * time.Second
	if got != want {
		t.Errorf("envDuration = %v, want %v (default for zero)", got, want)
	}

	t.Setenv("SOME_DURATION_VAR", "-1")
	got = envDuration("SOME_DURATION_VAR", 5, time.Second)
	if got != want {
		t.Errorf("envDuration = %v, want %v (default for negative)", got, want)
	}
}

// unsetEnv ensures key is unset for the duration of the test, restoring
// whatever value (or absence) it had beforehand on cleanup. t.Setenv alone
// can only set a value, so it's paired with os.Unsetenv to actually clear it
// while still getting t.Setenv's automatic restore behavior.
func unsetEnv(t *testing.T, key string) {
	t.Helper()
	t.Setenv(key, "")
	os.Unsetenv(key)
}

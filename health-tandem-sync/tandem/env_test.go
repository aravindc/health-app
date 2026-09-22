package tandem

import (
	"os"
	"path/filepath"
	"testing"
)

// unsetEnv ensures key is unset for the duration of the test, restoring
// whatever value (or absence) it had beforehand on cleanup. t.Setenv alone
// can only set a value, so it's paired with os.Unsetenv to actually clear it
// while still getting t.Setenv's automatic restore behavior.
func unsetEnv(t *testing.T, key string) {
	t.Helper()
	t.Setenv(key, "")
	os.Unsetenv(key)
}

func TestLoadDotEnv_SetsVars(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "FOO=bar\nBAZ=qux\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}
	unsetEnv(t, "FOO")
	unsetEnv(t, "BAZ")

	if err := LoadDotEnv(path); err != nil {
		t.Fatalf("LoadDotEnv failed: %v", err)
	}
	if got := os.Getenv("FOO"); got != "bar" {
		t.Errorf("FOO = %q, want bar", got)
	}
	if got := os.Getenv("BAZ"); got != "qux" {
		t.Errorf("BAZ = %q, want qux", got)
	}
}

func TestLoadDotEnv_SkipsBlankAndCommentLines(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "# a comment\n\nFOO=bar\n   # indented comment\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}
	unsetEnv(t, "FOO")

	if err := LoadDotEnv(path); err != nil {
		t.Fatalf("LoadDotEnv failed: %v", err)
	}
	if got := os.Getenv("FOO"); got != "bar" {
		t.Errorf("FOO = %q, want bar", got)
	}
}

func TestLoadDotEnv_DoesNotOverrideExisting(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "FOO=fromfile\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}
	t.Setenv("FOO", "fromenv")

	if err := LoadDotEnv(path); err != nil {
		t.Fatalf("LoadDotEnv failed: %v", err)
	}
	if got := os.Getenv("FOO"); got != "fromenv" {
		t.Errorf("FOO = %q, want fromenv (existing value should not be overridden)", got)
	}
}

func TestLoadDotEnv_MissingFileIsNotAnError(t *testing.T) {
	if err := LoadDotEnv("/nonexistent/path/.env"); err != nil {
		t.Errorf("expected no error for a missing file, got %v", err)
	}
}

func TestLoadDotEnv_TrimsQuotesAndWhitespace(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := `  SPACED  =   value  ` + "\n" + `QUOTED="quoted value"` + "\n" + `SINGLE='single value'` + "\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}
	unsetEnv(t, "SPACED")
	unsetEnv(t, "QUOTED")
	unsetEnv(t, "SINGLE")

	if err := LoadDotEnv(path); err != nil {
		t.Fatalf("LoadDotEnv failed: %v", err)
	}
	if got := os.Getenv("SPACED"); got != "value" {
		t.Errorf("SPACED = %q, want %q", got, "value")
	}
	if got := os.Getenv("QUOTED"); got != "quoted value" {
		t.Errorf("QUOTED = %q, want %q", got, "quoted value")
	}
	if got := os.Getenv("SINGLE"); got != "single value" {
		t.Errorf("SINGLE = %q, want %q", got, "single value")
	}
}

func TestLoadDotEnv_SkipsLineWithoutEquals(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := "NOTKEYVALUE\nFOO=bar\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}
	unsetEnv(t, "FOO")

	if err := LoadDotEnv(path); err != nil {
		t.Fatalf("LoadDotEnv failed: %v", err)
	}
	if got := os.Getenv("FOO"); got != "bar" {
		t.Errorf("FOO = %q, want bar", got)
	}
}

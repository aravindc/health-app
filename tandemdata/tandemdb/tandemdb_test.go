package tandemdb

import (
	"strings"
	"testing"

	"github.com/pressly/goose/v3"

	"tandemdata/pumplog"
)

func TestMigrationsParse(t *testing.T) {
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

func TestBuildBatchInsert(t *testing.T) {
	events := []pumplog.Event{
		{
			DeviceAssignmentID: "dev-1",
			SequenceGroup:      0,
			SequenceNumber:     1,
			EventCode:          399,
			PumpDateTime:       "2026-01-01T00:00:00",
			EstimatedDateTime:  "2026-01-01T00:00:00Z",
			EventProperties:    map[string]interface{}{"rate": 5},
		},
		{
			DeviceAssignmentID: "dev-1",
			SequenceGroup:      0,
			SequenceNumber:     2,
			EventCode:          16,
			PumpDateTime:       "2026-01-01T00:05:00",
			EstimatedDateTime:  "2026-01-01T00:05:00Z",
			EventProperties:    map[string]interface{}{"bg": 120},
		},
	}

	query, args, err := buildBatchInsert(events, false)
	if err != nil {
		t.Fatalf("buildBatchInsert failed: %v", err)
	}

	if !strings.Contains(query, "($1, $2, $3, $4, $5, $6, $7, $8, $9)") {
		t.Errorf("expected first value group with $1-$9 placeholders in query: %s", query)
	}
	if !strings.Contains(query, "($10, $11, $12, $13, $14, $15, $16, $17, $18)") {
		t.Errorf("expected second value group with $10-$18 placeholders in query: %s", query)
	}

	wantArgs := len(events) * 9
	if len(args) != wantArgs {
		t.Errorf("expected %d args, got %d", wantArgs, len(args))
	}

	if !strings.Contains(query, "ON CONFLICT") {
		t.Errorf("expected upsert clause in query: %s", query)
	}

	if args[0] != "dev-1" || args[3] != 399 {
		t.Errorf("unexpected first-row args: %v", args[:9])
	}
	if args[9] != "dev-1" || args[12] != 16 {
		t.Errorf("unexpected second-row args: %v", args[9:18])
	}

	// event_properties must be a string, not []byte: with QueryExecModeExec,
	// pgx infers the PostgreSQL parameter type from the Go type, and []byte
	// always maps to bytea regardless of the jsonb target column, which
	// PostgreSQL then rejects. A string defaults to text and casts to jsonb.
	firstProps, ok := args[7].(string)
	if !ok {
		t.Fatalf("expected event_properties arg to be string (for jsonb), got %T", args[7])
	}
	if firstProps != `{"rate":5}` {
		t.Errorf("unexpected first-row event_properties JSON: %s", firstProps)
	}
	secondProps, ok := args[16].(string)
	if !ok {
		t.Fatalf("expected event_properties arg to be string (for jsonb), got %T", args[16])
	}
	if secondProps != `{"bg":120}` {
		t.Errorf("unexpected second-row event_properties JSON: %s", secondProps)
	}
}

func TestBuildBatchInsertEmpty(t *testing.T) {
	query, args, err := buildBatchInsert(nil, false)
	if err != nil {
		t.Fatalf("buildBatchInsert failed: %v", err)
	}
	if len(args) != 0 {
		t.Errorf("expected no args for empty batch, got %d", len(args))
	}
	if strings.Contains(query, "()") {
		t.Errorf("expected no empty value group in query: %s", query)
	}
}

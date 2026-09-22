package pumplog

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEventName_Known(t *testing.T) {
	if got := EventName(3); got != "BasalRateChange" {
		t.Errorf("EventName(3) = %q, want BasalRateChange", got)
	}
}

func TestEventName_UnknownFallsBackToCode(t *testing.T) {
	if got := EventName(99999); got != "EventCode99999" {
		t.Errorf("EventName(99999) = %q, want EventCode99999", got)
	}
}

func TestParse(t *testing.T) {
	data := []byte(`{
		"events": [
			{"deviceAssignmentId": "dev-1", "eventCode": 3, "pumpDateTime": "2025-01-01T00:00:00"},
			{"deviceAssignmentId": "dev-1", "eventCode": 20, "pumpDateTime": "2025-01-02T00:00:00"}
		],
		"clockChanges": [
			{"deviceAssignmentId": "dev-1", "eventCode": 52, "pumpDateTime": "2025-01-01T12:00:00"}
		]
	}`)

	logs, err := Parse(data)
	if err != nil {
		t.Fatalf("Parse failed: %v", err)
	}
	if len(logs.Events) != 2 {
		t.Errorf("expected 2 events, got %d", len(logs.Events))
	}
	if len(logs.ClockChanges) != 1 {
		t.Errorf("expected 1 clock change, got %d", len(logs.ClockChanges))
	}
}

func TestParse_InvalidJSON(t *testing.T) {
	if _, err := Parse([]byte("not json")); err == nil {
		t.Error("expected an error for invalid JSON")
	}
}

func TestLoad(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pump-logs-raw.json")
	content := `{"events": [{"eventCode": 3}], "clockChanges": []}`
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test fixture: %v", err)
	}

	logs, err := Load(path)
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}
	if len(logs.Events) != 1 {
		t.Errorf("expected 1 event, got %d", len(logs.Events))
	}
}

func TestLoad_MissingFile(t *testing.T) {
	if _, err := Load("/nonexistent/path/pump-logs-raw.json"); err == nil {
		t.Error("expected an error for a missing file")
	}
}

func TestEventCounts(t *testing.T) {
	logs := &Logs{
		Events: []Event{
			{EventCode: 3},
			{EventCode: 3},
			{EventCode: 20},
			{EventCode: 3},
			{EventCode: 99999}, // unknown code
		},
	}
	counts := logs.EventCounts()
	if len(counts) != 3 {
		t.Fatalf("expected 3 distinct codes, got %d", len(counts))
	}
	// Most frequent first.
	if counts[0].Code != 3 || counts[0].Count != 3 {
		t.Errorf("expected first entry to be code 3 with count 3, got %+v", counts[0])
	}
	if counts[0].Name != "BasalRateChange" {
		t.Errorf("expected name BasalRateChange, got %q", counts[0].Name)
	}
	// Unknown code should still show up, with a fallback name.
	found := false
	for _, c := range counts {
		if c.Code == 99999 {
			found = true
			if c.Name != "EventCode99999" {
				t.Errorf("expected fallback name for unknown code, got %q", c.Name)
			}
		}
	}
	if !found {
		t.Error("expected unknown code 99999 to be present in counts")
	}
}

func TestEventCounts_Empty(t *testing.T) {
	logs := &Logs{}
	counts := logs.EventCounts()
	if len(counts) != 0 {
		t.Errorf("expected 0 counts for no events, got %d", len(counts))
	}
}

func TestFilterByCode(t *testing.T) {
	logs := &Logs{
		Events: []Event{
			{EventCode: 3, SequenceNumber: 1},
			{EventCode: 20, SequenceNumber: 2},
			{EventCode: 3, SequenceNumber: 3},
		},
	}
	filtered := logs.FilterByCode(3)
	if len(filtered) != 2 {
		t.Fatalf("expected 2 events with code 3, got %d", len(filtered))
	}
	if filtered[0].SequenceNumber != 1 || filtered[1].SequenceNumber != 3 {
		t.Errorf("unexpected filtered events: %+v", filtered)
	}
}

func TestFilterByCode_NoMatches(t *testing.T) {
	logs := &Logs{Events: []Event{{EventCode: 3}}}
	filtered := logs.FilterByCode(999)
	if len(filtered) != 0 {
		t.Errorf("expected 0 matches, got %d", len(filtered))
	}
}

func TestSortedByTime(t *testing.T) {
	logs := &Logs{
		Events: []Event{
			{PumpDateTime: "2025-01-03T00:00:00", SequenceNumber: 3},
			{PumpDateTime: "2025-01-01T00:00:00", SequenceNumber: 1},
			{PumpDateTime: "2025-01-02T00:00:00", SequenceNumber: 2},
		},
	}
	sorted := logs.SortedByTime()
	if len(sorted) != 3 {
		t.Fatalf("expected 3 events, got %d", len(sorted))
	}
	for i, want := range []int{1, 2, 3} {
		if sorted[i].SequenceNumber != want {
			t.Errorf("sorted[%d].SequenceNumber = %d, want %d", i, sorted[i].SequenceNumber, want)
		}
	}
	// Original slice must be untouched (SortedByTime returns a copy).
	if logs.Events[0].SequenceNumber != 3 {
		t.Error("SortedByTime mutated the original Events slice")
	}
}

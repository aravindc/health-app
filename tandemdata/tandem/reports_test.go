package tandem

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func sampleMeta() map[string]interface{} {
	var meta map[string]interface{}
	raw := `{
		"pumps": [{
			"assignmentId": "assign-123",
			"availableDataRange": {"start": "2025-05-18T16:35:36", "end": "2026-09-14T07:46:07"}
		}]
	}`
	if err := json.Unmarshal([]byte(raw), &meta); err != nil {
		panic(err)
	}
	return meta
}

func TestPumperAssignmentID(t *testing.T) {
	id, err := PumperAssignmentID(sampleMeta())
	if err != nil {
		t.Fatalf("PumperAssignmentID failed: %v", err)
	}
	if id != "assign-123" {
		t.Errorf("expected assign-123, got %q", id)
	}
}

func TestPumperAssignmentIDMissing(t *testing.T) {
	if _, err := PumperAssignmentID(map[string]interface{}{}); err == nil {
		t.Error("expected error for missing pumps array")
	}
}

func TestPumperAvailableDataStart(t *testing.T) {
	start, err := PumperAvailableDataStart(sampleMeta())
	if err != nil {
		t.Fatalf("PumperAvailableDataStart failed: %v", err)
	}
	want := time.Date(2025, 5, 18, 16, 35, 36, 0, time.UTC)
	if !start.Equal(want) {
		t.Errorf("expected %s, got %s", want, start)
	}
}

func TestDateChunks(t *testing.T) {
	start := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	end := time.Date(2025, 1, 25, 0, 0, 0, 0, time.UTC) // 24 days

	chunks := DateChunks(start, end, 10)
	if len(chunks) != 3 {
		t.Fatalf("expected 3 chunks, got %d", len(chunks))
	}
	if !chunks[0].Start.Equal(start) {
		t.Errorf("first chunk should start at %s, got %s", start, chunks[0].Start)
	}
	if !chunks[len(chunks)-1].End.Equal(end) {
		t.Errorf("last chunk should end at %s, got %s", end, chunks[len(chunks)-1].End)
	}
	// no gaps or overlaps
	for i := 1; i < len(chunks); i++ {
		if !chunks[i].Start.Equal(chunks[i-1].End) {
			t.Errorf("gap/overlap between chunk %d end %s and chunk %d start %s",
				i-1, chunks[i-1].End, i, chunks[i].Start)
		}
	}
}

func TestMergePumpLogs(t *testing.T) {
	chunks := [][]byte{
		[]byte(`{"events": [{"n": 1}], "clockChanges": [{"c": 1}]}`),
		[]byte(`{"events": [{"n": 2}, {"n": 3}], "clockChanges": []}`),
	}
	raw, err := MergePumpLogs(chunks)
	if err != nil {
		t.Fatalf("MergePumpLogs failed: %v", err)
	}

	var merged struct {
		Events       []json.RawMessage `json:"events"`
		ClockChanges []json.RawMessage `json:"clockChanges"`
	}
	if err := json.Unmarshal(raw, &merged); err != nil {
		t.Fatalf("failed to parse merged output: %v", err)
	}
	if len(merged.Events) != 3 {
		t.Errorf("expected 3 merged events, got %d", len(merged.Events))
	}
	if len(merged.ClockChanges) != 1 {
		t.Errorf("expected 1 merged clock change, got %d", len(merged.ClockChanges))
	}
}

func TestFetchPumpLogsRawUsesQueryParams(t *testing.T) {
	var gotStart, gotEnd string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotStart = r.URL.Query().Get("startDate")
		gotEnd = r.URL.Query().Get("endDate")
		fmt.Fprint(w, `{"events": [], "clockChanges": []}`)
	}))
	defer server.Close()

	origBase := reportsBase
	reportsBase = server.URL
	defer func() { reportsBase = origBase }()

	start := time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)
	end := time.Date(2025, 1, 10, 0, 0, 0, 0, time.UTC)

	if _, err := FetchPumpLogsRaw(server.Client(), "token", "report-1", "pumper-1", start, end); err != nil {
		t.Fatalf("FetchPumpLogsRaw failed: %v", err)
	}
	if gotStart != "2025-01-01T00:00:00Z" {
		t.Errorf("unexpected startDate param: %s", gotStart)
	}
	if gotEnd != "2025-01-10T00:00:00Z" {
		t.Errorf("unexpected endDate param: %s", gotEnd)
	}
}

package insulin

import (
	"testing"

	"tandemdata/pumplog"
)

func TestExtractBoluses(t *testing.T) {
	events := []pumplog.Event{
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusRequestedCarb,
			EstimatedDateTime:  "2025-05-18T16:20:27Z",
			EventProperties: map[string]interface{}{
				"bolusId":    float64(5419),
				"carbAmount": float64(0),
				"bg":         float64(211),
			},
		},
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusRequestedStandard,
			EstimatedDateTime:  "2025-05-18T16:20:42Z",
			EventProperties: map[string]interface{}{
				"bolusId":   float64(5419),
				"bolusSize": float64(0.27925098),
			},
		},
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusCompleted,
			EstimatedDateTime:  "2025-05-18T16:21:17Z",
			EventProperties: map[string]interface{}{
				"bolusId":          float64(5419),
				"completionStatus": float64(3),
				"insulinDelivered": float64(0.27925098),
				"insulinRequested": float64(0.27925098),
			},
		},
		// An event with no bolusId should be skipped entirely.
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusRequestedStandard,
			EstimatedDateTime:  "2025-05-18T17:00:00Z",
			EventProperties:    map[string]interface{}{},
		},
		// An unrelated event code should be ignored.
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          399,
			EstimatedDateTime:  "2025-05-18T17:05:00Z",
			EventProperties:    map[string]interface{}{"bolusId": float64(5419)},
		},
	}

	boluses := ExtractBoluses(events)
	if len(boluses) != 1 {
		t.Fatalf("expected 1 bolus, got %d", len(boluses))
	}

	b := boluses[0]
	if b.BolusID != 5419 {
		t.Errorf("expected bolusId 5419, got %d", b.BolusID)
	}
	if b.DeviceAssignmentID != "dev-1" {
		t.Errorf("expected device-1, got %s", b.DeviceAssignmentID)
	}
	if b.BolusType != "STANDARD" {
		t.Errorf("expected STANDARD bolus type, got %q", b.BolusType)
	}
	if b.RequestedAt == nil {
		t.Fatal("expected RequestedAt to be set")
	}
	if b.CompletedAt == nil {
		t.Fatal("expected CompletedAt to be set")
	}
	if b.InsulinDelivered == nil || *b.InsulinDelivered != 0.27925098 {
		t.Errorf("unexpected InsulinDelivered: %v", b.InsulinDelivered)
	}
	if b.InsulinRequested == nil || *b.InsulinRequested != 0.27925098 {
		t.Errorf("unexpected InsulinRequested: %v", b.InsulinRequested)
	}
	if b.CarbAmount == nil || *b.CarbAmount != 0 {
		t.Errorf("unexpected CarbAmount: %v", b.CarbAmount)
	}
	if b.BG == nil || *b.BG != 211 {
		t.Errorf("unexpected BG: %v", b.BG)
	}
	if b.CompletionStatus == nil || *b.CompletionStatus != 3 {
		t.Errorf("unexpected CompletionStatus: %v", b.CompletionStatus)
	}
}

func TestExtractBolusesExtendedType(t *testing.T) {
	events := []pumplog.Event{
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusRequestedExtended,
			EstimatedDateTime:  "2025-05-29T19:29:01Z",
			EventProperties: map[string]interface{}{
				"bolusId":   float64(5518),
				"bolexSize": float64(7.2215004),
			},
		},
	}

	boluses := ExtractBoluses(events)
	if len(boluses) != 1 {
		t.Fatalf("expected 1 bolus, got %d", len(boluses))
	}
	if boluses[0].BolusType != "EXTENDED" {
		t.Errorf("expected EXTENDED bolus type, got %q", boluses[0].BolusType)
	}
	if boluses[0].InsulinRequested == nil || *boluses[0].InsulinRequested != 7.2215004 {
		t.Errorf("unexpected InsulinRequested: %v", boluses[0].InsulinRequested)
	}
}

func TestExtractBolusesRequestOnlyNoCompletion(t *testing.T) {
	// A bolus that's been requested but not yet completed should still be
	// extracted (with CompletedAt/InsulinDelivered left nil), since it's
	// still a real in-flight delivery.
	events := []pumplog.Event{
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBolusRequestedStandard,
			EstimatedDateTime:  "2025-05-18T16:20:42Z",
			EventProperties: map[string]interface{}{
				"bolusId":   float64(9001),
				"bolusSize": float64(1.5),
			},
		},
	}

	boluses := ExtractBoluses(events)
	if len(boluses) != 1 {
		t.Fatalf("expected 1 bolus, got %d", len(boluses))
	}
	b := boluses[0]
	if b.RequestedAt == nil {
		t.Error("expected RequestedAt to be set")
	}
	if b.CompletedAt != nil {
		t.Error("expected CompletedAt to be nil")
	}
	if b.InsulinDelivered != nil {
		t.Error("expected InsulinDelivered to be nil")
	}
}

func TestExtractBasalChanges(t *testing.T) {
	events := []pumplog.Event{
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBasalRateChange,
			SequenceGroup:      0,
			SequenceNumber:     2031668,
			EstimatedDateTime:  "2025-05-18T16:05:13Z",
			EventProperties: map[string]interface{}{
				"commandedBasalRate": float64(0.26),
				"baseBasalRate":      float64(0.26),
				"maxBasalRate":       float64(0.6),
			},
		},
		// Not a basal-rate-change event: ignored.
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          279,
			EstimatedDateTime:  "2025-05-18T15:40:10Z",
			EventProperties:    map[string]interface{}{"commandedRate": float64(290)},
		},
		// Missing commandedBasalRate: skipped defensively.
		{
			DeviceAssignmentID: "dev-1",
			EventCode:          codeBasalRateChange,
			EstimatedDateTime:  "2025-05-18T16:10:00Z",
			EventProperties:    map[string]interface{}{},
		},
	}

	changes := ExtractBasalChanges(events)
	if len(changes) != 1 {
		t.Fatalf("expected 1 basal change, got %d", len(changes))
	}

	c := changes[0]
	if c.CommandedRate != 0.26 {
		t.Errorf("expected commandedRate 0.26, got %v", c.CommandedRate)
	}
	if c.BaseRate == nil || *c.BaseRate != 0.26 {
		t.Errorf("unexpected BaseRate: %v", c.BaseRate)
	}
	if c.MaxRate == nil || *c.MaxRate != 0.6 {
		t.Errorf("unexpected MaxRate: %v", c.MaxRate)
	}
	if c.SequenceNumber != 2031668 {
		t.Errorf("expected sequenceNumber 2031668, got %d", c.SequenceNumber)
	}
}

func TestEventIDs(t *testing.T) {
	got := EventIDs()
	want := "3,20,21,55,59,64,65,66"
	if got != want {
		t.Errorf("expected %q, got %q", want, got)
	}
}

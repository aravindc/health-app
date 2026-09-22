package handlers

import "testing"

func floatPtr(v float64) *float64 { return &v }

func TestSplitDelivered_ProportionalSplit(t *testing.T) {
	// requested 5u = 3u food + 2u correction; delivered 5u -> same ratio.
	food, correction := splitDelivered(5, floatPtr(5), floatPtr(3), floatPtr(2))
	if food != 3 || correction != 2 {
		t.Errorf("got food=%v correction=%v, want food=3 correction=2", food, correction)
	}
}

func TestSplitDelivered_PartialCompletion_SameRatio(t *testing.T) {
	// A combo bolus's first completion delivers only part of the total
	// requested amount; the split should still apply the same ratio.
	food, correction := splitDelivered(1, floatPtr(4), floatPtr(3), floatPtr(1))
	wantFood, wantCorrection := 0.75, 0.25
	if food != wantFood || correction != wantCorrection {
		t.Errorf("got food=%v correction=%v, want food=%v correction=%v", food, correction, wantFood, wantCorrection)
	}
}

func TestSplitDelivered_NoSplitRecorded_FallsBackToFood(t *testing.T) {
	food, correction := splitDelivered(2, nil, nil, nil)
	if food != 2 || correction != 0 {
		t.Errorf("got food=%v correction=%v, want food=2 correction=0 (full fallback)", food, correction)
	}
}

// Regression: a real tandem_bolus row observed with insulin_requested=2,
// insulin_delivered=2, food_bolus_size=0, correction_bolus_size=0 — a
// recorded split that is technically present but sums to zero, which
// previously caused 2 delivered units to vanish from the activity curve
// entirely (neither food nor correction was > 0, so no insulinactivity.Dose
// was ever created for it).
func TestSplitDelivered_ZeroZeroSplitWithNonzeroDelivery_FallsBackToFood(t *testing.T) {
	food, correction := splitDelivered(2, floatPtr(2), floatPtr(0), floatPtr(0))
	if food != 2 || correction != 0 {
		t.Errorf("got food=%v correction=%v, want food=2 correction=0 (fallback, not dropped)", food, correction)
	}
}

func TestSplitDelivered_ZeroDelivered_NoDose(t *testing.T) {
	food, correction := splitDelivered(0, floatPtr(5), floatPtr(3), floatPtr(2))
	if food != 0 || correction != 0 {
		t.Errorf("got food=%v correction=%v, want both 0 for a zero-unit delivery", food, correction)
	}
}

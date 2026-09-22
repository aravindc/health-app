package insulinactivity

import (
	"math"
	"testing"
	"time"
)

func TestCurve_IntegralRecoversDeliveredUnits(t *testing.T) {
	base := time.Date(2026, 1, 1, 6, 19, 54, 0, time.UTC) // arbitrary second, like a real dose
	points := Curve([]Dose{{DeliveredAt: base, Units: 3.4}})

	var sum float64
	for _, p := range points {
		sum += p.Units
	}

	if math.Abs(sum-3.4) > 1e-9 {
		t.Errorf("curve integral = %v, want 3.4 (delivered units)", sum)
	}
}

func TestCurve_MultipleDoses_EachRecoveredIndependently(t *testing.T) {
	base := time.Date(2026, 1, 1, 8, 0, 0, 0, time.UTC)
	doses := []Dose{
		{DeliveredAt: base, Units: 2.0},
		{DeliveredAt: base.Add(90 * time.Minute), Units: 1.5},
	}
	points := Curve(doses)

	var sum float64
	for _, p := range points {
		sum += p.Units
	}
	want := 3.5
	if math.Abs(sum-want) > 1e-9 {
		t.Errorf("summed curve integral = %v, want %v", sum, want)
	}
}

func TestCurve_ZeroAtEndpoints(t *testing.T) {
	// The first sample (h=0) should be ~0 by construction (sin(0)^2 == 0);
	// h=4 itself is dropped entirely (also exactly 0), not emitted as a
	// redundant trailing zero point.
	base := time.Date(2026, 1, 1, 6, 20, 0, 0, time.UTC) // already grid-aligned
	points := Curve([]Dose{{DeliveredAt: base, Units: 1.0}})

	if len(points) == 0 {
		t.Fatal("expected non-empty curve")
	}
	if !points[0].Time.Equal(base) {
		t.Errorf("first point time = %v, want %v", points[0].Time, base)
	}
	if points[0].Units > 1e-6 {
		t.Errorf("first point units = %v, want ~0 (h=0 endpoint)", points[0].Units)
	}

	last := points[len(points)-1].Time
	gotDuration := last.Sub(base)
	maxWant := Duration - StepInterval // h=4 itself is dropped
	if gotDuration != maxWant {
		t.Errorf("last point at h=%v, want h=%v (h=4 dropped as exactly zero)", gotDuration, maxWant)
	}
}

func TestCurve_GridAlignment_UniformGaps(t *testing.T) {
	// Two doses at arbitrary, unaligned seconds, less than 4h apart so
	// their curves overlap and get summed by timestamp key.
	d1 := time.Date(2026, 1, 1, 6, 19, 54, 0, time.UTC)
	d2 := time.Date(2026, 1, 1, 7, 42, 11, 0, time.UTC)
	points := Curve([]Dose{
		{DeliveredAt: d1, Units: 2.0},
		{DeliveredAt: d2, Units: 1.0},
	})

	for i := 1; i < len(points); i++ {
		gap := points[i].Time.Sub(points[i-1].Time)
		if gap%StepInterval != 0 {
			t.Fatalf("gap between point %d and %d = %v, want a multiple of %v", i-1, i, gap, StepInterval)
		}
	}
}

func TestCurve_SkipsNonPositiveUnits(t *testing.T) {
	base := time.Date(2026, 1, 1, 6, 20, 0, 0, time.UTC)
	points := Curve([]Dose{
		{DeliveredAt: base, Units: 0},
		{DeliveredAt: base, Units: -1},
	})
	if len(points) != 0 {
		t.Errorf("expected no points for non-positive doses, got %d", len(points))
	}
}

func TestCurve_EmptyInput(t *testing.T) {
	points := Curve(nil)
	if points == nil {
		t.Error("expected non-nil empty slice for empty input")
	}
	if len(points) != 0 {
		t.Errorf("expected 0 points, got %d", len(points))
	}
}

func TestSnapToGrid(t *testing.T) {
	cases := []struct {
		in   time.Time
		want time.Time
	}{
		{time.Date(2026, 1, 1, 6, 19, 54, 0, time.UTC), time.Date(2026, 1, 1, 6, 20, 0, 0, time.UTC)},
		{time.Date(2026, 1, 1, 6, 17, 0, 0, time.UTC), time.Date(2026, 1, 1, 6, 15, 0, 0, time.UTC)},
		{time.Date(2026, 1, 1, 6, 20, 0, 0, time.UTC), time.Date(2026, 1, 1, 6, 20, 0, 0, time.UTC)},
	}
	for _, c := range cases {
		got := snapToGrid(c.in)
		if !got.Equal(c.want) {
			t.Errorf("snapToGrid(%v) = %v, want %v", c.in, got, c.want)
		}
	}
}

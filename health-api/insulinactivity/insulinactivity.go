// Package insulinactivity models a bolus dose's insulin activity over time
// as a raised-cosine bell, for the "Bolus insulin activity" chart panel.
// See tandemdata/prompt.md's "Insulin activity curve" section for the
// derivation and the pitfalls this avoids (exponential decay's long tail,
// unaligned timing grids producing a jagged sum).
package insulinactivity

import (
	"math"
	"sort"
	"time"
)

// Duration is the length of a dose's modeled activity window.
const Duration = 4 * time.Hour

// StepInterval is the discretization step for the activity curve, and the
// grid every dose's start time is snapped to before stepping.
const StepInterval = 5 * time.Minute

// stepsPerDose is how many StepInterval-sized samples cover Duration,
// starting at h=0 (inclusive) up to but not including h=4 (which is
// exactly zero by construction, so it contributes nothing and is dropped
// rather than emitted as a redundant zero sample).
var stepsPerDose = int(Duration / StepInterval) // 48

// normalizedWeights holds sin(pi*h/4)^2 sampled at each 5-minute step,
// normalized so they sum to 1 — computed once at package init so every
// dose's curve is built from the same table and each curve's own integral
// exactly recovers its delivered units (sum of normalizedWeights[i] *
// delivered == delivered).
var normalizedWeights = buildNormalizedWeights()

func buildNormalizedWeights() []float64 {
	weights := make([]float64, stepsPerDose)
	var total float64
	for i := 0; i < stepsPerDose; i++ {
		h := float64(i) * StepInterval.Hours()
		w := math.Sin(math.Pi*h/4) * math.Sin(math.Pi*h/4)
		weights[i] = w
		total += w
	}
	for i := range weights {
		weights[i] /= total
	}
	return weights
}

// Dose is one insulin delivery to spread over its activity window.
type Dose struct {
	// DeliveredAt is when this dose was actually delivered. It is snapped
	// to the nearest StepInterval before building the curve, so
	// overlapping doses land on a shared timestamp grid and sum cleanly
	// (see package doc).
	DeliveredAt time.Time
	Units       float64
}

// Point is one sample of a summed activity curve.
type Point struct {
	Time  time.Time `json:"time"`
	Units float64   `json:"units"`
}

// Curve sums the raised-cosine-bell activity of every dose in doses onto a
// shared 5-minute grid and returns the result sorted by time. Doses with
// Units <= 0 are skipped. An empty input returns an empty (non-nil) slice.
func Curve(doses []Dose) []Point {
	byTime := make(map[time.Time]float64)

	for _, d := range doses {
		if d.Units <= 0 {
			continue
		}
		start := snapToGrid(d.DeliveredAt)
		for i, w := range normalizedWeights {
			t := start.Add(time.Duration(i) * StepInterval)
			byTime[t] += d.Units * w
		}
	}

	points := make([]Point, 0, len(byTime))
	for t, units := range byTime {
		points = append(points, Point{Time: t, Units: units})
	}
	sort.Slice(points, func(i, j int) bool { return points[i].Time.Before(points[j].Time) })
	return points
}

// snapToGrid rounds t to the nearest StepInterval boundary (ties round up),
// so every dose's curve shares one timing grid regardless of the arbitrary
// second its raw timestamp landed on.
func snapToGrid(t time.Time) time.Time {
	return t.Round(StepInterval)
}

package handlers

import (
	"math"
	"testing"
)

var defaultThresholds = rangeThresholds{Min: 4.0, StrictMax: 7.0, MedicalMax: 10.0}

// readings builds DataPoints 5 minutes apart, in the given order.
func readings(mmols ...float64) []DataPoint {
	points := make([]DataPoint, len(mmols))
	for i, m := range mmols {
		points[i] = DataPoint{Epoch: int64(i) * 300_000, Mmol: m}
	}
	return points
}

func approx(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func TestComputeInsightStats_Empty(t *testing.T) {
	s := computeInsightStats(24, nil, defaultThresholds)
	if s.Count != 0 || s.InRangePct != 0 || s.Mean != 0 || s.Median != 0 || s.StdDev != 0 || s.CV != 0 || s.Highs != 0 || s.Lows != 0 || s.Unicorns != 0 {
		t.Errorf("want all zeros, got %+v", s)
	}
	// Matches the frontend's old behaviour: no readings shows 100% in range.
	if s.Normal != (RangeSplit{0, 100, 0}) || s.Strict != (RangeSplit{0, 100, 0}) {
		t.Errorf("want 0/100/0 splits, got normal=%+v strict=%+v", s.Normal, s.Strict)
	}
	if len(s.Sparkline) != 0 {
		t.Errorf("want empty sparkline, got %d points", len(s.Sparkline))
	}
}

func TestComputeInsightStats_MedianOddAndEven(t *testing.T) {
	if got := computeInsightStats(24, readings(7, 5, 6), defaultThresholds).Median; got != 6 {
		t.Errorf("odd count: median=%v, want 6", got)
	}
	if got := computeInsightStats(24, readings(8, 5, 6, 7), defaultThresholds).Median; got != 6.5 {
		t.Errorf("even count: median=%v, want 6.5 (mean of middle two)", got)
	}
}

func TestComputeInsightStats_StdDevAndCV(t *testing.T) {
	// Mean 6, population variance ((−2)²+0²+2²)/3 = 8/3.
	s := computeInsightStats(24, readings(4, 6, 8), defaultThresholds)
	if s.Mean != 6 {
		t.Errorf("mean=%v, want 6", s.Mean)
	}
	wantSD := round(math.Sqrt(8.0/3), 2)
	if s.StdDev != wantSD {
		t.Errorf("std_dev=%v, want %v (population)", s.StdDev, wantSD)
	}
	if want := round(math.Sqrt(8.0/3)/6*100, 2); s.CV != want {
		t.Errorf("cv=%v, want %v", s.CV, want)
	}
}

func TestComputeInsightStats_Quartiles(t *testing.T) {
	// Sorted: 1..8; floor(8*0.25)=2 -> 3, floor(8*0.75)=6 -> 7.
	s := computeInsightStats(24, readings(8, 1, 7, 2, 6, 3, 5, 4), defaultThresholds)
	if s.Q1 != 3 || s.Q3 != 7 {
		t.Errorf("q1=%v q3=%v, want 3 and 7", s.Q1, s.Q3)
	}
}

func TestComputeInsightStats_HighsLowsAndBoundaries(t *testing.T) {
	// 4.0 and 10.0 are in range (inclusive); 3.9 is low, 10.1 is high.
	s := computeInsightStats(24, readings(3.9, 4.0, 10.0, 10.1, 12), defaultThresholds)
	if s.Lows != 1 || s.Highs != 2 {
		t.Errorf("lows=%d highs=%d, want 1 and 2", s.Lows, s.Highs)
	}
}

func TestComputeInsightStats_RangeSplits(t *testing.T) {
	// 10 readings: 1 low, 5 within 4–7, 2 in 7–10, 2 above 10.
	s := computeInsightStats(24, readings(3, 4, 5, 5, 6, 7, 8, 9, 11, 12), defaultThresholds)
	if s.Normal != (RangeSplit{Low: 10, In: 70, High: 20}) {
		t.Errorf("normal=%+v, want 10/70/20", s.Normal)
	}
	if s.Strict != (RangeSplit{Low: 10, In: 50, High: 40}) {
		t.Errorf("strict=%+v, want 10/50/40", s.Strict)
	}
	if s.InRangePct != 50 {
		t.Errorf("in_range_pct=%d, want 50", s.InRangePct)
	}
}

func TestComputeInsightStats_InRangePctRoundsLikePercentInRange(t *testing.T) {
	// One low, one in range, one high: in_range_pct rounds 33.3 to 33,
	// while Strict.In is what's left after rounding Low and High to 33
	// each, i.e. 34.
	s := computeInsightStats(24, readings(3, 5, 8), defaultThresholds)
	if s.InRangePct != 33 || s.Strict.In != 34 {
		t.Errorf("in_range_pct=%d strict.in=%d, want 33 and 34", s.InRangePct, s.Strict.In)
	}
	// Boundaries are inclusive, as in getDataInWindow's InRange.
	if got := computeInsightStats(24, readings(4.0, 7.0), defaultThresholds).InRangePct; got != 100 {
		t.Errorf("in_range_pct=%d for 4.0 and 7.0, want 100", got)
	}
}

func TestComputeInsightStats_SplitAlwaysSumsTo100(t *testing.T) {
	// Thirds round to 33/33, so In takes the remaining 34.
	s := computeInsightStats(24, readings(3, 5, 11), defaultThresholds)
	if s.Normal.Low+s.Normal.In+s.Normal.High != 100 {
		t.Errorf("normal=%+v doesn't sum to 100", s.Normal)
	}
}

func TestComputeInsightStats_Unicorns(t *testing.T) {
	// Only readings that are 5.5 at 0.1 precision count; 5.5 built from
	// float arithmetic (0.1*55) must still match.
	s := computeInsightStats(24, readings(5.5, 5.4, 5.6, 0.1*55, 5.55), defaultThresholds)
	if s.Unicorns != 2 {
		t.Errorf("unicorns=%d, want 2", s.Unicorns)
	}
}

func TestComputeInsightStats_UsesConfiguredThresholds(t *testing.T) {
	s := computeInsightStats(24, readings(7.5), rangeThresholds{Min: 4, StrictMax: 8, MedicalMax: 10})
	if s.Strict != (RangeSplit{Low: 0, In: 100, High: 0}) {
		t.Errorf("strict=%+v, want 7.5 in range with StrictMax=8", s.Strict)
	}
}

func TestDownsampleSparkline_ShortPeriodKeepsEveryReadingInOrder(t *testing.T) {
	// Given newest first, as getData returns them.
	pts := []DataPoint{{Epoch: 600_000, Mmol: 7}, {Epoch: 300_000, Mmol: 6}, {Epoch: 0, Mmol: 5}}
	got := downsampleSparkline(pts, 288)
	if len(got) != 3 || got[0].BgTime != 0 || got[2].BgTime != 600_000 || got[0].BgMmol != 5 {
		t.Errorf("want 3 points oldest first, got %+v", got)
	}
}

func TestDownsampleSparkline_LongPeriodIsCappedAndAveraged(t *testing.T) {
	// 90 days of 5-minute readings.
	mmols := make([]float64, 90*288)
	for i := range mmols {
		mmols[i] = float64(i % 2) // alternating 0, 1
	}
	got := downsampleSparkline(readings(mmols...), InsightSparklineMaxPoints)
	if len(got) > InsightSparklineMaxPoints {
		t.Fatalf("got %d points, want at most %d", len(got), InsightSparklineMaxPoints)
	}
	// Each bucket holds an even number of alternating readings: mean 0.5.
	if !approx(got[0].BgMmol, 0.5) {
		t.Errorf("first bucket mean=%v, want 0.5", got[0].BgMmol)
	}
	for i := 1; i < len(got); i++ {
		if got[i].BgTime <= got[i-1].BgTime {
			t.Fatalf("not oldest first at %d: %v <= %v", i, got[i].BgTime, got[i-1].BgTime)
		}
	}
}

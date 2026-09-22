package model

import "testing"

func TestTrendFromRate(t *testing.T) {
	cases := []struct {
		rate float64
		want int
	}{
		{20, TrendDoubleUp},
		{15, TrendDoubleUp}, // boundary, inclusive
		{12, TrendSingleUp},
		{10, TrendSingleUp}, // boundary, inclusive
		{7, TrendFortyFiveUp},
		{5, TrendFortyFiveUp}, // boundary, inclusive
		{0, TrendFlat},
		{-4.9, TrendFlat},
		{-5, TrendFortyFiveDown}, // boundary: -5 is NOT > -5, so falls through
		{-8, TrendFortyFiveDown},
		{-10, TrendSingleDown}, // boundary: -10 is NOT > -10
		{-12, TrendSingleDown},
		{-15, TrendDoubleDown}, // boundary: -15 is NOT > -15
		{-20, TrendDoubleDown},
	}
	for _, c := range cases {
		if got := TrendFromRate(c.rate); got != c.want {
			t.Errorf("TrendFromRate(%v) = %v, want %v", c.rate, got, c.want)
		}
	}
}

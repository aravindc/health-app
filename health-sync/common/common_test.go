package common

import "testing"

func TestTrendToDirection(t *testing.T) {
	cases := []struct {
		trend string
		want  int
	}{
		{"NONE", 0},
		{"DoubleUp", 1},
		{"SingleUp", 2},
		{"FortyFiveUp", 3},
		{"Flat", 4},
		{"FortyFiveDown", 5},
		{"SingleDown", 6},
		{"DoubleDown", 7},
		{"NotComputable", 8},
		{"RATE OUT OF RANGE", 9},
		{"", 99},
		{"garbage", 99},
	}
	for _, c := range cases {
		if got := TrendToDirection(c.trend); got != c.want {
			t.Errorf("TrendToDirection(%q) = %d, want %d", c.trend, got, c.want)
		}
	}
}

func TestTernaryIf(t *testing.T) {
	if got := TernaryIf(true, "a", "b"); got != "a" {
		t.Errorf("TernaryIf(true, ...) = %q, want %q", got, "a")
	}
	if got := TernaryIf(false, "a", "b"); got != "b" {
		t.Errorf("TernaryIf(false, ...) = %q, want %q", got, "b")
	}
	if got := TernaryIf(false, 1, 2); got != 2 {
		t.Errorf("TernaryIf(false, 1, 2) = %d, want 2", got)
	}
}

func TestCleanString(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{`"US"`, "US"},
		{`"quoted on one side`, "quoted on one side"},
		{"no quotes", "no quotes"},
		{`""`, ""},
	}
	for _, c := range cases {
		if got := CleanString(c.in); got != c.want {
			t.Errorf("CleanString(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestCleanDateString(t *testing.T) {
	cases := []struct {
		in   string
		want int64
	}{
		{"Date(1700000000000)", 1700000000000},
		{"Date(0)", 0},
		{"1700000000000", 1700000000000}, // no wrapper, parses as-is
	}
	for _, c := range cases {
		if got := CleanDateString(c.in); got != c.want {
			t.Errorf("CleanDateString(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

// TestCleanDateString_Unparseable verifies the function degrades to 0 (the
// zero value of int64) rather than panicking when it can't parse the input,
// since CleanDateString only logs the strconv error and returns retval.
func TestCleanDateString_Unparseable(t *testing.T) {
	if got := CleanDateString("Date(not-a-number)"); got != 0 {
		t.Errorf("CleanDateString(unparseable) = %d, want 0", got)
	}
}

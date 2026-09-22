package mongo

import "testing"

func TestDirectionToInt(t *testing.T) {
	cases := []struct {
		in   string
		want int
	}{
		{"DoubleUp", 1},
		{"SingleUp", 2},
		{"FortyFiveUp", 3},
		{"Flat", 4},
		{"FortyFiveDown", 5},
		{"SingleDown", 6},
		{"DoubleDown", 7},
		{"NOT COMPUTABLE", 8},
		{"RATE OUT OF RANGE", 9},
		{"", 0},         // missing direction
		{"Unknown", 0},  // unrecognised value
		{"doubleup", 0}, // case-sensitive: no match
	}
	for _, c := range cases {
		got := directionToInt(c.in)
		if got != c.want {
			t.Errorf("directionToInt(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

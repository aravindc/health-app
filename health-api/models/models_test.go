package models

import "testing"

// These models are mostly struct definitions (GORM tags, json tags) with no
// computed fields or other logic. The only behavior worth pinning down is
// TableName(), since GORM uses it to resolve the actual table each model
// reads/writes, and a typo there would silently point a query at the wrong
// (or a nonexistent) table.
func TestTableNames(t *testing.T) {
	cases := []struct {
		name  string
		model interface{ TableName() string }
		want  string
	}{
		{"Latest", Latest{}, "latest"},
		{"DailyAvg", DailyAvg{}, "daily_avg"},
		{"DailyTir", DailyTir{}, "daily_tir"},
		{"AvgMmol", AvgMmol{}, "avg_mmol"},
		{"NsPart", NsPart{}, "ns_part"},
		{"QuartMmolStats", QuartMmolStats{}, "quart_mmol_stats"},
		{"Insulin", Insulin{}, "insulin"},
		{"InsulinActivity", InsulinActivity{}, "insulin_activity"},
		{"TandemBolus", TandemBolus{}, "tandem_bolus"},
		{"TandemBasal", TandemBasal{}, "tandem_basal"},
	}
	for _, c := range cases {
		if got := c.model.TableName(); got != c.want {
			t.Errorf("%s.TableName() = %q, want %q", c.name, got, c.want)
		}
	}
}

package handlers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"health-api/config"
)

var predAt = time.Date(2026, 10, 6, 12, 0, 0, 0, time.UTC)

func mins(m int) time.Time { return predAt.Add(time.Duration(m) * time.Minute) }

func TestTrendOrdinal(t *testing.T) {
	want := map[int]*int{0: nil, 1: ptr(3), 2: ptr(2), 3: ptr(1), 4: ptr(0), 5: ptr(-1), 6: ptr(-2), 7: ptr(-3), 8: nil, 99: nil}
	for code, w := range want {
		got := trendOrdinal(code)
		if (got == nil) != (w == nil) || (got != nil && *got != *w) {
			t.Errorf("trendOrdinal(%d) = %v, want %v", code, got, w)
		}
	}
}

func TestBolusDeliveryType(t *testing.T) {
	for _, tc := range []struct {
		bt, opt *int
		carbs   bool
		want    string
	}{
		{ptr(2), ptr(3), false, "automated_correction"},
		{ptr(3), ptr(4), true, "bolus"},
		{ptr(3), ptr(4), false, "manual_correction"},
		{ptr(1), ptr(0), true, "bolus"},
		{ptr(1), ptr(1), false, "bolus"},
		{ptr(2), ptr(0), false, ""},
		{nil, nil, true, ""},
	} {
		if got := bolusDeliveryType(tc.bt, tc.opt, tc.carbs); got != tc.want {
			t.Errorf("bolusType=%v options=%v carbs=%v: got %q, want %q", tc.bt, tc.opt, tc.carbs, got, tc.want)
		}
	}
}

func TestMergeCGM(t *testing.T) {
	from := mins(-360)
	tandem := []tandemCGMRow{
		{ReadingAt: mins(-10), Sgv: 120, Trend: ptr(4), Rate: ptr(5.0), SequenceNumber: 1, Egv: ptr(int64(100))},
		// Same sensor timestamp, later sequence number: replaces the one above.
		{ReadingAt: mins(-10), Sgv: 125, Trend: ptr(3), Rate: ptr(6.0), SequenceNumber: 2, Egv: ptr(int64(100))},
		{ReadingAt: mins(-5), Sgv: 50, GlucoseValueStatus: ptr(2), SequenceNumber: 3, Egv: ptr(int64(400))}, // LOW
		{ReadingAt: mins(-3), Sgv: 5, SequenceNumber: 7, Egv: ptr(int64(500))},                              // impossible
		{ReadingAt: mins(-400), Sgv: 110, SequenceNumber: 0, Egv: ptr(int64(1))},                            // before the window
		{ReadingAt: mins(5), Sgv: 110, SequenceNumber: 4, Egv: ptr(int64(700))},                             // after T
	}
	ms := func(t time.Time) int64 { return t.UnixMilli() }
	ns := []nightscoutRow{
		{ID: 1, NsTime: ms(mins(-10).Add(100 * time.Second)), Sgv: 130, Trend: 4}, // Tandem reading 100 s away: skipped
		{ID: 2, NsTime: ms(mins(-20)), Sgv: 140, Trend: 5},                        // fills a gap
		{ID: 3, NsTime: ms(mins(-20)), Sgv: 141, Trend: 5},                        // duplicate time: later row wins
		{ID: 4, NsTime: ms(mins(0)), Sgv: 39, Trend: 0},                           // LOW, no direction
		{ID: 5, NsTime: ms(mins(-30)), Sgv: 10, Trend: 4},                         // <= 39 is LOW, as in health-predict
		{ID: 6, NsTime: ms(mins(-40)), Sgv: 450, Trend: 1},                        // HIGH
	}

	got := mergeCGM(tandem, ns, from, predAt)
	type row struct {
		at    time.Time
		mg    float64
		trend *int
		rate  *float64
	}
	want := []row{
		{mins(-40), 400, ptr(3), nil},
		{mins(-30), 40, ptr(0), nil},
		{mins(-20), 141, ptr(-1), nil},
		{mins(-10), 125, ptr(1), ptr(0.6)},
		{mins(-5), 40, nil, nil},
		{mins(0), 40, nil, nil},
	}
	if len(got) != len(want) {
		t.Fatalf("got %d readings, want %d: %+v", len(got), len(want), got)
	}
	eqInt := func(a, b *int) bool { return (a == nil) == (b == nil) && (a == nil || *a == *b) }
	eqF := func(a, b *float64) bool { return (a == nil) == (b == nil) && (a == nil || *a == *b) }
	for i, w := range want {
		g := got[i]
		if !g.Timestamp.Equal(w.at) || g.Glucose != w.mg || !eqInt(g.Trend, w.trend) || !eqF(g.Rate, w.rate) {
			t.Errorf("reading %d: got %v %v trend=%v rate=%v, want %v %v trend=%v rate=%v",
				i, g.Timestamp, g.Glucose, g.Trend, g.Rate, w.at, w.mg, w.trend, w.rate)
		}
	}
}

func TestBolusEvents(t *testing.T) {
	std, ext := ptr("STANDARD"), ptr("EXTENDED")
	rows := []predictBolusRow{
		// Meal bolus, completed before T.
		{BolusType: std, RequestedAt: mins(-62), CompletedAt: ptr(mins(-60)), InsulinRequested: ptr(4.0),
			InsulinDelivered: ptr(4.0), CarbAmount: ptr(45.0), JSONBolusType: ptr(1), JSONOptions: ptr(0)},
		// Control-IQ correction, partly delivered: units are those delivered.
		{BolusType: std, RequestedAt: mins(-31), CompletedAt: ptr(mins(-30)), InsulinRequested: ptr(0.5),
			InsulinDelivered: ptr(0.3), JSONBolusType: ptr(2), JSONOptions: ptr(3)},
		// Extended: from when it started, with its duration and split.
		{BolusType: ext, RequestedAt: mins(-20), CompletedAt: ptr(mins(40)), InsulinRequested: ptr(3.0),
			InsulinDelivered: ptr(3.0), JSONBolusType: ptr(1), JSONOptions: ptr(1),
			StandardPercent: ptr(40.0), Duration: ptr(60.0)},
		// Completes after T: not known yet.
		{BolusType: std, RequestedAt: mins(-1), CompletedAt: ptr(mins(1)), InsulinRequested: ptr(1.0),
			InsulinDelivered: ptr(1.0), JSONBolusType: ptr(3), JSONOptions: ptr(4)},
		// Unmapped type.
		{BolusType: std, RequestedAt: mins(-90), CompletedAt: ptr(mins(-89)), InsulinRequested: ptr(1.0),
			InsulinDelivered: ptr(1.0), JSONBolusType: ptr(9), JSONOptions: ptr(9)},
	}
	got := bolusEvents(rows, predAt)
	if len(got) != 3 {
		t.Fatalf("got %d events, want 3: %+v", len(got), got)
	}
	if e := got[0]; e.Type != "bolus" || !e.Timestamp.Equal(mins(-60)) || *e.Units != 4.0 || *e.CarbsG != 45 {
		t.Errorf("meal bolus: %+v", e)
	}
	if e := got[1]; e.Type != "automated_correction" || *e.Units != 0.3 || e.CarbsG != nil {
		t.Errorf("automated correction: %+v", e)
	}
	if e := got[2]; e.Type != "bolus" || !e.Timestamp.Equal(mins(-20)) || *e.Units != 3.0 ||
		*e.DurationMinutes != 60 || *e.StandardPercent != 40 {
		t.Errorf("extended bolus: %+v", e)
	}
}

func TestBasalEvents(t *testing.T) {
	rows := []predictBasalRow{
		{ChangedAt: mins(-30), SequenceNumber: 5, CommandedRate: 0.8, BaseRate: ptr(0.6)},
		{ChangedAt: mins(-400), SequenceNumber: 1, CommandedRate: 0.6, BaseRate: ptr(0.6)},
		{ChangedAt: mins(-30), SequenceNumber: 4, CommandedRate: 0.9}, // superseded
		{ChangedAt: mins(-10), SequenceNumber: 6, CommandedRate: 20},  // out of range
	}
	got := basalEvents(rows)
	if len(got) != 2 {
		t.Fatalf("got %d events, want 2: %+v", len(got), got)
	}
	if e := got[0]; !e.Timestamp.Equal(mins(-400)) || *e.RateUPerH != 0.6 || *e.ProfileRateUPerH != 0.6 {
		t.Errorf("carry-in rate: %+v", e)
	}
	if e := got[1]; !e.Timestamp.Equal(mins(-30)) || *e.RateUPerH != 0.8 || e.Type != "basal_rate" {
		t.Errorf("latest rate: %+v", e)
	}
}

func TestPumpLagWarning(t *testing.T) {
	if w := pumpLagWarning(predAt, mins(-20)); w != "" {
		t.Errorf("20 min behind: unexpected warning %q", w)
	}
	if w := pumpLagWarning(predAt, mins(-95)); !strings.Contains(w, "95 min") {
		t.Errorf("95 min behind: got %q", w)
	}
	if w := pumpLagWarning(predAt, time.Time{}); !strings.Contains(w, "No pump data") {
		t.Errorf("no pump data: got %q", w)
	}
}

func TestCallPredict(t *testing.T) {
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/predict" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &gotBody)
		switch gotBody["timestamp"] {
		case "2026-10-06T12:00:00Z":
			_, _ = io.WriteString(w, `{"timestamp":"2026-10-06T12:00:00Z","current_glucose":7.8,"unit":"mmol/L",
				"predictions":{"15m":{"value":8.2,"lower":7.6,"upper":8.9,"lower_95":7.1,"upper_95":9.4,"confidence_level":0.8}},
				"model_version":{"15m":"random_forest/v1"},"warnings":null,"disclaimer":"Research use only."}`)
		case "2026-10-06T12:05:00Z":
			w.WriteHeader(http.StatusUnprocessableEntity)
			_, _ = io.WriteString(w, `{"detail":"no glucose reading in the 15 minutes before the prediction time"}`)
		default:
			w.WriteHeader(http.StatusServiceUnavailable)
		}
	}))
	defer srv.Close()

	req := &predictRequest{Timestamp: predAt, GlucoseUnit: "mg/dL",
		RecentGlucose: []predictGlucose{{Timestamp: predAt, Glucose: 140}}, RecentInsulin: []predictInsulin{}}
	resp, unavailable, err := callPredict(context.Background(), srv.URL, req)
	if err != nil || unavailable != "" {
		t.Fatalf("ok: err=%v unavailable=%q", err, unavailable)
	}
	if !resp.Available || resp.Predictions["15m"].Upper95 != 9.4 || resp.Disclaimer == "" || resp.Warnings == nil {
		t.Errorf("ok: unexpected response %+v", resp)
	}
	if gotBody["glucose_unit"] != "mg/dL" {
		t.Errorf("request glucose_unit = %v", gotBody["glucose_unit"])
	}

	req.Timestamp = mins(5)
	if _, unavailable, err := callPredict(context.Background(), srv.URL, req); err != nil || !strings.Contains(unavailable, "15 minutes") {
		t.Errorf("422: err=%v unavailable=%q", err, unavailable)
	}

	req.Timestamp = mins(10)
	if _, _, err := callPredict(context.Background(), srv.URL, req); err == nil {
		t.Error("503: expected an error")
	}
}

func TestGetPrediction_NotConfigured(t *testing.T) {
	h := &Handler{Cfg: &config.Config{}}
	c, w := testContext()
	c.Request = httptest.NewRequest(http.MethodGet, "/prediction", nil)
	h.GetPrediction(c)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"available":false`) {
		t.Errorf("status %d body %s", w.Code, w.Body.String())
	}
}

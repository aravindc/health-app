package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

// Glucose predictions from health-predict, the prediction API in
// health-predict/ (PREDICT_URL). health-api builds the request from its own
// tables, the same way health-predict builds its training data (its
// src/preprocessing.py, config/mappings.yaml and serving.history_from_db),
// so the model sees inputs like the ones it was trained on:
//
//   - Glucose: the last 6 hours. Tandem CGM readings first, with Dexcom
//     (ns_part) readings filling in where no Tandem reading is within 150 s.
//     Tandem backfills are left out: their logged time is when they arrived,
//     not when they were measured, and Dexcom covers those gaps.
//   - Basal: every rate change in the last 6 hours, plus the one in effect
//     6 hours ago.
//   - Boluses: the last 24 hours, typed with the same bolusType/options rules.
//
// The prediction time T is the latest CGM reading. Each response is cached
// for its T and pump-data watermark, so the dashboard's polling calls
// health-predict about once per reading. Predictions are for research and
// analysis only: health-predict's disclaimer is passed through and shown.

const (
	predictGlucoseHistory = 6 * time.Hour
	predictBasalHistory   = 6 * time.Hour
	predictBolusHistory   = 24 * time.Hour
	// No prediction from a reading older than this (health-predict's
	// features.max_current_staleness_minutes): hide it rather than show a
	// stale one.
	predictMaxReadingAge = 15 * time.Minute
	// Pump data (synced from Tandem Source, hourly by default) further
	// behind T than this gets a warning: insulin since then is missing.
	predictPumpLagWarning = 30 * time.Minute
	// A Dexcom reading fills in only when no Tandem reading is this close.
	nightscoutFillTolerance = 150 * time.Second
	predictTimeout          = 10 * time.Second

	mgdlPerMmol    = 18.016 // health-predict's conversion factor
	sensorLowMgdl  = 40.0   // sensor LOW is sent as this limit
	sensorHighMgdl = 400.0  // sensor HIGH is sent as this limit
	// Readings outside 1.0–33.3 mmol/L that aren't LOW/HIGH are impossible.
	impossibleMinMgdl = 1.0 * mgdlPerMmol
	impossibleMaxMgdl = 33.3 * mgdlPerMmol
)

var predictHTTPClient = &http.Client{Timeout: predictTimeout}

// --- Request sent to health-predict (its POST /predict) ---

type predictGlucose struct {
	Timestamp time.Time `json:"timestamp"`
	Glucose   float64   `json:"glucose"` // mg/dL
	Trend     *int      `json:"trend,omitempty"`
	Rate      *float64  `json:"rate,omitempty"` // mg/dL per minute
}

type predictInsulin struct {
	Timestamp        time.Time `json:"timestamp"`
	Type             string    `json:"type"`
	Units            *float64  `json:"units,omitempty"`
	RateUPerH        *float64  `json:"rate_u_per_h,omitempty"`
	ProfileRateUPerH *float64  `json:"profile_rate_u_per_h,omitempty"`
	DurationMinutes  *float64  `json:"duration_minutes,omitempty"`
	StandardPercent  *float64  `json:"standard_percent,omitempty"`
	CarbsG           *float64  `json:"carbs_g,omitempty"`
}

type predictRequest struct {
	Timestamp     time.Time        `json:"timestamp"`
	GlucoseUnit   string           `json:"glucose_unit"`
	RecentGlucose []predictGlucose `json:"recent_glucose"`
	RecentInsulin []predictInsulin `json:"recent_insulin"`
}

// --- Response, from health-predict and passed on to the dashboard ---

// PredictionInterval is one horizon's prediction (mmol/L) with its 80%
// (lower/upper) and 95% intervals.
type PredictionInterval struct {
	Value           float64 `json:"value"`
	Lower           float64 `json:"lower"`
	Upper           float64 `json:"upper"`
	Lower95         float64 `json:"lower_95"`
	Upper95         float64 `json:"upper_95"`
	ConfidenceLevel float64 `json:"confidence_level"`
}

// PredictionResponse is GET /prediction's answer when a prediction is
// available.
type PredictionResponse struct {
	Available      bool                          `json:"available"`
	Timestamp      time.Time                     `json:"timestamp"`
	CurrentGlucose float64                       `json:"current_glucose"`
	Unit           string                        `json:"unit"`
	Predictions    map[string]PredictionInterval `json:"predictions"`
	ModelVersion   map[string]string             `json:"model_version"`
	Warnings       []string                      `json:"warnings"`
	Disclaimer     string                        `json:"disclaimer"`
}

// predictionCache holds the last prediction, for one T and pump watermark.
type predictionCache struct {
	mu       sync.Mutex
	at, pump time.Time
	resp     *PredictionResponse
}

// --- Rows read from the database ---

type tandemCGMRow struct {
	ReadingAt          time.Time `gorm:"column:reading_at"`
	Sgv                float64   `gorm:"column:sgv"`
	Trend              *int      `gorm:"column:trend"`
	Rate               *float64  `gorm:"column:rate"`
	GlucoseValueStatus *int      `gorm:"column:glucose_value_status"`
	SequenceNumber     int       `gorm:"column:sequence_number"`
	Egv                *int64    `gorm:"column:egv"`
}

type nightscoutRow struct {
	ID     int64 `gorm:"column:id"`
	NsTime int64 `gorm:"column:ns_time"` // epoch ms
	Sgv    int   `gorm:"column:sgv"`
	Trend  int   `gorm:"column:trend"`
}

type predictBolusRow struct {
	BolusType        *string    `gorm:"column:bolus_type"`
	RequestedAt      time.Time  `gorm:"column:requested_at"`
	CompletedAt      *time.Time `gorm:"column:completed_at"`
	InsulinRequested *float64   `gorm:"column:insulin_requested"`
	InsulinDelivered *float64   `gorm:"column:insulin_delivered"`
	CarbAmount       *float64   `gorm:"column:carb_amount"`
	JSONBolusType    *int       `gorm:"column:json_bolus_type"`
	JSONOptions      *int       `gorm:"column:json_options"`
	StandardPercent  *float64   `gorm:"column:standard_percent"`
	Duration         *float64   `gorm:"column:duration"`
}

type predictBasalRow struct {
	ChangedAt      time.Time `gorm:"column:changed_at"`
	SequenceNumber int       `gorm:"column:sequence_number"`
	CommandedRate  float64   `gorm:"column:commanded_rate"`
	BaseRate       *float64  `gorm:"column:base_rate"`
}

// --- Mapping (health-predict/config/mappings.yaml) ---

// trendOrdinal maps a CGM trend code (Tandem and Nightscout share 1–7) to
// health-predict's +3 (fastest rise) … −3 (fastest fall). Other codes (none,
// not computable) have no direction.
func trendOrdinal(code int) *int {
	if code < 1 || code > 7 {
		return nil
	}
	o := 4 - code
	return &o
}

// bolusDeliveryType applies health-predict's bolus_rules, first match wins.
// "" means unmapped; those boluses aren't sent.
func bolusDeliveryType(bolusType, options *int, hasCarbs bool) string {
	is := func(p *int, v int) bool { return p != nil && *p == v }
	switch {
	case is(bolusType, 2) && is(options, 3):
		return "automated_correction"
	case is(bolusType, 3) && is(options, 4) && hasCarbs:
		return "bolus"
	case is(bolusType, 3) && is(options, 4):
		return "manual_correction"
	case is(bolusType, 1):
		return "bolus"
	}
	return ""
}

func ptr[T any](v T) *T { return &v }

// --- Building the request ---

// mergeCGM returns the glucose series to send (mg/dL, ascending): live
// Tandem readings, deduplicated by sensor timestamp (highest sequence
// number wins), plus Dexcom readings with no Tandem reading within
// nightscoutFillTolerance. Sensor LOW/HIGH become 40/400; impossible
// values are dropped. Only readings in (from, at] are kept.
func mergeCGM(tandem []tandemCGMRow, ns []nightscoutRow, from, at time.Time) []predictGlucose {
	inWindow := func(t time.Time) bool { return t.After(from) && !t.After(at) }

	// Tandem: one reading per sensor timestamp.
	sort.SliceStable(tandem, func(i, j int) bool { return tandem[i].SequenceNumber > tandem[j].SequenceNumber })
	seenEgv := map[int64]bool{}
	var out []predictGlucose
	var tandemTimes []time.Time
	for _, r := range tandem {
		if r.Egv != nil {
			if seenEgv[*r.Egv] {
				continue
			}
			seenEgv[*r.Egv] = true
		}
		if !inWindow(r.ReadingAt) {
			continue
		}
		mg := r.Sgv
		censored := false
		if r.GlucoseValueStatus != nil {
			switch *r.GlucoseValueStatus {
			case 2:
				mg, censored = sensorLowMgdl, true
			case 1:
				mg, censored = sensorHighMgdl, true
			}
		}
		if !censored && (mg < impossibleMinMgdl || mg > impossibleMaxMgdl) {
			continue
		}
		g := predictGlucose{Timestamp: r.ReadingAt, Glucose: mg}
		if r.Trend != nil {
			g.Trend = trendOrdinal(*r.Trend)
		}
		if r.Rate != nil {
			g.Rate = ptr(*r.Rate / 10) // Tandem rate is in 0.1 mg/dL per minute
		}
		out = append(out, g)
		tandemTimes = append(tandemTimes, r.ReadingAt)
	}
	sort.Slice(tandemTimes, func(i, j int) bool { return tandemTimes[i].Before(tandemTimes[j]) })

	// Dexcom: one reading per timestamp (the latest row wins), filling in
	// only where Tandem has no reading close by.
	sort.SliceStable(ns, func(i, j int) bool { return ns[i].ID > ns[j].ID })
	seenNs := map[int64]bool{}
	for _, r := range ns {
		if seenNs[r.NsTime] {
			continue
		}
		seenNs[r.NsTime] = true
		t := time.UnixMilli(r.NsTime).UTC()
		if !inWindow(t) || nearTandem(tandemTimes, t) {
			continue
		}
		mg := float64(r.Sgv)
		censored := false
		switch {
		case r.Sgv <= 39:
			mg, censored = sensorLowMgdl, true
		case r.Sgv >= 401:
			mg, censored = sensorHighMgdl, true
		}
		if !censored && (mg < impossibleMinMgdl || mg > impossibleMaxMgdl) {
			continue
		}
		out = append(out, predictGlucose{Timestamp: t, Glucose: mg, Trend: trendOrdinal(r.Trend)})
	}

	sort.Slice(out, func(i, j int) bool { return out[i].Timestamp.Before(out[j].Timestamp) })
	return out
}

// nearTandem reports whether a Tandem reading (times sorted ascending) is
// within nightscoutFillTolerance of t.
func nearTandem(times []time.Time, t time.Time) bool {
	i := sort.Search(len(times), func(i int) bool { return !times[i].Before(t) })
	for _, j := range []int{i - 1, i} {
		if j >= 0 && j < len(times) {
			if d := times[j].Sub(t); d <= nightscoutFillTolerance && d >= -nightscoutFillTolerance {
				return true
			}
		}
	}
	return false
}

// bolusEvents returns the boluses known at `at`: a standard bolus once it
// completed (timestamped then), an extended one from when it started.
// Units are those delivered (requested, for an extended bolus, with its
// duration and the share delivered at once). Unmapped or out-of-range
// boluses are skipped.
func bolusEvents(rows []predictBolusRow, at time.Time) []predictInsulin {
	out := []predictInsulin{}
	for _, r := range rows {
		if r.InsulinDelivered == nil || *r.InsulinDelivered <= 0 {
			continue
		}
		var carbs *float64
		if r.CarbAmount != nil && *r.CarbAmount > 0 && *r.CarbAmount <= 500 {
			carbs = r.CarbAmount
		}
		kind := bolusDeliveryType(r.JSONBolusType, r.JSONOptions, carbs != nil)
		if kind == "" {
			continue
		}
		ev := predictInsulin{Type: kind, CarbsG: carbs}
		if r.BolusType != nil && *r.BolusType == "EXTENDED" {
			if r.RequestedAt.After(at) || r.InsulinRequested == nil || r.Duration == nil ||
				*r.Duration < 0 || *r.Duration > 480 {
				continue
			}
			pct := 100.0
			if r.StandardPercent != nil {
				pct = math.Min(math.Max(*r.StandardPercent, 0), 100)
			}
			ev.Timestamp = r.RequestedAt
			ev.Units = ptr(*r.InsulinRequested)
			ev.DurationMinutes = ptr(*r.Duration)
			ev.StandardPercent = ptr(pct)
		} else {
			if r.CompletedAt == nil || r.CompletedAt.After(at) {
				continue
			}
			ev.Timestamp = *r.CompletedAt
			ev.Units = ptr(*r.InsulinDelivered)
		}
		if *ev.Units > 50 {
			continue
		}
		out = append(out, ev)
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Timestamp.Before(out[j].Timestamp) })
	return out
}

// basalEvents returns one basal_rate event per rate change (for changes
// sharing a timestamp, the highest sequence number wins).
func basalEvents(rows []predictBasalRow) []predictInsulin {
	sort.SliceStable(rows, func(i, j int) bool {
		if !rows[i].ChangedAt.Equal(rows[j].ChangedAt) {
			return rows[i].ChangedAt.Before(rows[j].ChangedAt)
		}
		return rows[i].SequenceNumber < rows[j].SequenceNumber
	})
	out := []predictInsulin{}
	for i, r := range rows {
		if i+1 < len(rows) && rows[i+1].ChangedAt.Equal(r.ChangedAt) {
			continue // superseded
		}
		if r.CommandedRate < 0 || r.CommandedRate > 15 {
			continue
		}
		ev := predictInsulin{Timestamp: r.ChangedAt, Type: "basal_rate", RateUPerH: ptr(r.CommandedRate)}
		if r.BaseRate != nil && *r.BaseRate >= 0 && *r.BaseRate <= 15 {
			ev.ProfileRateUPerH = ptr(*r.BaseRate)
		}
		out = append(out, ev)
	}
	return out
}

// pumpLagWarning describes pump data that ends well before the reading.
func pumpLagWarning(at, pump time.Time) string {
	if pump.IsZero() {
		return "No pump data was found, so the prediction has no insulin information."
	}
	lag := at.Sub(pump)
	if lag <= predictPumpLagWarning {
		return ""
	}
	return fmt.Sprintf("Pump data is %d min behind this reading (it syncs from Tandem periodically), "+
		"so insulin delivered since then isn't included.", int(lag.Minutes()))
}

// --- Handler ---

func predictionUnavailable(c *gin.Context, reason string) {
	c.JSON(http.StatusOK, gin.H{"available": false, "reason": reason})
}

// GetPrediction (GET /prediction)
// Predicted glucose 15–60 minutes after the latest CGM reading, from
// health-predict. Answers {"available": false, "reason": ...} when
// predictions are off (no PREDICT_URL), the latest reading is too old, or
// health-predict can't predict from the data; 502 when it can't be reached.
func (h *Handler) GetPrediction(c *gin.Context) {
	if h.Cfg == nil || h.Cfg.PredictURL == "" {
		predictionUnavailable(c, "Predictions are not configured")
		return
	}
	ctx := c.Request.Context()
	db := h.DB.WithContext(ctx)

	// T: the latest CGM reading from either source, as measured (Dexcom's
	// ns_time, a live Tandem reading's reading_at).
	var latest struct {
		NsTime   *int64     `gorm:"column:ns_time"`
		TandemAt *time.Time `gorm:"column:tandem_at"`
	}
	if err := db.Raw(`SELECT
		(SELECT ns_time FROM ns_part WHERE ns_datetime <= now() ORDER BY ns_datetime DESC LIMIT 1) AS ns_time,
		(SELECT reading_at FROM tandem_cgm WHERE reading_at <= now()
			AND NOT coalesce(event_properties->'cgmDataType' @> '[1]', false)
			ORDER BY reading_at DESC LIMIT 1) AS tandem_at`).Scan(&latest).Error; err != nil {
		serverError(c, fmt.Errorf("latest reading: %w", err))
		return
	}
	var at time.Time
	if latest.NsTime != nil {
		at = time.UnixMilli(*latest.NsTime).UTC()
	}
	if latest.TandemAt != nil && latest.TandemAt.After(at) {
		at = latest.TandemAt.UTC()
	}
	if at.IsZero() || time.Since(at) > predictMaxReadingAge {
		predictionUnavailable(c, "The latest reading is more than 15 minutes old")
		return
	}

	// How far the pump data (insulin and the pump's CGM log, synced
	// together) reaches, up to T.
	var watermark struct {
		PumpUntil *time.Time `gorm:"column:pump_until"`
	}
	if err := db.Raw(`SELECT GREATEST(
		(SELECT max(changed_at) FROM tandem_basal WHERE changed_at <= ?),
		(SELECT max(completed_at) FROM tandem_bolus WHERE completed_at <= ?),
		(SELECT max(reading_at) FROM tandem_cgm WHERE reading_at <= ?)) AS pump_until`,
		at, at, at).Scan(&watermark).Error; err != nil {
		serverError(c, fmt.Errorf("pump data watermark: %w", err))
		return
	}
	var pump time.Time
	if watermark.PumpUntil != nil {
		pump = watermark.PumpUntil.UTC()
	}

	h.pred.mu.Lock()
	defer h.pred.mu.Unlock()
	if h.pred.resp != nil && h.pred.at.Equal(at) && h.pred.pump.Equal(pump) {
		c.JSON(http.StatusOK, h.pred.resp)
		return
	}

	req, err := h.buildPredictRequest(ctx, at)
	if err != nil {
		serverError(c, err)
		return
	}
	resp, unavailable, err := callPredict(ctx, h.Cfg.PredictURL, req)
	if err != nil {
		slog.Error("Prediction service failed", "error", err)
		c.JSON(http.StatusBadGateway, gin.H{"detail": "Prediction service unavailable"})
		return
	}
	if unavailable != "" {
		slog.Info("Prediction service declined the request", "detail", unavailable)
		predictionUnavailable(c, "No prediction can be made from the current data")
		return
	}
	if w := pumpLagWarning(at, pump); w != "" {
		resp.Warnings = append(resp.Warnings, w)
	}
	h.pred.at, h.pred.pump, h.pred.resp = at, pump, resp
	c.JSON(http.StatusOK, resp)
}

// buildPredictRequest reads what was known at `at` and maps it for
// health-predict.
func (h *Handler) buildPredictRequest(ctx context.Context, at time.Time) (*predictRequest, error) {
	db := h.DB.WithContext(ctx)
	glucoseFrom := at.Add(-predictGlucoseHistory)

	var tandem []tandemCGMRow
	if err := db.Raw(`SELECT reading_at, sgv, trend, rate, glucose_value_status, sequence_number,
			(event_properties->>'egvTimeStamp')::bigint AS egv
		FROM tandem_cgm
		WHERE reading_at > ? AND reading_at <= ?
			AND NOT coalesce(event_properties->'cgmDataType' @> '[1]', false)`,
		glucoseFrom, at).Scan(&tandem).Error; err != nil {
		return nil, fmt.Errorf("tandem_cgm: %w", err)
	}
	// ns_datetime is indexed; ns_time (ms) is the exact time, filtered in
	// mergeCGM. The minute of slack covers any rounding between them.
	var ns []nightscoutRow
	if err := db.Raw(`SELECT id, ns_time, sgv, trend FROM ns_part
		WHERE ns_datetime > ? AND ns_datetime <= ?`,
		glucoseFrom.Add(-time.Minute), at.Add(time.Minute)).Scan(&ns).Error; err != nil {
		return nil, fmt.Errorf("ns_part: %w", err)
	}

	var boluses []predictBolusRow
	if err := db.Raw(`SELECT bolus_type::text AS bolus_type, requested_at, completed_at,
			insulin_requested, insulin_delivered, carb_amount,
			(event_properties->>'bolusType')::float8::int AS json_bolus_type,
			(event_properties->>'options')::float8::int AS json_options,
			(event_properties->>'standardPercent')::float8 AS standard_percent,
			(event_properties->>'duration')::float8 AS duration
		FROM tandem_bolus
		WHERE requested_at > ? AND requested_at <= ? AND insulin_delivered > 0`,
		at.Add(-predictBolusHistory), at).Scan(&boluses).Error; err != nil {
		return nil, fmt.Errorf("tandem_bolus: %w", err)
	}

	// Rate changes in the window, plus the one in effect at its start.
	basalFrom := at.Add(-predictBasalHistory)
	var basal []predictBasalRow
	if err := db.Raw(`SELECT changed_at, sequence_number, commanded_rate, base_rate
		FROM tandem_basal
		WHERE changed_at <= ? AND changed_at >= coalesce(
			(SELECT max(changed_at) FROM tandem_basal WHERE changed_at <= ?), ?)`,
		at, basalFrom, basalFrom).Scan(&basal).Error; err != nil {
		return nil, fmt.Errorf("tandem_basal: %w", err)
	}

	insulin := append(basalEvents(basal), bolusEvents(boluses, at)...)
	return &predictRequest{
		Timestamp:     at,
		GlucoseUnit:   "mg/dL",
		RecentGlucose: mergeCGM(tandem, ns, glucoseFrom, at),
		RecentInsulin: insulin,
	}, nil
}

// callPredict posts the request to health-predict. A 422 (it can't predict
// from these inputs) comes back as `unavailable` with its detail, not as
// an error.
func callPredict(ctx context.Context, baseURL string, req *predictRequest) (resp *PredictionResponse, unavailable string, err error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, "", err
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/predict", bytes.NewReader(body))
	if err != nil {
		return nil, "", err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpResp, err := predictHTTPClient.Do(httpReq)
	if err != nil {
		return nil, "", err
	}
	defer httpResp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(httpResp.Body, 1<<20))
	if err != nil {
		return nil, "", err
	}
	switch httpResp.StatusCode {
	case http.StatusOK:
	case http.StatusUnprocessableEntity:
		return nil, string(data), nil
	default:
		return nil, "", fmt.Errorf("status %d: %s", httpResp.StatusCode, data)
	}

	var out PredictionResponse
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, "", fmt.Errorf("decoding response: %w", err)
	}
	if len(out.Predictions) == 0 {
		return nil, "", fmt.Errorf("response has no predictions")
	}
	out.Available = true
	if out.Warnings == nil {
		out.Warnings = []string{}
	}
	return &out, "", nil
}

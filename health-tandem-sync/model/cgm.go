package model

import "time"

// CGMReading is one CGMReading pump-log event (eventCode 399) — the glucose
// value the pump itself received from the CGM transmitter, independent of
// health-sync's Dexcom Share polling. Maps to the tandem_cgm table
// (health-db/05-add-tandem-cgm-table.sql).
//
// This is expected to closely duplicate nightscoutdb/ns_part (the table
// health-sync/health-mongo-sync populate, and every existing chart/TIR/GMI
// query reads from) since both ultimately come from the same Dexcom sensor.
// It's captured anyway, into its own table, specifically as a fallback: a
// gap in the Dexcom Share sync (missed poll, outage, session issue) may
// still have a reading here for the same moment. Reconciling this into
// nightscoutdb is a deliberate, separate step — never automatic — so a
// disagreement between the two sources for the same timestamp doesn't
// silently overwrite what health-sync already recorded.
type CGMReading struct {
	DeviceAssignmentID string
	SequenceGroup      int
	SequenceNumber     int
	ReadingAt          time.Time
	SGV                float64  // mg/dL, eventProperties.currentGlucoseDisplayValue
	Trend              *int     // bucketed from Rate; see TrendFromRate. Same 0-9/99 scheme as nightscoutdb/ns_part.trend
	Rate               *float64 // raw mg/dL per 5 minutes, as reported by the pump
	GlucoseValueStatus *int     // nonzero (e.g. an out-of-sensor-range clamp like LOW) marks the reading as suspect
	EventProperties    map[string]interface{}
}

// Trend direction codes, matching health-sync/common.TrendToDirection's
// scheme (nightscoutdb/ns_part.trend stores these same integers).
const (
	TrendNone           = 0
	TrendDoubleUp       = 1
	TrendSingleUp       = 2
	TrendFortyFiveUp    = 3
	TrendFlat           = 4
	TrendFortyFiveDown  = 5
	TrendSingleDown     = 6
	TrendDoubleDown     = 7
	TrendNotComputable  = 8
	TrendRateOutOfRange = 9
)

// TrendFromRate buckets a raw rate of change (mg/dL per 5 minutes, as
// reported in eventProperties.rate) into the same direction scheme
// nightscoutdb/ns_part.trend uses, approximating Dexcom's own published
// trend-arrow thresholds (roughly ±1, ±2, ±3 mg/dL/min, i.e. ±5, ±10, ±15
// mg/dL per 5-minute reading).
func TrendFromRate(rate float64) int {
	switch {
	case rate >= 15:
		return TrendDoubleUp
	case rate >= 10:
		return TrendSingleUp
	case rate >= 5:
		return TrendFortyFiveUp
	case rate > -5:
		return TrendFlat
	case rate > -10:
		return TrendFortyFiveDown
	case rate > -15:
		return TrendSingleDown
	default:
		return TrendDoubleDown
	}
}

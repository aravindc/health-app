// Package model holds the plain data shapes shared across health-tandem-sync
// — the normalised form both the Tandem Source fetch/parse side and the
// Postgres write side build against — mirroring health-mongo-sync's model
// package.
package model

import "time"

// Bolus is one merged bolus delivery: the BolusRequested*/BolusCompleted
// pump-log events for a given bolusId, combined into a single record. Maps
// to the tandem_bolus table (health-db/04-add-tandem-insulin-tables.sql).
//
// A bolus is often a mix of a food (carb-covering) component and a
// correction (high-BG-covering) component delivered together as one dose;
// FoodBolusSize + CorrectionBolusSize sum to InsulinRequested/bolusSize's
// totalBolusSize (eventCode 66, BolusRequestedSplit). CorrectionIncluded and
// CarbRatio come from eventCode 64 (BolusRequestedCarb) and explain why a
// correction was applied.
type Bolus struct {
	DeviceAssignmentID  string
	BolusID             int64
	BolusType           string // "STANDARD" or "EXTENDED", empty if unknown
	RequestedAt         *time.Time
	CompletedAt         *time.Time
	InsulinRequested    *float64
	InsulinDelivered    *float64
	FoodBolusSize       *float64 // portion of InsulinRequested covering carbs
	CorrectionBolusSize *float64 // portion of InsulinRequested covering high BG
	CorrectionIncluded  *bool    // whether a correction was applied at all
	CarbAmount          *float64
	CarbRatio           *float64
	BG                  *float64
	CompletionStatus    *int64
	EventProperties     map[string]interface{}
}

// Basal is one BasalRateChange pump-log event — a point-in-time change to
// the basal rate, not a continuous rate sample — rate normalized to U/hr.
// Maps to the tandem_basal table.
type Basal struct {
	DeviceAssignmentID string
	SequenceGroup      int
	SequenceNumber     int
	ChangedAt          time.Time
	CommandedRate      float64
	BaseRate           *float64
	MaxRate            *float64
	EventProperties    map[string]interface{}
}

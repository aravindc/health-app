// Package insulin extracts bolus and basal-rate-change events from parsed
// Tandem pump-logs data and upserts them into the normalized tandem_bolus /
// tandem_basal tables in health-db (see
// health-db/04-add-tandem-insulin-tables.sql) — a different database than
// tandemdata's own generic events store (tandemdb).
package insulin

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"tandemdata/pumplog"
)

// Event codes that together describe insulin delivery. See pumplog.EventCodeNames.
const (
	codeBolusRequestedStandard = 55
	codeBolusRequestedExtended = 59
	codeBolusRequestedCarb     = 64
	codeBolusRequestedDetail   = 65
	codeBolusRequestedSplit    = 66
	codeBolusCompleted         = 20
	codeBolusCompleted2        = 21
	codeBasalRateChange        = 3
)

var bolusRequestCodes = map[int]bool{
	codeBolusRequestedStandard: true,
	codeBolusRequestedExtended: true,
	codeBolusRequestedCarb:     true,
	codeBolusRequestedDetail:   true,
	codeBolusRequestedSplit:    true,
}

var bolusCompletedCodes = map[int]bool{
	codeBolusCompleted:  true,
	codeBolusCompleted2: true,
}

// estimatedDateTimeLayout matches pumplog.Event.EstimatedDateTime, which is
// an RFC3339-ish UTC timestamp (e.g. "2025-05-18T16:21:17Z" or with
// fractional seconds).
func parseEstimatedDateTime(s string) (time.Time, error) {
	if s == "" {
		return time.Time{}, fmt.Errorf("empty estimatedDateTime")
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t, nil
	}
	return time.Parse(time.RFC3339, s)
}

// Bolus is one merged bolus delivery, combining the BolusRequested* events
// with their matching BolusCompleted event (both keyed by bolusId).
type Bolus struct {
	DeviceAssignmentID string
	BolusID            int64
	BolusType          string // "STANDARD" or "EXTENDED", empty if unknown
	RequestedAt        *time.Time
	CompletedAt        *time.Time
	InsulinRequested   *float64
	InsulinDelivered   *float64
	CarbAmount         *float64
	BG                 *float64
	CompletionStatus   *int64
	EventProperties    map[string]interface{}
}

// BasalChange is one BasalRateChange event, normalized to U/hr.
type BasalChange struct {
	DeviceAssignmentID string
	SequenceGroup      int
	SequenceNumber     int
	ChangedAt          time.Time
	CommandedRate      float64
	BaseRate           *float64
	MaxRate            *float64
	EventProperties    map[string]interface{}
}

func floatProp(props map[string]interface{}, key string) *float64 {
	v, ok := props[key]
	if !ok {
		return nil
	}
	f, ok := v.(float64)
	if !ok {
		return nil
	}
	return &f
}

func intProp(props map[string]interface{}, key string) *int64 {
	f := floatProp(props, key)
	if f == nil {
		return nil
	}
	i := int64(*f)
	return &i
}

// ExtractBoluses groups bolus-related events (event codes 20/21/55/59/64/65/66)
// by bolusId and merges each group into one Bolus. Events with no bolusId
// property are skipped.
func ExtractBoluses(events []pumplog.Event) []Bolus {
	type group struct {
		deviceAssignmentID string
		requests           []pumplog.Event
		completions        []pumplog.Event
	}
	groups := make(map[int64]*group)
	var order []int64

	for _, e := range events {
		isRequest := bolusRequestCodes[e.EventCode]
		isCompleted := bolusCompletedCodes[e.EventCode]
		if !isRequest && !isCompleted {
			continue
		}
		bolusID := intProp(e.EventProperties, "bolusId")
		if bolusID == nil {
			continue
		}
		g, ok := groups[*bolusID]
		if !ok {
			g = &group{deviceAssignmentID: e.DeviceAssignmentID}
			groups[*bolusID] = g
			order = append(order, *bolusID)
		}
		if isRequest {
			g.requests = append(g.requests, e)
		} else {
			g.completions = append(g.completions, e)
		}
	}

	boluses := make([]Bolus, 0, len(order))
	for _, id := range order {
		g := groups[id]
		b := Bolus{
			DeviceAssignmentID: g.deviceAssignmentID,
			BolusID:            id,
			EventProperties:    map[string]interface{}{},
		}

		var extended bool
		for _, e := range g.requests {
			for k, v := range e.EventProperties {
				b.EventProperties[k] = v
			}
			if e.EventCode == codeBolusRequestedExtended {
				extended = true
			}
			if t, err := parseEstimatedDateTime(e.EstimatedDateTime); err == nil {
				if b.RequestedAt == nil || t.Before(*b.RequestedAt) {
					b.RequestedAt = &t
				}
			}
			if size := floatProp(e.EventProperties, "bolusSize"); size != nil {
				b.InsulinRequested = size
			}
			if size := floatProp(e.EventProperties, "bolexSize"); size != nil {
				b.InsulinRequested = size
			}
			if carb := floatProp(e.EventProperties, "carbAmount"); carb != nil {
				b.CarbAmount = carb
			}
			if bg := floatProp(e.EventProperties, "bg"); bg != nil {
				b.BG = bg
			}
		}
		if extended {
			b.BolusType = "EXTENDED"
		} else if len(g.requests) > 0 {
			b.BolusType = "STANDARD"
		}

		for _, e := range g.completions {
			for k, v := range e.EventProperties {
				b.EventProperties[k] = v
			}
			if t, err := parseEstimatedDateTime(e.EstimatedDateTime); err == nil {
				if b.CompletedAt == nil || t.After(*b.CompletedAt) {
					b.CompletedAt = &t
				}
			}
			if delivered := floatProp(e.EventProperties, "insulinDelivered"); delivered != nil {
				b.InsulinDelivered = delivered
			}
			if requested := floatProp(e.EventProperties, "insulinRequested"); requested != nil {
				b.InsulinRequested = requested
			}
			if status := intProp(e.EventProperties, "completionStatus"); status != nil {
				b.CompletionStatus = status
			}
		}

		boluses = append(boluses, b)
	}
	return boluses
}

// ExtractBasalChanges extracts BasalRateChange events (eventCode 3), which
// report rates directly in U/hr.
func ExtractBasalChanges(events []pumplog.Event) []BasalChange {
	var out []BasalChange
	for _, e := range events {
		if e.EventCode != codeBasalRateChange {
			continue
		}
		rate := floatProp(e.EventProperties, "commandedBasalRate")
		if rate == nil {
			continue
		}
		changedAt, err := parseEstimatedDateTime(e.EstimatedDateTime)
		if err != nil {
			continue
		}
		out = append(out, BasalChange{
			DeviceAssignmentID: e.DeviceAssignmentID,
			SequenceGroup:      e.SequenceGroup,
			SequenceNumber:     e.SequenceNumber,
			ChangedAt:          changedAt,
			CommandedRate:      *rate,
			BaseRate:           floatProp(e.EventProperties, "baseBasalRate"),
			MaxRate:            floatProp(e.EventProperties, "maxBasalRate"),
			EventProperties:    e.EventProperties,
		})
	}
	return out
}

// UpsertResult summarizes an UpsertBoluses/UpsertBasalChanges call.
type UpsertResult struct {
	BolusesUpserted      int
	BasalChangesUpserted int
}

// UpsertBoluses upserts boluses into the tandem_bolus table, keyed on
// (device_assignment_id, bolus_id), so re-running with overlapping data is
// idempotent. A bolus row is only written once it has a requested_at or
// completed_at timestamp (both nil means the event group had neither, which
// shouldn't happen for well-formed input but is skipped defensively).
func UpsertBoluses(db *sql.DB, boluses []Bolus) (int, error) {
	const query = `
		INSERT INTO tandem_bolus (
			device_assignment_id, bolus_id, bolus_type, requested_at, completed_at,
			insulin_requested, insulin_delivered, carb_amount, bg, completion_status,
			event_properties
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
		ON CONFLICT (device_assignment_id, bolus_id) DO UPDATE SET
			bolus_type = COALESCE(excluded.bolus_type, tandem_bolus.bolus_type),
			requested_at = COALESCE(excluded.requested_at, tandem_bolus.requested_at),
			completed_at = COALESCE(excluded.completed_at, tandem_bolus.completed_at),
			insulin_requested = COALESCE(excluded.insulin_requested, tandem_bolus.insulin_requested),
			insulin_delivered = COALESCE(excluded.insulin_delivered, tandem_bolus.insulin_delivered),
			carb_amount = COALESCE(excluded.carb_amount, tandem_bolus.carb_amount),
			bg = COALESCE(excluded.bg, tandem_bolus.bg),
			completion_status = COALESCE(excluded.completion_status, tandem_bolus.completion_status),
			event_properties = tandem_bolus.event_properties || excluded.event_properties
	`

	var count int
	for _, b := range boluses {
		if b.RequestedAt == nil && b.CompletedAt == nil {
			continue
		}
		propsJSON, err := json.Marshal(b.EventProperties)
		if err != nil {
			return count, fmt.Errorf("marshaling event properties for bolus %d: %w", b.BolusID, err)
		}
		var bolusType interface{}
		if b.BolusType != "" {
			bolusType = b.BolusType
		}
		if _, err := db.Exec(query,
			b.DeviceAssignmentID, b.BolusID, bolusType, b.RequestedAt, b.CompletedAt,
			b.InsulinRequested, b.InsulinDelivered, b.CarbAmount, b.BG, b.CompletionStatus,
			string(propsJSON),
		); err != nil {
			return count, fmt.Errorf("upserting bolus %d: %w", b.BolusID, err)
		}
		count++
	}
	return count, nil
}

// UpsertBasalChanges upserts basal rate changes into the tandem_basal table,
// keyed on (device_assignment_id, sequence_group, sequence_number).
func UpsertBasalChanges(db *sql.DB, changes []BasalChange) (int, error) {
	const query = `
		INSERT INTO tandem_basal (
			device_assignment_id, sequence_group, sequence_number, changed_at,
			commanded_rate, base_rate, max_rate, event_properties
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (device_assignment_id, sequence_group, sequence_number) DO UPDATE SET
			changed_at = excluded.changed_at,
			commanded_rate = excluded.commanded_rate,
			base_rate = excluded.base_rate,
			max_rate = excluded.max_rate,
			event_properties = excluded.event_properties
	`

	var count int
	for _, c := range changes {
		propsJSON, err := json.Marshal(c.EventProperties)
		if err != nil {
			return count, fmt.Errorf("marshaling event properties for basal change %d/%d: %w",
				c.SequenceGroup, c.SequenceNumber, err)
		}
		if _, err := db.Exec(query,
			c.DeviceAssignmentID, c.SequenceGroup, c.SequenceNumber, c.ChangedAt,
			c.CommandedRate, c.BaseRate, c.MaxRate, string(propsJSON),
		); err != nil {
			return count, fmt.Errorf("upserting basal change %d/%d: %w", c.SequenceGroup, c.SequenceNumber, err)
		}
		count++
	}
	return count, nil
}

// Sync extracts boluses and basal changes from events and upserts both into
// db (expected to be health-db, already migrated with
// 04-add-tandem-insulin-tables.sql).
func Sync(db *sql.DB, events []pumplog.Event) (UpsertResult, error) {
	boluses := ExtractBoluses(events)
	basalChanges := ExtractBasalChanges(events)

	var result UpsertResult
	var err error
	result.BolusesUpserted, err = UpsertBoluses(db, boluses)
	if err != nil {
		return result, err
	}
	result.BasalChangesUpserted, err = UpsertBasalChanges(db, basalChanges)
	if err != nil {
		return result, err
	}
	return result, nil
}

// eventIDsForInsulin are the eventIds this package understands, useful for
// building a narrower pump-logs request than the full eventIDs list in
// tandem/reports.go when only insulin data is needed.
var eventIDsForInsulin = []string{"3", "20", "21", "55", "59", "64", "65", "66"}

// EventIDs returns the Tandem Source eventIds this package extracts data
// from, joined as a comma-separated string suitable for the pump-logs
// endpoint's eventIds query parameter.
func EventIDs() string {
	return strings.Join(eventIDsForInsulin, ",")
}

// Package insulin extracts bolus, basal-rate-change, and CGM events from
// parsed Tandem pump-logs data and upserts them into the normalized
// tandem_bolus / tandem_basal / tandem_cgm tables in health-db. Those
// tables' schema is owned by health-api's goose migrations (see
// health-api/database/migrations/00003_add_tandem_insulin_tables.sql and
// 00004_add_tandem_cgm_table.sql), not by anything in this module.
package insulin

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"tandemsync/model"
	"tandemsync/pumplog"
)

// Event codes this package extracts. See pumplog.EventCodeNames.
const (
	codeBasalRateChange        = 3
	codeBolusCompleted         = 20
	codeBolusCompleted2        = 21
	codeBolusRequestedStandard = 55
	codeBolusRequestedExtended = 59
	codeBolusRequestedCarb     = 64
	codeBolusRequestedDetail   = 65
	codeBolusRequestedSplit    = 66
	codeCGMReading             = 399
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

// parseEstimatedDateTime parses pumplog.Event.EstimatedDateTime, which is an
// RFC3339-ish UTC timestamp (e.g. "2025-05-18T16:21:17Z" or with fractional
// seconds).
func parseEstimatedDateTime(s string) (time.Time, error) {
	if s == "" {
		return time.Time{}, fmt.Errorf("empty estimatedDateTime")
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t, nil
	}
	return time.Parse(time.RFC3339, s)
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

func boolProp(props map[string]interface{}, key string) *bool {
	i := intProp(props, key)
	if i == nil {
		return nil
	}
	b := *i != 0
	return &b
}

// ExtractBoluses groups bolus-related events (event codes 20/21/55/59/64/65/66)
// by bolusId and merges each group into one model.Bolus. Events with no
// bolusId property are skipped.
func ExtractBoluses(events []pumplog.Event) []model.Bolus {
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

	boluses := make([]model.Bolus, 0, len(order))
	for _, id := range order {
		g := groups[id]
		b := model.Bolus{
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
			if ratio := floatProp(e.EventProperties, "carbRatio"); ratio != nil {
				b.CarbRatio = ratio
			}
			if bg := floatProp(e.EventProperties, "bg"); bg != nil {
				b.BG = bg
			}
			if included := boolProp(e.EventProperties, "correctionBolusIncluded"); included != nil {
				b.CorrectionIncluded = included
			}
			if food := floatProp(e.EventProperties, "foodBolusSize"); food != nil {
				b.FoodBolusSize = food
			}
			if correction := floatProp(e.EventProperties, "correctionBolusSize"); correction != nil {
				b.CorrectionBolusSize = correction
			}
			if total := floatProp(e.EventProperties, "totalBolusSize"); total != nil {
				b.InsulinRequested = total
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

// ExtractBasal extracts BasalRateChange events (eventCode 3), which report
// rates directly in U/hr.
func ExtractBasal(events []pumplog.Event) []model.Basal {
	var out []model.Basal
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
		out = append(out, model.Basal{
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

// ExtractCGMReadings extracts CGMReading events (eventCode 399).
func ExtractCGMReadings(events []pumplog.Event) []model.CGMReading {
	var out []model.CGMReading
	for _, e := range events {
		if e.EventCode != codeCGMReading {
			continue
		}
		sgv := floatProp(e.EventProperties, "currentGlucoseDisplayValue")
		if sgv == nil {
			continue
		}
		readingAt, err := parseEstimatedDateTime(e.EstimatedDateTime)
		if err != nil {
			continue
		}

		reading := model.CGMReading{
			DeviceAssignmentID: e.DeviceAssignmentID,
			SequenceGroup:      e.SequenceGroup,
			SequenceNumber:     e.SequenceNumber,
			ReadingAt:          readingAt,
			SGV:                *sgv,
			Rate:               floatProp(e.EventProperties, "rate"),
			EventProperties:    e.EventProperties,
		}
		if statusInt := intProp(e.EventProperties, "glucoseValueStatus"); statusInt != nil {
			status := int(*statusInt)
			reading.GlucoseValueStatus = &status
		}
		if reading.Rate != nil {
			trend := model.TrendFromRate(*reading.Rate)
			reading.Trend = &trend
		}
		out = append(out, reading)
	}
	return out
}

// UpsertResult summarizes a Sync call.
type UpsertResult struct {
	BolusesUpserted     int
	BasalUpserted       int
	CGMReadingsUpserted int
}

// UpsertBoluses upserts boluses into the tandem_bolus table, keyed on
// (device_assignment_id, bolus_id), so re-running with overlapping data is
// idempotent. A bolus row is only written once it has a requested_at or
// completed_at timestamp (both nil means the event group had neither, which
// shouldn't happen for well-formed input but is skipped defensively).
func UpsertBoluses(db *sql.DB, boluses []model.Bolus) (int, error) {
	const query = `
		INSERT INTO tandem_bolus (
			device_assignment_id, bolus_id, bolus_type, requested_at, completed_at,
			insulin_requested, insulin_delivered, food_bolus_size, correction_bolus_size,
			correction_included, carb_amount, carb_ratio, bg, completion_status,
			event_properties
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
		ON CONFLICT (device_assignment_id, bolus_id) DO UPDATE SET
			bolus_type = COALESCE(excluded.bolus_type, tandem_bolus.bolus_type),
			requested_at = COALESCE(excluded.requested_at, tandem_bolus.requested_at),
			completed_at = COALESCE(excluded.completed_at, tandem_bolus.completed_at),
			insulin_requested = COALESCE(excluded.insulin_requested, tandem_bolus.insulin_requested),
			insulin_delivered = COALESCE(excluded.insulin_delivered, tandem_bolus.insulin_delivered),
			food_bolus_size = COALESCE(excluded.food_bolus_size, tandem_bolus.food_bolus_size),
			correction_bolus_size = COALESCE(excluded.correction_bolus_size, tandem_bolus.correction_bolus_size),
			correction_included = COALESCE(excluded.correction_included, tandem_bolus.correction_included),
			carb_amount = COALESCE(excluded.carb_amount, tandem_bolus.carb_amount),
			carb_ratio = COALESCE(excluded.carb_ratio, tandem_bolus.carb_ratio),
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
			b.InsulinRequested, b.InsulinDelivered, b.FoodBolusSize, b.CorrectionBolusSize,
			b.CorrectionIncluded, b.CarbAmount, b.CarbRatio, b.BG, b.CompletionStatus,
			string(propsJSON),
		); err != nil {
			return count, fmt.Errorf("upserting bolus %d: %w", b.BolusID, err)
		}
		count++
	}
	return count, nil
}

// UpsertBasal upserts basal rate changes into the tandem_basal table, keyed
// on (device_assignment_id, sequence_group, sequence_number).
func UpsertBasal(db *sql.DB, changes []model.Basal) (int, error) {
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

// UpsertCGMReadings upserts CGM readings into the tandem_cgm table, keyed on
// (device_assignment_id, sequence_group, sequence_number).
func UpsertCGMReadings(db *sql.DB, readings []model.CGMReading) (int, error) {
	const query = `
		INSERT INTO tandem_cgm (
			device_assignment_id, sequence_group, sequence_number, reading_at,
			sgv, trend, rate, glucose_value_status, event_properties
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		ON CONFLICT (device_assignment_id, sequence_group, sequence_number) DO UPDATE SET
			reading_at = excluded.reading_at,
			sgv = excluded.sgv,
			trend = excluded.trend,
			rate = excluded.rate,
			glucose_value_status = excluded.glucose_value_status,
			event_properties = excluded.event_properties
	`

	var count int
	for _, r := range readings {
		propsJSON, err := json.Marshal(r.EventProperties)
		if err != nil {
			return count, fmt.Errorf("marshaling event properties for CGM reading %d/%d: %w",
				r.SequenceGroup, r.SequenceNumber, err)
		}
		if _, err := db.Exec(query,
			r.DeviceAssignmentID, r.SequenceGroup, r.SequenceNumber, r.ReadingAt,
			r.SGV, r.Trend, r.Rate, r.GlucoseValueStatus, string(propsJSON),
		); err != nil {
			return count, fmt.Errorf("upserting CGM reading %d/%d: %w", r.SequenceGroup, r.SequenceNumber, err)
		}
		count++
	}
	return count, nil
}

// LastSyncedAt returns the most recent timestamp already stored across
// tandem_bolus (completed_at, falling back to requested_at), tandem_basal
// (changed_at), and tandem_cgm (reading_at) — i.e. the watermark a caller
// can resume fetching from, instead of a fixed lookback window. ok is false
// if all three tables are empty (nothing synced yet, e.g. before the first
// tandemload run), in which case the caller should fall back to some other
// default window.
func LastSyncedAt(db *sql.DB) (t time.Time, ok bool, err error) {
	const query = `
		SELECT MAX(ts) FROM (
			SELECT COALESCE(completed_at, requested_at) AS ts FROM tandem_bolus
			UNION ALL
			SELECT changed_at AS ts FROM tandem_basal
			UNION ALL
			SELECT reading_at AS ts FROM tandem_cgm
		) all_ts
	`
	var maxTS sql.NullTime
	if err := db.QueryRow(query).Scan(&maxTS); err != nil {
		return time.Time{}, false, fmt.Errorf("querying last synced timestamp: %w", err)
	}
	if !maxTS.Valid {
		return time.Time{}, false, nil
	}
	return maxTS.Time, true, nil
}

// Sync extracts boluses, basal changes, and CGM readings from events and
// upserts all three into db (expected to be health-db, already migrated
// with 04-add-tandem-insulin-tables.sql and 05-add-tandem-cgm-table.sql).
func Sync(db *sql.DB, events []pumplog.Event) (UpsertResult, error) {
	boluses := ExtractBoluses(events)
	basal := ExtractBasal(events)
	cgmReadings := ExtractCGMReadings(events)

	var result UpsertResult
	var err error
	result.BolusesUpserted, err = UpsertBoluses(db, boluses)
	if err != nil {
		return result, err
	}
	result.BasalUpserted, err = UpsertBasal(db, basal)
	if err != nil {
		return result, err
	}
	result.CGMReadingsUpserted, err = UpsertCGMReadings(db, cgmReadings)
	if err != nil {
		return result, err
	}
	return result, nil
}

// eventIDsForSync are the eventIds this package understands, useful for
// building a narrower pump-logs request than the full eventIDs list in
// tandem/reports.go.
var eventIDsForSync = []string{"3", "20", "21", "55", "59", "64", "65", "66", "399"}

// EventIDs returns the Tandem Source eventIds this package extracts data
// from, joined as a comma-separated string suitable for the pump-logs
// endpoint's eventIds query parameter.
func EventIDs() string {
	return strings.Join(eventIDsForSync, ",")
}

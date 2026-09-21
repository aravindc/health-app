// Package pumplog parses the pump-logs-raw.json payload returned by
// Tandem Source's reports/bff/pump-logs/{reportId} endpoint.
package pumplog

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
)

// Event is one entry from either the "events" or "clockChanges" array.
// EventProperties varies by EventCode, so it's kept as a generic map rather
// than modeled per-code.
type Event struct {
	DeviceAssignmentID string                 `json:"deviceAssignmentId"`
	EstimatedDateTime  string                 `json:"estimatedDateTime"`
	PumpDateTime       string                 `json:"pumpDateTime"`
	EventCode          int                    `json:"eventCode"`
	SequenceGroup      int                    `json:"sequenceGroup"`
	SequenceNumber     int                    `json:"sequenceNumber"`
	EventProperties    map[string]interface{} `json:"eventProperties"`
}

// Logs mirrors the top-level shape of pump-logs-raw.json.
type Logs struct {
	Events       []Event `json:"events"`
	ClockChanges []Event `json:"clockChanges"`
}

// EventCodeNames maps known eventCode values to human-readable labels,
// derived from the eventIds queried in reports.go and the property sets
// observed in a real export. Codes without a confirmed meaning are labeled
// UnknownN rather than guessed.
var EventCodeNames = map[int]string{
	3:   "BasalRateChange",
	4:   "AlertActivated",
	5:   "AlarmActivated",
	6:   "Unknown6",
	8:   "AlarmCleared",
	9:   "DailySummary",
	11:  "PumpSuspended",
	12:  "PumpResumed",
	13:  "Unknown13",
	14:  "Unknown14",
	16:  "BGReading",
	20:  "BolusCompleted",
	21:  "BolusCompleted2",
	22:  "ScreenLock",
	23:  "ScreenUnlock",
	26:  "AlertCleared",
	27:  "AlertAcknowledged",
	28:  "AlarmCleared2",
	33:  "CartridgeFilled",
	34:  "BatteryStatus",
	35:  "BatteryStatus2",
	52:  "SystemReset",
	53:  "BatteryTelemetry",
	55:  "BolusRequestedStandard",
	57:  "ProfileNames",
	59:  "BolusRequestedExtended",
	60:  "Unknown60",
	61:  "CannulaFilled",
	63:  "TubingFilled",
	64:  "BolusRequestedCarb",
	65:  "BolusRequestedDetail",
	66:  "BolusRequestedSplit",
	69:  "ProfileNames2",
	81:  "DailyBasalTotal",
	90:  "BasalRateFeature",
	99:  "LogSummary",
	140: "Unknown140",
	171: "Unknown171",
	172: "Unknown172",
	191: "FirmwareInfo",
	203: "Unknown203",
	212: "Unknown212",
	213: "Unknown213",
	214: "Unknown214",
	219: "BLEScannerStatus",
	229: "UserModeChange",
	230: "ControlIQStatus",
	256: "Unknown256",
	279: "BasalRateDelivered",
	280: "BolusDeliveryStatus",
	307: "HardwareVersions",
	313: "PumpControlState",
	369: "CGMAlertActivated",
	370: "CGMAlertRaised",
	371: "CGMAlertAcknowledged",
	372: "Unknown372",
	394: "CGMSessionSignature",
	399: "CGMReading",
	404: "Unknown404",
	405: "Unknown405",
	406: "Unknown406",
	434: "AlarmAnnunciation",
	447: "CGMSessionEnded",
	460: "Unknown460",
	461: "Unknown461",
	477: "Unknown477",
	480: "Unknown480",
	486: "Unknown486",
}

// EventName returns the human-readable name for an event code, falling back
// to "EventCode<N>" for anything not in EventCodeNames.
func EventName(code int) string {
	if name, ok := EventCodeNames[code]; ok {
		return name
	}
	return fmt.Sprintf("EventCode%d", code)
}

// Load reads and parses a pump-logs-raw.json file.
func Load(path string) (*Logs, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", path, err)
	}
	defer f.Close()

	body, err := io.ReadAll(f)
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}

	logs, err := Parse(body)
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	return logs, nil
}

// Parse parses pump-logs-raw.json content already in memory, e.g. an API
// response that wasn't written to disk.
func Parse(data []byte) (*Logs, error) {
	var logs Logs
	if err := json.Unmarshal(data, &logs); err != nil {
		return nil, err
	}
	return &logs, nil
}

// EventCount is one row of a code -> count summary.
type EventCount struct {
	Code  int
	Name  string
	Count int
}

// EventCounts returns a count of events per eventCode, most frequent first.
func (l *Logs) EventCounts() []EventCount {
	counts := make(map[int]int)
	for _, e := range l.Events {
		counts[e.EventCode]++
	}

	result := make([]EventCount, 0, len(counts))
	for code, n := range counts {
		result = append(result, EventCount{Code: code, Name: EventName(code), Count: n})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Count > result[j].Count })
	return result
}

// FilterByCode returns only events matching the given eventCode.
func (l *Logs) FilterByCode(code int) []Event {
	var out []Event
	for _, e := range l.Events {
		if e.EventCode == code {
			out = append(out, e)
		}
	}
	return out
}

// SortedByTime returns a copy of events sorted by pumpDateTime ascending.
// pumpDateTime strings are ISO-8601-like and sort correctly lexically.
func (l *Logs) SortedByTime() []Event {
	out := make([]Event, len(l.Events))
	copy(out, l.Events)
	sort.Slice(out, func(i, j int) bool { return out[i].PumpDateTime < out[j].PumpDateTime })
	return out
}

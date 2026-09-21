package tandem

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// reportsBase is a var (not const) so tests can point it at a local server.
var reportsBase = "https://source.eu.tandemdiabetes.com/api/reports/bff"

// eventIDs mirrors the eventIds query param observed in the captured
// Daily Timeline export request.
var eventIDs = []string{
	"229", "5", "28", "4", "26", "99", "279", "3", "16", "59", "21", "55", "20",
	"280", "64", "65", "66", "61", "33", "371", "171", "369", "460", "172", "370",
	"461", "372", "480", "399", "256", "213", "406", "477", "394", "212", "404",
	"214", "405", "486", "447", "313", "60", "14", "6", "90", "230", "140", "12",
	"11", "53", "13", "63", "203", "307", "191",
}

func authedGet(client *http.Client, rawURL, accessToken string) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("Accept", "application/json")
	return client.Do(req)
}

// FetchPumperReportMeta calls the reports/bff/pumper/{pumperId} endpoint,
// which the browser calls before requesting pump-logs, and returns the raw
// parsed JSON so callers can locate the report ID to use.
func FetchPumperReportMeta(client *http.Client, accessToken, pumperID string) (map[string]interface{}, error) {
	reqURL := fmt.Sprintf("%s/pumper/%s", reportsBase, pumperID)
	resp, err := authedGet(client, reqURL, accessToken)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("pumper report meta failed: %d %s", resp.StatusCode, string(body))
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, fmt.Errorf("parsing pumper report meta: %w", err)
	}
	return raw, nil
}

// DateChunks splits [start, end) into consecutive windows of at most
// chunkDays each, covering the full range with no gaps or overlaps.
func DateChunks(start, end time.Time, chunkDays int) []struct{ Start, End time.Time } {
	var chunks []struct{ Start, End time.Time }
	for chunkStart := start; chunkStart.Before(end); chunkStart = chunkStart.AddDate(0, 0, chunkDays) {
		chunkEnd := chunkStart.AddDate(0, 0, chunkDays)
		if chunkEnd.After(end) {
			chunkEnd = end
		}
		chunks = append(chunks, struct{ Start, End time.Time }{chunkStart, chunkEnd})
	}
	return chunks
}

// MergePumpLogs combines multiple pump-logs JSON payloads (each shaped like
// FetchPumpLogsRaw's response) into one, concatenating their "events" and
// "clockChanges" arrays in the given order.
func MergePumpLogs(chunks [][]byte) ([]byte, error) {
	var allEvents []json.RawMessage
	var allClockChanges []json.RawMessage

	for i, raw := range chunks {
		var parsed struct {
			Events       []json.RawMessage `json:"events"`
			ClockChanges []json.RawMessage `json:"clockChanges"`
		}
		if err := json.Unmarshal(raw, &parsed); err != nil {
			return nil, fmt.Errorf("parsing chunk %d: %w", i, err)
		}
		allEvents = append(allEvents, parsed.Events...)
		allClockChanges = append(allClockChanges, parsed.ClockChanges...)
	}

	merged := struct {
		Events       []json.RawMessage `json:"events"`
		ClockChanges []json.RawMessage `json:"clockChanges"`
	}{Events: allEvents, ClockChanges: allClockChanges}

	out, err := json.Marshal(merged)
	if err != nil {
		return nil, fmt.Errorf("marshaling merged pump logs: %w", err)
	}
	return out, nil
}

// ChunkFileName returns the cache path for a chunk covering [start, end),
// named <start>_<end>.json.
func ChunkFileName(dir string, start, end time.Time) string {
	const layout = "2006-01-02"
	name := fmt.Sprintf("%s_%s.json", start.Format(layout), end.Format(layout))
	return filepath.Join(dir, name)
}

// FetchWithChunkCache fetches [start, end) in chunkDays windows, caching each
// chunk's raw response at chunkDir/<start>_<end>.json. A chunk whose file
// already exists is read from disk instead of re-fetched, so re-running
// after a partial failure only fetches the missing dates. All chunks are
// then joined into a single merged payload via MergePumpLogs. progress, if
// non-nil, is called before each chunk with its index (1-based), the total
// chunk count, and whether it was served from cache.
func FetchWithChunkCache(auth *AuthResult, reportID string, start, end time.Time, chunkDays int, chunkDir string, progress func(i, total int, chunkStart, chunkEnd time.Time, cached bool)) ([]byte, error) {
	if err := os.MkdirAll(chunkDir, 0700); err != nil {
		return nil, fmt.Errorf("creating chunk directory %s: %w", chunkDir, err)
	}

	chunks := DateChunks(start, end, chunkDays)

	chunkPayloads := make([][]byte, 0, len(chunks))
	for i, c := range chunks {
		path := ChunkFileName(chunkDir, c.Start, c.End)

		if cached, err := os.ReadFile(path); err == nil {
			if progress != nil {
				progress(i+1, len(chunks), c.Start, c.End, true)
			}
			chunkPayloads = append(chunkPayloads, cached)
			continue
		} else if !os.IsNotExist(err) {
			return nil, fmt.Errorf("reading cached chunk %s: %w", path, err)
		}

		if progress != nil {
			progress(i+1, len(chunks), c.Start, c.End, false)
		}
		raw, err := FetchPumpLogsRaw(auth.Client, auth.AccessToken, reportID, auth.PumperID, c.Start, c.End)
		if err != nil {
			return nil, fmt.Errorf("fetching chunk %s to %s (retry by re-running; completed chunks are cached in %s): %w",
				c.Start, c.End, chunkDir, err)
		}
		if err := os.WriteFile(path, raw, 0600); err != nil {
			return nil, fmt.Errorf("caching chunk to %s: %w", path, err)
		}
		chunkPayloads = append(chunkPayloads, raw)
	}

	merged, err := MergePumpLogs(chunkPayloads)
	if err != nil {
		return nil, fmt.Errorf("joining cached chunks: %w", err)
	}
	return merged, nil
}

// FetchChunksFiltered fetches [start, end) in chunkDays windows using
// eventIDsCSV (see FetchPumpLogsRawFiltered), caching each chunk's raw
// response at chunkDir/<start>_<end>.json exactly like FetchWithChunkCache.
// A chunk whose file already exists is read from disk instead of
// re-fetched, so re-running after a partial failure only fetches the
// missing dates.
//
// Unlike FetchWithChunkCache, chunks are not merged into one payload in
// memory — instead handle is called with each chunk's raw bytes as soon as
// it's available (from cache or freshly fetched), in chronological order,
// so a caller can parse and upsert one chunk at a time without holding a
// multi-year history in memory. progress, if non-nil, is called before each
// chunk with its index (1-based), the total chunk count, and whether it was
// served from cache.
func FetchChunksFiltered(auth *AuthResult, reportID string, start, end time.Time, chunkDays int, chunkDir, eventIDsCSV string, progress func(i, total int, chunkStart, chunkEnd time.Time, cached bool), handle func(chunkStart, chunkEnd time.Time, raw []byte) error) error {
	if err := os.MkdirAll(chunkDir, 0700); err != nil {
		return fmt.Errorf("creating chunk directory %s: %w", chunkDir, err)
	}

	chunks := DateChunks(start, end, chunkDays)

	for i, c := range chunks {
		path := ChunkFileName(chunkDir, c.Start, c.End)

		if cached, err := os.ReadFile(path); err == nil {
			if progress != nil {
				progress(i+1, len(chunks), c.Start, c.End, true)
			}
			if err := handle(c.Start, c.End, cached); err != nil {
				return fmt.Errorf("handling cached chunk %s to %s: %w", c.Start, c.End, err)
			}
			continue
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("reading cached chunk %s: %w", path, err)
		}

		if progress != nil {
			progress(i+1, len(chunks), c.Start, c.End, false)
		}
		raw, err := FetchPumpLogsRawFiltered(auth.Client, auth.AccessToken, reportID, auth.PumperID, c.Start, c.End, eventIDsCSV)
		if err != nil {
			return fmt.Errorf("fetching chunk %s to %s (retry by re-running; completed chunks are cached in %s): %w",
				c.Start, c.End, chunkDir, err)
		}
		if err := os.WriteFile(path, raw, 0600); err != nil {
			return fmt.Errorf("caching chunk to %s: %w", path, err)
		}
		if err := handle(c.Start, c.End, raw); err != nil {
			return fmt.Errorf("handling chunk %s to %s: %w", c.Start, c.End, err)
		}
	}

	return nil
}

// FetchPumpLogsRaw calls reports/bff/pump-logs/{reportId} for the given
// pumper and date range, requesting the full eventIDs list, and returns the
// raw JSON bytes unparsed (the payload can be very large).
func FetchPumpLogsRaw(client *http.Client, accessToken, reportID, pumperID string, start, end time.Time) ([]byte, error) {
	return FetchPumpLogsRawFiltered(client, accessToken, reportID, pumperID, start, end, strings.Join(eventIDs, ","))
}

// FetchPumpLogsRawFiltered is FetchPumpLogsRaw with an explicit, caller-chosen
// eventIds query value instead of the full eventIDs list, so a caller that
// only needs a subset of event types (e.g. insulin-only) can ask for a
// smaller, cheaper response.
func FetchPumpLogsRawFiltered(client *http.Client, accessToken, reportID, pumperID string, start, end time.Time, eventIDsCSV string) ([]byte, error) {
	params := url.Values{}
	params.Set("pumperId", pumperID)
	params.Set("startDate", start.UTC().Format("2006-01-02T15:04:05Z"))
	params.Set("endDate", end.UTC().Format("2006-01-02T15:04:05Z"))
	params.Set("eventIds", eventIDsCSV)

	reqURL := fmt.Sprintf("%s/pump-logs/%s?%s", reportsBase, reportID, params.Encode())
	resp, err := authedGet(client, reqURL, accessToken)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("pump-logs request failed: %d %s", resp.StatusCode, string(body))
	}
	return body, nil
}

// PumperAssignmentID extracts pumps[0].assignmentId from a pumper report meta
// map, which is the ID the pump-logs endpoint expects as {reportId} — the
// meta payload has no top-level "reportId" field.
func PumperAssignmentID(meta map[string]interface{}) (string, error) {
	firstPump, err := firstPumpMap(meta)
	if err != nil {
		return "", err
	}
	assignmentID, ok := firstPump["assignmentId"].(string)
	if !ok || assignmentID == "" {
		return "", fmt.Errorf("could not find pumps[0].assignmentId")
	}
	return assignmentID, nil
}

// PumperAvailableDataStart extracts pumps[0].availableDataRange.start from a
// pumper report meta map: the earliest timestamp the account has pump-log
// data for, useful for a full-history backfill.
func PumperAvailableDataStart(meta map[string]interface{}) (time.Time, error) {
	firstPump, err := firstPumpMap(meta)
	if err != nil {
		return time.Time{}, err
	}
	dataRange, ok := firstPump["availableDataRange"].(map[string]interface{})
	if !ok {
		return time.Time{}, fmt.Errorf("could not find pumps[0].availableDataRange")
	}
	startStr, ok := dataRange["start"].(string)
	if !ok || startStr == "" {
		return time.Time{}, fmt.Errorf("could not find pumps[0].availableDataRange.start")
	}
	start, err := time.Parse("2006-01-02T15:04:05", startStr)
	if err != nil {
		return time.Time{}, fmt.Errorf("parsing availableDataRange.start %q: %w", startStr, err)
	}
	return start, nil
}

func firstPumpMap(meta map[string]interface{}) (map[string]interface{}, error) {
	pumps, ok := meta["pumps"].([]interface{})
	if !ok || len(pumps) == 0 {
		return nil, fmt.Errorf(`no "pumps" array found in pumper report meta`)
	}
	firstPump, ok := pumps[0].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("unexpected shape for pumps[0]")
	}
	return firstPump, nil
}

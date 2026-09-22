package bridge

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

// TestGetDexServer verifies the US/OUS region switch driven by BRIDGE_SERVER.
func TestGetDexServer(t *testing.T) {
	cases := []struct {
		region string
		want   string
	}{
		{"US", "share2.dexcom.com"},
		{"OUS", "shareous1.dexcom.com"},
		{"", "shareous1.dexcom.com"},
	}
	for _, c := range cases {
		t.Setenv("BRIDGE_SERVER", c.region)
		if got := GetDexServer(); got != c.want {
			t.Errorf("GetDexServer() with BRIDGE_SERVER=%q = %q, want %q", c.region, got, c.want)
		}
	}
}

// TestGetAccountId_Success exercises the auth POST against a stub Dexcom
// server and confirms the quoted account id is unwrapped by CleanString.
func TestGetAccountId_Success(t *testing.T) {
	t.Setenv("BRIDGE_PASS", "secret")
	t.Setenv("APPLICATION_ID", "app-1")
	t.Setenv("BRIDGE_USER", "user@example.com")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `"account-123"`)
	}))
	defer server.Close()

	got, err := GetAccountId(server.URL)
	if err != nil {
		t.Fatalf("GetAccountId failed: %v", err)
	}
	if got != "account-123" {
		t.Errorf("GetAccountId() = %q, want %q", got, "account-123")
	}
}

// TestGetAccountId_HTTPError confirms a non-200 response is surfaced as an
// error rather than the raw (error page) body.
func TestGetAccountId_HTTPError(t *testing.T) {
	t.Setenv("BRIDGE_PASS", "secret")
	t.Setenv("APPLICATION_ID", "app-1")
	t.Setenv("BRIDGE_USER", "user@example.com")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, "invalid credentials")
	}))
	defer server.Close()

	if _, err := GetAccountId(server.URL); err == nil {
		t.Error("expected error for non-200 response, got nil")
	}
}

// TestGetSessionId_Success chains the account-id and login calls together,
// since GetSessionId internally calls GetAccountId against auth_url before
// POSTing to login_url.
func TestGetSessionId_Success(t *testing.T) {
	t.Setenv("BRIDGE_PASS", "secret")
	t.Setenv("APPLICATION_ID", "app-1")
	t.Setenv("BRIDGE_USER", "user@example.com")

	authServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `"account-123"`)
	}))
	defer authServer.Close()

	loginServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `"session-abc"`)
	}))
	defer loginServer.Close()

	got, err := GetSessionId(loginServer.URL, authServer.URL)
	if err != nil {
		t.Fatalf("GetSessionId failed: %v", err)
	}
	if got != "session-abc" {
		t.Errorf("GetSessionId() = %q, want %q", got, "session-abc")
	}
}

// TestGetSessionId_AuthFailurePropagates confirms that a failure fetching
// the account id short-circuits before any login call is attempted. Uses a
// 4xx status so the client's built-in 5xx retry policy doesn't slow the test
// down with real backoff waits.
func TestGetSessionId_AuthFailurePropagates(t *testing.T) {
	t.Setenv("BRIDGE_PASS", "secret")
	t.Setenv("APPLICATION_ID", "app-1")
	t.Setenv("BRIDGE_USER", "user@example.com")

	authServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer authServer.Close()

	if _, err := GetSessionId("http://unused.invalid", authServer.URL); err == nil {
		t.Error("expected error when auth call fails, got nil")
	}
}

// TestIsSessionIdValid confirms the boolean result tracks the status code
// only (200 -> valid, anything else -> invalid). Uses a 4xx status rather
// than 5xx/a dial failure so the client's built-in retry policy doesn't
// slow the test down with real backoff waits.
func TestIsSessionIdValid(t *testing.T) {
	okServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer okServer.Close()

	badServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer badServer.Close()

	if !IsSessionIdValid("session-abc", okServer.URL) {
		t.Error("expected valid session for 200 response")
	}
	if IsSessionIdValid("session-abc", badServer.URL) {
		t.Error("expected invalid session for 403 response")
	}
}

// TestGetLatestBG_Success verifies the Dexcom -> Nightscoutdb transform:
// WT millisecond timestamps become Ns_time/Ns_datetime/Systime, and the
// trend string is mapped to its numeric direction code.
func TestGetLatestBG_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `[{"WT":"Date(1700000000000)","ST":"Date(1700000000000)","DT":"Date(1700000000000)","Value":123,"Trend":"Flat"}]`)
	}))
	defer server.Close()

	entries, err := GetLatestBG(server.URL, "session-abc")
	if err != nil {
		t.Fatalf("GetLatestBG failed: %v", err)
	}
	if len(entries) != 1 {
		t.Fatalf("expected 1 entry, got %d", len(entries))
	}

	e := entries[0]
	if e.Sgv != 123 {
		t.Errorf("Sgv = %d, want 123", e.Sgv)
	}
	if e.Ns_time != 1700000000000 {
		t.Errorf("Ns_time = %d, want 1700000000000", e.Ns_time)
	}
	wantTime := time.UnixMilli(1700000000000)
	if !e.Ns_datetime.Equal(wantTime) {
		t.Errorf("Ns_datetime = %v, want %v", e.Ns_datetime, wantTime)
	}
	if !e.Systime.Equal(wantTime) {
		t.Errorf("Systime = %v, want %v", e.Systime, wantTime)
	}
	if e.Trend != 4 { // "Flat" -> 4, see common.TrendToDirection
		t.Errorf("Trend = %d, want 4", e.Trend)
	}
	if e.Utcoffset != 0 {
		t.Errorf("Utcoffset = %d, want 0", e.Utcoffset)
	}
}

// TestGetLatestBG_EmptyResponse confirms a zero-reading response yields a
// non-nil, empty slice (not an error), matching make([]model.Nightscoutdb, 0, ...).
func TestGetLatestBG_EmptyResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `[]`)
	}))
	defer server.Close()

	entries, err := GetLatestBG(server.URL, "session-abc")
	if err != nil {
		t.Fatalf("GetLatestBG failed: %v", err)
	}
	if entries == nil {
		t.Error("expected non-nil empty slice, got nil")
	}
	if len(entries) != 0 {
		t.Errorf("expected 0 entries, got %d", len(entries))
	}
}

// TestGetLatestBG_InvalidJSON confirms malformed response bodies are
// reported as an error rather than silently producing zero entries.
func TestGetLatestBG_InvalidJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `not json`)
	}))
	defer server.Close()

	if _, err := GetLatestBG(server.URL, "session-abc"); err == nil {
		t.Error("expected error for invalid JSON body, got nil")
	}
}

// TestGetLatestBG_HTTPError confirms non-200 responses are surfaced as an
// error rather than attempting to parse the body. Uses a 4xx status so the
// client's built-in 5xx retry policy doesn't slow the test down with real
// backoff waits.
func TestGetLatestBG_HTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	if _, err := GetLatestBG(server.URL, "session-abc"); err == nil {
		t.Error("expected error for non-200 response, got nil")
	}
}

// TestGetLatestBG_BGQueryMinutesOverride confirms the BG_QUERY_MINUTES env
// var, when a valid positive integer, overrides the 1440-minute default in
// the outgoing query string.
func TestGetLatestBG_BGQueryMinutesOverride(t *testing.T) {
	t.Setenv("BG_QUERY_MINUTES", "30")
	t.Setenv("RECORD_COUNT", "5")

	var gotQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		fmt.Fprint(w, `[]`)
	}))
	defer server.Close()

	if _, err := GetLatestBG(server.URL, "session-abc"); err != nil {
		t.Fatalf("GetLatestBG failed: %v", err)
	}
	assertQueryParams(t, gotQuery, map[string]string{
		"sessionId": "session-abc",
		"minutes":   "30",
		"maxCount":  "5",
	})
}

// TestGetLatestBG_BGQueryMinutesInvalid_FallsBackToDefault confirms a
// non-numeric BG_QUERY_MINUTES is ignored in favor of the 1440 default,
// rather than producing a malformed query string or crashing.
func TestGetLatestBG_BGQueryMinutesInvalid_FallsBackToDefault(t *testing.T) {
	t.Setenv("BG_QUERY_MINUTES", "not-a-number")
	t.Setenv("RECORD_COUNT", "5")

	var gotQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		fmt.Fprint(w, `[]`)
	}))
	defer server.Close()

	if _, err := GetLatestBG(server.URL, "session-abc"); err != nil {
		t.Fatalf("GetLatestBG failed: %v", err)
	}
	assertQueryParams(t, gotQuery, map[string]string{
		"sessionId": "session-abc",
		"minutes":   "1440",
		"maxCount":  "5",
	})
}

// assertQueryParams parses a raw query string and checks it holds exactly
// the given key/value pairs, independent of parameter order (resty/url.Values
// serializes query params alphabetically, not in call order).
func assertQueryParams(t *testing.T, rawQuery string, want map[string]string) {
	t.Helper()
	got, err := url.ParseQuery(rawQuery)
	if err != nil {
		t.Fatalf("failed to parse query string %q: %v", rawQuery, err)
	}
	if len(got) != len(want) {
		t.Errorf("query string %q has %d params, want %d", rawQuery, len(got), len(want))
	}
	for k, v := range want {
		if got.Get(k) != v {
			t.Errorf("query param %q = %q, want %q (full query: %q)", k, got.Get(k), v, rawQuery)
		}
	}
}

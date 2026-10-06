package handlers

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"

	"health-api/config"
)

func handlerWithHistory(days int) *Handler {
	return &Handler{Cfg: &config.Config{MaxHistoryDays: days}}
}

func testContext() (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	return c, w
}

func TestWithinHours_DefaultIs90Days(t *testing.T) {
	h := handlerWithHistory(0) // unset: falls back to the default
	for _, tc := range []struct {
		hours int
		ok    bool
	}{{24, true}, {2160, true}, {2161, false}, {876000, false}} {
		c, w := testContext()
		if got := h.withinHours(c, "hours", tc.hours); got != tc.ok {
			t.Errorf("hours=%d: got %v, want %v", tc.hours, got, tc.ok)
		}
		if !tc.ok && w.Code != http.StatusBadRequest {
			t.Errorf("hours=%d: status %d, want 400", tc.hours, w.Code)
		}
	}
}

func TestWithinDays_ExtendedByAdmin(t *testing.T) {
	h := handlerWithHistory(365)
	for _, tc := range []struct {
		days int
		ok   bool
	}{{90, true}, {365, true}, {366, false}} {
		c, w := testContext()
		if got := h.withinDays(c, "days", tc.days); got != tc.ok {
			t.Errorf("days=%d: got %v, want %v", tc.days, got, tc.ok)
		}
		if !tc.ok && w.Code != http.StatusBadRequest {
			t.Errorf("days=%d: status %d, want 400", tc.days, w.Code)
		}
	}
}

func TestHistoryStart(t *testing.T) {
	now := time.Date(2026, 10, 6, 12, 0, 0, 0, time.UTC)
	if got, want := handlerWithHistory(90).historyStart(now), time.Date(2026, 7, 8, 12, 0, 0, 0, time.UTC); !got.Equal(want) {
		t.Errorf("90 days: got %v, want %v", got, want)
	}
	if got, want := handlerWithHistory(365).historyStart(now), time.Date(2025, 10, 6, 12, 0, 0, 0, time.UTC); !got.Equal(want) {
		t.Errorf("365 days: got %v, want %v", got, want)
	}
}

func TestClipToHistory(t *testing.T) {
	start := time.Date(2026, 7, 8, 0, 0, 0, 0, time.UTC)
	day := 24 * time.Hour
	for _, tc := range []struct {
		name               string
		from, to           time.Time
		wantFrom, wantTo   time.Time
		wantEmptyWindowSet bool
	}{
		{"inside: unchanged", start.Add(day), start.Add(2 * day), start.Add(day), start.Add(2 * day), false},
		{"straddling: start moved up", start.Add(-day), start.Add(day), start, start.Add(day), false},
		{"entirely before: empty", start.Add(-3 * day), start.Add(-2 * day), start.Add(-2 * day), start.Add(-2 * day), true},
		{"ending exactly at start: empty", start.Add(-day), start, start, start, true},
		{"whole history: clipped", time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC), start.Add(90 * day), start, start.Add(90 * day), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			from, to := clipToHistory(tc.from, tc.to, start)
			if !from.Equal(tc.wantFrom) || !to.Equal(tc.wantTo) {
				t.Errorf("got [%v, %v), want [%v, %v)", from, to, tc.wantFrom, tc.wantTo)
			}
			if empty := !to.After(from); empty != tc.wantEmptyWindowSet {
				t.Errorf("empty=%v, want %v", empty, tc.wantEmptyWindowSet)
			}
		})
	}
}

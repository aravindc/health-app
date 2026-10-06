package handlers

import (
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"health-api/config"
)

// The API only serves the last MaxHistoryDays (default 90, see config),
// so no single request can return or process the whole history. Hour and
// day parameters beyond that are rejected with 400; range windows
// (/chart, /bolus, /basal) are clipped to it, so a chart paged back past
// it shows nothing.

func (h *Handler) maxHistoryDays() int {
	if h.Cfg != nil && h.Cfg.MaxHistoryDays > 0 {
		return h.Cfg.MaxHistoryDays
	}
	return config.DefaultMaxHistoryDays
}

// historyStart is the earliest instant the API serves data from.
func (h *Handler) historyStart(now time.Time) time.Time {
	return now.AddDate(0, 0, -h.maxHistoryDays())
}

// withinHours reports whether `hours` (the request parameter `name`) fits
// the history window, answering 400 if it doesn't.
func (h *Handler) withinHours(c *gin.Context, name string, hours int) bool {
	max := h.maxHistoryDays() * 24
	if hours <= max {
		return true
	}
	c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf(
		"%s must be at most %d: the API serves the last %d days", name, max, h.maxHistoryDays())})
	return false
}

// withinDays is withinHours for day parameters.
func (h *Handler) withinDays(c *gin.Context, name string, days int) bool {
	max := h.maxHistoryDays()
	if days <= max {
		return true
	}
	c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf(
		"%s must be at most %d: the API serves the last %d days", name, max, max)})
	return false
}

// clipToHistory moves a window's start up to `start`. A window that ends
// at or before `start` comes back empty (from == to).
func clipToHistory(from, to, start time.Time) (time.Time, time.Time) {
	if from.Before(start) {
		from = start
	}
	if !to.After(from) {
		return to, to
	}
	return from, to
}

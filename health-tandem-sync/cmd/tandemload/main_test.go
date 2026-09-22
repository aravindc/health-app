package main

import (
	"testing"
	"time"
)

func TestParseDateRange_ExplicitStartAndEnd(t *testing.T) {
	now := time.Date(2025, 6, 1, 0, 0, 0, 0, time.UTC)
	start, end, err := parseDateRange("2024-01-01", "2024-06-01", now)
	if err != nil {
		t.Fatalf("parseDateRange failed: %v", err)
	}
	wantStart := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	wantEnd := time.Date(2024, 6, 1, 0, 0, 0, 0, time.UTC)
	if !start.Equal(wantStart) {
		t.Errorf("start = %v, want %v", start, wantStart)
	}
	if !end.Equal(wantEnd) {
		t.Errorf("end = %v, want %v", end, wantEnd)
	}
}

func TestParseDateRange_EndDefaultsToNow(t *testing.T) {
	now := time.Date(2025, 6, 1, 12, 0, 0, 0, time.UTC)
	_, end, err := parseDateRange("2024-01-01", "", now)
	if err != nil {
		t.Fatalf("parseDateRange failed: %v", err)
	}
	if !end.Equal(now) {
		t.Errorf("end = %v, want %v (now)", end, now)
	}
}

func TestParseDateRange_InvalidStart(t *testing.T) {
	now := time.Now()
	if _, _, err := parseDateRange("not-a-date", "", now); err == nil {
		t.Error("expected an error for an invalid -start date")
	}
}

func TestParseDateRange_InvalidEnd(t *testing.T) {
	now := time.Now()
	if _, _, err := parseDateRange("2024-01-01", "not-a-date", now); err == nil {
		t.Error("expected an error for an invalid -end date")
	}
}

func TestParseDateRange_EndNotAfterStart(t *testing.T) {
	now := time.Now()
	if _, _, err := parseDateRange("2024-06-01", "2024-01-01", now); err == nil {
		t.Error("expected an error when -end is before -start")
	}
	if _, _, err := parseDateRange("2024-06-01", "2024-06-01", now); err == nil {
		t.Error("expected an error when -end equals -start")
	}
}

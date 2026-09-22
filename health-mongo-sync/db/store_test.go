package db

import (
	"health-mongo-sync/model"
	"testing"
	"time"
)

// These tests cover only the pure, no-I/O paths of BulkInsert (empty input
// and "everything already exists") — both return before any Postgres call
// is made. The actual insert path requires a live database connection and
// is not covered here.

func TestBulkInsert_EmptyEntries_NoOp(t *testing.T) {
	n, err := BulkInsert(nil, nil, nil)
	if err != nil {
		t.Fatalf("BulkInsert failed: %v", err)
	}
	if n != 0 {
		t.Errorf("expected 0 inserted for empty entries, got %d", n)
	}
}

func TestBulkInsert_AllEntriesSkipped_NoOp(t *testing.T) {
	// Every entry's NsTime is present in the skip set, so BulkInsert should
	// short-circuit before ever touching the DB handle (nil sqlDB would
	// panic if it tried).
	entries := []model.NsEntry{
		{Sgv: 120, NsTime: 1000, NsDatetime: time.Now(), Trend: 4, Utcoffset: 0, Systime: time.Now()},
		{Sgv: 130, NsTime: 2000, NsDatetime: time.Now(), Trend: 4, Utcoffset: 0, Systime: time.Now()},
	}
	skip := map[int64]struct{}{
		1000: {},
		2000: {},
	}

	n, err := BulkInsert(nil, entries, skip)
	if err != nil {
		t.Fatalf("BulkInsert failed: %v", err)
	}
	if n != 0 {
		t.Errorf("expected 0 inserted when all entries are skipped, got %d", n)
	}
}

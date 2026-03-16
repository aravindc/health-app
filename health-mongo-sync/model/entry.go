package model

import "time"

// NsEntry is the normalised blood glucose record used across both
// the MongoDB fetch and the Postgres insert.
type NsEntry struct {
	Sgv         int
	NsTime      int64     // Unix milliseconds — primary dedup key
	NsDatetime  time.Time
	Trend       int
	Utcoffset   int
	Systime     time.Time
}

// Gap is a time window [From, To) that has no data in Postgres.
type Gap struct {
	From time.Time
	To   time.Time
}

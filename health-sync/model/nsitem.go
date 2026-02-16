package model

import (
	"time"

	"github.com/uptrace/bun"
)

type DexBgReading struct {
	WT    string `json:"WT"`
	ST    string `json:"ST"`
	DT    string `json:"DT"`
	Value int    `json:"Value"`
	Trend string `json:"Trend"`
}

type Nightscoutdb struct {
	bun.BaseModel `bun:"table:nightscoutdb,alias:ns"`

	Id          int64     `bun:"id,pk,autoincrement"`
	Sgv         int       `bun:"sgv"`
	Ns_time     int64     `bun:"ns_time,type:bigint"`
	Ns_datetime time.Time `bun:"ns_datetime,type:timestampz"`
	Trend       int       `bun:"trend"`
	Utcoffset   int       `bun:"utcoffset"`
	Systime     time.Time `bun:"systime,type:timestampz"`
}

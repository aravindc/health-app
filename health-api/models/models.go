package models

import (
	"time"
)

// Python: class Latest(SQLModel, table=True):
type Latest struct {
	BgTime    time.Time `gorm:"primaryKey" json:"bg_time"`
	BgMmol    float64   `json:"bg_mmol"`
	BgTrend   int       `json:"bg_trend"`
	CreatedAt time.Time `json:"created_at"`
}

func (Latest) TableName() string {
	return "latest"
}

// Python: class Daily_Avg(SQLModel, table=True):
type DailyAvg struct {
	BgDate time.Time `gorm:"primaryKey" json:"bg_date"`
	BgMmol float64   `json:"bg_mmol"`
}

func (DailyAvg) TableName() string {
	return "daily_avg"
}

// Python: class Daily_Tir(SQLModel, table=True):
type DailyTir struct {
	BgDate            time.Time `gorm:"primaryKey" json:"bg_date"`
	InRangeStrictVal  int       `json:"in_range_strict_val"`
	InRangeMedicalVal int       `json:"in_range_medical_val"`
	TotalRecs         int       `json:"total_recs"`
	PirStrict         float64   `json:"pir_strict"`
	PirMedical        float64   `json:"pir_medical"`
}

func (DailyTir) TableName() string {
	return "daily_tir"
}

// Python: class Avg_mmol(SQLModel, table=True):
type AvgMmol struct {
	TimePeriod string  `gorm:"primaryKey" json:"time_period"`
	BgMmol     float64 `json:"bg_mmol"`
}

func (AvgMmol) TableName() string {
	return "avg_mmol"
}

// Python: class Ns_part(SQLModel, table=True):
type NsPart struct {
	ID         int       `gorm:"primaryKey" json:"id"`
	Sgv        int       `json:"sgv"`
	NsTime     time.Time `json:"ns_time"` // Note: This was `datetime` in Python
	NsDatetime time.Time `json:"ns_datetime"`
	Trend      int       `json:"trend"`
	Utcoffset  int       `json:"utcoffset"`
	Systime    time.Time `json:"systime"`
}

func (NsPart) TableName() string {
	return "ns_part"
}

// Python: class Quart_mmol_stats(SQLModel, table=True):
type QuartMmolStats struct {
	TimePeriod string  `gorm:"primaryKey" json:"time_period"`
	MinMmol    float64 `json:"min_mmol"`
	Q1Mmol     float64 `json:"q1_mmol"`
	Q2Mmol     float64 `json:"q2_mmol"`
	Q3Mmol     float64 `json:"q3_mmol"`
	MaxMmol    float64 `json:"max_mmol"`
	NumRecs    int     `json:"num_recs"`
}

func (QuartMmolStats) TableName() string {
	return "quart_mmol_stats"
}

// Python: class Insulin(SQLModel, table=True):
type Insulin struct {
	ID            int       `gorm:"primaryKey" json:"id"`
	InsulinType   string    `json:"insulin_type"`
	InsulinQty    float64   `json:"insulin_qty"`
	DateUtcMillis int64     `json:"date_utc_millis"`
	DateUtc       time.Time `json:"date_utc"`
}

func (Insulin) TableName() string {
	return "insulin"
}

// Python: class Insulin_Activity(SQLModel, table=True):
type InsulinActivity struct {
	ID            int       `gorm:"primaryKey" json:"id"`
	InsulinID     int       `json:"insulin_id"` // Assuming this is a foreign key
	InsulinQty    float64   `json:"insulin_qty"`
	DateUtcMillis int64     `json:"date_utc_millis"`
	DateUtc       time.Time `json:"date_utc"`
}

func (InsulinActivity) TableName() string {
	return "insulin_activity"
}

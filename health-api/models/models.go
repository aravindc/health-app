package models

import (
	"time"
)

// Python: class Latest(SQLModel, table=True):
type Latest struct {
	BgTime    int64     `gorm:"primaryKey" json:"bg_time"`
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
	NsTime     int64     `json:"ns_time"`
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

// TandemBolus mirrors health-api/database/migrations/00003_add_tandem_insulin_tables.sql's
// tandem_bolus table: one row per pump bolus, merged from Tandem's
// BolusRequested*/BolusCompleted pump-log events by health-tandem-sync.
type TandemBolus struct {
	ID                  int64      `gorm:"primaryKey" json:"id"`
	DeviceAssignmentID  string     `json:"device_assignment_id"`
	BolusID             int64      `json:"bolus_id"`
	BolusType           *string    `json:"bolus_type"`
	RequestedAt         *time.Time `json:"requested_at"`
	CompletedAt         *time.Time `json:"completed_at"`
	InsulinRequested    *float64   `json:"insulin_requested"`
	InsulinDelivered    *float64   `json:"insulin_delivered"`
	FoodBolusSize       *float64   `json:"food_bolus_size"`
	CorrectionBolusSize *float64   `json:"correction_bolus_size"`
	CorrectionIncluded  *bool      `json:"correction_included"`
	CarbAmount          *float64   `json:"carb_amount"`
	CarbRatio           *float64   `json:"carb_ratio"`
	Bg                  *float64   `json:"bg"`
	CompletionStatus    *int16     `json:"completion_status"`
}

func (TandemBolus) TableName() string {
	return "tandem_bolus"
}

// TandemBasal mirrors the tandem_basal table: one row per basal rate
// change (eventCode 3 / BasalRateChange), rate already normalized to U/hr.
type TandemBasal struct {
	ID                 int64     `gorm:"primaryKey" json:"id"`
	DeviceAssignmentID string    `json:"device_assignment_id"`
	SequenceGroup      int       `json:"sequence_group"`
	SequenceNumber     int       `json:"sequence_number"`
	ChangedAt          time.Time `json:"changed_at"`
	CommandedRate      float64   `json:"commanded_rate"`
	BaseRate           *float64  `json:"base_rate"`
	MaxRate            *float64  `json:"max_rate"`
}

func (TandemBasal) TableName() string {
	return "tandem_basal"
}

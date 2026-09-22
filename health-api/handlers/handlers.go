package handlers

import (
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"health-api/config"
	"health-api/insulinactivity"
	"health-api/models"
)

// Handler holds the application state, like the DB connection and config
type Handler struct {
	DB  *gorm.DB
	Cfg *config.Config
}

// NewHandler creates a new handler with DB connection
func NewHandler(db *gorm.DB, cfg *config.Config) *Handler {
	return &Handler{DB: db, Cfg: cfg}
}

// --- Constants ---

const (
	// SgvToMmolFactor converts mg/dL to mmol/L
	SgvToMmolFactor = 18.0

	// Page sizes for paginated endpoints
	PageSizeLatest = 36  // ~3 hours at 5-min intervals
	PageSize12h    = 144 // 12 hours at 5-min intervals
	PageSize4h     = 48  // 4 hours at 5-min intervals
)

// --- Helper Structs for JSON Responses ---

// DataPoint is a custom struct for /lastxh response
type DataPoint struct {
	Epoch      int64     `json:"epoch"`
	Mmol       float64   `json:"mmol"`
	Datetime   time.Time `json:"datetime"`
	Date       string    `json:"date"`
	Time       string    `json:"time"`
	InRange    bool      `json:"in_range"`
	PointColor string    `json:"point_color"`
}

// XYTimeResponse is for /last12h and /last4h
type XYTimeResponse struct {
	X int64     `json:"x"`
	Y float64   `json:"y"`
	Z time.Time `json:"z"`
}

// DailyTirResponse is for /dailytir
type DailyTirResponse struct {
	BgDate     time.Time `json:"bg_date"`
	PirStrict  float64   `json:"pir_strict"`
	PirMedical float64   `json:"pir_medical"`
}

// QuartResponse is for /quart
type QuartResponse struct {
	Group string  `json:"group"`
	Mu    float64 `json:"mu"`
	Sd    float64 `json:"sd"`
	N     int     `json:"n"`
	Value float64 `json:"value"`
}

// SparklineResponse is for /7dsparkline and /last24hsparkline
type SparklineResponse struct {
	BgTime int64   `json:"bg_time"`
	BgMmol float64 `json:"bg_mmol"`
}

// BolusDose is one delivered bolus, split into its food/correction
// components, for the dose markers drawn on the CGM and activity panels.
type BolusDose struct {
	BolusID          int64     `json:"bolus_id"`
	DeliveredAt      time.Time `json:"delivered_at"`
	InsulinDelivered float64   `json:"insulin_delivered"`
	FoodUnits        float64   `json:"food_units"`
	CorrectionUnits  float64   `json:"correction_units"`
	// CarbAmount is the carbs (grams) entered for this dose, straight from
	// tandem_bolus — null when none was recorded (e.g. a correction-only
	// bolus with no carb entry), not the same as 0g.
	CarbAmount *float64 `json:"carb_amount"`
	// DominantCategory is "food" or "correction", whichever component is
	// larger for this dose — used to color the dose's dot/dashed line.
	DominantCategory string `json:"dominant_category"`
}

// ActivityPoint is one 5-minute sample of a summed insulin-activity curve.
type ActivityPoint struct {
	Time  time.Time `json:"time"`
	Units float64   `json:"units"`
}

// BolusChartResponse is for GET /bolus/:date.
type BolusChartResponse struct {
	Doses              []BolusDose     `json:"doses"`
	FoodActivity       []ActivityPoint `json:"food_activity"`
	CorrectionActivity []ActivityPoint `json:"correction_activity"`
}

// BasalPoint is one commanded basal-rate sample (a step point: the rate
// held from this timestamp until the next point, or until the window's
// end for the last point).
type BasalPoint struct {
	Time          time.Time `json:"time"`
	CommandedRate float64   `json:"commanded_rate"`
}

// --- Helper Functions (Methods) ---

// Round float64 to a given precision
func round(val float64, precision int) float64 {
	p := math.Pow10(precision)
	return math.Round(val*p) / p
}

// getDataInWindow fetches DataPoints in [from, to) time window
func (h *Handler) getDataInWindow(from, to time.Time) ([]DataPoint, error) {
	minMmol, err := strconv.ParseFloat(h.Cfg.MinMmol, 64)
	if err != nil {
		return nil, err
	}
	strictMaxMmol, err := strconv.ParseFloat(h.Cfg.StrictMaxMmol, 64)
	if err != nil {
		return nil, err
	}
	medicalMaxMmol, err := strconv.ParseFloat(h.Cfg.MedicalMaxMmol, 64)
	if err != nil {
		return nil, err
	}

	var results []models.NsPart
	latResponse := []DataPoint{}

	tx := h.DB.Select("ns_time", "sgv").
		Where("ns_datetime >= ? AND ns_datetime < ?", from, to).
		Order("ns_time desc").
		Find(&results)

	if tx.Error != nil {
		return nil, tx.Error
	}

	for _, result := range results {
		mmol := round(float64(result.Sgv)/SgvToMmolFactor, 1)
		inRange := mmol >= minMmol && mmol <= strictMaxMmol

		var pointColor string
		if inRange {
			pointColor = "hsl(124, 45%, 37%)"
		} else {
			if mmol < minMmol || mmol > medicalMaxMmol {
				pointColor = "hsl(360, 68%, 36%)"
			} else {
				pointColor = "hsl(29, 99%, 50%)"
			}
		}

		nsTime := time.UnixMilli(result.NsTime)
		latResponse = append(latResponse, DataPoint{
			Epoch:      result.NsTime,
			Mmol:       mmol,
			Datetime:   nsTime,
			Date:       nsTime.Format("2006-01-02"),
			Time:       nsTime.Format("15:04:05"),
			InRange:    inRange,
			PointColor: pointColor,
		})
	}

	return latResponse, nil
}

// getData is the Go equivalent of your Python `getData` function
func (h *Handler) getData(hours int) ([]DataPoint, error) {
	now := time.Now()
	return h.getDataInWindow(now.Add(-time.Duration(hours)*time.Hour), now.Add(time.Minute))
}

// --- Endpoint Handlers ---

// Root (GET /)
func (h *Handler) Root(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"message": "Welcome to Health App!"})
}

// HealthReady (GET /health)
func (h *Handler) HealthReady(c *gin.Context) {
	sqlDB, err := h.DB.DB()
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "error", "database": err.Error()})
		return
	}
	if err := sqlDB.Ping(); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "error", "database": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "OK", "database": "OK"})
}

// GetLatest (GET /latest/:page_num)
func (h *Handler) GetLatest(c *gin.Context) {
	pageNum, err := strconv.Atoi(c.Param("page_num"))
	if err != nil || pageNum < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong Page number"})
		return
	}

	offset := PageSizeLatest * (pageNum - 1)
	var latestResponse []models.Latest

	tx := h.DB.Order("bg_time desc").Limit(PageSizeLatest).Offset(offset).Find(&latestResponse)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	c.JSON(http.StatusOK, latestResponse)
}

// GetDailyAvg (GET /dailyavg/:days)
func (h *Handler) GetDailyAvg(c *gin.Context) {
	days, err := strconv.Atoi(c.Param("days"))
	if err != nil || days < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of days"})
		return
	}

	var dailyResponse []models.DailyAvg
	tx := h.DB.Order("bg_date desc").Limit(days).Offset(0).Find(&dailyResponse)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	c.JSON(http.StatusOK, dailyResponse)
}

// GetDailyTir (GET /dailytir/:days)
func (h *Handler) GetDailyTir(c *gin.Context) {
	days, err := strconv.Atoi(c.Param("days"))
	if err != nil || days < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of days"})
		return
	}

	var dailyResponse []DailyTirResponse
	tx := h.DB.Model(&models.DailyTir{}).
		Select("bg_date", "pir_strict", "pir_medical").
		Order("bg_date desc").
		Limit(days).
		Find(&dailyResponse)

	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	c.JSON(http.StatusOK, dailyResponse)
}

// GetAvgMmol (GET /avgmmol/:time_period)
func (h *Handler) GetAvgMmol(c *gin.Context) {
	timePeriod := c.Param("time_period")
	if timePeriod == "" {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong time period"})
		return
	}

	var avgMmol models.AvgMmol
	tx := h.DB.Where("time_period = ?", timePeriod).First(&avgMmol)
	if tx.Error != nil {
		if tx.Error == gorm.ErrRecordNotFound {
			c.JSON(http.StatusNotFound, gin.H{"detail": "Time period not found"})
		} else {
			c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		}
		return
	}

	c.JSON(http.StatusOK, avgMmol)
}

// GetLast12h (GET /last12h/:page_num)
func (h *Handler) GetLast12h(c *gin.Context) {
	pageNum, err := strconv.Atoi(c.Param("page_num"))
	if err != nil || pageNum < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong Page number"})
		return
	}

	offset := PageSize12h * (pageNum - 1)
	var results []models.Latest

	tx := h.DB.Order("bg_time desc").Limit(PageSize12h).Offset(offset).Find(&results)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	latResponse := make([]XYTimeResponse, 0, len(results))
	for _, r := range results {
		latResponse = append(latResponse, XYTimeResponse{
			X: r.BgTime,
			Y: round(r.BgMmol, 1),
			Z: r.CreatedAt,
		})
	}

	c.JSON(http.StatusOK, latResponse)
}

// GetQuart (GET /quart/:days)
func (h *Handler) GetQuart(c *gin.Context) {
	days, err := strconv.Atoi(c.Param("days"))
	if err != nil || days < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of days"})
		return
	}

	var sgvResults []int
	startTime := time.Now().AddDate(0, 0, -days) // -days

	// Pluck just the sgv column
	tx := h.DB.Model(&models.NsPart{}).
		Where("ns_datetime >= ?", startTime).
		Pluck("sgv", &sgvResults)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}
	if len(sgvResults) == 0 {
		c.JSON(http.StatusOK, []QuartResponse{}) // Return empty list
		return
	}

	// Calculate stats
	quartResponse := make([]float64, len(sgvResults))
	sum := 0.0
	for i, sgv := range sgvResults {
		val := round(float64(sgv)/SgvToMmolFactor, 2)
		quartResponse[i] = val
		sum += val
	}

	muVal := sum / float64(len(quartResponse))

	// Calculate Standard Deviation (Sample)
	sdVal := 0.0
	if len(quartResponse) > 1 {
		sdValSum := 0.0
		for _, val := range quartResponse {
			sdValSum += math.Pow(val-muVal, 2)
		}
		sdVal = math.Sqrt(sdValSum / float64(len(quartResponse)-1))
	}

	// Round final stats
	muVal = round(muVal, 2)
	sdVal = round(sdVal, 2)

	// Build the complex response
	quartOutput := make([]QuartResponse, len(quartResponse))
	for i, val := range quartResponse {
		quartOutput[i] = QuartResponse{
			Group: "mmol",
			Mu:    muVal,
			Sd:    sdVal,
			N:     len(quartResponse),
			Value: val,
		}
	}

	c.JSON(http.StatusOK, quartOutput)
}

// GetLast4h (GET /last4h/:page_num)
func (h *Handler) GetLast4h(c *gin.Context) {
	pageNum, err := strconv.Atoi(c.Param("page_num"))
	if err != nil || pageNum < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong Page number"})
		return
	}

	offset := PageSize4h * (pageNum - 1)
	var results []models.Latest

	tx := h.DB.Order("bg_time desc").Limit(PageSize4h).Offset(offset).Find(&results)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	latResponse := make([]XYTimeResponse, 0, len(results))
	for _, r := range results {
		latResponse = append(latResponse, XYTimeResponse{
			X: r.BgTime,
			Y: round(r.BgMmol, 1),
			Z: r.CreatedAt,
		})
	}

	c.JSON(http.StatusOK, latResponse)
}

// GetLastXh (GET /lastxh/:hours)
func (h *Handler) GetLastXh(c *gin.Context) {
	hours, err := strconv.Atoi(c.Param("hours"))
	if err != nil || hours < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of hours"})
		return
	}

	latResponse, err := h.getData(hours)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}
	c.JSON(http.StatusOK, latResponse)
}

// GetLastXhOffset (GET /lastxh/:hours/offset/:offset_hours)
// Returns data for a window of `hours` ending `offset_hours` ago.
func (h *Handler) GetLastXhOffset(c *gin.Context) {
	hours, err := strconv.Atoi(c.Param("hours"))
	if err != nil || hours < 1 {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "Wrong number of hours"})
		return
	}
	offsetHours, err := strconv.Atoi(c.Param("offset_hours"))
	if err != nil || offsetHours < 0 {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "Wrong offset_hours"})
		return
	}

	now := time.Now()
	to := now.Add(-time.Duration(offsetHours) * time.Hour)
	from := to.Add(-time.Duration(hours) * time.Hour)

	data, err := h.getDataInWindow(from, to)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}
	c.JSON(http.StatusOK, data)
}

// GetRangeChart (GET /chart?from=...&to=...)
// Returns glucose readings for an arbitrary [from, to) window (RFC3339
// timestamps), for the CGM panel's continuous, drag-to-pan viewport —
// unlike a calendar-day window, from/to need not be midnight-aligned
// (e.g. "yesterday 11pm to today 11pm" while panning).
func (h *Handler) GetRangeChart(c *gin.Context) {
	from, to, err := rangeWindow(c)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}

	data, err := h.getDataInWindow(from, to)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}
	c.JSON(http.StatusOK, data)
}

// rangeWindow parses the from/to query params (RFC3339 timestamps) shared
// by every range-paged endpoint (GetRangeChart, GetBolusRangeChart,
// GetBasalRangeChart) so they all page identically and stay in sync when
// charted side by side. to must be after from.
func rangeWindow(c *gin.Context) (from, to time.Time, err error) {
	fromStr := c.Query("from")
	toStr := c.Query("to")
	if fromStr == "" || toStr == "" {
		return time.Time{}, time.Time{}, fmt.Errorf("both from and to query params are required (RFC3339)")
	}
	from, err = time.Parse(time.RFC3339, fromStr)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid from (want RFC3339): %w", err)
	}
	to, err = time.Parse(time.RFC3339, toStr)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid to (want RFC3339): %w", err)
	}
	if !to.After(from) {
		return time.Time{}, time.Time{}, fmt.Errorf("to must be after from")
	}
	return from, to, nil
}

// GetFirstDate (GET /firstdate)
// Returns the earliest calendar date (YYYY-MM-DD) that has data in ns_part.
func (h *Handler) GetFirstDate(c *gin.Context) {
	var earliest time.Time
	row := h.DB.Raw("SELECT MIN(ns_datetime) FROM ns_part").Row()
	if err := row.Scan(&earliest); err != nil || earliest.IsZero() {
		c.JSON(http.StatusNotFound, gin.H{"detail": "No data found"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"date": earliest.Local().Format("2006-01-02")})
}

// splitDelivered divides a bolus's delivered units into food/correction
// components, proportionally to the requested split (food_bolus_size /
// correction_bolus_size, which sum to insulin_requested). It falls back to
// treating the full delivered amount as food when there's nothing to
// proportion from — either because no split was recorded at all, or
// because the recorded split is (0, 0) despite units having actually been
// delivered (a data quirk seen in practice: insulin_requested/delivered
// can be nonzero while food_bolus_size/correction_bolus_size are both 0).
// Either way, delivered insulin must never be silently dropped from the
// activity curve (should be rare in practice; see tandemdata/prompt.md).
func splitDelivered(delivered float64, insulinRequested, foodBolusSize, correctionBolusSize *float64) (food, correction float64) {
	requested := 0.0
	if insulinRequested != nil {
		requested = *insulinRequested
	}
	if requested > 0 && foodBolusSize != nil && correctionBolusSize != nil {
		food = delivered * (*foodBolusSize / requested)
		correction = delivered * (*correctionBolusSize / requested)
	}
	if food <= 0 && correction <= 0 {
		food = delivered
		correction = 0
	}
	return food, correction
}

// GetBolusRangeChart (GET /bolus?from=...&to=...)
// Returns the window's delivered boluses (split food/correction, for the
// dose markers) plus the modeled food/correction insulin-activity curves
// built from them — see insulinactivity for the raised-cosine-bell math.
// Doses are windowed by completed_at (when insulin was actually delivered,
// which is what the activity curve models), not requested_at.
func (h *Handler) GetBolusRangeChart(c *gin.Context) {
	from, to, err := rangeWindow(c)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}

	var rows []models.TandemBolus
	tx := h.DB.
		Where("completed_at >= ? AND completed_at < ? AND insulin_delivered IS NOT NULL", from, to).
		Order("completed_at asc").
		Find(&rows)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get bolus data"})
		slog.Error("Failed to query tandem_bolus", "error", tx.Error)
		return
	}

	doses := make([]BolusDose, 0, len(rows))
	foodDoses := make([]insulinactivity.Dose, 0, len(rows))
	correctionDoses := make([]insulinactivity.Dose, 0, len(rows))

	for _, r := range rows {
		delivered := *r.InsulinDelivered
		deliveredAt := *r.CompletedAt

		food, correction := splitDelivered(delivered, r.InsulinRequested, r.FoodBolusSize, r.CorrectionBolusSize)

		dominant := "food"
		if correction > food {
			dominant = "correction"
		}

		doses = append(doses, BolusDose{
			BolusID:          r.BolusID,
			DeliveredAt:      deliveredAt,
			InsulinDelivered: delivered,
			FoodUnits:        round(food, 3),
			CorrectionUnits:  round(correction, 3),
			CarbAmount:       r.CarbAmount,
			DominantCategory: dominant,
		})

		if food > 0 {
			foodDoses = append(foodDoses, insulinactivity.Dose{DeliveredAt: deliveredAt, Units: food})
		}
		if correction > 0 {
			correctionDoses = append(correctionDoses, insulinactivity.Dose{DeliveredAt: deliveredAt, Units: correction})
		}
	}

	c.JSON(http.StatusOK, BolusChartResponse{
		Doses:              doses,
		FoodActivity:       toActivityPoints(insulinactivity.Curve(foodDoses)),
		CorrectionActivity: toActivityPoints(insulinactivity.Curve(correctionDoses)),
	})
}

func toActivityPoints(points []insulinactivity.Point) []ActivityPoint {
	result := make([]ActivityPoint, len(points))
	for i, p := range points {
		result[i] = ActivityPoint{Time: p.Time, Units: round(p.Units, 4)}
	}
	return result
}

// GetBasalRangeChart (GET /basal?from=...&to=...)
// Returns the window's commanded basal-rate changes as step points: the
// rate in commanded_rate holds from that timestamp until the next point
// (or until the window's end, for the chart to draw a final step through
// to rather than stopping short at the last change).
func (h *Handler) GetBasalRangeChart(c *gin.Context) {
	from, to, err := rangeWindow(c)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}

	// The rate in effect at the start of the window may have been set by a
	// change before `from` (e.g. an overnight rate carrying into the day),
	// so also fetch the latest change strictly before `from` and use it as
	// the window's opening step.
	var carryIn models.TandemBasal
	hasCarryIn := true
	if tx := h.DB.Where("changed_at < ?", from).Order("changed_at desc").First(&carryIn); tx.Error != nil {
		if tx.Error != gorm.ErrRecordNotFound {
			c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get basal data"})
			slog.Error("Failed to query carry-in tandem_basal", "error", tx.Error)
			return
		}
		hasCarryIn = false
	}

	var rows []models.TandemBasal
	tx := h.DB.
		Where("changed_at >= ? AND changed_at < ?", from, to).
		Order("changed_at asc").
		Find(&rows)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get basal data"})
		slog.Error("Failed to query tandem_basal", "error", tx.Error)
		return
	}

	points := make([]BasalPoint, 0, len(rows)+1)
	if hasCarryIn {
		points = append(points, BasalPoint{Time: from, CommandedRate: carryIn.CommandedRate})
	}
	for _, r := range rows {
		points = append(points, BasalPoint{Time: r.ChangedAt, CommandedRate: r.CommandedRate})
	}

	c.JSON(http.StatusOK, gin.H{"points": points, "window_end": to})
}

// GetTimeInRange (GET /timeinrange/:hours)
func (h *Handler) GetTimeInRange(c *gin.Context) {
	hours, err := strconv.Atoi(c.Param("hours"))
	if err != nil || hours < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of hours"})
		return
	}

	initData, err := h.getData(hours) // Already sorted desc by time
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}

	var tirDate time.Time
	// Find the datetime of the last *consecutive* in-range value
	for _, tirData := range initData {
		if !tirData.InRange {
			break // Stop at the first value that is out of range
		}
		tirDate = tirData.Datetime // This will be the oldest in-range value
	}

	if tirDate.IsZero() {
		c.JSON(http.StatusOK, gin.H{"hours": 0, "minutes": 0})
		return
	}

	// Calculate duration
	diff := time.Since(tirDate)
	totalMinutes := int(diff.Minutes())
	tirHours := totalMinutes / 60
	tirMins := totalMinutes % 60

	c.JSON(http.StatusOK, gin.H{"hours": tirHours, "minutes": tirMins})
}

// GetPercentInRange (GET /percentinrange/:hours)
func (h *Handler) GetPercentInRange(c *gin.Context) {
	hours, err := strconv.Atoi(c.Param("hours"))
	if err != nil || hours < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of hours"})
		return
	}

	initData, err := h.getData(hours)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}
	if len(initData) == 0 {
		c.JSON(http.StatusOK, gin.H{"detail": "No data found"})
		return
	}

	inRangeCount := 0
	for _, pirData := range initData {
		if pirData.InRange {
			inRangeCount++
		}
	}

	pir := math.Round((float64(inRangeCount) / float64(len(initData))) * 100)

	// Replicate the exact weird JSON structure
	response := []map[string]interface{}{
		{
			"id": "",
			"data": []map[string]interface{}{
				{
					"x": "PercentInRange",
					"y": pir,
				},
			},
		},
	}
	c.JSON(http.StatusOK, response)
}

// Get7DaySparkline (GET /7dsparkline/:day_num)
func (h *Handler) Get7DaySparkline(c *gin.Context) {
	dayNum, err := strconv.Atoi(c.Param("day_num"))
	if err != nil || dayNum < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong day number"})
		return
	}

	// Calculate date range
	selectDate := time.Now().AddDate(0, 0, -(dayNum - 1))
	// Get start of day (00:00:00)
	selectDateStart := time.Date(selectDate.Year(), selectDate.Month(), selectDate.Day(), 0, 0, 0, 0, selectDate.Location())
	// Get end of day (start of next day)
	selectDateEnd := selectDateStart.AddDate(0, 0, 1)

	var dayResponse []SparklineResponse
	tx := h.DB.Model(&models.Latest{}).
		Select("bg_time", "bg_mmol").
		Where("created_at >= ? AND created_at < ?", selectDateStart, selectDateEnd).
		Find(&dayResponse)

	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	c.JSON(http.StatusOK, dayResponse)
}

// GetLast24hSparkline (GET /last24hsparkline)
func (h *Handler) GetLast24hSparkline(c *gin.Context) {
	startDate := time.Now().Add(-24 * time.Hour)

	var dayResponse []SparklineResponse
	tx := h.DB.Model(&models.Latest{}).
		Select("bg_time", "bg_mmol").
		Where("created_at >= ?", startDate).
		Order("bg_time desc").
		Find(&dayResponse)

	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}

	c.JSON(http.StatusOK, dayResponse)
}

// GetGmi (GET /gmi/:days)
func (h *Handler) GetGmi(c *gin.Context) {
	days, err := strconv.Atoi(c.Param("days"))
	if err != nil || days < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of days"})
		return
	}
	if days <= 7 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Number of days should be minimum of 7"})
		return
	}

	// Use a small struct to pluck data efficiently
	var gmiResponse []struct {
		Sgv int
	}
	startDate := time.Now().AddDate(0, 0, -days)

	tx := h.DB.Model(&models.NsPart{}).
		Select("sgv").
		Where("ns_datetime >= ?", startDate).
		Find(&gmiResponse)

	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}
	if len(gmiResponse) == 0 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "No data for this period"})
		return
	}

	recCount := float64(len(gmiResponse))
	sumSgv := 0.0
	sumMmol := 0.0
	for _, r := range gmiResponse {
		sumSgv += float64(r.Sgv)
		sumMmol += float64(r.Sgv) / 18.0
	}

	avgSgv := sumSgv / recCount
	avgMmol := sumMmol / recCount

	// Ref: https://www.jaeb.org/gmi/
	gmiPercent := 3.31 + 0.02392*avgSgv
	gmiMmol := 12.71 + 4.70587*avgMmol

	c.JSON(http.StatusOK, gin.H{
		"gmi_percent": round(gmiPercent, 1),
		"gmi_mmol":    round(gmiMmol, 1),
	})
}

// GetLastReading (GET /lastreading)
func (h *Handler) GetLastReading(c *gin.Context) {
	var readings []models.Latest

	// Get the last 2 readings
	tx := h.DB.Order("bg_time desc").Limit(2).Find(&readings)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": tx.Error.Error()})
		return
	}
	if len(readings) < 2 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Not enough readings to compare"})
		return
	}

	lastReading := readings[0]
	prevReading := readings[1]

	bgMmol := round(lastReading.BgMmol, 1)
	bgMmolDiff := round(lastReading.BgMmol-prevReading.BgMmol, 1)

	c.JSON(http.StatusOK, gin.H{
		"bg_time":      lastReading.BgTime,
		"bg_mmol":      bgMmol,
		"bg_trend":     lastReading.BgTrend,
		"bg_mmol_diff": bgMmolDiff,
	})
}

// validInsulinTypes mirrors the insulin_types enum defined in health-db.
var validInsulinTypes = map[string]bool{
	"LONG_ACTING":  true,
	"RAPID_ACTING": true,
}

// PutInsulin (PUT /insulin)
func (h *Handler) PutInsulin(c *gin.Context) {
	var insulinData models.Insulin

	// Bind the request body JSON to the struct
	if err := c.ShouldBindJSON(&insulinData); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
	}

	if !validInsulinTypes[insulinData.InsulinType] {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "insulin_type must be one of LONG_ACTING, RAPID_ACTING"})
		return
	}
	if insulinData.InsulinQty <= 0 {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "insulin_qty must be greater than 0"})
		return
	}
	if insulinData.DateUtcMillis == 0 {
		insulinData.DateUtc = time.Now().UTC()
		insulinData.DateUtcMillis = insulinData.DateUtc.UnixMilli()
	}

	tx := h.DB.Create(&insulinData)
	if tx.Error != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Could not save insulin data"})
		slog.Error("Failed to create insulin record", "error", tx.Error)
		return
	}

	// Return the created object, which now has the ID
	c.JSON(http.StatusOK, insulinData)
}

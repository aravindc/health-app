package handlers

import (
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"health-api/config"
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
	X time.Time `json:"x"`
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
	BgTime time.Time `json:"bg_time"`
	BgMmol float64   `json:"bg_mmol"`
}

// --- Helper Functions (Methods) ---

// Round float64 to a given precision
func round(val float64, precision int) float64 {
	p := math.Pow10(precision)
	return math.Round(val*p) / p
}

// getData is the Go equivalent of your Python `getData` function
func (h *Handler) getData(hours int) ([]DataPoint, error) {
	// Parse config values
	minMmol, err := strconv.ParseFloat(h.Cfg.MinMmol, 64)
	if err != nil {
		slog.Error("Failed to parse MIN_MMOL", "error", err)
		return nil, err
	}
	strictMaxMmol, err := strconv.ParseFloat(h.Cfg.StrictMaxMmol, 64)
	if err != nil {
		slog.Error("Failed to parse STRICT_MAX_MMOL", "error", err)
		return nil, err
	}
	medicalMaxMmol, err := strconv.ParseFloat(h.Cfg.MedicalMaxMmol, 64)
	if err != nil {
		slog.Error("Failed to parse MEDICAL_MAX_MMOL", "error", err)
		return nil, err
	}

	var results []models.NsPart
	latResponse := []DataPoint{}

	startTime := time.Now().Add(-time.Duration(hours) * time.Hour)
	tx := h.DB.Select("ns_time", "sgv").
		Where("ns_datetime >= ?", startTime).
		Order("ns_time desc").
		Find(&results)

	if tx.Error != nil {
		return nil, tx.Error
	}

	// Process results
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

		latResponse = append(latResponse, DataPoint{
			Epoch:      result.NsTime.UnixMilli(),
			Mmol:       mmol,
			Datetime:   result.NsTime,
			Date:       result.NsTime.Format("2006-01-02"),
			Time:       result.NsTime.Format("15:04:05"),
			InRange:    inRange,
			PointColor: pointColor,
		})
	}

	return latResponse, nil
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

// PutInsulin (PUT /insulin)
func (h *Handler) PutInsulin(c *gin.Context) {
	var insulinData models.Insulin

	// Bind the request body JSON to the struct
	if err := c.ShouldBindJSON(&insulinData); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error()})
		return
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

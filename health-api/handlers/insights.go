package handlers

import (
	"math"
	"net/http"
	"sort"
	"strconv"

	"github.com/gin-gonic/gin"
)

// Insights cards' reading-based statistics, computed here so the frontend
// doesn't have to download every reading in a period to work them out (90
// days is ~26k readings, several MB from /lastxh or /quart).

// InsightSparklineMaxPoints caps the sparkline at a day of 5-minute
// readings; longer periods are averaged into this many buckets.
const InsightSparklineMaxPoints = 288

// RangeSplit is the percentage of readings below, within and above a range.
// Low and High are rounded; In takes the remainder so the three sum to 100.
type RangeSplit struct {
	Low  int `json:"low"`
	In   int `json:"in"`
	High int `json:"high"`
}

// InsightStats is the response of GET /insightstats/:hours.
type InsightStats struct {
	Hours int `json:"hours"`
	Count int `json:"count"`

	Mean   float64 `json:"mean"`    // average glucose
	Median float64 `json:"median"`  // mean of the middle two for an even count
	StdDev float64 `json:"std_dev"` // population standard deviation
	CV     float64 `json:"cv"`      // StdDev as a percentage of the mean
	Q1     float64 `json:"q1"`      // sorted[floor(n * 0.25)]
	Q3     float64 `json:"q3"`      // sorted[floor(n * 0.75)]

	Highs    int `json:"highs"`    // above the medical max
	Lows     int `json:"lows"`     // below the min
	Unicorns int `json:"unicorns"` // readings of exactly 5.5 mmol/L

	Normal RangeSplit `json:"normal"` // min to medical max
	Strict RangeSplit `json:"strict"` // min to strict max

	// % of readings from min to strict max, rounded on its own as
	// /percentinrange does (so it can be 1 more than Strict.In, which takes
	// the remainder after rounding Low and High). 0 with no readings.
	InRangePct int `json:"in_range_pct"`

	// Oldest first, at most InsightSparklineMaxPoints; BgTime in epoch ms.
	Sparkline []SparklineResponse `json:"sparkline"`
}

// rangeThresholds are the configured glucose range limits, in mmol/L.
type rangeThresholds struct {
	Min, StrictMax, MedicalMax float64
}

func (h *Handler) thresholds() (rangeThresholds, error) {
	var t rangeThresholds
	var err error
	if t.Min, err = strconv.ParseFloat(h.Cfg.MinMmol, 64); err != nil {
		return t, err
	}
	if t.StrictMax, err = strconv.ParseFloat(h.Cfg.StrictMaxMmol, 64); err != nil {
		return t, err
	}
	if t.MedicalMax, err = strconv.ParseFloat(h.Cfg.MedicalMaxMmol, 64); err != nil {
		return t, err
	}
	return t, nil
}

func rangeSplit(values []float64, min, max float64) RangeSplit {
	lows, highs := 0, 0
	for _, v := range values {
		if v > max {
			highs++
		} else if v < min {
			lows++
		}
	}
	total := float64(len(values))
	if total == 0 {
		total = 1
	}
	low := int(math.Round(float64(lows) / total * 100))
	high := int(math.Round(float64(highs) / total * 100))
	return RangeSplit{Low: low, In: 100 - low - high, High: high}
}

// downsampleSparkline averages points (any order) into at most max
// buckets of consecutive readings, oldest first.
func downsampleSparkline(points []DataPoint, max int) []SparklineResponse {
	sorted := make([]DataPoint, len(points))
	copy(sorted, points)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Epoch < sorted[j].Epoch })

	size := int(math.Ceil(float64(len(sorted)) / float64(max)))
	if size < 1 {
		size = 1
	}
	out := make([]SparklineResponse, 0, (len(sorted)+size-1)/size)
	for i := 0; i < len(sorted); i += size {
		end := min(i+size, len(sorted))
		var sumTime, sumMmol float64
		for _, p := range sorted[i:end] {
			sumTime += float64(p.Epoch)
			sumMmol += p.Mmol
		}
		n := float64(end - i)
		out = append(out, SparklineResponse{
			BgTime: int64(math.Round(sumTime / n)),
			BgMmol: round(sumMmol/n, 2),
		})
	}
	return out
}

// computeInsightStats works out every InsightStats field from a period's
// readings. With no readings, it returns zeros and 100% in range.
func computeInsightStats(hours int, points []DataPoint, t rangeThresholds) InsightStats {
	values := make([]float64, len(points))
	for i, p := range points {
		values[i] = p.Mmol
	}
	sort.Float64s(values)

	stats := InsightStats{
		Hours:     hours,
		Count:     len(values),
		Normal:    rangeSplit(values, t.Min, t.MedicalMax),
		Strict:    rangeSplit(values, t.Min, t.StrictMax),
		Sparkline: downsampleSparkline(points, InsightSparklineMaxPoints),
	}

	n := len(values)
	if n == 0 {
		return stats
	}

	if n%2 == 0 {
		stats.Median = (values[n/2-1] + values[n/2]) / 2
	} else {
		stats.Median = values[n/2]
	}
	stats.Q1 = values[int(float64(n)*0.25)]
	stats.Q3 = values[int(float64(n)*0.75)]

	var sum float64
	inStrict := 0
	for _, v := range values {
		sum += v
		if v >= t.Min && v <= t.StrictMax {
			inStrict++
		}
		if v > t.MedicalMax {
			stats.Highs++
		} else if v < t.Min {
			stats.Lows++
		}
		// Readings are rounded to 0.1 mmol/L; compare at 2 decimal places
		// so float noise can't hide a 5.5.
		if math.Round(v*100) == 550 {
			stats.Unicorns++
		}
	}
	stats.InRangePct = int(math.Round(float64(inStrict) / float64(n) * 100))
	mean := sum / float64(n)
	var sq float64
	for _, v := range values {
		sq += (v - mean) * (v - mean)
	}
	stdDev := math.Sqrt(sq / float64(n))
	if mean > 0 {
		stats.CV = round(stdDev/mean*100, 2)
	}
	stats.Mean = round(mean, 2)
	stats.Median = round(stats.Median, 2)
	stats.StdDev = round(stdDev, 2)
	return stats
}

// GetInsightStats (GET /insightstats/:hours)
// Statistics for the Insights cards over the last `hours`.
func (h *Handler) GetInsightStats(c *gin.Context) {
	hours, err := strconv.Atoi(c.Param("hours"))
	if err != nil || hours < 1 {
		c.JSON(http.StatusNotFound, gin.H{"detail": "Wrong number of hours"})
		return
	}
	if !h.withinHours(c, "hours", hours) {
		return
	}
	t, err := h.thresholds()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Invalid range thresholds"})
		return
	}
	points, err := h.getData(hours)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "Failed to get data"})
		return
	}
	c.JSON(http.StatusOK, computeInsightStats(hours, points, t))
}

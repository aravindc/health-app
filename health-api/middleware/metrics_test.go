package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

// counterValue extracts the current value of one label combination from a
// CounterVec, or 0 if it hasn't been observed yet.
func counterValue(t *testing.T, c *prometheus.CounterVec, labels prometheus.Labels) float64 {
	t.Helper()
	m, err := c.GetMetricWith(labels)
	if err != nil {
		t.Fatalf("GetMetricWith failed: %v", err)
	}
	var pb dto.Metric
	if err := m.Write(&pb); err != nil {
		t.Fatalf("Write failed: %v", err)
	}
	return pb.GetCounter().GetValue()
}

func TestPrometheusMetrics_RecordsRequest(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(PrometheusMetrics())
	r.GET("/ping", func(c *gin.Context) {
		c.String(http.StatusOK, "pong")
	})

	before := counterValue(t, httpRequestsTotal, prometheus.Labels{"method": "GET", "path": "/ping", "status": "200"})

	req := httptest.NewRequest(http.MethodGet, "/ping", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	after := counterValue(t, httpRequestsTotal, prometheus.Labels{"method": "GET", "path": "/ping", "status": "200"})
	if after != before+1 {
		t.Errorf("expected httpRequestsTotal to increase by 1, went from %v to %v", before, after)
	}
}

func TestPrometheusMetrics_UnknownPathForUnmatchedRoute(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(PrometheusMetrics())
	// No routes registered, so c.FullPath() is "" and should fall back to "unknown".

	before := counterValue(t, httpRequestsTotal, prometheus.Labels{"method": "GET", "path": "unknown", "status": "404"})

	req := httptest.NewRequest(http.MethodGet, "/does-not-exist", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	after := counterValue(t, httpRequestsTotal, prometheus.Labels{"method": "GET", "path": "unknown", "status": "404"})
	if after != before+1 {
		t.Errorf("expected unknown-path counter to increase by 1, went from %v to %v", before, after)
	}
}

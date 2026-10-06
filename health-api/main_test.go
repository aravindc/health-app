package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMetricsServer_ServesOnlyMetrics(t *testing.T) {
	srv := newMetricsServer(":0")

	w := httptest.NewRecorder()
	srv.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "go_goroutines") {
		t.Errorf("/metrics: status %d, want 200 with Prometheus output", w.Code)
	}

	for _, path := range []string{"/", "/health", "/lastreading"} {
		w := httptest.NewRecorder()
		srv.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusNotFound {
			t.Errorf("%s on the metrics port: status %d, want 404", path, w.Code)
		}
	}
}

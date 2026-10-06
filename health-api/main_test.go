package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
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

func TestAPIKeyAuthMiddleware(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(APIKeyAuthMiddleware([]string{"key-one", "a-much-longer-second-key"}))
	r.GET("/x", func(c *gin.Context) { c.Status(http.StatusOK) })

	for _, tc := range []struct {
		name   string
		key    string
		header bool
		want   int
	}{
		{"first key", "key-one", true, http.StatusOK},
		{"second key", "a-much-longer-second-key", true, http.StatusOK},
		{"missing header", "", false, http.StatusUnauthorized},
		{"empty key", "", true, http.StatusUnauthorized},
		{"wrong key", "key-two", true, http.StatusUnauthorized},
		{"prefix of a key", "key-on", true, http.StatusUnauthorized},
		{"key with suffix", "key-one2", true, http.StatusUnauthorized},
		{"different case", "KEY-ONE", true, http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/x", nil)
			if tc.header {
				req.Header.Set("X-API-Key", tc.key)
			}
			w := httptest.NewRecorder()
			r.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Errorf("status %d, want %d", w.Code, tc.want)
			}
		})
	}
}

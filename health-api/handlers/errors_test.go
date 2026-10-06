package handlers

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestServerError_HidesErrorDetails(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	secret := errors.New(`pq: relation "ns_part" does not exist (dial tcp 172.18.0.2:5432)`)
	r.GET("/thing", func(c *gin.Context) { serverError(c, secret) })

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/thing", nil))

	if w.Code != http.StatusInternalServerError {
		t.Errorf("status %d, want 500", w.Code)
	}
	body := w.Body.String()
	if body != `{"detail":"Internal server error"}` {
		t.Errorf("body %q, want the generic message only", body)
	}
	for _, leak := range []string{"ns_part", "172.18.0.2", "pq:"} {
		if strings.Contains(body, leak) {
			t.Errorf("response leaks %q: %s", leak, body)
		}
	}
}

package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

// newTestRouter wraps RateLimiter around a trivial 200-OK handler.
func newTestRouter(rps float64, burst int) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(RateLimiter(rps, burst))
	r.GET("/", func(c *gin.Context) {
		c.Status(http.StatusOK)
	})
	return r
}

func doRequest(r *gin.Engine, ip string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = ip + ":12345"
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestRateLimiter_AllowsWithinBurst(t *testing.T) {
	r := newTestRouter(1, 3)
	for i := 0; i < 3; i++ {
		w := doRequest(r, "1.2.3.4")
		if w.Code != http.StatusOK {
			t.Fatalf("request %d: expected 200, got %d", i, w.Code)
		}
	}
}

func TestRateLimiter_BlocksOverBurst(t *testing.T) {
	r := newTestRouter(1, 2)
	// Two requests consume the whole burst...
	doRequest(r, "5.6.7.8")
	doRequest(r, "5.6.7.8")
	// ...the third, immediately after, should be rejected.
	w := doRequest(r, "5.6.7.8")
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429, got %d", w.Code)
	}
}

func TestRateLimiter_TracksClientsIndependently(t *testing.T) {
	r := newTestRouter(1, 1)
	// Exhaust the bucket for one IP.
	doRequest(r, "10.0.0.1")
	w := doRequest(r, "10.0.0.1")
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("expected first client to be rate-limited, got %d", w.Code)
	}
	// A different IP should have its own, unexhausted bucket.
	w2 := doRequest(r, "10.0.0.2")
	if w2.Code != http.StatusOK {
		t.Fatalf("expected second client to be unaffected, got %d", w2.Code)
	}
}

func TestRateLimiter_RefillsOverTime(t *testing.T) {
	// rps high enough that a short sleep guarantees at least one token back.
	r := newTestRouter(1000, 1)
	doRequest(r, "9.9.9.9") // consume the single token
	w := doRequest(r, "9.9.9.9")
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("expected immediate second request to be limited, got %d", w.Code)
	}

	time.Sleep(10 * time.Millisecond) // >> 1/1000s needed to refill one token
	w2 := doRequest(r, "9.9.9.9")
	if w2.Code != http.StatusOK {
		t.Fatalf("expected request after refill window to succeed, got %d", w2.Code)
	}
}

func TestRateLimiter_TokensCapAtBurst(t *testing.T) {
	// rps high enough that a short sleep implies many tokens' worth of
	// elapsed time. A client that consumes its whole burst, then sits idle,
	// should still only refill up to `burst` tokens, not accumulate beyond
	// it while idle.
	r := newTestRouter(1000, 2)
	doRequest(r, "8.8.8.8") // creates the client, consumes 1 of 2 tokens
	doRequest(r, "8.8.8.8") // consumes the 2nd token; bucket now empty

	time.Sleep(50 * time.Millisecond) // >> enough time to refill past burst if uncapped

	okCount := 0
	for i := 0; i < 5; i++ {
		w := doRequest(r, "8.8.8.8")
		if w.Code == http.StatusOK {
			okCount++
		}
	}
	if okCount != 2 {
		t.Errorf("expected exactly 2 successful requests (burst=2 cap), got %d", okCount)
	}
}

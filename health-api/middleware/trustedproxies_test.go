package middleware

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"

	"health-api/config"
)

// Rate limiting with the trusted proxies main.go configures. Without them,
// gin believes any X-Forwarded-For, and a client can send a different fake
// IP per request to get a fresh token bucket each time.

const (
	nginxIP = "172.18.0.4" // health-fe's nginx, the API's direct peer
	caddyIP = "172.19.0.2" // Caddy, in front of nginx
)

func newProxiedRouter(t *testing.T, burst int) *gin.Engine {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	if err := r.SetTrustedProxies(config.DefaultTrustedProxies); err != nil {
		t.Fatalf("SetTrustedProxies: %v", err)
	}
	r.Use(RateLimiter(0, burst)) // no refill: each client gets exactly `burst`
	r.GET("/", func(c *gin.Context) { c.String(http.StatusOK, c.ClientIP()) })
	return r
}

func send(r *gin.Engine, peer, xff string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = peer + ":12345"
	if xff != "" {
		req.Header.Set("X-Forwarded-For", xff)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestTrustedProxies_DirectPublicClientCannotSpoof(t *testing.T) {
	r := newProxiedRouter(t, 3)
	// A public client talking to the API directly, rotating fake IPs.
	codes := map[int]int{}
	for i := range 10 {
		w := send(r, "203.0.113.9", fmt.Sprintf("10.9.0.%d", i+1))
		codes[w.Code]++
	}
	if codes[http.StatusOK] != 3 || codes[http.StatusTooManyRequests] != 7 {
		t.Errorf("want 3×200 then 7×429 (one bucket for 203.0.113.9), got %v", codes)
	}
}

func TestTrustedProxies_SpoofedPrefixBehindProxiesIsIgnored(t *testing.T) {
	r := newProxiedRouter(t, 3)
	// Caddy → nginx → API, where the client sent its own fake
	// X-Forwarded-For first. Real client 198.51.100.7 is the first entry,
	// from the right, outside the trusted proxies.
	codes := map[int]int{}
	for i := range 10 {
		xff := fmt.Sprintf("10.9.0.%d, 198.51.100.7, %s", i+1, caddyIP)
		w := send(r, nginxIP, xff)
		codes[w.Code]++
		if w.Code == http.StatusOK && w.Body.String() != "198.51.100.7" {
			t.Fatalf("ClientIP=%q, want 198.51.100.7", w.Body.String())
		}
	}
	if codes[http.StatusOK] != 3 || codes[http.StatusTooManyRequests] != 7 {
		t.Errorf("want 3×200 then 7×429 (spoofed prefix ignored), got %v", codes)
	}
}

func TestTrustedProxies_RealClientsBehindProxiesGetSeparateBuckets(t *testing.T) {
	r := newProxiedRouter(t, 1)
	for _, client := range []string{"198.51.100.7", "198.51.100.8"} {
		w := send(r, nginxIP, client+", "+caddyIP)
		if w.Code != http.StatusOK || w.Body.String() != client {
			t.Errorf("client %s: got %d %q, want 200 %q", client, w.Code, w.Body.String(), client)
		}
	}
}

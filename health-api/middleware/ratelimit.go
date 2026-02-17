package middleware

import (
	"net/http"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

type client struct {
	tokens   float64
	lastSeen time.Time
}

// RateLimiter returns a Gin middleware that limits requests per IP
// using a token bucket algorithm.
// rps: requests per second allowed per client.
// burst: maximum burst size (bucket capacity).
func RateLimiter(rps float64, burst int) gin.HandlerFunc {
	var mu sync.Mutex
	clients := make(map[string]*client)

	// Clean up stale entries every minute
	go func() {
		for {
			time.Sleep(1 * time.Minute)
			mu.Lock()
			for ip, c := range clients {
				if time.Since(c.lastSeen) > 3*time.Minute {
					delete(clients, ip)
				}
			}
			mu.Unlock()
		}
	}()

	return func(c *gin.Context) {
		ip := c.ClientIP()

		mu.Lock()
		cl, exists := clients[ip]
		if !exists {
			cl = &client{tokens: float64(burst)}
			clients[ip] = cl
		}

		// Refill tokens based on elapsed time
		elapsed := time.Since(cl.lastSeen).Seconds()
		cl.tokens += elapsed * rps
		if cl.tokens > float64(burst) {
			cl.tokens = float64(burst)
		}
		cl.lastSeen = time.Now()

		if cl.tokens < 1 {
			mu.Unlock()
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"detail": "Rate limit exceeded. Try again shortly.",
			})
			return
		}

		cl.tokens--
		mu.Unlock()

		c.Next()
	}
}

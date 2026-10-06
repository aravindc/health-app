package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"health-api/config"
	"health-api/database"
	"health-api/handlers"
	"health-api/middleware"
)

// APIKeyAuthMiddleware validates the X-API-Key header against a list of allowed keys.
//
// Keys are compared as SHA-256 digests with subtle.ConstantTimeCompare, and
// every allowed key is checked, so the response time reveals neither how
// much of a key matched, nor its length, nor which key it was.
func APIKeyAuthMiddleware(apiKeys []string) gin.HandlerFunc {
	digests := make([][sha256.Size]byte, len(apiKeys))
	for i, key := range apiKeys {
		digests[i] = sha256.Sum256([]byte(key))
	}

	return func(c *gin.Context) {
		apiKey := c.GetHeader("X-API-Key")

		if apiKey == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Invalid or missing API Key"})
			return
		}

		presented := sha256.Sum256([]byte(apiKey))
		match := 0
		for i := range digests {
			match |= subtle.ConstantTimeCompare(presented[:], digests[i][:])
		}
		if match == 1 {
			c.Next()
			return
		}

		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Invalid or missing API Key"})
	}
}

func main() {
	// Use Go's new structured logger
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	// 1. Load Config
	cfg, err := config.LoadConfig()
	if err != nil {
		slog.Error("Failed to load configuration", "error", err)
		os.Exit(1)
	}

	// 2. Connect to Database
	db, err := database.ConnectDB(cfg.DSN)
	if err != nil {
		slog.Error("Failed to connect to database", "error", err)
		os.Exit(1)
	}

	// 2b. Run pending schema migrations before serving any requests.
	sqlDB, err := db.DB()
	if err != nil {
		slog.Error("Failed to get underlying sql.DB for migrations", "error", err)
		os.Exit(1)
	}
	if err := database.Migrate(sqlDB); err != nil {
		slog.Error("Failed to run database migrations", "error", err)
		os.Exit(1)
	}
	slog.Info("Database migrations up to date")

	// 3. Set Gin mode from config: release unless APP_ENV=development.
	if cfg.AppEnv == config.EnvDevelopment {
		gin.SetMode(gin.DebugMode)
	} else {
		gin.SetMode(gin.ReleaseMode)
	}
	slog.Info("Environment", "app_env", cfg.AppEnv, "gin_mode", gin.Mode())
	r := gin.Default()
	// Without this gin trusts X-Forwarded-For from anyone, so a client could
	// send a different fake IP per request and slip past the rate limiter.
	if err := r.SetTrustedProxies(cfg.TrustedProxies); err != nil {
		slog.Error("Invalid TRUSTED_PROXIES", "error", err)
		os.Exit(1)
	}
	slog.Info("Trusted proxies", "proxies", cfg.TrustedProxies)

	// 4. Add CORS Middleware
	corsConfig := cors.DefaultConfig()
	corsConfig.AllowOrigins = cfg.CORSAllowedOrigins
	corsConfig.AllowCredentials = true
	corsConfig.AllowMethods = []string{"GET", "OPTIONS"}
	corsConfig.AllowHeaders = []string{"*"}
	// Only called for origins not in AllowOrigins: log them so a 403 shows
	// what the browser actually sent, then reject as before.
	corsConfig.AllowOriginWithContextFunc = func(c *gin.Context, origin string) bool {
		slog.Warn("CORS origin rejected", "origin", origin, "host", c.Request.Host, "path", c.Request.URL.Path)
		return false
	}
	slog.Info("CORS allowed origins", "origins", cfg.CORSAllowedOrigins)
	r.Use(cors.New(corsConfig))

	// 5. Add metrics and rate limiting middleware
	r.Use(middleware.PrometheusMetrics())
	r.Use(middleware.RateLimiter(10, 30)) // 10 req/s per IP, burst of 30

	// 6. Create Handler instance
	h := handlers.NewHandler(db, cfg)

	// 6. Define Routes
	// Public endpoints (no auth). Prometheus metrics are served on their
	// own internal port instead (see newMetricsServer).
	r.GET("/health", h.HealthReady)

	// Protected routes with API key auth
	api := r.Group("/")
	if len(cfg.APIKeys) > 0 {
		api.Use(APIKeyAuthMiddleware(cfg.APIKeys))
		slog.Info("API key authentication enabled")
	} else {
		slog.Warn("No API_KEYS configured — protected routes are unauthenticated")
	}
	{
		api.GET("/", h.Root)
		api.GET("/latest/:page_num", h.GetLatest)
		api.GET("/last4h/:page_num", h.GetLast4h)
		api.GET("/last12h/:page_num", h.GetLast12h)
		api.GET("/lastxh/:hours", h.GetLastXh)
		api.GET("/lastxh/:hours/offset/:offset_hours", h.GetLastXhOffset)
		api.GET("/chart", h.GetRangeChart)
		api.GET("/bolus", h.GetBolusRangeChart)
		api.GET("/basal", h.GetBasalRangeChart)
		api.GET("/firstdate", h.GetFirstDate)
		api.GET("/lastreading", h.GetLastReading)
		api.GET("/dailyavg/:days", h.GetDailyAvg)
		api.GET("/dailytir/:days", h.GetDailyTir)
		api.GET("/avgmmol/:time_period", h.GetAvgMmol)
		api.GET("/quart/:days", h.GetQuart)
		api.GET("/timeinrange/:hours", h.GetTimeInRange)
		api.GET("/percentinrange/:hours", h.GetPercentInRange)
		api.GET("/gmi/:days", h.GetGmi)
		api.GET("/insightstats/:hours", h.GetInsightStats)
		api.GET("/7dsparkline/:day_num", h.Get7DaySparkline)
		api.GET("/last24hsparkline", h.GetLast24hSparkline)
	}

	// 7. Start server with graceful shutdown
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	go func() {
		slog.Info("Starting server", "port", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("Server error", "error", err)
			os.Exit(1)
		}
	}()

	metricsPort := os.Getenv("METRICS_PORT")
	if metricsPort == "" {
		metricsPort = "9090"
	}
	metricsSrv := newMetricsServer(":" + metricsPort)
	go func() {
		slog.Info("Starting metrics server", "port", metricsPort)
		if err := metricsSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("Metrics server error", "error", err)
			os.Exit(1)
		}
	}()

	// Wait for interrupt signal
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	sig := <-sigChan
	slog.Info("Received signal, shutting down gracefully", "signal", sig)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		slog.Error("Server shutdown error", "error", err)
	}
	if err := metricsSrv.Shutdown(ctx); err != nil {
		slog.Error("Metrics server shutdown error", "error", err)
	}
	slog.Info("Server stopped")
}

// newMetricsServer serves Prometheus metrics, and nothing else, on its own
// port. Keeping them off the API's port keeps them out of the health-fe
// /api proxy (and anything else in front of the API): the metrics list
// every route and when the app is used. The port isn't published by
// docker-compose, so only containers on health-api's networks (e.g. a
// Prometheus scraping http://health-api:9090/metrics) can reach it.
func newMetricsServer(addr string) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	return &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
}

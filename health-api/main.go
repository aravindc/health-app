package main

import (
	"context"
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
func APIKeyAuthMiddleware(apiKeys []string) gin.HandlerFunc {
	return func(c *gin.Context) {
		apiKey := c.GetHeader("X-API-Key")

		if apiKey == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Invalid or missing API Key"})
			return
		}

		for _, key := range apiKeys {
			if key == apiKey {
				c.Next()
				return
			}
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

	// 3. Set Gin mode from config
	if cfg.AppEnv == "production" {
		gin.SetMode(gin.ReleaseMode)
	} else {
		gin.SetMode(gin.DebugMode)
	}
	r := gin.Default()

	// 4. Add CORS Middleware
	corsConfig := cors.DefaultConfig()
	if cfg.AppEnv == "production" {
		corsConfig.AllowOrigins = []string{"https://ui.health.pers.dev"}
	} else {
		corsConfig.AllowOrigins = []string{
			"http://localhost:5173",
			"http://localhost:4000",
			"http://health-ui:9093",
			"http://localhost:9083",
		}
	}
	corsConfig.AllowCredentials = true
	corsConfig.AllowMethods = []string{"GET", "PUT", "OPTIONS"}
	corsConfig.AllowHeaders = []string{"*"}
	r.Use(cors.New(corsConfig))

	// 5. Add metrics and rate limiting middleware
	r.Use(middleware.PrometheusMetrics())
	r.Use(middleware.RateLimiter(10, 30)) // 10 req/s per IP, burst of 30

	// 6. Create Handler instance
	h := handlers.NewHandler(db, cfg)

	// 6. Define Routes
	// Public endpoints (no auth)
	r.GET("/health", h.HealthReady)
	r.GET("/metrics", gin.WrapH(promhttp.Handler()))

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
		api.GET("/daychart/:date", h.GetDayChart)
		api.GET("/firstdate", h.GetFirstDate)
		api.GET("/lastreading", h.GetLastReading)
		api.GET("/dailyavg/:days", h.GetDailyAvg)
		api.GET("/dailytir/:days", h.GetDailyTir)
		api.GET("/avgmmol/:time_period", h.GetAvgMmol)
		api.GET("/quart/:days", h.GetQuart)
		api.GET("/timeinrange/:hours", h.GetTimeInRange)
		api.GET("/percentinrange/:hours", h.GetPercentInRange)
		api.GET("/gmi/:days", h.GetGmi)
		api.GET("/7dsparkline/:day_num", h.Get7DaySparkline)
		api.GET("/last24hsparkline", h.GetLast24hSparkline)

		api.PUT("/insulin", h.PutInsulin)
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
	slog.Info("Server stopped")
}

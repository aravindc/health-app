package main

import (
	"log/slog"
	"net/http"
	"os"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"health-api/config"
	"health-api/database"
	"health-api/handlers"
)

// APIKeyAuthMiddleware is the Go equivalent of your `get_api_key`
func APIKeyAuthMiddleware(apiKeys []string) gin.HandlerFunc {
	return func(c *gin.Context) {
		apiKey := c.GetHeader("X-API-Key")

		if apiKey == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Invalid or missing API Key"})
			return
		}

		for _, key := range apiKeys {
			if key == apiKey {
				c.Next() // Key is valid, continue
				return
			}
		}

		// If loop finishes, key was not found
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

	// 4. Create Gin Router
	gin.SetMode(gin.DebugMode)
	r := gin.Default()

	// 5. Add CORS Middleware
	// This replicates your production/development logic
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
	corsConfig.AllowMethods = []string{"*"}
	corsConfig.AllowHeaders = []string{"*"}
	r.Use(cors.New(corsConfig))

	// 6. Create Handler instance
	h := handlers.NewHandler(db, cfg)

	// 7. Define Routes
	// Public health check
	r.GET("/health", h.HealthReady)

	// Create a group for routes that need API key auth
	api := r.Group("/")
	if len(cfg.APIKeys) > 0 {
		api.Use(APIKeyAuthMiddleware(cfg.APIKeys))
		slog.Info("API key authentication enabled")
	} else {
		slog.Warn("No API_KEYS configured — protected routes are unauthenticated")
	}
	{
		api.GET("/", h.Root)
		api.GET("/latest/:page_num", h.GetLatest) // You would need to create GetLatest
		api.GET("/dailyavg/:days", h.GetDailyAvg)
		api.GET("/dailytir/:days", h.GetDailyTir) // You would need to create GetDailyTir
		api.GET("/avgmmol/:time_period", h.GetAvgMmol)
		api.GET("/last12h/:page_num", h.GetLast12h) // ... and so on
		api.GET("/quart/:days", h.GetQuart)
		api.GET("/last4h/:page_num", h.GetLast4h)
		api.GET("/lastxh/:hours/:page_num", h.GetLastXh)
		api.GET("/timeinrange/:hours", h.GetTimeInRange)
		api.GET("/percentinrange/:hours", h.GetPercentInRange)
		api.GET("/7dsparkline/:day_num", h.Get7DaySparkline)
		api.GET("/last24hsparkline", h.GetLast24hSparkline)
		api.GET("/gmi/:days", h.GetGmi)
		api.GET("/lastreading", h.GetLastReading)

		api.PUT("/insulin", h.PutInsulin)
	}

	// 8. Run Server
	slog.Info("Starting server on :8080") // Gin defaults to port 8080
	if err := r.Run(); err != nil {
		slog.Error("Failed to run server", "error", err)
	}
}

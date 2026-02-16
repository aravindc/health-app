package server

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	log "github.com/sirupsen/logrus"
)

type healthResponse struct {
	Status   string `json:"status"`
	Database string `json:"database"`
	Uptime   string `json:"uptime"`
}

// Start launches the HTTP server for health checks and metrics.
// It blocks until the provided context is cancelled, then shuts down gracefully.
func Start(ctx context.Context, dbClient *sql.DB, startTime time.Time) {
	port := os.Getenv("HEALTH_PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		dbStatus := "ok"
		if err := dbClient.Ping(); err != nil {
			dbStatus = "error: " + err.Error()
		}

		status := "ok"
		if dbStatus != "ok" {
			status = "degraded"
		}

		resp := healthResponse{
			Status:   status,
			Database: dbStatus,
			Uptime:   time.Since(startTime).Round(time.Second).String(),
		}

		w.Header().Set("Content-Type", "application/json")
		if status != "ok" {
			w.WriteHeader(http.StatusServiceUnavailable)
		}
		json.NewEncoder(w).Encode(resp)
	})

	mux.Handle("/metrics", promhttp.Handler())

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}

	go func() {
		log.Info("Health/metrics server listening on :", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Error("Health server error: ", err)
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	srv.Shutdown(shutdownCtx)
	log.Info("Health/metrics server stopped")
}

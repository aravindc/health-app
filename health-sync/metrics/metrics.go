package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	SyncCyclesTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_sync_cycles_total",
		Help: "Total number of sync cycles executed",
	})

	SyncErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_sync_errors_total",
		Help: "Total number of sync cycle errors",
	})

	BGReadingsFetched = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_bg_readings_fetched_total",
		Help: "Total number of BG readings fetched from Dexcom",
	})

	BGReadingsInserted = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_bg_readings_inserted_total",
		Help: "Total number of BG readings inserted into the database",
	})

	BGReadingsDuplicate = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_bg_readings_duplicate_total",
		Help: "Total number of duplicate BG readings skipped",
	})

	DBErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_db_errors_total",
		Help: "Total number of database errors",
	})

	APIErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_api_errors_total",
		Help: "Total number of Dexcom API errors",
	})

	SessionRenewals = promauto.NewCounter(prometheus.CounterOpts{
		Name: "healthsync_session_renewals_total",
		Help: "Total number of session renewals",
	})

	LastSyncTimestamp = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "healthsync_last_sync_timestamp",
		Help: "Unix timestamp of the last successful sync",
	})

	LastBGValue = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "healthsync_last_bg_value",
		Help: "Most recent blood glucose value (mg/dL)",
	})
)

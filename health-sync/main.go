package main

import (
	"database/sql"
	"health-sync/bridge"
	"health-sync/db"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	log "github.com/sirupsen/logrus"
	easy "github.com/t-tomalak/logrus-easy-formatter"
)

var sessionID string
var baseUrl string
var authUrl string
var loginUrl string
var latestbgUrl string

func init() {

	// Setup URLs
	baseUrl = "https://" + bridge.GetDexServer()
	authUrl = baseUrl + "/ShareWebServices/Services/General/AuthenticatePublisherAccount"
	loginUrl = baseUrl + "/ShareWebServices/Services/General/LoginPublisherAccountById"
	latestbgUrl = baseUrl + "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues"

	// Setup Log
	log.SetOutput(os.Stdout)
	log.SetFormatter(
		&easy.Formatter{
			TimestampFormat: "2006-01-02 15:04:05",
			LogFormat:       "[%lvl%]: %time% - %msg%\n",
		},
	)
	logLevel, err := log.ParseLevel(os.Getenv("LOG_LEVEL"))
	if err != nil {
		logLevel = log.InfoLevel
	}
	log.SetLevel(logLevel)

	_, recordCountExists := os.LookupEnv("RECORD_COUNT")
	if !recordCountExists {
		os.Setenv("RECORD_COUNT", "3")
	}

	// Check if BRIDGE_SERVER variable value is set
	_, bridgeServerExists := os.LookupEnv("BRIDGE_SERVER")
	if !bridgeServerExists {
		log.Fatal("Environment variables: BRIDGE_SERVER should be set")
	}
	// Check if BRIDGE_USER variable value is set
	_, bridgeUserExists := os.LookupEnv("BRIDGE_USER")
	if !bridgeUserExists {
		log.Fatal("Environment variables: BRIDGE_USER should be set")
	}
	// Check if BRIDGE_PASS variable value is set
	_, bridgePassExists := os.LookupEnv("BRIDGE_PASS")
	if !bridgePassExists {
		log.Fatal("Environment variables: BRIDGE_PASS should be set")
	}
}

func renewSession() {
	newSession, err := bridge.GetSessionId(loginUrl, authUrl)
	if err != nil {
		log.Error("Failed to renew session: ", err)
		return
	}
	sessionID = newSession
	log.Info("Session renewed successfully")
}

func syncBGReadings(pgClient *sql.DB) {
	if !bridge.IsSessionIdValid(sessionID, latestbgUrl) {
		log.Info("Session ID is invalid. Renewing it")
		renewSession()
	}

	latestBG, err := bridge.GetLatestBG(latestbgUrl, sessionID)
	if err != nil {
		log.Error("Failed to fetch BG data: ", err)
		return
	}

	for _, val := range latestBG {
		exists, err := db.EntriesExist(pgClient, int64(val.Ns_time))
		if err != nil {
			log.Error("Failed to check entry existence: ", err)
			continue
		}
		if !exists {
			if err := db.InsertEntries(pgClient, val); err != nil {
				log.Error("Failed to insert entry: ", err)
			}
		} else {
			log.Debug("Record already exists: ", val)
		}
	}
}

func main() {
	// Initialize session
	var err error
	sessionID, err = bridge.GetSessionId(loginUrl, authUrl)
	if err != nil {
		log.Fatal("Failed to get initial session ID: ", err)
	}
	log.Info("Session ID obtained")
	log.Info("Latest BG URL is: ", latestbgUrl)

	// Initialize DB connection once
	pgClient, err := db.DbClient(
		os.Getenv("POSTGRES_HOST"),
		os.Getenv("POSTGRES_PORT"),
		os.Getenv("POSTGRES_USER"),
		os.Getenv("POSTGRES_PASSWORD"),
		os.Getenv("POSTGRES_DB"),
	)
	if err != nil {
		log.Fatal("Failed to connect to database: ", err)
	}
	defer pgClient.Close()

	// Initial sync
	syncBGReadings(pgClient)

	// Set up ticker
	syncInterval := 60
	if v := os.Getenv("SYNC_INTERVAL_SECONDS"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			syncInterval = parsed
		}
	}
	ticker := time.NewTicker(time.Duration(syncInterval) * time.Second)
	defer ticker.Stop()

	// Set up signal handling for graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	log.Info("Health-sync started. Polling every ", syncInterval, " seconds.")

	for {
		select {
		case <-ticker.C:
			syncBGReadings(pgClient)
		case sig := <-sigChan:
			log.Info("Received signal: ", sig, ". Shutting down gracefully.")
			return
		}
	}
}

package main

import (
	"health-sync/bridge"
	"health-sync/db"
	"os"
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
		log.Fatal("Environment variables: BRIDGE_SERVER shoud be set")
	}
	// Check if BRIDGE_USER variable value is set
	_, bridgeUserExists := os.LookupEnv("BRIDGE_USER")
	if !bridgeUserExists {
		log.Fatal("Environment variables: BRIDGE_USER shoud be set")
	}
	// Check if BRIDGE_PASS variable value is set
	_, bridgePassExists := os.LookupEnv("BRIDGE_PASS")
	if !bridgePassExists {
		log.Fatal("Environment variables: BRIDGE_PASS shoud be set")
	}

	// Session is initialized only once and may become invalid after its expiry
	// TODO: Write a function to check the validity of session_id and renew if required
	sessionID = bridge.GetSessionId(loginUrl, authUrl)
	log.Info("Session ID is: ", sessionID)
	log.Info("Latest BG URL is: ", latestbgUrl)

}

// Retrieve BG data every 2 minutes
func getBGData() {

	pg_client := db.DbClient(
		os.Getenv("POSTGRES_HOST"),
		os.Getenv("POSTGRES_PORT"),
		os.Getenv("POSTGRES_USER"),
		os.Getenv("POSTGRES_PASSWORD"),
		os.Getenv("POSTGRES_DB"),
	)

	checkSessionValidity := func() {
		if !bridge.IsSessionIdValid(sessionID, latestbgUrl) {
			sessionID = bridge.GetSessionId(loginUrl, authUrl)
			log.Info("Session ID is invalid. Renewing it")
		}
	} // Check if session_id is valid

	checkSessionValidity()
	latest_bg := bridge.GetLatestBG(latestbgUrl, sessionID)

	// Initial Connection
	for _, val := range latest_bg {
		// Check if record exists based on hash value
		if !db.EntriesExist(pg_client, int64(val.Ns_time)) {
			// Insert record into db if it does exist
			db.InsertEntries(pg_client, val)
		} else {
			log.Info("Record already exists: ", val)
		}
		// log.Info("The value of SGV is: ", val)
		// log.Info("Records: ", db.SelectEntries(pg_client))
	}

	// Run this ticker every 2 minutes
	ticker := time.NewTicker(1 * time.Minute)
	for range ticker.C {
		checkSessionValidity()
		latest_bg := bridge.GetLatestBG(latestbgUrl, sessionID)
		for _, val := range latest_bg {
			if !db.EntriesExist(pg_client, int64(val.Ns_time)) {
				db.InsertEntries(pg_client, val)
			} else {
				log.Info("Record already exists: ", val)
			}
			// log.Info(val)
		}
	}
}

func main() {
	go getBGData()
	// Insert other function calls here
	select {}
}

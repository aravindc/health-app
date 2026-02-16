package bridge

import (
	"fmt"
	"health-sync/common"
	"health-sync/model"
	"os"
	"strconv"
	"time"

	resty "github.com/go-resty/resty/v2"
	"github.com/goccy/go-json"
	log "github.com/sirupsen/logrus"
)

const (
	httpTimeout    = 30 * time.Second
	retryCount     = 3
	retryWaitTime  = 2 * time.Second
	retryMaxWait   = 10 * time.Second
)

func newHTTPClient() *resty.Client {
	return resty.New().
		SetTimeout(httpTimeout).
		SetRetryCount(retryCount).
		SetRetryWaitTime(retryWaitTime).
		SetRetryMaxWaitTime(retryMaxWait).
		AddRetryCondition(func(r *resty.Response, err error) bool {
			return err != nil || r.StatusCode() >= 500
		})
}

func GetDexServer() string {
	bridge_region := os.Getenv("BRIDGE_SERVER")
	return common.TernaryIf(bridge_region == "US", "share2.dexcom.com", "shareous1.dexcom.com")
}

func getPayload(input_type string, input_string string) []byte {
	payload := []byte(fmt.Sprintf(`{"password": "%s", "applicationId": "%s", "%s": "%s"}`, os.Getenv("BRIDGE_PASS"), os.Getenv("APPLICATION_ID"), input_type, input_string))
	return payload
}

// GetAccountId authenticates with Dexcom and retrieves the account ID.
func GetAccountId(auth_url string) (string, error) {
	client := newHTTPClient()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetBody(getPayload("accountName", os.Getenv("BRIDGE_USER"))).
		Post(auth_url)
	if err != nil {
		return "", fmt.Errorf("GetAccountId request failed: %w", err)
	}
	if resp.StatusCode() != 200 {
		return "", fmt.Errorf("GetAccountId returned status %d: %s", resp.StatusCode(), resp.String())
	}
	accountId := common.CleanString(resp.String())
	return accountId, nil
}

// GetSessionId retrieves a Dexcom session ID using the account ID.
func GetSessionId(login_url string, auth_url string) (string, error) {
	accountId, err := GetAccountId(auth_url)
	if err != nil {
		return "", fmt.Errorf("GetSessionId: %w", err)
	}
	client := newHTTPClient()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetBody(getPayload("accountId", accountId)).
		Post(login_url)
	if err != nil {
		return "", fmt.Errorf("GetSessionId request failed: %w", err)
	}
	if resp.StatusCode() != 200 {
		return "", fmt.Errorf("GetSessionId returned status %d: %s", resp.StatusCode(), resp.String())
	}
	sessionId := common.CleanString(resp.String())
	return sessionId, nil
}

// IsSessionIdValid checks if the current session ID is still valid.
func IsSessionIdValid(session_id string, latestbg_url string) bool {
	query_string := common.CleanString(fmt.Sprintf("sessionId=%s&minutes=60&maxCount=1", session_id))
	client := newHTTPClient()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetQueryString(query_string).
		Post(latestbg_url)
	if err != nil {
		log.Warning("Session validity check failed: ", err)
		return false
	}
	if resp.StatusCode() == 200 {
		log.Info("Session is valid")
		return true
	}
	return false
}

// GetLatestBG fetches the latest blood glucose readings from Dexcom.
func GetLatestBG(latestbg_url string, session_id string) ([]model.Nightscoutdb, error) {
	bgQueryMinutes := 1440
	if v := os.Getenv("BG_QUERY_MINUTES"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			bgQueryMinutes = parsed
		}
	}
	query_string := common.CleanString(fmt.Sprintf("sessionId=%s&minutes=%d&maxCount=%s", session_id, bgQueryMinutes, os.Getenv("RECORD_COUNT")))
	client := newHTTPClient()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetQueryString(query_string).
		Post(latestbg_url)
	if err != nil {
		return nil, fmt.Errorf("GetLatestBG request failed: %w", err)
	}
	if resp.StatusCode() != 200 {
		return nil, fmt.Errorf("GetLatestBG returned status %d: %s", resp.StatusCode(), resp.String())
	}

	var data []model.DexBgReading
	if err := json.Unmarshal(resp.Body(), &data); err != nil {
		return nil, fmt.Errorf("GetLatestBG JSON parse error: %w", err)
	}

	nsEntries := make([]model.Nightscoutdb, 0, len(data))
	for _, val := range data {
		var NsEntry model.Nightscoutdb
		NsEntry.Sgv = val.Value
		NsEntry.Ns_time = common.CleanDateString(val.WT)
		NsEntry.Ns_datetime = time.UnixMilli(common.CleanDateString(val.WT))
		NsEntry.Trend = common.TrendToDirection(val.Trend)
		NsEntry.Utcoffset = 0
		NsEntry.Systime = time.UnixMilli(common.CleanDateString(val.WT))
		nsEntries = append(nsEntries, NsEntry)
	}
	return nsEntries, nil
}

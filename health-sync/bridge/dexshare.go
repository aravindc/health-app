package bridge

import (
	"fmt"
	"health-sync/common"
	"health-sync/model"
	"os"
	"time"

	resty "github.com/go-resty/resty/v2"
	"github.com/goccy/go-json"
	log "github.com/sirupsen/logrus"
)

func GetDexServer() string {
	bridge_region := os.Getenv("BRIDGE_SERVER")
	return common.TernaryIf(bridge_region == "US", "share2.dexcom.com", "shareous1.dexcom.com")
}

func getPayload(input_type string, input_string string) []byte {
	payload := []byte(fmt.Sprintf(`{"password": "%s", "applicationId": "%s", "%s": "%s"}`, os.Getenv("BRIDGE_PASS"), os.Getenv("APPLICATION_ID"), input_type, input_string))
	return payload
}

// Using Dexcom Username and Password, This method get the dexcom accountid
// Params:
// authUrl string Dexcom Authentication URL

func GetAccountId(auth_url string) string {
	client := resty.New()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetBody(getPayload("accountName", os.Getenv("BRIDGE_USER"))).
		Post(auth_url)
	if err != nil {
		log.Warning("Request did not complete successfully ", err)
	}
	accountId := common.CleanString(resp.String())
	return accountId
}

// Using Dexcom accountid got from GetAccountId method and Password, This method get the dexcom sessionid
// Params:
// loginUrl string Dexcom Login URL
// authUrl string Dexcom Authentication URL

func GetSessionId(login_url string, auth_url string) string {
	client := resty.New()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetBody(getPayload("accountId", GetAccountId(auth_url))).
		Post(login_url)
	if err != nil {
		log.Warning("Request did not complete successfully ", err)
	}
	sessionId := common.CleanString(resp.String())
	return sessionId
}

func IsSessionIdValid(session_id string, latestbg_url string) bool {
	log.Info(latestbg_url)
	query_string := common.CleanString(fmt.Sprintf("sessionId=%s&minutes=60&maxCount=1", session_id))
	client := resty.New()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetQueryString(query_string).
		Post(latestbg_url)
	if err != nil {
		log.Warning("Request did not complete successfully ", err)
	}
	if resp.StatusCode() == 200 {
		log.Info("Session is valid")
		return true
	}
	return false
}

func GetLatestBG(latestbg_url string, session_id string) []model.Nightscoutdb {
	query_string := common.CleanString(fmt.Sprintf("sessionId=%s&minutes=1440&maxCount=%s", session_id, os.Getenv("RECORD_COUNT")))
	client := resty.New()
	resp, err := client.R().
		SetHeader("Content-Type", "application/json; charset=UTF-8").
		SetQueryString(query_string).
		Post(latestbg_url)
	if err != nil {
		log.Warning("Request did not complete successfully ", err)
	}
	latest_bg := resp.Body()
	var data []model.DexBgReading
	err = json.Unmarshal(latest_bg, &data)
	if err != nil {
		log.Info(latest_bg)
		log.Error(err)
	}

	nsEntries := []model.Nightscoutdb{}
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
	return nsEntries
}

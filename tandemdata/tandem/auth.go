// Package tandem is a pure-HTTP client for Tandem Source, reverse-engineered
// from a redacted browser capture (auth-capture.json). No browser needed at
// runtime.
package tandem

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"

	"golang.org/x/net/publicsuffix"
)

const (
	clientID      = "1519e414-eeec-492e-8c5e-97bea4815a10"
	loginClientID = "afead6a9-c53b-45b8-9efe-69ed0cc2711b"
	redirectURI   = "https://source.eu.tandemdiabetes.com/authorize/callback"
	accountsBase  = "https://tdcservices.eu.tandemdiabetes.com/accounts/api"
	scope         = "openid email profile tandem.devices.assign"
)

// AuthResult holds what's needed to call the reports API afterward.
type AuthResult struct {
	AccessToken string
	PumperID    string
	Client      *http.Client
}

func base64URLEncode(b []byte) string {
	return base64.RawURLEncoding.EncodeToString(b)
}

func randomToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64URLEncode(b), nil
}

// newHTTPClient returns a client with a cookie jar and manual redirect
// handling, since we need to inspect Location headers (to pull the
// authorization code) rather than following them automatically.
func newHTTPClient() (*http.Client, error) {
	jar, err := cookiejar.New(&cookiejar.Options{PublicSuffixList: publicsuffix.List})
	if err != nil {
		return nil, err
	}
	return &http.Client{
		Jar: jar,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}, nil
}

func doGet(client *http.Client, rawURL string, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return client.Do(req)
}

func doPost(client *http.Client, rawURL, contentType, body string, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodPost, rawURL, strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", contentType)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return client.Do(req)
}

func resolveLocation(base string, resp *http.Response) (string, error) {
	loc := resp.Header.Get("Location")
	if loc == "" {
		return "", fmt.Errorf("no Location header in response from %s (status %d)", base, resp.StatusCode)
	}
	baseURL, err := url.Parse(base)
	if err != nil {
		return "", err
	}
	locURL, err := url.Parse(loc)
	if err != nil {
		return "", err
	}
	return baseURL.ResolveReference(locURL).String(), nil
}

func readBody(resp *http.Response) string {
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return string(b)
}

// Login performs the full PKCE authorization-code flow and returns an
// access token plus the pumper (patient) ID extracted from its JWT payload.
func Login(username, password string) (*AuthResult, error) {
	if username == "" || password == "" {
		return nil, fmt.Errorf("username and password are required")
	}

	client, err := newHTTPClient()
	if err != nil {
		return nil, fmt.Errorf("creating http client: %w", err)
	}

	codeVerifier, err := randomToken(32)
	if err != nil {
		return nil, err
	}
	challengeSum := sha256.Sum256([]byte(codeVerifier))
	codeChallenge := base64URLEncode(challengeSum[:])
	state, err := randomToken(16)
	if err != nil {
		return nil, err
	}
	nonce, err := randomToken(16)
	if err != nil {
		return nil, err
	}

	authorizeParams := url.Values{}
	authorizeParams.Set("client_id", clientID)
	authorizeParams.Set("code_challenge", codeChallenge)
	authorizeParams.Set("code_challenge_method", "S256")
	authorizeParams.Set("nonce", nonce)
	authorizeParams.Set("redirect_uri", redirectURI)
	authorizeParams.Set("response_mode", "query")
	authorizeParams.Set("response_type", "code")
	authorizeParams.Set("scope", scope)
	authorizeParams.Set("state", state)

	authorizeURL := accountsBase + "/connect/authorize?" + authorizeParams.Encode()

	// Step 1: kick off the authorize request -> expect redirect to SSO login.
	resp, err := doGet(client, authorizeURL, nil)
	if err != nil {
		return nil, fmt.Errorf("authorize request: %w", err)
	}
	if resp.StatusCode != http.StatusFound {
		return nil, fmt.Errorf("expected 302 from /connect/authorize, got %d: %s", resp.StatusCode, readBody(resp))
	}
	ssoURL, err := resolveLocation(authorizeURL, resp)
	resp.Body.Close()
	if err != nil {
		return nil, err
	}

	// Step 2: follow redirect to sso.tandemdiabetes.com to establish session cookies.
	resp, err = doGet(client, ssoURL, nil)
	if err != nil {
		return nil, fmt.Errorf("sso landing request: %w", err)
	}
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		next, err := resolveLocation(ssoURL, resp)
		resp.Body.Close()
		if err != nil {
			return nil, err
		}
		resp, err = doGet(client, next, nil)
		if err != nil {
			return nil, fmt.Errorf("sso redirect follow-up: %w", err)
		}
	}
	resp.Body.Close()

	// Step 3: challenge call, mirrors observed browser traffic.
	challengeURL := accountsBase + "/auth/challenge?loginHint=" + url.QueryEscape(username)
	resp, err = doGet(client, challengeURL, map[string]string{"Accept": "*/*"})
	if err != nil {
		return nil, fmt.Errorf("challenge request: %w", err)
	}
	resp.Body.Close()

	// Step 4: submit credentials.
	loginBody, err := json.Marshal(map[string]string{"username": username, "password": password})
	if err != nil {
		return nil, err
	}
	resp, err = doPost(client, accountsBase+"/login", "application/json", string(loginBody), map[string]string{
		"ClientId": loginClientID,
		"Origin":   "https://sso.tandemdiabetes.com",
	})
	if err != nil {
		return nil, fmt.Errorf("login request: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		body := readBody(resp)
		return nil, fmt.Errorf("login failed: %d %s", resp.StatusCode, body)
	}
	resp.Body.Close()

	// Step 5: re-hit authorize/callback now that the SSO session is authenticated;
	// it 302s back with the authorization code in the query string.
	callbackURL := accountsBase + "/connect/authorize/callback?" + authorizeParams.Encode()
	resp, err = doGet(client, callbackURL, nil)
	if err != nil {
		return nil, fmt.Errorf("authorize callback: %w", err)
	}
	if resp.StatusCode != http.StatusFound {
		return nil, fmt.Errorf("expected 302 with auth code, got %d: %s", resp.StatusCode, readBody(resp))
	}
	finalRedirect, err := resolveLocation(callbackURL, resp)
	resp.Body.Close()
	if err != nil {
		return nil, err
	}
	finalURL, err := url.Parse(finalRedirect)
	if err != nil {
		return nil, err
	}
	code := finalURL.Query().Get("code")
	if code == "" {
		return nil, fmt.Errorf("no authorization code found in callback redirect: %s", finalRedirect)
	}

	// Step 6: exchange the code + PKCE verifier for an access token.
	tokenParams := url.Values{}
	tokenParams.Set("client_id", clientID)
	tokenParams.Set("code_verifier", codeVerifier)
	tokenParams.Set("code", code)
	tokenParams.Set("grant_type", "authorization_code")
	tokenParams.Set("redirect_uri", redirectURI)

	resp, err = doPost(client, accountsBase+"/connect/token", "application/x-www-form-urlencoded", tokenParams.Encode(), nil)
	if err != nil {
		return nil, fmt.Errorf("token exchange: %w", err)
	}
	tokenBodyBytes, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("token exchange failed: %d %s", resp.StatusCode, string(tokenBodyBytes))
	}

	var tokenResp struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.Unmarshal(tokenBodyBytes, &tokenResp); err != nil {
		return nil, fmt.Errorf("parsing token response: %w", err)
	}
	if tokenResp.AccessToken == "" {
		return nil, fmt.Errorf("no access_token in token response")
	}

	pumperID, err := extractPumperID(tokenResp.AccessToken)
	if err != nil {
		return nil, fmt.Errorf("extracting pumper id from token: %w", err)
	}

	return &AuthResult{AccessToken: tokenResp.AccessToken, PumperID: pumperID, Client: client}, nil
}

// extractPumperID decodes the JWT payload (no signature verification needed;
// we trust the issuer we just talked to directly over HTTPS) to pull out
// the tandem_pumper_id claim.
func extractPumperID(jwt string) (string, error) {
	parts := strings.Split(jwt, ".")
	if len(parts) != 3 {
		return "", fmt.Errorf("malformed JWT")
	}
	payloadBytes, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return "", err
	}
	var claims struct {
		TandemPumperID string `json:"tandem_pumper_id"`
	}
	if err := json.Unmarshal(payloadBytes, &claims); err != nil {
		return "", err
	}
	if claims.TandemPumperID == "" {
		return "", fmt.Errorf("tandem_pumper_id claim missing")
	}
	return claims.TandemPumperID, nil
}

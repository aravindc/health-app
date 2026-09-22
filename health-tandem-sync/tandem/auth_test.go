package tandem

import (
	"encoding/base64"
	"encoding/json"
	"testing"
)

// makeJWT builds a fake (unsigned, unverified) JWT with the given claims
// object as its payload, matching what extractPumperID expects to decode.
func makeJWT(t *testing.T, claims interface{}) string {
	t.Helper()
	payload, err := json.Marshal(claims)
	if err != nil {
		t.Fatalf("marshaling claims: %v", err)
	}
	seg := base64.RawURLEncoding.EncodeToString(payload)
	return "header." + seg + ".signature"
}

func TestExtractPumperID(t *testing.T) {
	jwt := makeJWT(t, map[string]string{"tandem_pumper_id": "pumper-abc-123"})
	id, err := extractPumperID(jwt)
	if err != nil {
		t.Fatalf("extractPumperID failed: %v", err)
	}
	if id != "pumper-abc-123" {
		t.Errorf("expected pumper-abc-123, got %q", id)
	}
}

func TestExtractPumperID_MalformedJWT(t *testing.T) {
	if _, err := extractPumperID("not-a-jwt"); err == nil {
		t.Error("expected an error for a malformed JWT (wrong number of segments)")
	}
}

func TestExtractPumperID_InvalidBase64Payload(t *testing.T) {
	if _, err := extractPumperID("header.not!valid!base64.sig"); err == nil {
		t.Error("expected an error for an invalid base64 payload segment")
	}
}

func TestExtractPumperID_InvalidJSONPayload(t *testing.T) {
	seg := base64.RawURLEncoding.EncodeToString([]byte("not json"))
	if _, err := extractPumperID("header." + seg + ".sig"); err == nil {
		t.Error("expected an error for a non-JSON payload")
	}
}

func TestExtractPumperID_MissingClaim(t *testing.T) {
	jwt := makeJWT(t, map[string]string{"other_claim": "value"})
	if _, err := extractPumperID(jwt); err == nil {
		t.Error("expected an error when tandem_pumper_id claim is missing")
	}
}

func TestBase64URLEncode(t *testing.T) {
	// base64.RawURLEncoding is unpadded and uses -/_ instead of +/; verify
	// base64URLEncode wires up to it as expected.
	got := base64URLEncode([]byte{0xfb, 0xff, 0xfe})
	want := base64.RawURLEncoding.EncodeToString([]byte{0xfb, 0xff, 0xfe})
	if got != want {
		t.Errorf("base64URLEncode = %q, want %q", got, want)
	}
}

func TestRandomToken(t *testing.T) {
	tok, err := randomToken(32)
	if err != nil {
		t.Fatalf("randomToken failed: %v", err)
	}
	if tok == "" {
		t.Error("expected a non-empty token")
	}
	// Decoding should succeed and yield exactly the requested byte length.
	decoded, err := base64.RawURLEncoding.DecodeString(tok)
	if err != nil {
		t.Fatalf("token is not valid unpadded base64url: %v", err)
	}
	if len(decoded) != 32 {
		t.Errorf("expected 32 decoded bytes, got %d", len(decoded))
	}
}

func TestRandomToken_Unique(t *testing.T) {
	t1, err := randomToken(16)
	if err != nil {
		t.Fatalf("randomToken failed: %v", err)
	}
	t2, err := randomToken(16)
	if err != nil {
		t.Fatalf("randomToken failed: %v", err)
	}
	if t1 == t2 {
		t.Error("expected two random tokens to differ")
	}
}

func TestLogin_RequiresCredentials(t *testing.T) {
	if _, err := Login("", "password"); err == nil {
		t.Error("expected an error for empty username")
	}
	if _, err := Login("user", ""); err == nil {
		t.Error("expected an error for empty password")
	}
	if _, err := Login("", ""); err == nil {
		t.Error("expected an error for both empty")
	}
}

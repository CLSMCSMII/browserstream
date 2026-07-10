package core

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base64"
	"strconv"
	"strings"
	"testing"
)

func TestTURNCredentialsUseCoturnRESTAlgorithm(t *testing.T) {
	username, credential, err := GenerateTURNCredentials("shared-secret", 600, 1700000000)
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(username, ":")
	if len(parts) != 2 || parts[0] != strconv.FormatInt(1700000600, 10) {
		t.Fatalf("unexpected username %q", username)
	}
	mac := hmac.New(sha1.New, []byte("shared-secret"))
	_, _ = mac.Write([]byte(username))
	want := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	if credential != want {
		t.Fatalf("credential mismatch: got %q want %q", credential, want)
	}
}

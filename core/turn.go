package core

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"fmt"
)

func GenerateTURNCredentials(secret string, ttl, now int64) (string, string, error) {
	if secret == "" || ttl <= 0 {
		return "", "", fmt.Errorf("TURN secret and positive TTL are required")
	}
	random := make([]byte, 12)
	if _, err := rand.Read(random); err != nil {
		return "", "", err
	}
	username := fmt.Sprintf("%d:%s", now+ttl, base64.RawURLEncoding.EncodeToString(random))
	mac := hmac.New(sha1.New, []byte(secret))
	_, _ = mac.Write([]byte(username))
	return username, base64.StdEncoding.EncodeToString(mac.Sum(nil)), nil
}

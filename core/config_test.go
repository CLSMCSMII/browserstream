package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func validTestConfig() Config {
	return Config{
		AppName:        "Meeting",
		PublicURL:      "https://meet.example.com",
		ListenAddress:  "127.0.0.1:8080",
		Rooms:          []RoomConfig{{ID: "board-room", Label: "Board Room", DisplayToken: "long-random-display-token"}},
		TURN:           TURNConfig{URLs: []string{"turn:turn.example.com:3478?transport=udp"}, TransportPolicy: "relay", SharedSecret: "long-random-turn-secret", CredentialTTLSeconds: 600},
		Coturn:         CoturnConfig{Realm: "meet.example.com", MinPort: 49160, MaxPort: 49200},
		AllowedOrigins: []string{"https://meet.example.com"},
		Limits:         LimitsConfig{MaxMessageBytes: 65536, ReadTimeoutSeconds: 30, WriteTimeoutSeconds: 10, IdleTimeoutSeconds: 60, MaxFailedAuth: 3, LockoutSeconds: 60},
	}
}

func TestConfigValidation(t *testing.T) {
	cfg := validTestConfig()
	if err := cfg.Validate(); err != nil {
		t.Fatalf("valid config rejected: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*Config)
		want   string
	}{
		{"duplicate room", func(c *Config) { c.Rooms = append(c.Rooms, c.Rooms[0]) }, "duplicate"},
		{"bad room id", func(c *Config) { c.Rooms[0].ID = "Bad Room" }, "room id"},
		{"bad public url", func(c *Config) { c.PublicURL = "javascript:alert(1)" }, "public_url"},
		{"public URL path", func(c *Config) { c.PublicURL = "https://meet.example.com/subpath" }, "public_url"},
		{"missing display token", func(c *Config) { c.Rooms[0].DisplayToken = "" }, "display_token"},
		{"placeholder display token", func(c *Config) { c.Rooms[0].DisplayToken = "GENERATE_A_RANDOM_DISPLAY_TOKEN" }, "display_token"},
		{"duplicate display token", func(c *Config) {
			c.Rooms = append(c.Rooms, RoomConfig{ID: "other", Label: "Other", DisplayToken: c.Rooms[0].DisplayToken})
		}, "unique"},
		{"bad origin", func(c *Config) { c.AllowedOrigins = []string{"*"} }, "allowed_origins"},
		{"origin query", func(c *Config) { c.AllowedOrigins = []string{"https://meet.example.com?x=1"} }, "allowed_origins"},
		{"bad trusted proxy", func(c *Config) { c.TrustedProxyCIDRs = []string{"not-a-cidr"} }, "trusted_proxy_cidrs"},
		{"bad turn url", func(c *Config) { c.TURN.URLs = []string{"http://turn.example.com"} }, "turn"},
		{"turn URL credentials", func(c *Config) { c.TURN.URLs = []string{"turn:user:pass@turn.example.com"} }, "credentials"},
		{"turn URL query", func(c *Config) { c.TURN.URLs = []string{"turn:turn.example.com?secret=value"} }, "invalid query"},
		{"missing turn secret", func(c *Config) { c.TURN.SharedSecret = "" }, "shared_secret"},
		{"bad coturn IP", func(c *Config) { c.Coturn.ExternalIP = "not-an-ip" }, "coturn.external_ip"},
		{"bad relay range", func(c *Config) { c.Coturn.MinPort = 50000; c.Coturn.MaxPort = 49000 }, "port range"},
		{"relative TLS paths", func(c *Config) { c.TLS = TLSConfig{CertFile: "cert.pem", KeyFile: "key.pem"} }, "absolute"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c := validTestConfig()
			tc.mutate(&c)
			err := c.Validate()
			if err == nil || !strings.Contains(strings.ToLower(err.Error()), tc.want) {
				t.Fatalf("got %v, want error containing %q", err, tc.want)
			}
		})
	}
}

func TestSafeConfigDoesNotExposeSecrets(t *testing.T) {
	cfg := validTestConfig()
	safe, err := cfg.SafeConfig(1700000000)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(safe), cfg.TURN.SharedSecret) || strings.Contains(string(safe), cfg.Rooms[0].DisplayToken) || strings.Contains(string(safe), "ice_servers") {
		t.Fatal("safe config exposed a secret or unauthenticated ICE configuration")
	}
	ice, err := cfg.AuthenticatedICEConfig(1700000000)
	if err != nil || !strings.Contains(string(ice), "ice_servers") || !strings.Contains(string(ice), "username") {
		t.Fatalf("authenticated ICE config unavailable: %s (%v)", ice, err)
	}
	if !strings.Contains(string(safe), "Board Room") {
		t.Fatal("safe config omitted room label")
	}
}

func TestSTUNOnlyDoesNotRequireOrExposeCredentials(t *testing.T) {
	cfg := validTestConfig()
	cfg.TURN.URLs = []string{"stun:stun.example.com:3478"}
	cfg.TURN.TransportPolicy = "all"
	cfg.TURN.SharedSecret = ""
	if err := cfg.Validate(); err != nil {
		t.Fatalf("STUN-only config rejected: %v", err)
	}
	b, err := cfg.SafeConfig(1700000000)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), "username") || strings.Contains(string(b), "credential") {
		t.Fatalf("STUN-only config contains credentials: %s", b)
	}
}

func TestLoadConfigRejectsTrailingJSON(t *testing.T) {
	b, err := json.Marshal(validTestConfig())
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(p, append(b, []byte("\n{}")...), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(p); err == nil || !strings.Contains(err.Error(), "trailing") {
		t.Fatalf("expected trailing-data error, got %v", err)
	}
}

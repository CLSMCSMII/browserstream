package core

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var roomIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

type Config struct {
	AppName           string       `json:"app_name"`
	PublicURL         string       `json:"public_url"`
	ListenAddress     string       `json:"listen_address"`
	Rooms             []RoomConfig `json:"rooms"`
	TURN              TURNConfig   `json:"turn"`
	Coturn            CoturnConfig `json:"coturn"`
	AllowedOrigins    []string     `json:"allowed_origins"`
	TrustedProxyCIDRs []string     `json:"trusted_proxy_cidrs"`
	Debug             bool         `json:"debug"`
	Limits            LimitsConfig `json:"limits"`
	TLS               TLSConfig    `json:"tls"`
}

type RoomConfig struct {
	ID           string `json:"id"`
	Label        string `json:"label"`
	DisplayToken string `json:"display_token"`
}
type TURNConfig struct {
	URLs                 []string `json:"urls"`
	TransportPolicy      string   `json:"transport_policy"`
	SharedSecret         string   `json:"shared_secret"`
	CredentialTTLSeconds int64    `json:"credential_ttl_seconds"`
}
type CoturnConfig struct {
	Realm       string `json:"realm"`
	ListeningIP string `json:"listening_ip"`
	RelayIP     string `json:"relay_ip"`
	ExternalIP  string `json:"external_ip"`
	MinPort     int    `json:"min_port"`
	MaxPort     int    `json:"max_port"`
}
type LimitsConfig struct {
	MaxMessageBytes     int64 `json:"max_message_bytes"`
	ReadTimeoutSeconds  int   `json:"read_timeout_seconds"`
	WriteTimeoutSeconds int   `json:"write_timeout_seconds"`
	IdleTimeoutSeconds  int   `json:"idle_timeout_seconds"`
	MaxFailedAuth       int   `json:"max_failed_auth"`
	LockoutSeconds      int   `json:"lockout_seconds"`
}
type TLSConfig struct {
	CertFile string `json:"cert_file"`
	KeyFile  string `json:"key_file"`
}

type PublicRoom struct {
	ID    string `json:"id"`
	Label string `json:"label"`
}
type ICEConfig struct {
	URLs       []string `json:"urls"`
	Username   string   `json:"username,omitempty"`
	Credential string   `json:"credential,omitempty"`
}
type FrontendConfig struct {
	AppName   string       `json:"app_name"`
	PublicURL string       `json:"public_url"`
	Rooms     []PublicRoom `json:"rooms"`
	Debug     bool         `json:"debug"`
}
type AuthenticatedICEConfig struct {
	ICETransportPolicy string      `json:"ice_transport_policy"`
	ICEServers         []ICEConfig `json:"ice_servers"`
}

func LoadConfig(path string) (Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read config: %w", err)
	}
	var c Config
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.DisallowUnknownFields()
	if err := d.Decode(&c); err != nil {
		return c, fmt.Errorf("parse config: %w", err)
	}
	if err := d.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return c, errors.New("parse config: trailing data after JSON object")
	}
	if err := c.Validate(); err != nil {
		return c, err
	}
	return c, nil
}

func (c Config) Validate() error {
	if strings.TrimSpace(c.AppName) == "" {
		return errors.New("app_name is required")
	}
	u, err := url.Parse(c.PublicURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("public_url must be an absolute HTTP(S) URL without credentials, query, or fragment")
	}
	if _, _, err := net.SplitHostPort(c.ListenAddress); err != nil {
		return fmt.Errorf("listen_address: %w", err)
	}
	if len(c.Rooms) == 0 || len(c.Rooms) > 100 {
		return errors.New("rooms must contain between 1 and 100 entries")
	}
	seen := map[string]bool{}
	seenTokens := map[string]bool{}
	for i, r := range c.Rooms {
		if !roomIDPattern.MatchString(r.ID) {
			return fmt.Errorf("room id %d is invalid", i)
		}
		if seen[r.ID] {
			return fmt.Errorf("duplicate room id %q", r.ID)
		}
		seen[r.ID] = true
		if strings.TrimSpace(r.Label) == "" || len(r.Label) > 100 {
			return fmt.Errorf("room %q label is required and must be at most 100 characters", r.ID)
		}
		if len(r.DisplayToken) < 16 || strings.HasPrefix(r.DisplayToken, "GENERATE_") || strings.HasPrefix(r.DisplayToken, "CHANGE_") {
			return fmt.Errorf("room %q display_token must be a generated secret of at least 16 characters", r.ID)
		}
		if seenTokens[r.DisplayToken] {
			return errors.New("room display_tokens must be unique")
		}
		seenTokens[r.DisplayToken] = true
	}
	if len(c.AllowedOrigins) == 0 {
		return errors.New("allowed_origins is required")
	}
	for _, o := range c.AllowedOrigins {
		u, e := url.Parse(o)
		if e != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || o == "*" {
			return fmt.Errorf("allowed_origins contains invalid origin %q", o)
		}
	}
	if len(c.TrustedProxyCIDRs) > 20 {
		return errors.New("trusted_proxy_cidrs may contain at most 20 entries")
	}
	for _, raw := range c.TrustedProxyCIDRs {
		if _, _, e := net.ParseCIDR(raw); e != nil {
			return fmt.Errorf("trusted_proxy_cidrs contains invalid CIDR %q", raw)
		}
	}
	if c.TURN.TransportPolicy != "all" && c.TURN.TransportPolicy != "relay" {
		return errors.New("turn transport_policy must be all or relay")
	}
	hasTURN := false
	for _, raw := range c.TURN.URLs {
		u, e := url.Parse(raw)
		if e != nil || (u.Scheme != "turn" && u.Scheme != "turns" && u.Scheme != "stun" && u.Scheme != "stuns") || u.Opaque == "" || u.Fragment != "" {
			return fmt.Errorf("turn URL %q is invalid", raw)
		}
		hostURL, e := url.Parse("//" + u.Opaque)
		if e != nil || hostURL.Hostname() == "" || hostURL.User != nil || hostURL.Path != "" {
			return fmt.Errorf("turn URL %q must not contain credentials or a path", raw)
		}
		if u.RawQuery != "" {
			q, e := url.ParseQuery(u.RawQuery)
			values, ok := q["transport"]
			if e != nil || !ok || len(q) != 1 || len(values) != 1 || (values[0] != "udp" && values[0] != "tcp") {
				return fmt.Errorf("turn URL %q has an invalid query", raw)
			}
		}
		if u.Scheme == "turn" || u.Scheme == "turns" {
			hasTURN = true
		}
	}
	if c.TURN.TransportPolicy == "relay" && !hasTURN {
		return errors.New("turn transport_policy relay requires at least one turn: or turns: URL")
	}
	if hasTURN && len(c.TURN.SharedSecret) < 16 {
		return errors.New("turn shared_secret must be at least 16 characters")
	}
	if hasTURN && (c.TURN.CredentialTTLSeconds < 60 || c.TURN.CredentialTTLSeconds > 86400) {
		return errors.New("turn credential_ttl_seconds must be between 60 and 86400")
	}
	if strings.TrimSpace(c.Coturn.Realm) == "" || strings.ContainsAny(c.Coturn.Realm, " \t\r\n") {
		return errors.New("coturn.realm is required and must not contain whitespace")
	}
	for name, value := range map[string]string{"listening_ip": c.Coturn.ListeningIP, "relay_ip": c.Coturn.RelayIP, "external_ip": c.Coturn.ExternalIP} {
		if value != "" && net.ParseIP(value) == nil {
			return fmt.Errorf("coturn.%s must be an IP address", name)
		}
	}
	if c.Coturn.MinPort < 1024 || c.Coturn.MaxPort > 65535 || c.Coturn.MinPort > c.Coturn.MaxPort {
		return errors.New("coturn relay port range is invalid")
	}
	if c.Limits.MaxMessageBytes < 1024 || c.Limits.MaxMessageBytes > 1048576 {
		return errors.New("limits.max_message_bytes must be between 1024 and 1048576")
	}
	if c.Limits.ReadTimeoutSeconds < 1 || c.Limits.WriteTimeoutSeconds < 1 || c.Limits.IdleTimeoutSeconds < 5 || c.Limits.MaxFailedAuth < 1 || c.Limits.LockoutSeconds < 1 {
		return errors.New("all operational limits must be positive and idle timeout at least 5 seconds")
	}
	if (c.TLS.CertFile == "") != (c.TLS.KeyFile == "") {
		return errors.New("tls cert_file and key_file must be set together")
	}
	if c.TLS.CertFile != "" && (!filepath.IsAbs(c.TLS.CertFile) || !filepath.IsAbs(c.TLS.KeyFile)) {
		return errors.New("tls cert_file and key_file must be absolute external paths")
	}
	return nil
}

func (c Config) Room(id string) (RoomConfig, bool) {
	for _, r := range c.Rooms {
		if r.ID == id {
			return r, true
		}
	}
	return RoomConfig{}, false
}
func (c Config) OriginAllowed(origin string) bool {
	for _, o := range c.AllowedOrigins {
		if origin == o {
			return true
		}
	}
	return false
}
func (c Config) SafeConfig(_ int64) ([]byte, error) {
	out := FrontendConfig{AppName: c.AppName, PublicURL: c.PublicURL, Debug: c.Debug}
	for _, r := range c.Rooms {
		out.Rooms = append(out.Rooms, PublicRoom{ID: r.ID, Label: r.Label})
	}
	return json.Marshal(out)
}
func (c Config) AuthenticatedICEConfig(now int64) ([]byte, error) {
	out := AuthenticatedICEConfig{ICETransportPolicy: c.TURN.TransportPolicy}
	var stunURLs, turnURLs []string
	for _, raw := range c.TURN.URLs {
		u, _ := url.Parse(raw)
		if u.Scheme == "turn" || u.Scheme == "turns" {
			turnURLs = append(turnURLs, raw)
		} else {
			stunURLs = append(stunURLs, raw)
		}
	}
	if len(stunURLs) > 0 {
		out.ICEServers = append(out.ICEServers, ICEConfig{URLs: stunURLs})
	}
	if len(turnURLs) > 0 {
		user, cred, err := GenerateTURNCredentials(c.TURN.SharedSecret, c.TURN.CredentialTTLSeconds, now)
		if err != nil {
			return nil, err
		}
		out.ICEServers = append(out.ICEServers, ICEConfig{URLs: turnURLs, Username: user, Credential: cred})
	}
	return json.Marshal(out)
}

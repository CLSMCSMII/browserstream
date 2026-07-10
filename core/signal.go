package core

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type WSMessage struct {
	SessionID string `json:"SessionID,omitempty"`
	Type      string `json:"Type"`
	Value     string `json:"Value,omitempty"`
}
type authState struct {
	failures    int
	lockedUntil time.Time
	updatedAt   time.Time
}
type App struct {
	cfg             Config
	rooms           *RoomStore
	authMu          sync.Mutex
	auth            map[string]authState
	trustedProxies  []*net.IPNet
	lastAuthCleanup time.Time
	preAuth         chan struct{}
	upgrader        websocket.Upgrader
	now             func() time.Time
}

func NewApp(cfg Config) *App {
	a := &App{cfg: cfg, rooms: NewRoomStore(), auth: map[string]authState{}, preAuth: make(chan struct{}, 128), now: time.Now}
	for _, raw := range cfg.TrustedProxyCIDRs {
		if _, network, err := net.ParseCIDR(raw); err == nil {
			a.trustedProxies = append(a.trustedProxies, network)
		}
	}
	a.upgrader = websocket.Upgrader{ReadBufferSize: 4096, WriteBufferSize: 4096, CheckOrigin: func(r *http.Request) bool { return cfg.OriginAllowed(r.Header.Get("Origin")) }}
	return a
}
func GetHttp(cfg Config) http.Handler { return NewApp(cfg).Handler() }

var wsWriteLocks sync.Map

func (a *App) write(conn *websocket.Conn, v any) error {
	l, _ := wsWriteLocks.LoadOrStore(conn, &sync.Mutex{})
	m := l.(*sync.Mutex)
	m.Lock()
	defer m.Unlock()
	_ = conn.SetWriteDeadline(a.now().Add(time.Duration(a.cfg.Limits.WriteTimeoutSeconds) * time.Second))
	return conn.WriteJSON(v)
}
func closeWS(c *websocket.Conn) { wsWriteLocks.Delete(c); _ = c.Close() }
func secureEqual(a, b string) bool {
	return len(a) == len(b) && subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

func (a *App) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("files/static"))))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", 405)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/api/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", 405)
			return
		}
		b, e := a.cfg.SafeConfig(a.now().Unix())
		if e != nil {
			http.Error(w, "configuration unavailable", 500)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write(b)
	})
	mux.HandleFunc("/ws_display", a.handleDisplay)
	mux.HandleFunc("/ws_present", a.handlePresenter)
	serveUI := func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", 405)
			return
		}
		http.ServeFile(w, r, "files/main.html")
	}
	mux.HandleFunc("/room/", serveUI)
	mux.HandleFunc("/", serveUI)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
		w.Header().Set("Permissions-Policy", "camera=(), geolocation=(), microphone=(), display-capture=(self)")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		mux.ServeHTTP(w, r)
	})
}

func (a *App) upgrade(w http.ResponseWriter, r *http.Request) (*websocket.Conn, bool) {
	c, e := a.upgrader.Upgrade(w, r, nil)
	if e != nil {
		return nil, false
	}
	c.SetReadLimit(a.cfg.Limits.MaxMessageBytes)
	return c, true
}
func decodeMessage(c *websocket.Conn, m *WSMessage) error {
	_, data, err := c.ReadMessage()
	if err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(m); err != nil {
		return err
	}
	if err := d.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("message must contain exactly one JSON object")
	}
	return nil
}
func (a *App) read(c *websocket.Conn, m *WSMessage) error {
	_ = c.SetReadDeadline(a.now().Add(time.Duration(a.cfg.Limits.IdleTimeoutSeconds) * time.Second))
	return decodeMessage(c, m)
}

func (a *App) startHeartbeat(c *websocket.Conn) func() {
	idle := time.Duration(a.cfg.Limits.IdleTimeoutSeconds) * time.Second
	c.SetPongHandler(func(string) error { return c.SetReadDeadline(a.now().Add(idle)) })
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(idle / 2)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				deadline := a.now().Add(time.Duration(a.cfg.Limits.WriteTimeoutSeconds) * time.Second)
				if err := c.WriteControl(websocket.PingMessage, nil, deadline); err != nil {
					_ = c.Close()
					return
				}
			case <-done:
				return
			}
		}
	}()
	return func() { close(done) }
}
func (a *App) initialAuth(c *websocket.Conn) (string, bool) {
	select {
	case a.preAuth <- struct{}{}:
		defer func() { <-a.preAuth }()
	default:
		_ = a.write(c, WSMessage{Type: "serverBusy"})
		return "", false
	}
	_ = c.SetReadDeadline(a.now().Add(time.Duration(a.cfg.Limits.ReadTimeoutSeconds) * time.Second))
	var m WSMessage
	if e := decodeMessage(c, &m); e != nil {
		return "", false
	}
	if m.Type != "auth" || m.Value == "" || m.SessionID != "" {
		return "", false
	}
	return strings.TrimSpace(m.Value), true
}

func (a *App) handleDisplay(w http.ResponseWriter, r *http.Request) {
	roomCfg, ok := a.cfg.Room(r.URL.Query().Get("id"))
	if !ok {
		http.Error(w, "unknown room", 404)
		return
	}
	c, ok := a.upgrade(w, r)
	if !ok {
		return
	}
	defer closeWS(c)
	token, ok := a.initialAuth(c)
	if !ok || !secureEqual(token, roomCfg.DisplayToken) {
		_ = a.write(c, WSMessage{Type: "unauthorized"})
		return
	}
	stopHeartbeat := a.startHeartbeat(c)
	defer stopHeartbeat()
	room, oldDisplay, stalePresenters := a.rooms.SetDisplay(roomCfg.ID, c)
	if oldDisplay != nil {
		closeWS(oldDisplay)
	}
	for _, presenter := range stalePresenters {
		closeWS(presenter)
	}
	defer func() {
		for _, presenter := range a.rooms.RemoveDisplay(room.ID, c) {
			closeWS(presenter)
		}
	}()
	ice, err := a.cfg.AuthenticatedICEConfig(a.now().Unix())
	if err != nil || a.write(c, WSMessage{Type: "iceConfig", Value: string(ice)}) != nil {
		return
	}
	if a.write(c, WSMessage{Type: "displayReady", Value: room.ID, SessionID: room.Code()}) != nil {
		return
	}
	for {
		var m WSMessage
		if a.read(c, &m) != nil {
			return
		}
		if m.Type != "addCalleeIceCandidate" && m.Type != "gotAnswer" {
			_ = a.write(c, WSMessage{Type: "unsupportedMessage"})
			continue
		}
		s := room.Session(m.SessionID)
		if s == nil {
			_ = a.write(c, WSMessage{Type: "invalidSession"})
			continue
		}
		if e := a.write(s.CallerConn, m); e != nil {
			log.Printf("display relay failed: %v", e)
		}
	}
}

func remoteHost(r *http.Request) string {
	host, _, e := net.SplitHostPort(r.RemoteAddr)
	if e == nil {
		return host
	}
	return r.RemoteAddr
}
func (a *App) isTrustedProxy(ip net.IP) bool {
	for _, network := range a.trustedProxies {
		if network.Contains(ip) {
			return true
		}
	}
	return false
}
func (a *App) clientHost(r *http.Request) string {
	peer := remoteHost(r)
	if !a.isTrustedProxy(net.ParseIP(peer)) {
		return peer
	}
	forwarded := strings.Split(r.Header.Get("X-Forwarded-For"), ",")
	for i := len(forwarded) - 1; i >= 0; i-- {
		candidate := strings.TrimSpace(forwarded[i])
		ip := net.ParseIP(candidate)
		if ip != nil && !a.isTrustedProxy(ip) {
			return ip.String()
		}
	}
	return peer
}
func (a *App) authKey(room string, r *http.Request) string { return room + "|" + a.clientHost(r) }
func (a *App) locked(key string) bool {
	a.authMu.Lock()
	defer a.authMu.Unlock()
	s := a.auth[key]
	return a.now().Before(s.lockedUntil)
}
func (a *App) authFailed(key string) bool {
	a.authMu.Lock()
	defer a.authMu.Unlock()
	now := a.now()
	lockout := time.Duration(a.cfg.Limits.LockoutSeconds) * time.Second
	if a.lastAuthCleanup.IsZero() || now.Sub(a.lastAuthCleanup) >= lockout {
		for k, state := range a.auth {
			if now.Sub(state.updatedAt) >= 2*lockout {
				delete(a.auth, k)
			}
		}
		a.lastAuthCleanup = now
	}
	s := a.auth[key]
	if !s.lockedUntil.IsZero() && !now.Before(s.lockedUntil) {
		s = authState{}
	}
	s.failures++
	s.updatedAt = now
	if s.failures >= a.cfg.Limits.MaxFailedAuth {
		s.lockedUntil = now.Add(lockout)
	}
	a.auth[key] = s
	return !s.lockedUntil.IsZero()
}
func (a *App) authSucceeded(key string) { a.authMu.Lock(); delete(a.auth, key); a.authMu.Unlock() }

func (a *App) handlePresenter(w http.ResponseWriter, r *http.Request) {
	roomCfg, ok := a.cfg.Room(r.URL.Query().Get("id"))
	if !ok {
		http.Error(w, "unknown room", 404)
		return
	}
	c, ok := a.upgrade(w, r)
	if !ok {
		return
	}
	defer closeWS(c)
	key := a.authKey(roomCfg.ID, r)
	if a.locked(key) {
		_ = a.write(c, WSMessage{Type: "lockedOut"})
		return
	}
	code, ok := a.initialAuth(c)
	if !ok {
		return
	}
	room := a.rooms.Get(roomCfg.ID)
	if room == nil || room.Display() == nil {
		_ = a.write(c, WSMessage{Type: "displayNotFound"})
		return
	}
	display, generation, newCode, authorized := room.AuthorizePresenter(strings.ToUpper(code), c)
	if !authorized {
		typ := "invalidCode"
		if room.Display() != nil && room.HasPresenter() {
			typ = "presenterBusy"
		} else if a.authFailed(key) {
			typ = "lockedOut"
		}
		_ = a.write(c, WSMessage{Type: typ})
		return
	}
	a.authSucceeded(key)
	defer func() {
		d, sessionID, code := room.ReleasePresenter(c)
		if d != nil {
			_ = a.write(d, WSMessage{Type: "presenterClosed", SessionID: sessionID, Value: code})
		}
	}()
	stopHeartbeat := a.startHeartbeat(c)
	defer stopHeartbeat()
	_ = a.write(display, WSMessage{Type: "refreshCode", Value: newCode})
	ice, err := a.cfg.AuthenticatedICEConfig(a.now().Unix())
	if err != nil || a.write(c, WSMessage{Type: "iceConfig", Value: string(ice)}) != nil {
		return
	}
	if a.write(c, WSMessage{Type: "authAccepted"}) != nil {
		return
	}
	var start WSMessage
	if a.read(c, &start) != nil || start.Type != "startSession" || start.SessionID != "" || start.Value != "" {
		_ = a.write(c, WSMessage{Type: "unsupportedMessage"})
		return
	}
	session, ok := room.StartSession(c, generation)
	if !ok {
		_ = a.write(c, WSMessage{Type: "displayNotFound"})
		return
	}
	msg := WSMessage{Type: "newSession", SessionID: session.ID, Value: session.ID}
	if a.write(display, msg) != nil {
		_ = a.write(c, WSMessage{Type: "displayNotFound"})
		return
	}
	if a.write(c, msg) != nil {
		return
	}
	for {
		var m WSMessage
		if a.read(c, &m) != nil {
			return
		}
		if m.SessionID != session.ID {
			_ = a.write(c, WSMessage{Type: "invalidSession"})
			continue
		}
		if m.Type != "addCallerIceCandidate" && m.Type != "gotOffer" {
			_ = a.write(c, WSMessage{Type: "unsupportedMessage"})
			continue
		}
		if e := a.write(display, m); e != nil {
			return
		}
	}
}

package core

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestSecurityHeaders(t *testing.T) {
	ts := httptest.NewServer(NewApp(validTestConfig()).Handler())
	defer ts.Close()
	resp, err := http.Get(ts.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	for _, name := range []string{"Content-Security-Policy", "Permissions-Policy", "Referrer-Policy", "X-Content-Type-Options", "X-Frame-Options"} {
		if resp.Header.Get(name) == "" {
			t.Errorf("missing %s", name)
		}
	}
}

func dialWS(t *testing.T, serverURL, path, origin string) (*websocket.Conn, *httptest.ResponseRecorder) {
	t.Helper()
	header := map[string][]string{"Origin": {origin}}
	conn, resp, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(serverURL, "http")+path, header)
	if err != nil {
		t.Fatalf("dial %s: %v (response %v)", path, err, resp)
	}
	return conn, nil
}

func readType(t *testing.T, c *websocket.Conn) WSMessage {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		var msg WSMessage
		if err := c.ReadJSON(&msg); err != nil {
			t.Fatal(err)
		}
		if msg.Type != "iceConfig" && msg.Type != "refreshCode" {
			return msg
		}
	}
}

func TestConfiguredRoomsAndAuthenticationFlow(t *testing.T) {
	cfg := validTestConfig()
	app := NewApp(cfg)
	ts := httptest.NewServer(app.Handler())
	defer ts.Close()
	display, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display.Close()
	if err := display.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken}); err != nil {
		t.Fatal(err)
	}
	ready := readType(t, display)
	if ready.Type != "displayReady" || len(ready.SessionID) != 6 {
		t.Fatalf("unexpected display response: %+v", ready)
	}

	presenter, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	defer presenter.Close()
	if err := presenter.WriteJSON(WSMessage{Type: "auth", Value: ready.SessionID}); err != nil {
		t.Fatal(err)
	}
	if msg := readType(t, presenter); msg.Type != "authAccepted" {
		t.Fatalf("presenter was not authenticated: %+v", msg)
	}
	if err := presenter.WriteJSON(WSMessage{Type: "startSession"}); err != nil {
		t.Fatal(err)
	}
	if msg := readType(t, presenter); msg.Type != "newSession" {
		t.Fatalf("unexpected presenter response: %+v", msg)
	}
	if msg := readType(t, display); msg.Type != "newSession" {
		t.Fatalf("display did not receive session: %+v", msg)
	}
}

func TestDisplayTokenPreventsTakeover(t *testing.T) {
	cfg := validTestConfig()
	ts := httptest.NewServer(NewApp(cfg).Handler())
	defer ts.Close()
	c, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer c.Close()
	_ = c.WriteJSON(WSMessage{Type: "auth", Value: "wrong-token"})
	if msg := readType(t, c); msg.Type != "unauthorized" {
		t.Fatalf("got %+v", msg)
	}
}

func TestPresenterCodeIsNotAcceptedInQueryAndFailuresLockOut(t *testing.T) {
	cfg := validTestConfig()
	cfg.Limits.MaxFailedAuth = 2
	app := NewApp(cfg)
	ts := httptest.NewServer(app.Handler())
	defer ts.Close()
	display, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display.Close()
	_ = display.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken})
	ready := readType(t, display)
	for i := 0; i < 2; i++ {
		c, _ := dialWS(t, ts.URL, "/ws_present?id=board-room&code="+ready.SessionID, cfg.AllowedOrigins[0])
		_ = c.WriteJSON(WSMessage{Type: "auth", Value: "wrong"})
		msg := readType(t, c)
		_ = c.Close()
		if i == 1 && msg.Type != "lockedOut" {
			t.Fatalf("expected lockout, got %+v", msg)
		}
	}
	c, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	defer c.Close()
	_ = c.WriteJSON(WSMessage{Type: "auth", Value: ready.SessionID})
	if msg := readType(t, c); msg.Type != "lockedOut" {
		t.Fatalf("valid code bypassed lockout: %+v", msg)
	}
}

func TestRejectsDisallowedOriginAndUnsupportedMessage(t *testing.T) {
	cfg := validTestConfig()
	ts := httptest.NewServer(NewApp(cfg).Handler())
	defer ts.Close()
	header := map[string][]string{"Origin": {"https://evil.example"}}
	_, resp, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(ts.URL, "http")+"/ws_display?id=board-room", header)
	if err == nil || resp == nil || resp.StatusCode != 403 {
		t.Fatalf("disallowed origin accepted: err=%v response=%v", err, resp)
	}

	display, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display.Close()
	_ = display.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken})
	_ = readType(t, display)
	_ = display.WriteJSON(WSMessage{Type: "deleteEverything"})
	if msg := readType(t, display); msg.Type != "unsupportedMessage" {
		t.Fatalf("got %+v", msg)
	}
}

func TestDisplayReplacementClosesPresenterAndPreservesNewCode(t *testing.T) {
	cfg := validTestConfig()
	ts := httptest.NewServer(NewApp(cfg).Handler())
	defer ts.Close()

	display1, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display1.Close()
	_ = display1.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken})
	ready1 := readType(t, display1)
	presenter1, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	defer presenter1.Close()
	_ = presenter1.WriteJSON(WSMessage{Type: "auth", Value: ready1.SessionID})
	if msg := readType(t, presenter1); msg.Type != "authAccepted" {
		t.Fatalf("got %+v", msg)
	}
	_ = presenter1.WriteJSON(WSMessage{Type: "startSession"})
	_ = readType(t, presenter1)
	_ = readType(t, display1)

	display2, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display2.Close()
	_ = display2.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken})
	ready2 := readType(t, display2)
	_ = presenter1.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, _, err := presenter1.ReadMessage(); err == nil {
		t.Fatal("replaced display left old presenter connected")
	}

	presenter2, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	defer presenter2.Close()
	_ = presenter2.WriteJSON(WSMessage{Type: "auth", Value: ready2.SessionID})
	if msg := readType(t, presenter2); msg.Type != "authAccepted" {
		t.Fatalf("new display code was invalidated: %+v", msg)
	}
}

func TestTrustedProxyClientAddress(t *testing.T) {
	cfg := validTestConfig()
	cfg.TrustedProxyCIDRs = []string{"127.0.0.1/32"}
	app := NewApp(cfg)
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "127.0.0.1:1234"
	r.Header.Set("X-Forwarded-For", "198.51.100.9, 127.0.0.1")
	if got := app.clientHost(r); got != "198.51.100.9" {
		t.Fatalf("trusted proxy client = %q", got)
	}
	r.RemoteAddr = "203.0.113.5:1234"
	if got := app.clientHost(r); got != "203.0.113.5" {
		t.Fatalf("untrusted peer spoofed client = %q", got)
	}
}

func TestPresenterCodeIsSingleUseAndRoomIsExclusive(t *testing.T) {
	cfg := validTestConfig()
	ts := httptest.NewServer(NewApp(cfg).Handler())
	defer ts.Close()
	display, _ := dialWS(t, ts.URL, "/ws_display?id=board-room", cfg.AllowedOrigins[0])
	defer display.Close()
	_ = display.WriteJSON(WSMessage{Type: "auth", Value: cfg.Rooms[0].DisplayToken})
	ready := readType(t, display)

	presenter1, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	_ = presenter1.WriteJSON(WSMessage{Type: "auth", Value: ready.SessionID})
	if msg := readType(t, presenter1); msg.Type != "authAccepted" {
		t.Fatalf("first presenter: %+v", msg)
	}

	presenter2, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	_ = presenter2.WriteJSON(WSMessage{Type: "auth", Value: ready.SessionID})
	if msg := readType(t, presenter2); msg.Type != "presenterBusy" {
		t.Fatalf("second presenter: %+v", msg)
	}
	_ = presenter2.Close()
	_ = presenter1.Close()
	if msg := readType(t, display); msg.Type != "presenterClosed" {
		t.Fatalf("display cleanup: %+v", msg)
	}

	presenter3, _ := dialWS(t, ts.URL, "/ws_present?id=board-room", cfg.AllowedOrigins[0])
	defer presenter3.Close()
	_ = presenter3.WriteJSON(WSMessage{Type: "auth", Value: ready.SessionID})
	if msg := readType(t, presenter3); msg.Type != "invalidCode" {
		t.Fatalf("consumed code replay: %+v", msg)
	}
}

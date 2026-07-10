package core

import (
	"crypto/rand"
	"fmt"
	"math/big"
	"sync"

	"github.com/gorilla/websocket"
)

type Room struct {
	ID            string
	DisplayConn   *websocket.Conn
	VerifyCode    string
	Generation    uint64
	PresenterConn *websocket.Conn
	Sessions      map[string]*StreamSession
	mu            sync.RWMutex
}
type StreamSession struct {
	ID                     string
	CallerConn, CalleeConn *websocket.Conn
}
type RoomStore struct {
	rooms map[string]*Room
	mu    sync.RWMutex
}

func NewRoomStore() *RoomStore           { return &RoomStore{rooms: map[string]*Room{}} }
func (s *RoomStore) Get(id string) *Room { s.mu.RLock(); defer s.mu.RUnlock(); return s.rooms[id] }
func (s *RoomStore) SetDisplay(id string, c *websocket.Conn) (*Room, *websocket.Conn, []*websocket.Conn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r := s.rooms[id]
	if r == nil {
		r = &Room{ID: id, Sessions: map[string]*StreamSession{}}
		s.rooms[id] = r
	}
	r.mu.Lock()
	oldDisplay := r.DisplayConn
	stale := r.presenterConnectionsLocked()
	r.Sessions = map[string]*StreamSession{}
	r.PresenterConn = nil
	r.DisplayConn = c
	r.Generation++
	r.VerifyCode = NewVerifyCode()
	r.mu.Unlock()
	return r, oldDisplay, stale
}
func (s *RoomStore) RemoveDisplay(id string, c *websocket.Conn) []*websocket.Conn {
	s.mu.Lock()
	defer s.mu.Unlock()
	r := s.rooms[id]
	if r == nil {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.DisplayConn != c {
		return nil
	}
	stale := r.presenterConnectionsLocked()
	r.Sessions = map[string]*StreamSession{}
	r.PresenterConn = nil
	r.DisplayConn = nil
	r.Generation++
	delete(s.rooms, id)
	return stale
}
func (r *Room) presenterConnectionsLocked() []*websocket.Conn {
	seen := map[*websocket.Conn]bool{}
	out := make([]*websocket.Conn, 0, len(r.Sessions)+1)
	if r.PresenterConn != nil {
		seen[r.PresenterConn] = true
		out = append(out, r.PresenterConn)
	}
	for _, session := range r.Sessions {
		if !seen[session.CallerConn] {
			seen[session.CallerConn] = true
			out = append(out, session.CallerConn)
		}
	}
	return out
}
func (r *Room) Display() *websocket.Conn { r.mu.RLock(); defer r.mu.RUnlock(); return r.DisplayConn }
func (r *Room) Code() string             { r.mu.RLock(); defer r.mu.RUnlock(); return r.VerifyCode }
func (r *Room) HasPresenter() bool       { r.mu.RLock(); defer r.mu.RUnlock(); return r.PresenterConn != nil }

// AuthorizePresenter atomically consumes the displayed code and reserves the
// room's single presenter slot. The generation binds the reservation to the
// current display connection.
func (r *Room) AuthorizePresenter(code string, c *websocket.Conn) (*websocket.Conn, uint64, string, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.DisplayConn == nil || r.PresenterConn != nil || !secureEqual(code, r.VerifyCode) {
		return nil, 0, "", false
	}
	r.PresenterConn = c
	r.VerifyCode = NewVerifyCode()
	return r.DisplayConn, r.Generation, r.VerifyCode, true
}
func (r *Room) StartSession(c *websocket.Conn, generation uint64) (*StreamSession, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.DisplayConn == nil || r.Generation != generation || r.PresenterConn != c || len(r.Sessions) != 0 {
		return nil, false
	}
	id := fmt.Sprintf("%s$%s", r.ID, randomString(12))
	ss := &StreamSession{ID: id, CallerConn: c, CalleeConn: r.DisplayConn}
	r.Sessions[id] = ss
	return ss, true
}
func (r *Room) ReleasePresenter(c *websocket.Conn) (*websocket.Conn, string, string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.PresenterConn != c {
		return nil, "", ""
	}
	var sessionID string
	for id, session := range r.Sessions {
		if session.CallerConn == c {
			delete(r.Sessions, id)
			sessionID = id
		}
	}
	r.PresenterConn = nil
	return r.DisplayConn, sessionID, r.VerifyCode
}
func (r *Room) Session(id string) *StreamSession {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.Sessions[id]
}
func NewVerifyCode() string     { return randomFrom("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", 6) }
func randomString(n int) string { return randomFrom("abcdefghijklmnopqrstuvwxyz0123456789", n) }
func randomFrom(chars string, n int) string {
	b := make([]byte, n)
	for i := range b {
		x, e := rand.Int(rand.Reader, big.NewInt(int64(len(chars))))
		if e != nil {
			panic(e)
		}
		b[i] = chars[x.Int64()]
	}
	return string(b)
}

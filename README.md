# BrowserStream

BrowserStream is a small, self-hosted WebRTC screen-sharing service for presenter browsers and enrolled room displays. It is derived from [Laplace](https://github.com/adamyordan/laplace).

## Prerequisites

Install Docker Engine with the Compose v2 plugin, Python 3, `iproute2` (the `ip` command), and a POSIX shell. Then clone the clean repository:

```sh
git clone https://github.com/CLSMCSMII/browserstream.git
cd browserstream
```

## Quick start

1. Run `./install.sh --init-only`. It creates a mode-`0600` `config.json`, detects the IPv4 address whose subnet contains the default gateway, and uses that address for the TURN URL plus `coturn.listening_ip` and `coturn.relay_ip`. It also generates independent room and TURN secrets. Replace the remaining `browserstream.example.com` placeholders with values for your deployment before starting the service.
2. Review `config.json` without publishing its generated `display_token` or `shared_secret`. If Nginx has a fixed private address, add that exact `/32` to `trusted_proxy_cidrs`.
3. Run `./install.sh --with-turn`. Compose publishes BrowserStream on `${BROWSERSTREAM_BIND_ADDRESS:-172.16.10.18}:18080`; the external Nginx proxy should forward HTTPS/WebSockets to that address.

Remote screen capture requires HTTPS. Never commit generated `config.json`, `coturn/turnserver.conf`, certificates, or private keys.

## Presenting with AwareStream

Presentation is desktop-only. On a computer, open your configured `public_url` (the generated example uses `https://browserstream.example.com`), select the meeting-room display, enter its six-character verification code, and choose the screen or window to share. QR enrollment and mobile/tablet casting are intentionally unsupported.

## Configuration

The application reads JSON from `-config PATH`; `BROWSERSTREAM_CONFIG` sets the default path. Only the secret-free Aware template `config.example.json` is committed. `install.sh` generates fresh room and TURN secrets every time it creates a new config. Compose runs the application as the non-root installer UID/GID; when the installer itself runs as root, it uses unprivileged UID/GID `65532` and aligns ownership of the mode-`0600` configuration.

| Key | Meaning |
|---|---|
| `app_name` | Public UI name. |
| `public_url` | Canonical HTTP(S) origin; no subpath, credentials, query, or fragment. |
| `listen_address` | Internal container address (`0.0.0.0:8080` for Compose); `BROWSERSTREAM_BIND_ADDRESS` controls the private host address published to Nginx. |
| `rooms[].id` | Unique lowercase ID matching `[a-z0-9][a-z0-9-]{0,62}`. |
| `rooms[].label` | Display-safe user-facing label. |
| `rooms[].display_token` | Per-room secret (16+ characters) required before a display can own the room. Never send it to presenters. |
| `turn.urls` | `turn:`, `turns:`, `stun:`, or `stuns:` URLs. |
| `turn.transport_policy` | `relay` to require TURN, or `all` to permit direct ICE. |
| `turn.shared_secret` | coturn REST shared secret. It remains server-side and is only required when `turn:`/`turns:` URLs are configured. |
| `turn.credential_ttl_seconds` | Lifetime (60–86400 seconds) of generated HMAC credentials. |
| `coturn.*` | Optional bundled coturn realm, interface/NAT IPs, and relay-port range. Empty IP fields let coturn auto-detect. |
| `allowed_origins` | Exact browser origins permitted to open WebSockets; wildcards are rejected. |
| `trusted_proxy_cidrs` | Reverse-proxy source CIDRs allowed to supply `X-Forwarded-For`; leave empty unless the proxy network is known exactly. |
| `debug` | Allows `?debug=1`; false disables the overlay regardless of URL. |
| `limits` | WebSocket message size, HTTP/WebSocket timeouts, failed-code threshold, and lockout duration. |
| `tls.cert_file`, `tls.key_file` | Optional external direct-TLS paths; both must be set. HTTP behind a TLS reverse proxy is preferred. |

Direct application TLS is for bare-metal/source runs. The supplied Compose deployment expects TLS at a reverse proxy and deliberately excludes certificates from the repository and image.

Validate without starting: `go run . -config config.json -validate-config` or `docker compose run --rm --no-deps browserstream -validate-config`.

`GET /api/config` returns only non-secret UI settings. Short-lived coturn credentials are delivered only after display-token or presenter-code authentication. It never returns display tokens or the TURN shared secret. `GET /healthz` is the health probe.

## Reverse proxy

Example Nginx location (certificate setup omitted):

```nginx
location / {
    proxy_pass http://172.16.10.18:18080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 120s;
}
```

Replace `browserstream.example.com` in `public_url`, `allowed_origins`, and the coturn realm with your deployment hostname. For a new configuration, the installer replaces the example TURN URL with `turn:<detected-LAN-IP>:3478`. The Compose default binds the backend to private address `172.16.10.18`; override it with `BROWSERSTREAM_BIND_ADDRESS` when the deployment host differs. Permit TCP port 18080 only from the Nginx server. If per-client lockouts should use `X-Forwarded-For`, set `trusted_proxy_cidrs` to the reverse proxy's exact source `/32`; headers from other peers are ignored.

On multi-homed hosts, override route detection explicitly when creating the file:

```bash
BROWSERSTREAM_LAN_IP=192.0.2.10 ./install.sh --init-only
```

The override must be a usable unicast IPv4 address. Route detection and overrides apply only when `config.json` does not already exist.

## TURN

BrowserStream uses the [coturn REST authentication mechanism](https://github.com/coturn/coturn/wiki/turnserver#turn-rest-api): the server creates an expiry-based username and HMAC-SHA1 credential. Configure coturn with `use-auth-secret` and the same `turn.shared_secret`.

`install.sh` renders the ignored `coturn/turnserver.conf` entirely from `config.json`. Configure at least one reachable `turn:` URL and the `coturn` section, then start the optional pinned service with the app. Use an externally managed TURN service for `turns:` TLS:

```sh
./install.sh --with-turn
```

To explicitly stop and remove a previously started bundled coturn container:

```sh
./install.sh --stop-turn
```

The installer initializes `coturn.listening_ip` and `coturn.relay_ip` from the default-gateway interface. Set `coturn.external_ip` when the TURN server is behind NAT, and review all three values for multi-homed hosts. Typical ports are TCP/UDP 3478 and the configured relay range 49160–49200. TURN is media traffic and is not proxied by Nginx. See `coturn/README.md`.

## Display and presenter flow

1. An enrolled display opens `/room/ROOM_ID`, authenticates with its per-room token, and receives a rotating six-character presenter code.
2. A presenter chooses the configured room, enters that code, and sends it as the first WebSocket message (never in a URL).
3. Signaling accepts only the expected offer/answer and ICE message types. Failed codes are rate-limited and lock out by room/client.
4. When sharing ends, the display clears the stream and the code rotates.

A new display connection with the valid display token replaces the old display. Protect tokens as administrative enrollment credentials.

## Development

Requires Go 1.26 (the same release family as the Docker builder):

```sh
go test -race ./...
go vet ./...
go build -o browserstream .
./browserstream -config config.json
```

## Security and contributions

Read [SECURITY.md](SECURITY.md) before deployment and [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Third-party notices are in [NOTICE](NOTICE). Licensed under [MIT](LICENSE).

To create a secure configuration without starting containers, run `./install.sh --init-only`.

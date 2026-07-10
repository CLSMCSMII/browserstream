# BrowserStream

BrowserStream is a small, self-hosted WebRTC screen-sharing service for presenter browsers and enrolled room displays. It is derived from [Laplace](https://github.com/adamyordan/laplace).

## Prerequisites

Install Docker Engine with the Compose v2 plugin, Python 3, and a POSIX shell. Then clone the clean repository:

```sh
git clone https://github.com/CLSMCSMII/browserstream.git
cd browserstream
```

## Three-step quick start

1. Run `./install.sh`. On first run it creates mode-`0600` `config.json`, generates independent TURN and display secrets, validates the configuration, builds the image, and starts the app on `127.0.0.1:18080`.
2. Open `http://localhost:18080` for a local test. For production, edit `config.json`: set `public_url`, matching `allowed_origins`, rooms, and optional STUN/TURN URLs; run `./install.sh` again. Existing configuration is never overwritten.
3. Put an HTTPS reverse proxy in front of `127.0.0.1:18080`, then enroll each kiosk once at `https://meet.example.com/room/ROOM_ID#token=DISPLAY_TOKEN`. The fragment is removed immediately and retained only in that browser's local storage.

Remote screen capture requires HTTPS (localhost is the browser exception). Never commit generated `config.json`, `coturn/turnserver.conf`, certificates, or private keys.

## Configuration

The application reads JSON from `-config PATH`; `BROWSERSTREAM_CONFIG` sets the default path. Only `config.example.json` is committed. `install.sh` also passes the installing user's UID/GID to Compose so the non-root container can read the mode-`0600` configuration; when invoking Compose directly, set `BROWSERSTREAM_UID` and `BROWSERSTREAM_GID` to the configuration owner's numeric IDs.

| Key | Meaning |
|---|---|
| `app_name` | Public UI name. |
| `public_url` | Canonical HTTP(S) origin; no subpath, credentials, query, or fragment. |
| `listen_address` | Internal host and port; Compose publishes it on loopback only. |
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
    proxy_pass http://127.0.0.1:18080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 120s;
}
```

Set `public_url` and `allowed_origins` to the external HTTPS origin. If per-client lockouts should use `X-Forwarded-For`, set `trusted_proxy_cidrs` to the reverse proxy's exact source network; headers from other peers are ignored. Do not expose port 18080 publicly.

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

Set `coturn.listening_ip`, `coturn.relay_ip`, and (behind NAT) `coturn.external_ip` for your topology; leave them empty only when coturn auto-detection is correct. Typical ports are TCP/UDP 3478 and the configured relay range 49160–49200. TURN is media traffic and is not proxied by Nginx. See `coturn/README.md`.

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

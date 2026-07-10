# BrowserStream

BrowserStream is a self-hosted WebRTC screen-sharing service for meeting-room
displays. It is derived from
[Laplace](https://github.com/adamyordan/laplace).

## Install

### Requirements

- Linux with Docker Engine and Docker Compose v2
- Python 3
- `iproute2` (`ip` command)

### 1. Clone

```sh
git clone https://github.com/CLSMCSMII/browserstream.git
cd browserstream
```

### 2. Generate configuration

```sh
./install.sh --init-only
```

This creates `config.json` with random room/TURN secrets and automatically
detects the LAN IPv4 address on the default-gateway network.

### 3. Edit `config.json`

At minimum, replace `browserstream.example.com` in:

- `public_url`
- `allowed_origins`
- `coturn.realm`

Review `rooms`, then keep `display_token` and `turn.shared_secret` private.

### 4. Start BrowserStream and coturn

```sh
BROWSERSTREAM_BIND_ADDRESS="$(python3 scripts/detect_lan_ip.py)" ./install.sh --with-turn
```

The backend listens on `http://<LAN-IP>:18080`. Existing `config.json` files
are never overwritten.

### 5. Add HTTPS reverse proxy

Screen capture requires HTTPS. Example Nginx location:

```nginx
location / {
    proxy_pass http://LAN-IP:18080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 120s;
}
```

Replace `LAN-IP` with the detected server address. Allow TCP `18080` only from
the reverse proxy. TURN uses TCP/UDP `3478` and UDP `49160-49200`.

## Use

1. Enroll the room display once at
   `https://YOUR-DOMAIN/room/ROOM_ID#token=DISPLAY_TOKEN`.
2. The display stores the token locally and shows a six-character code.
3. On a desktop computer, open `https://YOUR-DOMAIN`.
4. Select the room, enter its code, and choose a screen or window.

## Useful commands

```sh
# Validate configuration
docker compose run --rm --no-deps browserstream -validate-config

# Stop and remove bundled coturn
BROWSERSTREAM_BIND_ADDRESS="$(python3 scripts/detect_lan_ip.py)" ./install.sh --stop-turn
```

For a multi-homed server, use the same address for initialization and startup:

```sh
LAN_IP=192.168.1.10
BROWSERSTREAM_LAN_IP="$LAN_IP" ./install.sh --init-only
BROWSERSTREAM_BIND_ADDRESS="$LAN_IP" ./install.sh --with-turn
```

If TURN is behind NAT, set `coturn.external_ip` in `config.json`. Never commit
`config.json`, `coturn/turnserver.conf`, certificates, or private keys.

## Development

Requires Go 1.26:

```sh
go test -race ./...
go vet ./...
go build -o browserstream .
```

See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), and
[coturn/README.md](coturn/README.md). Licensed under [MIT](LICENSE).

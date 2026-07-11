# BrowserStream

BrowserStream is a self-hosted WebRTC screen-sharing service for meeting-room
displays. It is derived from
[Laplace](https://github.com/adamyordan/laplace).

![BrowserStream architecture](docs/images/browserstream-architecture.jpg)

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

### 2. Configure and deploy

```sh
./install.sh
```

The installer detects the LAN IP and asks:

```text
Application name [AwareStream]:
Public URL / allowed origin [https://browserstream.example.com]:
Room ID [awmeeting]:
Room label [Aware Building]:
Install bundled coturn? [Y/n]:
TURN realm [hostname from Public URL]:
TURN URL [turn:<detected-IP>:3478]:
coturn listening IP [<detected-IP>]:
coturn relay IP [<detected-IP>]:
```

Press **Enter** to accept a displayed default. `allowed_origins` is derived from
the Public URL, random room/TURN secrets are generated, and the selected
containers are deployed. Existing `config.json` files are never overwritten.

### 3. Add HTTPS reverse proxy

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
./install.sh --stop-turn
```

Use the listening and relay IP prompts to select an address on a multi-homed
server. Use `./install.sh --init-only` to create configuration without deploying.

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

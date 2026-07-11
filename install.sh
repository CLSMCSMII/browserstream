#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
CONFIG=${BROWSERSTREAM_CONFIG:-config.json}
case "$CONFIG" in /*) ;; *) CONFIG="$SCRIPT_DIR/$CONFIG" ;; esac
export BROWSERSTREAM_CONFIG="$CONFIG"
BROWSERSTREAM_BIND_ADDRESS=${BROWSERSTREAM_BIND_ADDRESS:-}
# Run the application container as a non-root UID/GID that can read the
# mode-0600 bind-mounted configuration. Root installs use an unprivileged
# numeric identity; non-root installs use the installing user's identity.
HOST_UID=$(id -u)
HOST_GID=$(id -g)
if [ "$HOST_UID" -eq 0 ]; then
  BROWSERSTREAM_UID=${BROWSERSTREAM_UID:-65532}
  BROWSERSTREAM_GID=${BROWSERSTREAM_GID:-65532}
else
  BROWSERSTREAM_UID=${BROWSERSTREAM_UID:-$HOST_UID}
  BROWSERSTREAM_GID=${BROWSERSTREAM_GID:-$HOST_GID}
  if [ "$BROWSERSTREAM_UID" != "$HOST_UID" ] || [ "$BROWSERSTREAM_GID" != "$HOST_GID" ]; then
    echo "Non-root installs require BROWSERSTREAM_UID:GID to match the configuration owner ($HOST_UID:$HOST_GID)" >&2
    exit 1
  fi
fi
export BROWSERSTREAM_UID BROWSERSTREAM_GID
WITH_TURN=0
WITH_TURN_FORCED=
STOP_TURN=0
INIT_ONLY=0
ADD_ROOM=0
for arg in "$@"; do
  case "$arg" in
    --with-turn) WITH_TURN=1; WITH_TURN_FORCED=1 ;;
    --add-room) ADD_ROOM=1 ;;
    --init-only) INIT_ONLY=1 ;;
    --stop-turn) STOP_TURN=1 ;;
    -h|--help) echo "Usage: ./install.sh [--with-turn|--stop-turn|--add-room] [--init-only]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ "$WITH_TURN" -eq 1 ] && [ "$STOP_TURN" -eq 1 ]; then
  echo "--with-turn and --stop-turn are mutually exclusive" >&2
  exit 2
fi
if [ "$ADD_ROOM" -eq 1 ] && { [ "$WITH_TURN" -eq 1 ] || [ "$STOP_TURN" -eq 1 ]; }; then
  echo "--add-room cannot be combined with --with-turn or --stop-turn" >&2
  exit 2
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
if [ "$ADD_ROOM" -eq 1 ]; then
  if [ ! -f "$CONFIG" ]; then
    echo "--add-room requires an existing configuration: $CONFIG" >&2
    exit 1
  fi
  echo "Using existing $CONFIG."
  python3 scripts/configure.py --add-room "$CONFIG"
elif [ ! -f "$CONFIG" ]; then
  LAN_IP=$(python3 scripts/detect_lan_ip.py)
  WITH_TURN=$(python3 scripts/configure.py "$CONFIG" "$LAN_IP" "$WITH_TURN_FORCED")
  echo "Created $CONFIG with LAN IPv4 $LAN_IP and random secrets."
else
  echo "Using existing $CONFIG (not overwritten)."
fi
chmod 600 "$CONFIG"
if [ "$HOST_UID" -eq 0 ]; then
  chown "$BROWSERSTREAM_UID:$BROWSERSTREAM_GID" "$CONFIG"
fi

if [ -z "$BROWSERSTREAM_BIND_ADDRESS" ]; then
  BROWSERSTREAM_BIND_ADDRESS=$(python3 - "$CONFIG" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: c=json.load(f)
print(c.get('coturn',{}).get('listening_ip',''))
PY
)
fi
if [ -z "$BROWSERSTREAM_BIND_ADDRESS" ]; then
  BROWSERSTREAM_BIND_ADDRESS=$(python3 scripts/detect_lan_ip.py)
fi
export BROWSERSTREAM_BIND_ADDRESS

if [ "$ADD_ROOM" -eq 0 ]; then
  python3 - "$CONFIG" coturn/turnserver.conf "$WITH_TURN" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: c=json.load(f)
t=c.get('turn',{}); server=c.get('coturn',{}); with_turn=sys.argv[3]=='1'
turn_urls=[u for u in t.get('urls',[]) if u.startswith('turn:')]
if with_turn and not turn_urls:
 raise SystemExit('--with-turn requires at least one turn: URL; bundled coturn TLS is not configured by this installer')
lines=['use-auth-secret','static-auth-secret='+t.get('shared_secret',''),'realm='+server.get('realm','browserstream'),'fingerprint','no-cli','no-multicast-peers','proc-user=nobody','proc-group=nogroup']
for key,name in [('listening_ip','listening-ip'),('relay_ip','relay-ip')]:
 if server.get(key): lines.append(name+'='+server[key])
if server.get('external_ip'):
 value=server['external_ip']
 if server.get('relay_ip'): value += '/'+server['relay_ip']
 lines.append('external-ip='+value)
lines += ['min-port='+str(server.get('min_port',49160)),'max-port='+str(server.get('max_port',49200))]
with open(sys.argv[2],'w',encoding='utf-8') as f: f.write('\n'.join(lines)+'\n')
PY
  chmod 600 coturn/turnserver.conf
fi

if [ "$INIT_ONLY" -eq 1 ]; then
  if [ "$ADD_ROOM" -eq 1 ]; then
    echo "Room update complete. Run ./install.sh to deploy."
  elif [ "$WITH_TURN" -eq 1 ]; then
    echo "Initialization complete. Run ./install.sh --with-turn to deploy."
  else
    echo "Initialization complete. Run ./install.sh to deploy."
  fi
  exit 0
fi
command -v docker >/dev/null 2>&1 || { echo "Docker with the Compose plugin is required" >&2; exit 1; }
docker compose version >/dev/null
python3 - "$CONFIG" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8') as f: c=json.load(f)
if c.get('listen_address') != '0.0.0.0:8080':
 raise SystemExit('Compose installation requires listen_address 0.0.0.0:8080')
if c.get('tls',{}).get('cert_file') or c.get('tls',{}).get('key_file'):
 raise SystemExit('Compose installation expects TLS at a reverse proxy; direct TLS is for bare-metal runs')
PY
docker compose config >/dev/null
if [ "$STOP_TURN" -eq 1 ]; then
  docker compose --profile turn stop coturn
  docker compose --profile turn rm -f coturn
fi
docker compose build browserstream
docker compose run --rm --no-deps browserstream -validate-config
if [ "$WITH_TURN" -eq 1 ]; then
  docker compose --profile turn up -d
elif [ "$ADD_ROOM" -eq 1 ]; then
  docker compose up -d --force-recreate browserstream
else
  docker compose up -d browserstream
fi
echo "BrowserStream is running on http://${BROWSERSTREAM_BIND_ADDRESS}:${BROWSERSTREAM_PORT:-18080}. Configure the external TLS reverse proxy to use this backend."

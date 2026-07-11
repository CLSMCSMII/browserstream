#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
[ ! -L .env ] || { echo "Refusing to use symlink: $SCRIPT_DIR/.env" >&2; exit 1; }
if [ -e .env ] && [ ! -f .env ]; then
  echo "Refusing to use non-regular file: $SCRIPT_DIR/.env" >&2
  exit 1
fi
persisted_value() {
  python3 - .env "$1" <<'PY'
import os
import re
import stat
import sys

path, wanted = sys.argv[1:]
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except FileNotFoundError:
    raise SystemExit(0)
value = None
if not stat.S_ISREG(os.fstat(fd).st_mode):
    os.close(fd)
    raise SystemExit(f"refusing non-regular dotenv file: {path}")
with os.fdopen(fd, encoding="utf-8") as source:
    for line in source:
        key, separator, candidate = line.rstrip("\n").partition("=")
        if separator and key.strip() == wanted:
            value = candidate
if value is not None:
    if not value or not re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        raise SystemExit(f"unsupported dotenv characters in persisted {wanted}")
    print(value)
PY
}
if [ -z "${BROWSERSTREAM_CONFIG+x}" ]; then BROWSERSTREAM_CONFIG=$(persisted_value BROWSERSTREAM_CONFIG); fi
if [ -z "${BROWSERSTREAM_BIND_ADDRESS+x}" ]; then BROWSERSTREAM_BIND_ADDRESS=$(persisted_value BROWSERSTREAM_BIND_ADDRESS); fi
if [ -z "${BROWSERSTREAM_PORT+x}" ]; then BROWSERSTREAM_PORT=$(persisted_value BROWSERSTREAM_PORT); fi
if [ -z "${BROWSERSTREAM_UID+x}" ]; then BROWSERSTREAM_UID=$(persisted_value BROWSERSTREAM_UID); fi
if [ -z "${BROWSERSTREAM_GID+x}" ]; then BROWSERSTREAM_GID=$(persisted_value BROWSERSTREAM_GID); fi
CONFIG=${BROWSERSTREAM_CONFIG:-config.json}
case "$CONFIG" in /*) ;; *) CONFIG="$SCRIPT_DIR/$CONFIG" ;; esac
export BROWSERSTREAM_CONFIG="$CONFIG"
BROWSERSTREAM_BIND_ADDRESS=${BROWSERSTREAM_BIND_ADDRESS:-}
BROWSERSTREAM_PORT=${BROWSERSTREAM_PORT:-18080}
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
python3 - "$CONFIG" "$BROWSERSTREAM_PORT" "$BROWSERSTREAM_UID" "$BROWSERSTREAM_GID" <<'PY'
import re
import sys

for key, value in zip(
    ("BROWSERSTREAM_CONFIG", "BROWSERSTREAM_PORT", "BROWSERSTREAM_UID", "BROWSERSTREAM_GID"),
    sys.argv[1:],
):
    if not value or not re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        raise SystemExit(f"unsupported dotenv characters in {key}")
PY
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
export BROWSERSTREAM_PORT

# Persist Compose interpolation values so later direct Compose commands use the
# same bind address and unprivileged configuration owner as the installer.
# Preserve unrelated operator-managed .env entries.
python3 - .env "$BROWSERSTREAM_BIND_ADDRESS" "$BROWSERSTREAM_PORT" "$BROWSERSTREAM_UID" "$BROWSERSTREAM_GID" "$CONFIG" <<'PY'
import os
import re
import stat
import sys
import tempfile

path, bind, port, uid, gid, config = sys.argv[1:]
managed = {
    "BROWSERSTREAM_BIND_ADDRESS": bind,
    "BROWSERSTREAM_PORT": port,
    "BROWSERSTREAM_UID": uid,
    "BROWSERSTREAM_GID": gid,
    "BROWSERSTREAM_CONFIG": config,
}
lines = []
if os.path.exists(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise SystemExit(f"refusing non-regular dotenv file: {path}")
    with os.fdopen(fd, encoding="utf-8") as source:
        for line in source:
            key = line.split("=", 1)[0].strip()
            if key not in managed:
                lines.append(line.rstrip("\n"))
for key, value in managed.items():
    if not value or not re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        raise SystemExit(f"unsupported dotenv characters in {key}")
    lines.append(f"{key}={value}")
directory = os.path.dirname(os.path.abspath(path))
fd, temporary = tempfile.mkstemp(prefix=".env.tmp-", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write("\n".join(lines) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

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

#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
CONFIG=${BROWSERSTREAM_CONFIG:-config.json}
case "$CONFIG" in /*) ;; *) CONFIG="$SCRIPT_DIR/$CONFIG" ;; esac
export BROWSERSTREAM_CONFIG="$CONFIG"
BROWSERSTREAM_BIND_ADDRESS=${BROWSERSTREAM_BIND_ADDRESS:-172.16.10.18}
export BROWSERSTREAM_BIND_ADDRESS
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
STOP_TURN=0
INIT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --with-turn) WITH_TURN=1 ;;
    --init-only) INIT_ONLY=1 ;;
    --stop-turn) STOP_TURN=1 ;;
    -h|--help) echo "Usage: ./install.sh [--with-turn|--stop-turn] [--init-only]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ "$WITH_TURN" -eq 1 ] && [ "$STOP_TURN" -eq 1 ]; then
  echo "--with-turn and --stop-turn are mutually exclusive" >&2
  exit 2
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
if [ ! -f "$CONFIG" ]; then
  LAN_IP=$(python3 scripts/detect_lan_ip.py)
  python3 - "$CONFIG" "$LAN_IP" <<'PY'
import json,os,secrets,sys,tempfile
p,lan_ip=sys.argv[1:3]
with open('config.example.json',encoding='utf-8') as f: c=json.load(f)
c['turn']['urls']=[f'turn:{lan_ip}:3478']
c['turn']['shared_secret']=secrets.token_urlsafe(48)
c['coturn']['listening_ip']=lan_ip
c['coturn']['relay_ip']=lan_ip
for room in c['rooms']: room['display_token']=secrets.token_urlsafe(32)
directory=os.path.dirname(os.path.abspath(p))
fd,tmp=tempfile.mkstemp(prefix='.browserstream-config-',dir=directory,text=True)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w',encoding='utf-8') as f:
  json.dump(c,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 try:
  os.link(tmp,p)
 except FileExistsError as exc:
  raise SystemExit(f'Configuration already exists: {p}') from exc
finally:
 try: os.unlink(tmp)
 except FileNotFoundError: pass
PY
  echo "Created $CONFIG with LAN IPv4 $LAN_IP and random secrets."
else
  echo "Using existing $CONFIG (not overwritten)."
fi
chmod 600 "$CONFIG"
if [ "$HOST_UID" -eq 0 ]; then
  chown "$BROWSERSTREAM_UID:$BROWSERSTREAM_GID" "$CONFIG"
fi

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

if [ "$INIT_ONLY" -eq 1 ]; then
  echo "Initialization complete. Edit $CONFIG, then run ./install.sh."
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
else
  docker compose up -d browserstream
fi
echo "BrowserStream is running on http://${BROWSERSTREAM_BIND_ADDRESS}:${BROWSERSTREAM_PORT:-18080}. Configure the external TLS reverse proxy to use this backend."

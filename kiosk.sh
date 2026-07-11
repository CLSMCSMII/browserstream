#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
CONFIG=${BROWSERSTREAM_CONFIG:-$SCRIPT_DIR/config.json}

command -v python3 >/dev/null 2>&1 || {
  echo "kiosk.sh: python3 is required" >&2
  exit 1
}

exec python3 "$SCRIPT_DIR/scripts/kiosk_url.py" "$CONFIG" "$@"

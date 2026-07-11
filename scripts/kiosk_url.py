#!/usr/bin/env python3
"""Print copyable BrowserStream display-enrollment URLs from config.json."""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ConfigError(ValueError):
    pass


def parse_args(arguments):
    parser = argparse.ArgumentParser(
        prog="./kiosk.sh",
        description="Print a kiosk display-enrollment URL from BrowserStream config.",
    )
    parser.add_argument("--all", action="store_true", help="print URLs for every room")
    parser.add_argument("room", nargs="?", help="room ID to print without prompting")
    args = parser.parse_args(arguments)
    if args.all and args.room:
        parser.error("--all cannot be combined with a room ID")
    return args


def load_config(path):
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration not found: {config_path}") from error
    except PermissionError as error:
        raise ConfigError(f"configuration is not readable: {config_path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"invalid JSON in {config_path} at line {error.lineno}, column {error.colno}"
        ) from error
    except OSError as error:
        raise ConfigError(f"cannot read configuration {config_path}: {error.strerror}") from error

    if not isinstance(config, dict):
        raise ConfigError("configuration root must be a JSON object")
    base_url = validate_public_url(config.get("public_url"))
    rooms = validate_rooms(config.get("rooms"))
    return base_url, rooms


def validate_public_url(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError("public_url must be a non-empty HTTP or HTTPS origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ConfigError("public_url must be a valid HTTP or HTTPS origin") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not 1 <= port <= 65535
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ConfigError("public_url must be a valid HTTP or HTTPS origin")
    return value.rstrip("/")


def validate_rooms(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ConfigError("configuration must contain 1-100 rooms")

    identifiers = set()
    tokens = set()
    rooms = []
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError("every room must be a JSON object")
        room_id = item.get("id")
        label = item.get("label")
        token = item.get("display_token")
        if not isinstance(room_id, str) or not ROOM_ID_RE.fullmatch(room_id):
            raise ConfigError("every room ID must use lowercase letters, numbers, and hyphens")
        if room_id in identifiers:
            raise ConfigError("room IDs must be unique")
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label.encode("utf-8")) > 100
            or any(ord(char) < 32 or ord(char) == 127 for char in label)
        ):
            raise ConfigError(f'room "{room_id}" has an invalid label')
        if (
            not isinstance(token, str)
            or len(token) < 16
            or token.startswith("GENERATE_")
            or token.startswith("CHANGE_")
        ):
            raise ConfigError(f'room "{room_id}" display_token must be a generated secret')
        if token in tokens:
            raise ConfigError("room display tokens must be unique")
        identifiers.add(room_id)
        tokens.add(token)
        rooms.append({"id": room_id, "label": label, "display_token": token})
    return rooms


def enrollment_url(base_url, room):
    room_id = quote(room["id"], safe="")
    token = quote(room["display_token"], safe="")
    return f"{base_url}/room/{room_id}#token={token}"


def select_room(rooms):
    if len(rooms) == 1:
        return rooms[0]

    print("Available rooms:", file=sys.stderr)
    for index, room in enumerate(rooms, start=1):
        print(f"  {index}) {room['label']} ({room['id']})", file=sys.stderr)
    while True:
        print("Select room [1]: ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if answer == "":
            raise ConfigError(
                "stdin is closed; specify a room ID or use --all"
            )
        answer = answer.strip()
        if not answer:
            return rooms[0]
        try:
            selected = int(answer)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(rooms):
            return rooms[selected - 1]
        print(f"Please enter a number from 1 to {len(rooms)}.", file=sys.stderr)


def main(arguments=None):
    raw_arguments = sys.argv[1:] if arguments is None else arguments
    if not raw_arguments:
        print("kiosk.sh: internal configuration path is required", file=sys.stderr)
        return 1
    config_path, cli_arguments = raw_arguments[0], raw_arguments[1:]
    args = parse_args(cli_arguments)
    try:
        base_url, rooms = load_config(config_path)
        if args.all:
            blocks = [
                f"{room['label']} ({room['id']}):\n{enrollment_url(base_url, room)}"
                for room in rooms
            ]
            print("\n\n".join(blocks))
            return 0
        if args.room:
            selected = next((room for room in rooms if room["id"] == args.room), None)
            if selected is None:
                raise ConfigError(f'room "{args.room}" was not found')
        else:
            selected = select_room(rooms)
        print(enrollment_url(base_url, selected))
        return 0
    except ConfigError as error:
        print(f"kiosk.sh: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

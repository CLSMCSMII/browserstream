#!/usr/bin/env python3
"""Interactively create a complete BrowserStream configuration."""

import ipaddress
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from detect_lan_ip import validate_override

ROOM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def ask(label, default, validator):
    while True:
        print(f"{label} [{default}]: ", end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        value = default if line == "" or not line.strip() else line.strip()
        try:
            return validator(value)
        except ValueError as exc:
            print(f"Invalid value: {exc}", file=sys.stderr)
            if line == "":
                raise


def ask_yes_no(question="Install bundled coturn?", default=True):
    choice = "Y/n" if default else "y/N"
    label = f"{question} [{choice}]: "
    while True:
        print(label, end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        value = line.strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Invalid value: enter yes or no", file=sys.stderr)
        if line == "":
            raise ValueError(f"{question} choice is required")


def nonempty(value, name, maximum=100):
    value = value.strip()
    if not value or len(value.encode("utf-8")) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must contain 1-{maximum} printable UTF-8 bytes")
    return value


def public_url(value):
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("use an absolute http(s) URL without a path, credentials, query, or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    return f"{parsed.scheme}://{parsed.netloc}"


def room_id(value):
    if not ROOM_ID_PATTERN.fullmatch(value):
        raise ValueError("use lowercase letters, digits, or hyphens (maximum 63 characters)")
    return value


def realm(value):
    value = value.strip()
    if not value or len(value) > 255 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("realm must be 1-255 characters without whitespace")
    return value


def turn_url(value):
    value = value.strip()
    if ":" not in value:
        raise ValueError("use turn:HOST:PORT")
    scheme, opaque = value.split(":", 1)
    if scheme != "turn" or not opaque or "#" in opaque:
        raise ValueError("bundled coturn requires a turn: URL")
    host_port, separator, query = opaque.partition("?")
    parsed = urlsplit("//" + host_port)
    if parsed.username is not None or parsed.password is not None or not parsed.hostname or parsed.path:
        raise ValueError("TURN URL must contain only a host and optional port")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("TURN port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("TURN port must be between 1 and 65535")
    if separator:
        values = parse_qs(query, keep_blank_values=True)
        if set(values) != {"transport"} or values["transport"] not in (["udp"], ["tcp"]):
            raise ValueError("TURN query may only set transport=udp or transport=tcp")
    return value


def coturn_ip(value):
    try:
        return validate_override(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ValueError("use a usable unicast IPv4 address") from exc


def write_atomic(path, config):
    destination = Path(path).resolve()
    fd, temporary = tempfile.mkstemp(
        prefix=".browserstream-config-",
        dir=destination.parent,
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise SystemExit(f"Configuration already exists: {destination}") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def validate_existing_rooms(config):
    rooms = config.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise SystemExit("configuration must contain at least one room")
    if len(rooms) >= 100:
        raise SystemExit("configuration already contains the maximum of 100 rooms")

    identifiers = set()
    tokens = set()
    for index, existing in enumerate(rooms, start=1):
        if not isinstance(existing, dict):
            raise SystemExit(f"room {index} must be an object")
        try:
            identifier = room_id(existing.get("id", ""))
            nonempty(existing.get("label", ""), "room label")
        except ValueError as exc:
            raise SystemExit(f"existing room {index} is invalid: {exc}") from exc
        token = existing.get("display_token", "")
        if not isinstance(token, str) or len(token) < 16 or token.startswith(("GENERATE_", "CHANGE_")):
            raise SystemExit(f'existing room "{identifier}" has an invalid display token')
        if identifier in identifiers:
            raise SystemExit(f'duplicate existing room ID "{identifier}"')
        if token in tokens:
            raise SystemExit("existing room display tokens must be unique")
        identifiers.add(identifier)
        tokens.add(token)
    return rooms, identifiers, tokens


def next_room_number(identifiers, start):
    number = start
    while f"room-{number}" in identifiers:
        number += 1
    return number


def replace_with_backup(path, config):
    destination = Path(path).resolve()
    os.chmod(destination, 0o600)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = destination.with_name(f"{destination.name}.backup-{timestamp}")
    suffix = 1
    while backup.exists():
        backup = destination.with_name(f"{destination.name}.backup-{timestamp}-{suffix}")
        suffix += 1

    fd, temporary = tempfile.mkstemp(
        prefix=".browserstream-config-",
        dir=destination.parent,
        text=True,
    )
    linked_backup = False
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(destination, backup)
        linked_backup = True
        os.replace(temporary, destination)
        replaced = True
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if linked_backup and not replaced and destination.exists():
            try:
                os.unlink(backup)
            except FileNotFoundError:
                pass
        raise
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return backup


def add_rooms(config_path):
    destination = Path(config_path).resolve()
    try:
        with destination.open(encoding="utf-8") as stream:
            config = json.load(stream)
    except FileNotFoundError as exc:
        raise SystemExit("--add-room requires an existing configuration") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"existing configuration is invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise SystemExit("existing configuration must be a JSON object")

    rooms, identifiers, tokens = validate_existing_rooms(config)
    try:
        base_url = public_url(config.get("public_url", ""))
    except ValueError as exc:
        raise SystemExit(f"existing public_url is invalid: {exc}") from exc

    added = []
    while len(rooms) < 100:
        number = next_room_number(identifiers, len(rooms) + 1)

        def unique_identifier(value):
            identifier = room_id(value)
            if identifier in identifiers:
                raise ValueError(f'room ID "{identifier}" already exists')
            return identifier

        identifier = ask("Room ID", f"room-{number}", unique_identifier)
        label = ask("Room label", f"Room {number}", lambda value: nonempty(value, "room label"))
        token = secrets.token_urlsafe(32)
        while token in tokens:
            token = secrets.token_urlsafe(32)
        room = {"id": identifier, "label": label, "display_token": token}
        rooms.append(room)
        identifiers.add(identifier)
        tokens.add(token)
        added.append(room)
        if len(rooms) >= 100:
            print("Maximum of 100 rooms reached.", file=sys.stderr)
            break
        if not ask_yes_no("Add another room?", default=False):
            break

    backup = replace_with_backup(destination, config)
    print(f"Backup: {backup}")
    for room in added:
        enrollment_url = f"{base_url}/room/{quote(room['id'], safe='')}#token={quote(room['display_token'], safe='')}"
        print(f"Room added: {room['id']} ({room['label']})")
        print(f"Enrollment URL: {enrollment_url}")
    return added


def configure(config_path, lan_ip, forced_coturn=""):
    with open("config.example.json", encoding="utf-8") as stream:
        config = json.load(stream)

    default_room = config["rooms"][0]
    name = ask("Application name", config["app_name"], lambda value: nonempty(value, "app_name"))
    url = ask("Public URL / allowed origin", config["public_url"], public_url)
    selected_room_id = ask("Room ID", default_room["id"], room_id)
    room_label = ask(
        "Room label",
        default_room["label"],
        lambda value: nonempty(value, "room label"),
    )
    if forced_coturn == "1":
        install_coturn = True
    elif forced_coturn == "0":
        install_coturn = False
    else:
        install_coturn = ask_yes_no(default=True)

    hostname = urlsplit(url).hostname
    selected_realm = ask("TURN realm", hostname, realm)
    selected_turn_url = ask("TURN URL", f"turn:{lan_ip}:3478", turn_url)
    listening_ip = ask("coturn listening IP", lan_ip, coturn_ip)
    relay_ip = ask("coturn relay IP", lan_ip, coturn_ip)

    config["app_name"] = name
    config["public_url"] = url
    config["allowed_origins"] = [url]
    config["rooms"] = [
        {
            "id": selected_room_id,
            "label": room_label,
            "display_token": secrets.token_urlsafe(32),
        }
    ]
    config["turn"]["urls"] = [selected_turn_url]
    config["turn"]["shared_secret"] = secrets.token_urlsafe(48)
    config["coturn"]["realm"] = selected_realm
    config["coturn"]["listening_ip"] = listening_ip
    config["coturn"]["relay_ip"] = relay_ip
    write_atomic(config_path, config)
    return install_coturn


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--add-room":
        add_rooms(sys.argv[2])
        return
    if len(sys.argv) not in {3, 4}:
        raise SystemExit("usage: configure.py CONFIG_PATH LAN_IP [0|1] | --add-room CONFIG_PATH")
    forced_coturn = sys.argv[3] if len(sys.argv) == 4 else ""
    if forced_coturn not in {"", "0", "1"}:
        raise SystemExit("coturn selection must be 0 or 1")
    print("1" if configure(sys.argv[1], validate_override(sys.argv[2]), forced_coturn) else "0")


if __name__ == "__main__":
    main()

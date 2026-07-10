#!/usr/bin/env python3
"""Detect the IPv4 address sharing a subnet with the selected default gateway."""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
from typing import Any


def validate_override(value: str) -> str:
    """Return a normalized usable unicast IPv4 override or raise ValueError."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError("BROWSERSTREAM_LAN_IP must be a usable unicast IPv4 address") from exc
    if not isinstance(address, ipaddress.IPv4Address) or any(
        (
            address.is_unspecified,
            address.is_loopback,
            address.is_multicast,
            address.is_link_local,
            address.is_reserved,
            address in ipaddress.IPv4Network("0.0.0.0/8"),
        )
    ):
        raise ValueError("BROWSERSTREAM_LAN_IP must be a usable unicast IPv4 address")
    return str(address)


def _metric(route: dict[str, Any]) -> int:
    try:
        return int(route.get("metric", 0))
    except (TypeError, ValueError):
        return 0


def select_default_gateway_ip(
    routes: list[dict[str, Any]], addresses_by_device: dict[str, list[dict[str, Any]]]
) -> str:
    """Select the lowest-metric default-route address on the gateway's subnet."""
    for route in sorted(routes, key=_metric):
        if route.get("dst") != "default" or not route.get("gateway") or not route.get("dev"):
            continue
        try:
            gateway = ipaddress.IPv4Address(str(route["gateway"]))
        except ipaddress.AddressValueError:
            continue

        candidates: list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]] = []
        for item in addresses_by_device.get(str(route["dev"]), []):
            if item.get("family") != "inet" or item.get("scope") != "global":
                continue
            try:
                local = ipaddress.IPv4Address(str(item["local"]))
                network = ipaddress.IPv4Network(f"{local}/{int(item['prefixlen'])}", strict=False)
            except (ipaddress.AddressValueError, KeyError, TypeError, ValueError):
                continue
            if gateway in network:
                candidates.append((local, network))

        preferred = str(route.get("prefsrc", ""))
        for local, _ in candidates:
            if preferred and str(local) == preferred:
                return str(local)
        if candidates:
            return str(candidates[0][0])

    raise ValueError("no usable IPv4 address shares a subnet with a default gateway")


def _ip_json(*args: str) -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(["ip", "-j", "-4", *args], text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("the ip command from iproute2 is required") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ip command failed while reading {' '.join(args)}") from exc
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ip command returned invalid JSON") from exc
    if not isinstance(data, list):
        raise RuntimeError("ip command returned an unexpected response")
    return data


def detect_lan_ip() -> str:
    override = os.environ.get("BROWSERSTREAM_LAN_IP", "").strip()
    if override:
        return validate_override(override)

    routes = _ip_json("route", "show", "default")
    devices = {str(route["dev"]) for route in routes if route.get("dev")}
    addresses: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        links = _ip_json("addr", "show", "dev", device)
        addresses[device] = [
            item
            for link in links
            for item in link.get("addr_info", [])
            if isinstance(item, dict)
        ]
    return select_default_gateway_ip(routes, addresses)


def main() -> int:
    try:
        print(detect_lan_ip())
    except (RuntimeError, ValueError) as exc:
        print(f"Unable to detect LAN IPv4: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

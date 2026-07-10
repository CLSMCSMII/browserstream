import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.detect_lan_ip import select_default_gateway_ip, validate_override


class SelectDefaultGatewayIPTests(unittest.TestCase):
    def test_selects_ipv4_on_default_gateway_subnet(self):
        routes = [
            {
                "dst": "default",
                "gateway": "10.0.0.1",
                "dev": "eth0",
                "prefsrc": "10.0.0.4",
                "metric": 100,
            }
        ]
        addresses = {
            "eth0": [
                {
                    "family": "inet",
                    "local": "10.0.0.4",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ]
        }

        self.assertEqual(select_default_gateway_ip(routes, addresses), "10.0.0.4")

    def test_prefers_route_source_when_interface_has_multiple_matching_addresses(self):
        routes = [
            {
                "dst": "default",
                "gateway": "10.0.0.1",
                "dev": "eth0",
                "prefsrc": "10.0.0.5",
            }
        ]
        addresses = {
            "eth0": [
                {"family": "inet", "local": "10.0.0.4", "prefixlen": 24, "scope": "global"},
                {"family": "inet", "local": "10.0.0.5", "prefixlen": 24, "scope": "global"},
            ]
        }

        self.assertEqual(select_default_gateway_ip(routes, addresses), "10.0.0.5")

    def test_uses_lowest_metric_valid_default_route(self):
        routes = [
            {"dst": "default", "gateway": "192.168.50.1", "dev": "eth1", "metric": 200},
            {"dst": "default", "gateway": "10.20.30.1", "dev": "eth0", "metric": 50},
        ]
        addresses = {
            "eth0": [
                {
                    "family": "inet",
                    "local": "10.20.30.40",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ],
            "eth1": [
                {
                    "family": "inet",
                    "local": "192.168.50.20",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ],
        }

        self.assertEqual(select_default_gateway_ip(routes, addresses), "10.20.30.40")

    def test_skips_address_not_on_gateway_subnet(self):
        routes = [
            {"dst": "default", "gateway": "10.0.1.1", "dev": "eth0", "metric": 10},
            {"dst": "default", "gateway": "192.168.1.1", "dev": "eth1", "metric": 20},
        ]
        addresses = {
            "eth0": [
                {
                    "family": "inet",
                    "local": "10.0.0.4",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ],
            "eth1": [
                {
                    "family": "inet",
                    "local": "192.168.1.10",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ],
        }

        self.assertEqual(select_default_gateway_ip(routes, addresses), "192.168.1.10")

    def test_raises_when_no_address_shares_default_gateway_subnet(self):
        routes = [{"dst": "default", "gateway": "10.0.1.1", "dev": "eth0"}]
        addresses = {
            "eth0": [
                {
                    "family": "inet",
                    "local": "10.0.0.4",
                    "prefixlen": 24,
                    "scope": "global",
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "default gateway"):
            select_default_gateway_ip(routes, addresses)


class ValidateOverrideTests(unittest.TestCase):
    def test_accepts_unicast_ipv4(self):
        self.assertEqual(validate_override("192.0.2.10"), "192.0.2.10")

    def test_rejects_non_ipv4_or_non_host_addresses(self):
        for value in (
            "2001:db8::10",
            "127.0.0.1",
            "0.0.0.0",
            "0.0.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "not-an-ip",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "usable unicast IPv4"):
                    validate_override(value)


class InstallerAtomicCreationTests(unittest.TestCase):
    def test_failed_generation_does_not_leave_final_config(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(source / "install.sh", root / "install.sh")
            shutil.copytree(source / "scripts", root / "scripts")
            (root / "coturn").mkdir()
            (root / "config.example.json").write_text('{"broken": true}\n', encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "BROWSERSTREAM_CONFIG": str(root / "config.json"),
                    "BROWSERSTREAM_LAN_IP": "192.0.2.10",
                }
            )

            failed = subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((root / "config.json").exists())
            self.assertEqual(list(root.glob(".browserstream-config-*")), [])

            shutil.copy2(source / "config.example.json", root / "config.example.json")
            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=environment,
                stdout=subprocess.DEVNULL,
                check=True,
            )
            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["turn"]["urls"], ["turn:192.0.2.10:3478"])


if __name__ == "__main__":
    unittest.main()

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
    @staticmethod
    def copy_installer(root):
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / "install.sh", root / "install.sh")
        shutil.copy2(source / "config.example.json", root / "config.example.json")
        shutil.copytree(source / "scripts", root / "scripts")
        (root / "coturn").mkdir()

    @staticmethod
    def installer_environment(root, lan_ip="192.0.2.10"):
        environment = os.environ.copy()
        environment.update(
            {
                "BROWSERSTREAM_CONFIG": str(root / "config.json"),
                "BROWSERSTREAM_LAN_IP": lan_ip,
            }
        )
        return environment

    def test_interactive_answers_generate_complete_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            answers = "\n".join(
                (
                    "Meeting Room",
                    "https://meeting.example.com/",
                    "main-room",
                    "Main Meeting Room",
                    "n",
                    "turn.example.com",
                    "turn:198.51.100.20:3478",
                    "198.51.100.20",
                    "198.51.100.21",
                    "",
                )
            )

            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input=answers,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["app_name"], "Meeting Room")
            self.assertEqual(generated["public_url"], "https://meeting.example.com")
            self.assertEqual(generated["allowed_origins"], ["https://meeting.example.com"])
            self.assertEqual(len(generated["rooms"]), 1)
            self.assertEqual(generated["rooms"][0]["id"], "main-room")
            self.assertEqual(generated["rooms"][0]["label"], "Main Meeting Room")
            self.assertGreaterEqual(len(generated["rooms"][0]["display_token"]), 32)
            self.assertEqual(generated["turn"]["urls"], ["turn:198.51.100.20:3478"])
            self.assertGreaterEqual(len(generated["turn"]["shared_secret"]), 48)
            self.assertEqual(generated["coturn"]["realm"], "turn.example.com")
            self.assertEqual(generated["coturn"]["listening_ip"], "198.51.100.20")
            self.assertEqual(generated["coturn"]["relay_ip"], "198.51.100.21")

    def test_enter_accepts_all_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)

            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["app_name"], "AwareStream")
            self.assertEqual(generated["public_url"], "https://browserstream.example.com")
            self.assertEqual(generated["allowed_origins"], ["https://browserstream.example.com"])
            self.assertEqual(
                [(room["id"], room["label"]) for room in generated["rooms"]],
                [("awmeeting", "Aware Building")],
            )
            self.assertEqual(generated["turn"]["urls"], ["turn:192.0.2.10:3478"])
            self.assertEqual(generated["coturn"]["realm"], "browserstream.example.com")
            self.assertEqual(generated["coturn"]["listening_ip"], "192.0.2.10")
            self.assertEqual(generated["coturn"]["relay_ip"], "192.0.2.10")

    def test_invalid_answer_is_reprompted_before_config_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            answers = "\n\nINVALID ROOM\nvalid-room\n\n\n\n\n\n\n"

            completed = subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input=answers,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )

            self.assertIn("Invalid value:", completed.stderr)
            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["rooms"][0]["id"], "valid-room")

    def test_init_persists_compose_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)

            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            compose_env = root / ".env"
            values = dict(
                line.split("=", 1)
                for line in compose_env.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["BROWSERSTREAM_BIND_ADDRESS"], "192.0.2.10")
            self.assertEqual(values["BROWSERSTREAM_PORT"], "18080")
            self.assertEqual(values["BROWSERSTREAM_UID"], str(os.getuid()))
            self.assertEqual(values["BROWSERSTREAM_GID"], str(os.getgid()))
            self.assertEqual(compose_env.stat().st_mode & 0o777, 0o600)

    def test_init_updates_managed_environment_without_losing_operator_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            compose_env = root / ".env"
            compose_env.write_text(
                "OPERATOR_SETTING=keep-me\n"
                "BROWSERSTREAM_BIND_ADDRESS=198.51.100.99\n",
                encoding="utf-8",
            )

            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            lines = compose_env.read_text(encoding="utf-8").splitlines()
            self.assertIn("OPERATOR_SETTING=keep-me", lines)
            self.assertEqual(lines.count("BROWSERSTREAM_BIND_ADDRESS=192.0.2.10"), 1)
            self.assertNotIn("BROWSERSTREAM_BIND_ADDRESS=198.51.100.99", lines)

    def test_init_refuses_symlinked_compose_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            target = root / "operator.env"
            target.write_text("DO_NOT_CHANGE=yes\n", encoding="utf-8")
            (root / ".env").symlink_to(target)

            completed = subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Refusing to replace symlink", completed.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "DO_NOT_CHANGE=yes\n")

    def test_plain_install_uses_interactive_coturn_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.log"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\nprintf '%s %s\\n' \"$BROWSERSTREAM_BIND_ADDRESS\" \"$*\" >> \"$DOCKER_LOG\"\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            environment = self.installer_environment(root)
            environment.update(
                {
                    "DOCKER_LOG": str(docker_log),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )

            subprocess.run(
                ["sh", "install.sh"],
                cwd=root,
                env=environment,
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            calls = docker_log.read_text(encoding="utf-8")
            self.assertIn("192.0.2.10 compose --profile turn up -d", calls)

    def test_plain_install_can_skip_bundled_coturn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.log"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\nprintf '%s %s\\n' \"$BROWSERSTREAM_BIND_ADDRESS\" \"$*\" >> \"$DOCKER_LOG\"\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            environment = self.installer_environment(root)
            environment.update(
                {
                    "DOCKER_LOG": str(docker_log),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )
            answers = "\n\n\n\nn\n\n\n\n\n"

            subprocess.run(
                ["sh", "install.sh"],
                cwd=root,
                env=environment,
                input=answers,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            calls = docker_log.read_text(encoding="utf-8")
            self.assertIn("192.0.2.10 compose up -d browserstream", calls)
            self.assertNotIn("--profile turn up -d", calls)

    def test_add_room_preserves_existing_room_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            environment = self.installer_environment(root)
            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=environment,
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            before = (root / "config.json").read_bytes()
            old_config = json.loads(before)

            completed = subprocess.run(
                ["sh", "install.sh", "--add-room", "--init-only"],
                cwd=root,
                env=environment,
                input="training\nTraining Room\nn\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["rooms"][0], old_config["rooms"][0])
            self.assertEqual(generated["rooms"][1]["id"], "training")
            self.assertEqual(generated["rooms"][1]["label"], "Training Room")
            self.assertGreaterEqual(len(generated["rooms"][1]["display_token"]), 32)
            self.assertNotEqual(
                generated["rooms"][0]["display_token"],
                generated["rooms"][1]["display_token"],
            )
            backups = list(root.glob("config.json.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertIn("/room/training#token=", completed.stdout)

    def test_add_room_reprompts_duplicate_and_can_add_multiple_rooms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            environment = self.installer_environment(root)
            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=environment,
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            oversized_label = "😀" * 26
            completed = subprocess.run(
                ["sh", "install.sh", "--add-room", "--init-only"],
                cwd=root,
                env=environment,
                input=f"awmeeting\nroom-2\n{oversized_label}\nRoom 2\ny\nroom-3\nRoom 3\nn\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )

            self.assertIn("already exists", completed.stderr)
            self.assertIn("UTF-8 bytes", completed.stderr)
            generated = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual([room["id"] for room in generated["rooms"]], ["awmeeting", "room-2", "room-3"])
            tokens = [room["display_token"] for room in generated["rooms"]]
            self.assertEqual(len(tokens), len(set(tokens)))

    def test_add_room_requires_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            completed = subprocess.run(
                ["sh", "install.sh", "--add-room", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                input="room-2\nRoom 2\nn\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires an existing configuration", completed.stderr)
            self.assertFalse((root / "config.json").exists())

    def test_add_room_rejects_configuration_at_room_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            config = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
            config["rooms"] = [
                {
                    "id": f"room-{index}",
                    "label": f"Room {index}",
                    "display_token": f"secure-display-token-{index:03d}",
                }
                for index in range(1, 101)
            ]
            path = root / "config.json"
            path.write_text(json.dumps(config) + "\n", encoding="utf-8")
            before = path.read_bytes()

            completed = subprocess.run(
                ["sh", "install.sh", "--add-room", "--init-only"],
                cwd=root,
                env=self.installer_environment(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("maximum of 100 rooms", completed.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(root.glob("config.json.backup-*")), [])

    def test_add_room_redeploys_browserstream_without_touching_coturn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.copy_installer(root)
            environment = self.installer_environment(root)
            subprocess.run(
                ["sh", "install.sh", "--init-only"],
                cwd=root,
                env=environment,
                input="\n" * 9,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            turnserver = root / "coturn" / "turnserver.conf"
            custom_coturn = b"# operator-managed coturn settings\ncustom-option=yes\n"
            turnserver.write_bytes(custom_coturn)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.log"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            environment.update(
                {
                    "DOCKER_LOG": str(docker_log),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )

            subprocess.run(
                ["sh", "install.sh", "--add-room"],
                cwd=root,
                env=environment,
                input="training\nTraining Room\nn\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            calls = docker_log.read_text(encoding="utf-8")
            self.assertIn("compose up -d --force-recreate browserstream", calls)
            self.assertNotIn("--profile turn", calls)
            self.assertEqual(turnserver.read_bytes(), custom_coturn)

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

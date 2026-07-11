import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
KIOSK = ROOT / "kiosk.sh"


def room(room_id, label, token):
    return {"id": room_id, "label": label, "display_token": token}


class KioskURLTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = self.directory / "config.json"
        self.environment = os.environ.copy()
        self.environment["BROWSERSTREAM_CONFIG"] = str(self.config)

    def write_config(self, rooms=None, public_url="https://meeting.example.com/"):
        if rooms is None:
            rooms = [room("awmeeting", "Aware Building", "DisplayToken+/=123456789")]
        self.config.write_text(
            json.dumps({"public_url": public_url, "rooms": rooms}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.config, 0o600)

    def run_kiosk(self, *arguments, input_text=None, check=True, environment=None):
        return subprocess.run(
            [str(KIOSK), *arguments],
            env=environment or self.environment,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def test_single_room_prints_only_copyable_encoded_url_and_preserves_config(self):
        self.write_config()
        before = self.config.read_bytes()
        before_mode = self.config.stat().st_mode & 0o777

        completed = self.run_kiosk()

        token = quote("DisplayToken+/=123456789", safe="")
        self.assertEqual(
            completed.stdout,
            f"https://meeting.example.com/room/awmeeting#token={token}\n",
        )
        self.assertEqual(completed.stderr, "")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.config.stat().st_mode & 0o777, before_mode)

    def test_multiple_rooms_prompt_until_valid_selection(self):
        self.write_config(
            [
                room("awmeeting", "Aware Building", "DisplayToken-111111111111"),
                room("training", "Training Room", "DisplayToken-222222222222"),
            ]
        )

        completed = self.run_kiosk(input_text="invalid\n3\n2\n")

        self.assertEqual(
            completed.stdout,
            "https://meeting.example.com/room/training#token=DisplayToken-222222222222\n",
        )
        self.assertIn("1) Aware Building (awmeeting)", completed.stderr)
        self.assertIn("2) Training Room (training)", completed.stderr)
        self.assertEqual(completed.stderr.count("Select room [1]:"), 3)
        self.assertIn("enter a number from 1 to 2", completed.stderr)

    def test_closed_stdin_with_multiple_rooms_fails_without_disclosing_token(self):
        self.write_config(
            [
                room("awmeeting", "Aware Building", "DisplayToken-111111111111"),
                room("training", "Training Room", "DisplayToken-222222222222"),
            ]
        )

        completed = self.run_kiosk(input_text="", check=False)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("stdin is closed", completed.stderr)
        self.assertNotIn("DisplayToken", completed.stderr)

    def test_enter_selects_first_room_without_polluting_stdout(self):
        self.write_config(
            [
                room("awmeeting", "Aware Building", "DisplayToken-111111111111"),
                room("training", "Training Room", "DisplayToken-222222222222"),
            ]
        )

        completed = self.run_kiosk(input_text="\n")

        self.assertEqual(
            completed.stdout,
            "https://meeting.example.com/room/awmeeting#token=DisplayToken-111111111111\n",
        )
        self.assertIn("Select room [1]:", completed.stderr)

    def test_direct_room_selection_prints_only_url(self):
        self.write_config(
            [
                room("awmeeting", "Aware Building", "DisplayToken-111111111111"),
                room("training", "Training Room", "DisplayToken-222222222222"),
            ]
        )

        completed = self.run_kiosk("training")

        self.assertEqual(
            completed.stdout,
            "https://meeting.example.com/room/training#token=DisplayToken-222222222222\n",
        )
        self.assertEqual(completed.stderr, "")

    def test_all_prints_labeled_urls_for_every_room(self):
        self.write_config(
            [
                room("awmeeting", "Aware Building", "DisplayToken-111111111111"),
                room("training", "Training Room", "DisplayToken-222222222222"),
            ]
        )

        completed = self.run_kiosk("--all")

        self.assertEqual(
            completed.stdout,
            "Aware Building (awmeeting):\n"
            "https://meeting.example.com/room/awmeeting#token=DisplayToken-111111111111\n\n"
            "Training Room (training):\n"
            "https://meeting.example.com/room/training#token=DisplayToken-222222222222\n",
        )
        self.assertEqual(completed.stderr, "")

    def test_unknown_room_fails_without_printing_any_token(self):
        self.write_config()

        completed = self.run_kiosk("missing-room", check=False)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn('room "missing-room" was not found', completed.stderr)
        self.assertNotIn("DisplayToken", completed.stderr)

    def test_missing_malformed_and_invalid_public_url_fail_cleanly(self):
        missing_environment = self.environment.copy()
        missing_environment["BROWSERSTREAM_CONFIG"] = str(self.directory / "missing.json")
        missing = self.run_kiosk(check=False, environment=missing_environment)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("configuration not found", missing.stderr)

        self.config.write_text("{not-json\n", encoding="utf-8")
        malformed = self.run_kiosk(check=False)
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("invalid JSON", malformed.stderr)

        self.write_config(public_url="javascript:alert(1)")
        invalid_url = self.run_kiosk(check=False)
        self.assertNotEqual(invalid_url.returncode, 0)
        self.assertIn("public_url", invalid_url.stderr)

    def test_placeholder_or_duplicate_tokens_are_rejected_without_disclosure(self):
        self.write_config(
            [room("awmeeting", "Aware Building", "GENERATE_A_RANDOM_DISPLAY_TOKEN")]
        )
        placeholder = self.run_kiosk(check=False)
        self.assertNotEqual(placeholder.returncode, 0)
        self.assertIn("generated secret", placeholder.stderr)
        self.assertNotIn("GENERATE_A_RANDOM_DISPLAY_TOKEN", placeholder.stderr)

        duplicate = "DisplayToken-duplicate-123456"
        self.write_config(
            [
                room("awmeeting", "Aware Building", duplicate),
                room("training", "Training Room", duplicate),
            ]
        )
        repeated = self.run_kiosk(check=False)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("unique", repeated.stderr)
        self.assertNotIn(duplicate, repeated.stderr)

    def test_invalid_arguments_show_usage(self):
        self.write_config()

        completed = self.run_kiosk("awmeeting", "extra", check=False)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("usage:", completed.stderr.lower())

    def test_default_config_path_is_resolved_beside_launcher(self):
        installation = self.directory / "installation"
        scripts = installation / "scripts"
        caller = self.directory / "caller"
        scripts.mkdir(parents=True)
        caller.mkdir()
        shutil.copy2(KIOSK, installation / "kiosk.sh")
        shutil.copy2(ROOT / "scripts" / "kiosk_url.py", scripts / "kiosk_url.py")
        self.config = installation / "config.json"
        self.write_config()
        environment = os.environ.copy()
        environment.pop("BROWSERSTREAM_CONFIG", None)

        completed = subprocess.run(
            [str(installation / "kiosk.sh")],
            cwd=caller,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertIn("https://meeting.example.com/room/awmeeting#token=", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_room_id_longer_than_server_limit_is_rejected(self):
        self.write_config(
            [room("a" * 64, "Too Long", "DisplayToken-111111111111")]
        )

        completed = self.run_kiosk(check=False)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("room ID", completed.stderr)


if __name__ == "__main__":
    unittest.main()

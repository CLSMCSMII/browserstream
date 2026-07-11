import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "files" / "static" / "main.js"
MAIN_HTML = ROOT / "files" / "main.html"
MAIN_CSS = ROOT / "files" / "static" / "main.css"


def function_body(source, name):
    match = re.search(rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{", source)
    if not match:
        raise AssertionError(f"function {name} not found")
    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"function {name} is not closed")


class PresenterFlowRegressionTests(unittest.TestCase):
    def setUp(self):
        self.source = MAIN_JS.read_text(encoding="utf-8")

    def test_screen_capture_is_requested_before_presenter_websocket_authentication(self):
        body = function_body(self.source, "startSharing")
        capture = body.find("getDisplayMedia")
        websocket = body.find("new WebSocket")

        self.assertGreaterEqual(capture, 0, "startSharing must request screen capture")
        self.assertGreaterEqual(websocket, 0, "startSharing must open presenter signaling")
        self.assertLess(capture, websocket, "screen capture must precede authentication")

    def test_authentication_does_not_request_screen_capture_again(self):
        body = function_body(self.source, "startSharing")
        self.assertNotRegex(
            body,
            r"authAccepted[^\n]*capture",
            "authAccepted must start the captured stream without a second capture prompt",
        )

    def test_kiosk_hides_replacement_code_during_presenter_negotiation(self):
        self.assertIn("m.Type==='refreshCode'", self.source)
        self.assertIn("showDisplayConnecting()", self.source)
        self.assertNotIn(
            "m.Type==='refreshCode'){el('kiosk-code').textContent=m.Value",
            self.source,
        )

    def test_kiosk_shows_room_label_between_brand_and_public_url(self):
        html = MAIN_HTML.read_text(encoding="utf-8")
        css = MAIN_CSS.read_text(encoding="utf-8")

        brand = html.index('id="kiosk-brand"')
        location = html.index('id="kiosk-location"')
        public_url = html.index('id="public-url"')
        self.assertLess(brand, location)
        self.assertLess(location, public_url)
        self.assertIn('Location: <span id="kiosk-location"></span>', html)
        self.assertIn("el('kiosk-location').textContent = state.room ? state.room.label : '';", self.source)
        self.assertRegex(css, r"(?s)\.kiosk-location\s*\{[^}]*font-size:")


if __name__ == "__main__":
    unittest.main()

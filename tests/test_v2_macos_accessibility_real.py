"""Real AX reads of a window this test owns (tests/v2_macos_ax_window.py, run in a subprocess). macOS only; skipped
when the interpreter running the tests has no Accessibility permission (never prompted)."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from trace_v2.observations import Context, ScreenshotCapture
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import build_draft
from v2_observation_helpers import PNG, T0

try:
    import ApplicationServices
    from trace_v2.macos import AccessibilityReader, MacSources
    ON_MAC = sys.platform == "darwin"
    TRUSTED = bool(ApplicationServices.AXIsProcessTrusted())
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC, TRUSTED = False, False

HELPER = Path(__file__).with_name("v2_macos_ax_window.py")
TITLE = "Trace AX Test Window"
NEED = "Accessibility is not granted to the process running the tests (System Settings > Privacy & Security)"
SECURE = "hunter2-secret-value"
FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"


class OwnWindow:
    def __init__(self, normal, secure=SECURE):
        self.args = [sys.executable, str(HELPER), normal, secure]

    def __enter__(self):
        self.proc = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        ready = self.proc.stdout.readline().split()
        if len(ready) != 2 or ready[0] != "READY":
            self.proc.kill()
            raise RuntimeError("the AX test window did not start")
        self.pid = int(ready[1])
        return self

    def context(self):
        return Context({"bundle_id": "com.example.axtest", "name": "AXTest", "pid": self.pid},
                       {"title": TITLE, "id": 1}, {"id": 1, "bounds": [0, 0, 100, 100], "scale": 1.0})

    def __exit__(self, *exc):
        self.proc.stdin.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class _Source:
    def __init__(self, context):
        self._context = context

    def context(self):
        return self._context

    def secure_input(self):
        return False

    def screenshot(self, display_id, **_scope):
        return ScreenshotCapture(PNG, "png", 32, 32, T0)


class _Ocr:
    version = "ocr-fake/1"

    def read(self, screenshot, context):
        return {"lines": []}, ""


@unittest.skipUnless(ON_MAC, "macOS only")
@unittest.skipUnless(TRUSTED, NEED)
class RealAccessibility(unittest.TestCase):
    def test_normal_value_is_read_and_the_secure_value_never_appears(self):
        with OwnWindow("ordinary text 42") as w:
            raw, text = AccessibilityReader().read(w.context())
        blob = json.dumps(raw) + text
        self.assertIn("ordinary text 42", text)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn(SECURE, blob)
        nodes = {n.get("identifier"): n for n in raw["root"]["children"]}
        self.assertEqual(nodes["normal-field"]["value"], "ordinary text 42")
        self.assertEqual(nodes["secure-field"]["value_omitted"], "secure")
        self.assertNotIn("value", nodes["secure-field"])
        self.assertEqual(len(nodes["normal-field"]["frame"]), 4)
        self.assertFalse(any(raw["truncated"].values()))

    def test_a_fake_key_in_a_field_is_redacted_and_withholds_the_screenshot(self):
        with OwnWindow(f"aws key {FAKE_KEY}") as w:
            sources = MacSources(context_source=_Source(w.context()), screenshot_source=_Source(w.context()),
                                 ocr=_Ocr())
            draft, _ = build_draft(sources, EventBuffer(start_ns=T0 - 10), "ax:real", clock_ns=lambda: T0)
        self.assertEqual(draft.accessibility.status, "succeeded")
        blob = json.dumps(draft.accessibility.raw) + draft.accessibility.text
        self.assertNotIn(FAKE_KEY, blob)
        self.assertNotIn("hunter2", blob)
        self.assertIn("[REDACTED:aws_access_key]", draft.accessibility.text)
        self.assertEqual(draft.screenshot.reason, "secret_on_screen")


@unittest.skipUnless(ON_MAC, "macOS only")
class WithoutPermission(unittest.TestCase):
    def test_untrusted_process_is_unavailable_and_never_prompts(self):
        from trace_v2.macos.accessibility import SystemAX
        from trace_v2.observations.pipeline import ReaderUnavailable
        api = SystemAX()
        api.trusted = lambda: False
        with self.assertRaises(ReaderUnavailable):
            AccessibilityReader(api=api).read(Context({"bundle_id": "x", "name": "x", "pid": 1}, {}, {}))


if __name__ == "__main__":
    unittest.main()

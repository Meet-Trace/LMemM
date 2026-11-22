"""macOS screenshot and the real pipeline. Needs Screen Recording; skipped (with the reason) when not granted."""

import sys
import unittest

from trace_v2.observations import Context
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import observe_once
from v2_observation_helpers import SEC, T0, TempStore

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
try:
    import Quartz
    from trace_v2.macos import ContextSource, MacSources, ScreenshotSource, VisionOCR
    ON_MAC = sys.platform == "darwin"
    GRANTED = bool(Quartz.CGPreflightScreenCaptureAccess())
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC, GRANTED = False, False
NEED = "Screen Recording is not granted to the process running the tests (System Settings > Privacy & Security)"


def real_sources():
    # a long OCR timeout: the first Vision call on a fresh OS can take over a minute (see test_v2_macos_ocr_vision)
    return MacSources(context_source=ContextSource(browser_state=lambda bundle_id: {}), ocr=VisionOCR(timeout_s=180.0))


@unittest.skipUnless(ON_MAC, "macOS only")
@unittest.skipUnless(GRANTED, NEED)
class RealScreenshot(unittest.TestCase):
    def test_png_is_lossless_full_resolution_of_the_display(self):
        sources = real_sources()
        ctx = sources.context()
        shot = sources.screenshot()
        shot.validate()
        self.assertEqual(shot.format, "png")
        self.assertTrue(shot.data.startswith(PNG_SIGNATURE))
        self.assertEqual([shot.width, shot.height], ctx.display["pixel_size"])
        self.assertIn(sources._screenshot.last_path, ("screencapturekit", "cgdisplay"))

    def test_observe_once_stores_a_screenshot_through_the_real_pipeline(self):
        sources = real_sources()
        with TempStore() as t:
            result = observe_once(t.store, sources, EventBuffer(start_ns=T0), "mac:0")
            obs = t.store.get(result.observation_id)
            self.assertEqual(obs["evidence"]["screenshot"]["status"], "stored")
            data = t.store.screenshot_bytes(result.observation_id)
            self.assertTrue(data.startswith(PNG_SIGNATURE))
            self.assertEqual(obs["metadata"]["capture_version"], "macos-capture/1")

    def test_ocr_and_accessibility_run_on_a_real_capture(self):
        sources = real_sources()
        with TempStore() as t:
            result = observe_once(t.store, sources, EventBuffer(start_ns=T0), "mac:1")
            status = t.store.get(result.observation_id)["metadata"]["extraction_status"]
            self.assertEqual((status["ocr"]["status"], status["accessibility"]["status"]), ("succeeded", "succeeded"))


@unittest.skipUnless(ON_MAC, "macOS only")
class PermissionAndPrivacy(unittest.TestCase):
    def test_missing_permission_raises_and_never_prompts(self):
        source = ScreenshotSource()
        original = Quartz.CGPreflightScreenCaptureAccess
        Quartz.CGPreflightScreenCaptureAccess = lambda: False
        try:
            with self.assertRaises(PermissionError):
                source.screenshot()
        finally:
            Quartz.CGPreflightScreenCaptureAccess = original

    def test_excluded_app_never_calls_screenshot(self):
        calls = []

        class Fake(MacSources):
            def __init__(self):
                super().__init__(context_source=self, screenshot_source=self)

            def context(self):
                return Context({"bundle_id": "com.1password.1password", "name": "1Password", "pid": 9},
                               {"title": "Vault", "id": 1}, {"id": 1, "bounds": [0, 0, 1, 1]})

            def secure_input(self):
                return False

            def screenshot(self, display_id=None):
                calls.append(display_id)
                raise AssertionError("pixels must not be captured for an excluded app")
        sources = Fake()
        with TempStore() as t:
            result = observe_once(t.store, sources, EventBuffer(start_ns=T0 - 10 * SEC), "mac:x")
            self.assertEqual(calls, [])
            self.assertEqual(t.store.get(result.observation_id)["metadata"]["privacy"]["reason"], "excluded_app")


if __name__ == "__main__":
    unittest.main()

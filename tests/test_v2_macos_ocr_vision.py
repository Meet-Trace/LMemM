"""Real Vision on synthetic PNGs rendered here (no screen capture, no permissions). macOS only.

The context and screenshot sources are fakes; OCR is the real VisionOCR; the privacy logic is the real pipeline.
The first Vision call on a freshly installed OS can take over a minute (model compilation), so the reader gets a
generous timeout here; the production default is 10 s."""

import json
import sys
import unittest

from trace_v2.observations import Context, ScreenshotCapture
from trace_v2.observations.models import ScreenshotWithheld
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import build_draft, observe_once
from v2_observation_helpers import T0, TempStore

try:
    from trace_v2.macos import MacSources, VisionOCR
    from v2_macos_render import render_png
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False

FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"                    # AWS's documented example key, not a credential
WIDTH, HEIGHT = 1500, 420


class _Context:
    def context(self):
        return Context({"bundle_id": "com.example.editor", "name": "Editor", "pid": 7},
                       {"title": "notes.txt", "id": 3}, {"id": 1, "bounds": [0, 0, WIDTH, HEIGHT], "scale": 1.0})

    def secure_input(self):
        return False


class _Screen:
    def __init__(self, png):
        self.png = png

    def screenshot(self, display_id):
        return ScreenshotCapture(self.png, "png", WIDTH, HEIGHT, T0)


def sources_for(png):
    return MacSources(context_source=_Context(), screenshot_source=_Screen(png), ocr=VisionOCR(timeout_s=180.0))


def draft_for(png):
    draft, batch = build_draft(sources_for(png), EventBuffer(start_ns=T0 - 10), "vision:0", clock_ns=lambda: T0)
    return draft, batch


SECRET_LINES = ["Deploy notes for the staging cluster", f"aws_access_key_id = {FAKE_KEY}",
                "Meeting with the platform team at three"]
PLAIN_LINES = ["Quarterly planning review", "Invoice 20481 was approved on Tuesday",
               "The quick brown fox jumps over the lazy dog"]


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RealVisionThroughThePipeline(unittest.TestCase):
    def test_a_secret_on_screen_is_redacted_and_the_screenshot_is_withheld(self):
        draft, _ = draft_for(render_png(SECRET_LINES, WIDTH, HEIGHT, font_size=44.0))
        self.assertEqual(draft.ocr.status, "succeeded")
        self.assertIn("[REDACTED:aws_access_key]", draft.ocr.text)
        self.assertNotIn(FAKE_KEY, draft.ocr.text)
        self.assertNotIn(FAKE_KEY, json.dumps(draft.ocr.raw))
        self.assertIn("Meeting with the platform team", draft.ocr.text)       # the rest of the text is kept
        self.assertIsInstance(draft.screenshot, ScreenshotWithheld)
        self.assertEqual(draft.screenshot.reason, "secret_on_screen")
        self.assertGreaterEqual(draft.privacy["redactions"].get("aws_access_key", 0), 1)   # raw and text both count

    def test_stored_observation_has_no_key_and_no_screenshot(self):
        png = render_png(SECRET_LINES, WIDTH, HEIGHT, font_size=44.0)
        with TempStore() as t:
            result = observe_once(t.store, sources_for(png), EventBuffer(start_ns=T0 - 10), "vision:1",
                                  clock_ns=lambda: T0)
            obs = t.store.get(result.observation_id)
            self.assertEqual(obs["evidence"]["screenshot"]["status"], "withheld")
            self.assertNotIn(FAKE_KEY, json.dumps(obs))

    def test_ordinary_text_is_recognised_and_the_screenshot_is_kept(self):
        draft, _ = draft_for(render_png(PLAIN_LINES, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica"))
        self.assertEqual(draft.ocr.status, "succeeded")
        for line in PLAIN_LINES:
            self.assertIn(line, draft.ocr.text)
        self.assertEqual(draft.ocr.text.split("\n"), PLAIN_LINES)             # reading order
        self.assertIsInstance(draft.screenshot, ScreenshotCapture)
        self.assertEqual(draft.ocr.raw["image_size"], [WIDTH, HEIGHT])
        first = draft.ocr.raw["lines"][0]
        self.assertLess(first["box"][1], draft.ocr.raw["lines"][1]["box"][1])  # top-left origin: first line is higher
        self.assertTrue(0 < first["confidence"] <= 1)
        self.assertEqual(draft.privacy["redactions"], {})

    def test_light_text_on_a_dark_background_is_recognised(self):
        draft, _ = draft_for(render_png(PLAIN_LINES[:2], WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica", dark=True))
        self.assertEqual(draft.ocr.text.split("\n"), PLAIN_LINES[:2])


if __name__ == "__main__":
    unittest.main()

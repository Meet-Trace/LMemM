"""Vision OCR reader without Vision: a fake recognizer result drives box conversion, ordering, timeout and failure
recording through the real pipeline.run_reader. Skips (with the reason) off macOS or without pyobjc."""

import json
import sys
import threading
import unittest
from types import SimpleNamespace as NS

from trace_v2.observations import Context, ScreenshotCapture
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import ReaderUnavailable, build_draft, run_reader
from v2_observation_helpers import PNG, T0

try:
    from trace_v2.macos import MacSources, VisionOCR
    from trace_v2.macos import ocr as ocr_module
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False


def observation(text, x, y, w, h, confidence=0.9):
    """A Vision-like observation; (x, y, w, h) is Vision's normalised rect with a bottom-left origin."""
    candidate = NS(string=lambda: text, confidence=lambda: confidence)
    return NS(topCandidates_=lambda n: [candidate][:n] if text is not None else [],
              boundingBox=lambda: NS(origin=NS(x=x, y=y), size=NS(width=w, height=h)))


def recognition(observations, width=1000, height=500, languages=("en_US",)):
    return ocr_module.Recognition(observations, width, height, languages)


def fake_factory(result=None, error=None, cancel=None, block=None):
    class Recognizer:
        def __call__(self, png):
            self.png = png
            if block is not None:
                block.wait()
            if error:
                raise error
            return result
    recognizer = Recognizer()
    if cancel is not None:
        recognizer.cancel = cancel
    return lambda: recognizer


def shot(width=1000, height=500, fmt="png"):
    return ScreenshotCapture(PNG, fmt, width, height, T0)


def clock():
    ticks = iter(range(10, 10_000, 10))
    return lambda: next(ticks)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class BoxesAndOrder(unittest.TestCase):
    def test_box_is_pixels_with_a_top_left_origin(self):
        # Vision: origin at the bottom-left. y=0.8,h=0.1 sits near the top: top edge at (1-0.8-0.1)*500 = 50.
        reader = VisionOCR(recognizer_factory=fake_factory(recognition([observation("hi", 0.1, 0.8, 0.5, 0.1)])))
        raw, _ = reader.read(shot())
        self.assertEqual(raw["lines"][0]["box"], [100.0, 50.0, 500.0, 50.0])

    def test_box_is_clamped_to_the_image(self):
        lines = ocr_module.lines_from_observations([observation("edge", -0.1, 0.95, 1.3, 0.2)], 1000, 500)
        x, y, w, h = lines[0]["box"]
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1000)
        self.assertLessEqual(y + h, 500)

    def test_lines_are_read_top_to_bottom_then_left_to_right(self):
        observations = [observation("bottom", 0.1, 0.1, 0.3, 0.05),
                        observation("right", 0.6, 0.8, 0.2, 0.05),
                        observation("left", 0.1, 0.81, 0.2, 0.05),      # slightly higher than "right": same row
                        observation("middle", 0.1, 0.5, 0.3, 0.05)]
        raw, text = VisionOCR(recognizer_factory=fake_factory(recognition(observations))).read(shot())
        self.assertEqual(text, "left\nright\nmiddle\nbottom")
        self.assertEqual([ln["text"] for ln in raw["lines"]], text.split("\n"))

    def test_blank_and_candidate_less_observations_are_dropped(self):
        observations = [observation("   ", 0.1, 0.5, 0.1, 0.05), observation(None, 0.1, 0.4, 0.1, 0.05),
                        observation("kept", 0.1, 0.3, 0.1, 0.05)]
        raw, text = VisionOCR(recognizer_factory=fake_factory(recognition(observations))).read(shot())
        self.assertEqual((len(raw["lines"]), text), (1, "kept"))

    def test_no_text_is_a_success_with_empty_text(self):
        raw, text = VisionOCR(recognizer_factory=fake_factory(recognition([]))).read(shot())
        self.assertEqual((raw["lines"], text), ([], ""))


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RawOutput(unittest.TestCase):
    def test_raw_has_the_documented_shape_and_is_json(self):
        reader = VisionOCR(recognizer_factory=fake_factory(recognition([observation("a", 0.1, 0.5, 0.2, 0.1, 0.87654)],
                                                                       1400, 900, ["en_US", "fr_FR"])),
                           os_version="99.1")
        raw, text = reader.read(shot(1400, 900))
        json.dumps(raw, allow_nan=False)
        self.assertEqual(set(raw), {"lines", "image_size", "recognition_level", "languages", "os_version"})
        self.assertEqual(raw["image_size"], [1400, 900])
        self.assertEqual((raw["recognition_level"], raw["languages"], raw["os_version"]),
                         ("accurate", ["en_US", "fr_FR"], "99.1"))
        self.assertEqual(raw["lines"][0]["confidence"], 0.877)
        self.assertEqual(reader.version, "vision-ocr/1")

    def test_the_recognizer_gets_the_exact_screenshot_bytes(self):
        factory = fake_factory(recognition([]))
        recognizer = factory()
        VisionOCR(recognizer_factory=lambda: recognizer).read(shot())
        self.assertEqual(recognizer.png, PNG)

    def test_only_png_is_read(self):
        with self.assertRaises(ValueError):
            VisionOCR(recognizer_factory=fake_factory(recognition([]))).read(shot(fmt="jpeg"))

    def test_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            VisionOCR(timeout_s=0)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RecordedByThePipeline(unittest.TestCase):
    def test_success_records_versions_and_timestamps(self):
        reader = VisionOCR(recognizer_factory=fake_factory(recognition([observation("ok", 0.1, 0.5, 0.2, 0.1)])))
        result = run_reader(reader, clock(), shot(), None)
        self.assertEqual((result.status, result.text, result.reader_version), ("succeeded", "ok", "vision-ocr/1"))
        self.assertEqual((result.started_at_ns, result.finished_at_ns), (10, 20))

    def test_failure_is_recorded_not_raised(self):
        reader = VisionOCR(recognizer_factory=fake_factory(error=RuntimeError("Vision exploded")))
        result = run_reader(reader, clock(), shot(), None)
        self.assertEqual((result.status, result.error_code, result.raw, result.text), ("failed", "RuntimeError", None, None))
        self.assertIn("Vision exploded", result.error_message)

    def test_vision_missing_is_unavailable(self):
        reader = VisionOCR(recognizer_factory=fake_factory(error=ReaderUnavailable("no Vision")))
        result = run_reader(reader, clock(), shot(), None)
        self.assertEqual((result.status, result.error_code), ("unavailable", "ReaderUnavailable"))

    def test_timeout_is_a_failed_result_with_code_timeout_and_cancels_the_request(self):
        release, cancelled = threading.Event(), threading.Event()
        reader = VisionOCR(timeout_s=0.05, recognizer_factory=fake_factory(
            recognition([]), block=release, cancel=lambda: (cancelled.set(), release.set())))
        result = run_reader(reader, clock(), shot(), None)
        self.assertEqual((result.status, result.error_code), ("failed", "Timeout"))
        self.assertIsNotNone(result.finished_at_ns)
        self.assertTrue(cancelled.is_set())

    def test_a_stuck_worker_makes_the_next_read_fail_fast_then_recovery_works(self):
        release = threading.Event()                  # cancel does nothing: the worker stays stuck
        factory = fake_factory(recognition([observation("later", 0.1, 0.5, 0.2, 0.1)]), block=release, cancel=lambda: None)
        reader = VisionOCR(timeout_s=0.05, recognizer_factory=factory)
        self.assertEqual(run_reader(reader, clock(), shot(), None).error_code, "Timeout")
        second = run_reader(reader, clock(), shot(), None)
        self.assertEqual((second.status, second.error_code), ("failed", "Timeout"))
        self.assertIn("still running", second.error_message)
        release.set()
        reader._worker.join(5)
        self.assertEqual(run_reader(reader, clock(), shot(), None).text, "later")


class _Context:
    def __init__(self, bundle_id):
        self.bundle_id = bundle_id

    def context(self):
        return Context({"bundle_id": self.bundle_id, "name": "App", "pid": 5}, {"title": "w", "id": 1},
                       {"id": 1, "bounds": [0, 0, 100, 50], "scale": 1.0})

    def secure_input(self):
        return False


class _Screens:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def screenshot(self, display_id, **_scope):
        self.calls += 1
        if self.fail:
            raise PermissionError("Screen Recording is not granted")
        return shot(100, 50)


class _CountingOCR:
    version = "counting/1"

    def __init__(self):
        self.calls = 0

    def read(self, screenshot, context):
        self.calls += 1
        return {"lines": []}, ""


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class OcrOnlyRunsWhenAllowed(unittest.TestCase):
    def draft(self, bundle_id="com.example.editor", fail_screenshot=False):
        ocr = _CountingOCR()
        sources = MacSources(context_source=_Context(bundle_id), screenshot_source=_Screens(fail_screenshot), ocr=ocr)
        draft, _ = build_draft(sources, EventBuffer(start_ns=T0 - 10), "ocr:x", clock_ns=lambda: T0)
        return draft, ocr

    def test_ocr_runs_on_the_captures_own_screenshot(self):
        draft, ocr = self.draft()
        self.assertEqual((ocr.calls, draft.ocr.status), (1, "succeeded"))

    def test_withheld_context_is_never_read(self):
        draft, ocr = self.draft("com.1password.1password")
        self.assertEqual((ocr.calls, draft.ocr.status, draft.ocr.error_code), (0, "withheld", "excluded_app"))

    def test_a_failed_screenshot_skips_ocr(self):
        draft, ocr = self.draft(fail_screenshot=True)
        self.assertEqual((ocr.calls, draft.ocr.status, draft.ocr.error_code), (0, "skipped", "no_screenshot"))

    def test_default_ocr_is_vision(self):
        sources = MacSources(context_source=_Context("com.example.editor"), screenshot_source=_Screens())
        self.assertIsInstance(sources.ocr, VisionOCR)
        self.assertEqual(sources.ocr.version, "vision-ocr/1")


if __name__ == "__main__":
    unittest.main()

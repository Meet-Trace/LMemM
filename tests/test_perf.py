"""Cost control: in-memory frames, thumbnails, adaptive timer, fast OCR, retention, batching, metrics."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
import macos
import notes
import resolver
import retention
import store
import tracker
from frames import fake_frame
from test_content import observation


class FrameTests(unittest.TestCase):
    def synthetic_cg(self, w, h):
        import Quartz
        buf = bytearray(b"\xff" * (w * h * 4))                          # opaque white
        ctx = Quartz.CGBitmapContextCreate(buf, w, h, 8, w * 4, Quartz.CGColorSpaceCreateWithName(
            Quartz.kCGColorSpaceSRGB), Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big)
        Quartz.CGContextSetRGBFillColor(ctx, 0, 0, 0, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w // 2, h))   # left half black
        return Quartz.CGBitmapContextCreateImage(ctx)

    def test_cgimage_becomes_frame_with_luminance_and_thumbnail(self):
        frame = macos._frame_from_cg(self.synthetic_cg(400, 200))
        self.assertEqual((frame.width, frame.height, frame.gray.shape), (400, 200, (200, 400)))
        self.assertEqual(int(frame.gray.max()), 255)                     # 255 must not overflow
        self.assertLess(int(frame.gray[100, 50]), 5)                     # black half
        self.assertGreater(int(frame.gray[100, 350]), 250)               # white half
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "t.jpg")
            frame.save(path, long_edge=100, quality=60)
            self.assertEqual(Image.open(path).size, (100, 50))
        frame.release()
        self.assertIsNone(frame.gray)

    def test_very_large_screens_are_scaled_down(self):
        with patch.object(config, "MAX_PX", 300):
            frame = macos._frame_from_cg(self.synthetic_cg(600, 300))
        self.assertEqual((frame.width, frame.height), (300, 150))

    def test_isolated_window_capture_uses_only_requested_window(self):
        import Quartz
        image = self.synthetic_cg(400, 200)
        with patch.object(Quartz, "CGWindowListCreateImage", return_value=image) as capture:
            frame = macos.grab(1, window_id=42)
        capture.assert_called_once_with(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, 42,
                                        Quartz.kCGWindowImageNominalResolution | Quartz.kCGWindowImageBoundsIgnoreFraming)
        self.assertIsNotNone(frame)
        self.assertEqual((frame.width, frame.height), (400, 200))
        frame.release()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(config.use_paths(self.root))
        self.out = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.capture = tracker.Tracker()
        self.second = 0
        self.fast_flags = []

    def files(self):
        return sorted(p.name for p in self.root.iterdir() if p.suffix in {".jpg", ".json"})

    def feed(self, text, frame, ocr=None, trigger="timer", extra=None):
        """Hand the tracker a captured in-memory frame; Vision is replaced by `ocr` or a canned result."""
        self.second += 5
        ts = (datetime(2026, 10, 7, 10, 0, 0) + timedelta(seconds=self.second)).strftime("%Y%m%d-%H%M%S")
        res, meta = observation(text, iso=f"2026-10-07T10:{self.second // 60:02d}:{self.second % 60:02d}")
        meta.update(ts=ts, image=ts + ".jpg", screen={"w": 1000, "h": 800}, trigger=trigger, session="s")
        meta.update(extra or {})
        res = ocr or res

        def fake_resolve(meta_arg, frame_arg, fast=None):
            self.fast_flags.append(fast)
            return res
        self.capture.frames[ts] = frame
        with patch.object(resolver, "resolve_frame", side_effect=fake_resolve):
            self.capture.handle(str(self.root / (ts + ".json")), meta, trigger, False)
        return ts


class InMemoryPipelineTests(Base):
    def test_redundant_frames_never_touch_the_disk_and_kept_ones_are_thumbnails(self):
        screen = lambda *marks: fake_frame(1200, 800, 235, marks)
        self.feed("We chose SQLite because the data stays local.", screen())
        first_files = self.files()
        self.assertEqual(len(first_files), 2)                              # one thumbnail + its metadata
        thumb = next(self.root.glob("*.jpg"))
        with Image.open(thumb) as shown:
            self.assertLessEqual(max(shown.size), config.THUMB_PX)
        self.assertEqual(self.fast_flags, [False])                         # first look: accurate OCR

        self.feed("We chose SQLite because the data stays local.", screen())   # pixel-identical
        self.assertEqual(self.files(), first_files)                       # nothing written
        self.assertEqual(self.fast_flags, [False])                         # and no OCR at all
        self.assertEqual(self.capture.stats["no_ocr"], 1)
        self.assertEqual(self.capture.frames, {})                          # frames are released

        self.feed("We decided to use PostgreSQL for shared access.", screen((100, 100, 500, 60, 10)))
        self.assertEqual(self.fast_flags, [False, True])                   # later frame of that window: fast
        self.assertEqual(len(list(self.root.glob("*.jpg"))), 1)            # the thumbnail was replaced
        self.assertNotEqual(next(self.root.glob("*.jpg")), thumb)
        item = next(iter(self.capture.items.values()))
        self.assertEqual(item["screenshot"], next(self.root.glob("*.jpg")).name)

    def test_a_thin_fast_result_is_redone_accurately(self):
        many = [{"kind": "text", "text": f"line number {i} of the page", "conf": 1, "box": [10, 10 * i, 100, 8]}
                for i in range(20)]
        rich, _ = observation("first")
        rich["objects"] = many
        self.feed("first", fake_frame(1200, 800, 235), ocr=rich)
        thin, _ = observation("only one line left")                        # 3 lines vs 20 before
        self.feed("second", fake_frame(1200, 800, 235, [(0, 0, 600, 400, 0)]), ocr=thin)
        self.assertEqual(self.fast_flags, [False, True, False])            # fast, then accurate again

    def test_note_and_pin_captures_always_use_accurate_ocr(self):
        self.feed("first", fake_frame(1200, 800, 235))
        self.feed("second", fake_frame(1200, 800, 235, [(0, 0, 600, 400, 0)]), trigger="note")
        self.assertEqual(self.fast_flags, [False, False])

    def test_fast_continuation_can_be_turned_off(self):
        self.feed("first", fake_frame(1200, 800, 235))
        with patch.object(config, "FAST_CONTINUATION", False):
            self.feed("second", fake_frame(1200, 800, 235, [(0, 0, 600, 400, 0)]))
        self.assertEqual(self.fast_flags, [False, False])


class AdaptiveTimerTests(Base):
    def interval(self, steps, kind="document", input_ages=None, since=60):
        self.capture.idle_steps = steps
        self.capture.interval_cap = (config.BACKOFF_CAP_CHAT if kind in tracker.CHATTY else config.BACKOFF_CAP)
        now = 1000.0
        self.capture.last_capture = now - since
        with patch.object(macos, "input_ages", return_value=input_ages or {"key": 99, "scroll": 99, "click": 99}):
            return self.capture.current_interval(now)

    def test_unchanged_frames_double_the_interval_up_to_the_cap(self):
        self.assertEqual([self.interval(n) for n in range(6)], [5, 10, 20, 30, 30, 30])

    def test_chats_back_off_less(self):
        self.assertEqual([self.interval(n, "chat") for n in range(5)], [5, 10, 15, 15, 15])

    def test_input_since_the_last_capture_resets_it(self):
        self.assertEqual(self.interval(4, input_ages={"key": 2, "scroll": 99, "click": 99}), 5)
        self.assertEqual(self.capture.idle_steps, 0)

    def test_switching_resets_it(self):
        self.capture.idle_steps = 4
        self.capture.trigger("app_switch")
        self.assertEqual(self.capture.idle_steps, 0)

    def test_handling_identical_frames_slows_the_timer_and_a_change_speeds_it_up(self):
        for _ in range(3):
            self.feed("same text", fake_frame(1200, 800, 235))
        self.assertEqual(self.capture.idle_steps, 2)                       # 1st frame is new, 2 unchanged after it
        self.feed("new text", fake_frame(1200, 800, 235, [(0, 0, 600, 400, 0)]))
        self.assertEqual(self.capture.idle_steps, 0)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(config.use_paths(self.tmp.name))
        self.now = datetime(2026, 10, 14, 12, 0, 0)

    def thing(self, iid, days_ago, **extra):
        self.count = getattr(self, "count", 0) + 1                        # unique file names
        name = (self.now - timedelta(days=days_ago, seconds=self.count)).strftime("%Y%m%d-%H%M%S") + ".jpg"
        (Path(self.tmp.name) / name).write_bytes(b"x")
        (Path(self.tmp.name) / (name[:-4] + ".json")).write_text("{}")
        return {"id": iid, "app": "Docs", "kind": "document", "doing": "x", "screenshot": name, "seconds": 1,
                "visits": 1, "first_seen": "x", "last_seen": "x", **extra}

    def test_old_thumbnails_go_but_pinned_and_open_notes_stay(self):
        items = {
            "old": self.thing("old", 8),
            "fresh": self.thing("fresh", 2),
            "pinned": self.thing("pinned", 30, pinned=True),
            "noted": self.thing("noted", 30, notes=[{"at": "2026-09-01T10:00:00", "text": "todo"}]),
            "finished": self.thing("finished", 30, notes=[{"at": "2026-09-01T10:00:00", "text": "done thing"}]),
        }
        notes.set_done(items, [notes.note_id(items["finished"]["notes"][0])])
        removed = retention.expire_screenshots(items, now=self.now)
        self.assertEqual(removed, 2)
        self.assertIsNone(items["old"]["screenshot"])
        self.assertIsNone(items["finished"]["screenshot"])
        for kept in ("fresh", "pinned", "noted"):
            self.assertTrue((Path(self.tmp.name) / items[kept]["screenshot"]).exists())
        self.assertEqual(sorted(p.suffix for p in Path(self.tmp.name).iterdir()), [".jpg"] * 3 + [".json"] * 3)
        self.assertNotIn("screenshot", store.readable(items["old"]))
        self.assertEqual(retention.expire_screenshots(items, now=self.now), 0)    # nothing left to do

    def test_a_ticked_off_note_lets_its_thumbnail_expire_later(self):
        item = self.thing("a", 30, notes=[{"at": "2026-09-01T10:00:00", "text": "todo"}])
        self.assertEqual(retention.expire_screenshots({"a": item}, now=self.now), 0)
        notes.set_done({"a": item}, [notes.note_id(item["notes"][0])])
        self.assertEqual(retention.expire_screenshots({"a": item}, now=self.now), 1)


class BatchedSavesAndCostTests(Base):
    def test_while_running_memory_is_written_at_most_once_per_interval(self):
        self.capture.save_interval = 5
        self.capture.last_retention_sweep = tracker.time.time()  # This test measures save batching, not expiry of its fixed-date fixtures.
        with patch.object(store, "save_memory", wraps=store.save_memory) as writes:
            self.feed("one", fake_frame(1200, 800, 235))
            self.feed("two", fake_frame(1200, 800, 235, [(0, 0, 600, 400, 0)]))
            self.feed("three", fake_frame(1200, 800, 235, [(0, 0, 700, 500, 0)]))
            self.assertEqual(writes.call_count, 1)                         # first write only; the rest batched
            self.assertTrue(self.capture.dirty)
            self.capture.last_save -= 10                                   # time passes
            self.capture.maintain()
            self.assertEqual(writes.call_count, 2)
            self.assertFalse(self.capture.dirty)

    def test_notes_and_ticks_are_written_immediately(self):
        self.capture.save_interval = 1000
        ts = self.feed("one", fake_frame(1200, 800, 235))
        with patch.object(store, "save_memory", wraps=store.save_memory) as writes:
            self.capture.save_note(("frame", ts), "add pricing")
            self.assertEqual(writes.call_count, 1)

    def test_the_cost_report_and_warning(self):
        self.feed("one", fake_frame(1200, 800, 235))
        with patch.object(tracker.subprocess, "check_output", return_value=b"921600\n"):
            self.capture.sample_resources(self.capture.started + 20)       # startup grace: no warning yet
            self.assertNotIn("using", self.out.getvalue())
            self.capture.sample_resources(self.capture.started + 100)
        self.assertEqual(self.capture.resources["rss_mb"], 900)
        self.assertIn("using 900 MB", self.out.getvalue())                 # over RSS_WARN_MB
        report = self.capture.cost_report()
        self.assertEqual(report["ocr"]["accurate"], 1)
        self.assertEqual(set(report["avg_ms"]), {"capture", "ocr_fast", "ocr_accurate", "handle", "save"})
        self.assertEqual(report["capture_interval_s"], 5)
        json.dumps(report)                                                 # it goes into the status file


if __name__ == "__main__":
    unittest.main()

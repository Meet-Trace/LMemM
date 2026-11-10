"""Persistence tests use synthetic OCR; the Vision engine has its own real test."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image, ImageDraw

import config
import lmemm
import macos
import resolver
import store
import tracker

from frames import fake_frame
from test_content import observation


class MemoryPipelineTests(unittest.TestCase):
    def test_note_capture_rejects_missing_or_changed_native_context(self):
        screen = SimpleNamespace(origin=SimpleNamespace(x=0, y=0),
                                 size=SimpleNamespace(width=1000, height=800))
        window = {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": 123,
                  "win_id": 7, "window": "Project plan",
                  "bounds": {"X": 200, "Y": 80, "Width": 600, "Height": 640}}
        for after in [None, dict(window, window="Another document"),
                      dict(window, bounds={"X": 100, "Y": 80, "Width": 600, "Height": 640})]:
            with self.subTest(after=after), tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
                root = Path(directory)
                stack.enter_context(config.use_paths(directory))
                stack.enter_context(patch.object(macos, "front", side_effect=[window, after]))
                stack.enter_context(patch.object(macos, "browser_info", return_value=(None, None, False)))
                stack.enter_context(patch.object(macos, "display_for", return_value=(1, screen)))
                stack.enter_context(patch.object(macos, "input_ages", return_value={}))
                stack.enter_context(patch.object(macos, "pointer_on", return_value=None))
                screens = stack.enter_context(patch.object(macos, "NSScreen"))
                screens.screens.return_value = [SimpleNamespace(frame=lambda: screen)]
                stack.enter_context(patch.object(macos, "grab", side_effect=lambda display, *, window_id: fake_frame()))
                capture = tracker.Tracker()
                self.assertIsNone(capture.capture("note", pinned=True))
                self.assertTrue(capture.q.empty())
                self.assertEqual(capture.frames, {})
                self.assertEqual(list(root.glob("*.jpg")), [])

    def test_capture_records_foreground_region_for_content_extraction(self):
        # Isolate OS capture side effects while exercising real metadata/queue code.
        screen = SimpleNamespace(origin=SimpleNamespace(x=0, y=0),
                                 size=SimpleNamespace(width=1000, height=800))
        window = {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": 123,
                  "win_id": 7, "window": "Project plan",
                  "bounds": {"X": 200, "Y": 80, "Width": 600, "Height": 640}}
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(config.use_paths(directory))
            stack.enter_context(patch.object(macos, "front", return_value=window))
            stack.enter_context(patch.object(macos, "browser_info", return_value=(None, None, False)))
            stack.enter_context(patch.object(macos, "display_for", return_value=(1, screen)))
            stack.enter_context(patch.object(macos, "input_ages", return_value={"key": 50, "scroll": 50, "click": 50}))
            stack.enter_context(patch.object(macos, "pointer_on", return_value={"x": 300, "y": 200}))
            screens = stack.enter_context(patch.object(macos, "NSScreen"))
            screens.screens.return_value = [SimpleNamespace(frame=lambda: screen)]
            stack.enter_context(patch.object(macos, "grab", side_effect=lambda display, *, window_id: fake_frame()))
            capture = tracker.Tracker()
            capture.capture("timer")
            path, meta, _, _ = capture.q.get_nowait()
            self.assertEqual(meta["window_region"], [0.0, 0.0, 1.0, 1.0])
            self.assertEqual(meta["capture_scope"], "window")
            self.assertEqual(meta["inputs"]["key"], 50)
            self.assertIsNone(meta["pointer"])
            # the frame waits in memory; nothing is written unless it ends up kept
            self.assertIn(meta["ts"], capture.frames)
            self.assertEqual(list(Path(directory).glob("*.jpg")) + list(Path(directory).glob("*.json")), [])

    def test_content_changes_under_same_title_survive_reload_and_cli(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            sessions = Path(stack.enter_context(config.use_paths(directory)).sessions_dir)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            capture = tracker.Tracker()
            for second, text in [(0, "We chose SQLite because the data stays local."),
                                 (5, "We chose SQLite because the data stays local."),
                                 (10, "We decided to use PostgreSQL for shared access.")]:
                res, meta = observation(text, iso=f"2026-10-04T09:00:{second:02d}")
                ts = f"20261004-0900{second:02d}"
                meta.update(ts=ts, image=ts + ".jpg")
                path = root / (ts + ".json")
                path.write_text(json.dumps(meta))
                image = Image.new("RGB", (1000, 800), "white")
                if second == 10:
                    ImageDraw.Draw(image).rectangle((240, 190, 750, 230), fill="black")
                image.save(root / meta["image"])
                with patch.object(resolver, "resolve", return_value=res):
                    capture.handle(str(path), meta, "timer", False)

            items = store.load_items()
            self.assertEqual(len(items), 1)
            item = next(iter(items.values()))
            excerpts = item.get("content", {}).get("excerpts", [])
            self.assertEqual(len(excerpts), 2)
            self.assertIn("SQLite", excerpts[0]["text"])
            self.assertIn("PostgreSQL", excerpts[1]["text"])
            self.assertEqual(item["seconds"], 10)
            self.assertEqual(item["visits"], 1)
            self.assertEqual(item["updates"], 1)
            self.assertEqual(capture.stats["no_ocr"], 1)
            self.assertEqual(item["activity"]["reading"]["seconds"], 5)
            self.assertEqual(item["activity"]["receiving"]["seconds"], 5)
            self.assertEqual(item["activity"]["typing"]["text"], [])
            self.assertEqual([p.name for p in root.glob("*.jpg")], ["20261004-090010.jpg"])
            timeline = json.loads(next(sessions.glob("*.json")).read_text())["timeline"]
            self.assertEqual(len(timeline), 1)
            self.assertEqual(timeline[0]["item"], item["id"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch("sys.argv", ["lmemm.py", "memory", "1", "--content"]):
                lmemm.main()
            self.assertIn("SQLite", output.getvalue())
            self.assertIn("PostgreSQL", output.getvalue())
            self.assertIn("Decision quote (unverified)", output.getvalue())
            self.assertIn("Project plan", output.getvalue())
            self.assertIn("receiving 5s", output.getvalue())


if __name__ == "__main__":
    unittest.main()

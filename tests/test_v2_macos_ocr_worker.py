"""The OCR worker process with real Vision: equivalence with the in-process reader, the real pipeline (redaction,
`secret_on_screen`, stored extraction shape), real timeouts and crashes, and no orphan processes however the runner ends.
macOS with pyobjc only. Synthetic PNGs rendered here; no screen capture, no permissions needed."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from trace_v2.macos.ocr_client import OcrWorkerClient, WorkerStarting
from trace_v2.macos.ocr import Timeout
from trace_v2.observations import Context, ScreenshotCapture
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import build_draft, observe_once
from v2_observation_helpers import T0, TempStore
from test_v2_ocr_worker import alive, until

try:
    from trace_v2.macos import MacSources, VisionOCR
    from v2_macos_render import render_png
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"                    # AWS's documented example key, not a credential
WIDTH, HEIGHT = 1500, 420
SECRET_LINES = ["Deploy notes for the staging cluster", f"aws_access_key_id = {FAKE_KEY}",
                "Meeting with the platform team at three"]
PLAIN_LINES = ["Quarterly planning review", "Invoice 20481 was approved on Tuesday",
               "The quick brown fox jumps over the lazy dog"]


class _Context:
    def context(self):
        return Context({"bundle_id": "com.example.editor", "name": "Editor", "pid": 7},
                       {"title": "notes.txt", "id": 3}, {"id": 1, "bounds": [0, 0, WIDTH, HEIGHT], "scale": 1.0})

    def secure_input(self):
        return False


class _Screen:
    def __init__(self, png):
        self.png = png

    def screenshot(self, display_id, **_scope):
        return ScreenshotCapture(self.png, "png", WIDTH, HEIGHT, T0)


def drafts(png, reader, key="w:0"):
    sources = MacSources(context_source=_Context(), screenshot_source=_Screen(png), ocr=reader)
    draft, _ = build_draft(sources, EventBuffer(start_ns=T0 - 10), key, clock_ns=lambda: T0)
    return draft


def capture(png):
    return ScreenshotCapture(png, "png", WIDTH, HEIGHT, time.time_ns())


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RealWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = OcrWorkerClient(timeout_s=180.0)
        cls.inprocess = VisionOCR(timeout_s=180.0)
        if not cls.worker.wait_ready(300):
            raise unittest.SkipTest(f"the OCR worker did not become ready: {cls.worker.stats()}")

    @classmethod
    def tearDownClass(cls):
        cls.worker.close()

    def setUp(self):
        self.assertTrue(self.worker.wait_ready(120), self.worker.stats())
        self.worker.timeout_s = 180.0

    def test_the_same_png_gives_the_same_lines_and_text_in_the_worker_and_in_process(self):
        for lines, name in ((PLAIN_LINES, "Helvetica"), (SECRET_LINES, "Menlo")):
            png = render_png(lines, WIDTH, HEIGHT, font_size=40.0, font_name=name)
            expected = self.inprocess.read(capture(png))
            got = self.worker.read(capture(png))
            self.assertEqual(got, expected)
            self.assertEqual(got[1].split("\n")[0], lines[0])
        self.assertEqual(self.worker.version, self.inprocess.version)

    def test_redaction_and_secret_on_screen_are_the_same_through_the_worker(self):
        png = render_png(SECRET_LINES, WIDTH, HEIGHT, font_size=44.0)
        a, b = drafts(png, self.inprocess), drafts(png, self.worker)
        self.assertEqual((b.ocr.status, b.ocr.reader_version, b.ocr.error_code), (a.ocr.status, a.ocr.reader_version, None))
        self.assertEqual((b.ocr.raw, b.ocr.text), (a.ocr.raw, a.ocr.text))
        self.assertIn("[REDACTED:aws_access_key]", b.ocr.text)
        self.assertNotIn(FAKE_KEY, json.dumps(b.ocr.raw) + b.ocr.text)
        self.assertEqual((b.screenshot.reason, b.privacy), (a.screenshot.reason, a.privacy))
        self.assertEqual(b.screenshot.reason, "secret_on_screen")

    def test_the_stored_extraction_has_the_same_shape_and_values_as_an_in_process_one(self):
        png = render_png(PLAIN_LINES, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica")
        rows = []
        for reader in (self.inprocess, self.worker):
            with TempStore() as t:
                result = observe_once(t.store, MacSources(context_source=_Context(), screenshot_source=_Screen(png),
                                                          ocr=reader), EventBuffer(start_ns=T0 - 10), "w:1",
                                      clock_ns=lambda: T0)
                obs = t.store.get(result.observation_id)
                row = t.store.db.execute("SELECT reader, reader_version, revision, status, error_code, text FROM "
                                         "extractions WHERE observation_id=? AND reader='ocr'",
                                         (result.observation_id,)).fetchall()
                rows.append((row, obs["evidence"]["ocr"]["raw"]))
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(rows[1][0][0][:5], ("ocr", "vision-ocr/1", 1, "succeeded", None))

    def test_real_timeouts_in_a_row_recover_and_leave_one_worker(self):
        png = render_png(PLAIN_LINES, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica")
        for _ in range(3):
            self.worker.timeout_s = 0.001
            started = time.monotonic()
            with self.assertRaises(Timeout):
                self.worker.read(capture(png))
            self.assertLess(time.monotonic() - started, 0.5)
            self.worker.timeout_s = 180.0
            self.assertTrue(self.worker.wait_ready(120), self.worker.stats())
            self.assertEqual(self.worker.read(capture(png))[1].split("\n"), PLAIN_LINES)
        self.assertTrue(until(lambda: len(worker_pids(os.getpid())) == 1, 10), worker_pids(os.getpid()))

    def test_a_killed_real_worker_is_replaced_and_ocr_resumes(self):
        png = render_png(PLAIN_LINES, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica")
        for _ in range(3):
            victim = self.worker.worker_pid()
            os.kill(victim, signal.SIGKILL)
            self.assertTrue(until(lambda: self.worker.worker_pid() != victim, 15))
            self.assertTrue(self.worker.wait_ready(120))
            self.assertEqual(self.worker.read(capture(png))[1].split("\n"), PLAIN_LINES)

    def test_the_worker_is_a_separate_process_and_carries_no_appkit(self):
        pid = self.worker.worker_pid()
        self.assertNotEqual(pid, os.getpid())
        self.assertEqual(ppid(pid), os.getpid())


class NotReady(unittest.TestCase):
    @unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
    def test_a_cold_worker_is_unavailable_then_reads_with_real_vision(self):
        client = OcrWorkerClient(timeout_s=180.0)
        try:
            png = render_png(PLAIN_LINES, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica")
            started = time.monotonic()
            with self.assertRaises(WorkerStarting):
                client.read(capture(png))
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(client.wait_ready(300))
            self.assertEqual(client.read(capture(png))[1].split("\n"), PLAIN_LINES)
            self.assertTrue(client.stats()["worker"]["warmup_s"] is not None)
        finally:
            client.close()


def ppid(pid):
    return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0)


def worker_pids(parent):
    out = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and "trace_v2.macos.ocr_worker" in parts[2] and int(parts[1]) == parent:
            found.append(int(parts[0]))
    return found


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class NoOrphans(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="trace-v2-run-")
        self.addCleanup(self.dir.cleanup)
        self.data = str(Path(self.dir.name).resolve())

    def run_runner(self):
        env = {**os.environ, "TRACE_V2_DATA_DIR": self.data, "PYTHONPATH": str(ROOT)}
        proc = subprocess.Popen([sys.executable, "-m", "trace_v2.run", "--duration", "300", "--interval", "3"], cwd=ROOT,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close(), proc.stderr.close()))
        self.assertTrue(until(lambda: worker_pids(proc.pid), 60), "the runner never started an OCR worker")
        return proc, worker_pids(proc.pid)[0]

    def test_sigterm_to_the_runner_leaves_no_worker_process(self):
        proc, worker = self.run_runner()
        time.sleep(4)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(until(lambda: not alive(worker), 5), "the OCR worker outlived a clean shutdown")

    def test_sigkill_to_the_runner_leaves_no_worker_process(self):
        proc, worker = self.run_runner()
        time.sleep(3)
        proc.kill()
        proc.wait(30)
        self.assertTrue(until(lambda: not alive(worker), 5), "the OCR worker outlived a SIGKILLed runner")

    def test_runner_stats_carry_the_worker_counters(self):
        proc, worker = self.run_runner()
        time.sleep(8)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=60)
        stats = json.loads((Path(self.data) / "runner_stats.json").read_text())
        self.assertEqual(stats["ocr_worker"]["isolation"], "process")
        self.assertIn("late_results_discarded", stats["ocr_worker"])


if __name__ == "__main__":
    unittest.main()

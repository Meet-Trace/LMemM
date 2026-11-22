"""Asynchronous OCR with the real Vision worker (macOS with pyobjc only). Synthetic PNGs rendered here; no screen capture."""

import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from trace_v2.observations import ObservationStore, ReaderResult, ScreenshotCapture
from trace_v2.observations.ocr_processor import OcrProcessor
from trace_v2.observations.ocr_queue import QueuePolicy
from v2_observation_helpers import draft
from test_v2_async_ocr import view

try:
    import Quartz  # noqa: F401
    import Vision  # noqa: F401
    from trace_v2.macos.ocr_client import OcrWorkerClient
    from v2_macos_render import render_png
    ON_MAC = sys.platform == "darwin"
except ImportError:
    ON_MAC = False

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"
WIDTH, HEIGHT = 1500, 420
PLAIN = ["Quarterly planning review", "Invoice 20481 was approved on Tuesday", "The quick brown fox jumps over the lazy dog"]
SECRET = ["Deploy notes for the staging cluster", f"aws_access_key_id = {FAKE_KEY}", "Meeting with the platform team at three"]
POLICY = QueuePolicy(max_attempts=3, backoff_base_s=0.2, backoff_cap_s=0.5, lease_slack_s=1.0)


def pdraft(png, key, t=None):
    t = t or time.time_ns()
    return draft(key=key, t=t, ocr=ReaderResult("vision-ocr/1", "pending"),
                 shot=ScreenshotCapture(png, "png", WIDTH, HEIGHT, t))


def drain(proc, store, want, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end and store.ocr_queue_stats()["done"] + store.ocr_queue_stats()["failed"] < want:
        if not proc.run_once():
            time.sleep(0.1)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RealAsyncOcr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = OcrWorkerClient(timeout_s=30.0)
        if not cls.worker.wait_ready(300):
            raise unittest.SkipTest("the OCR worker did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.worker.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.store = ObservationStore(base / "data", base / "Legacy-V1")
        self.addCleanup(self.store.close)
        self.worker.timeout_s = 30.0
        self.proc = OcrProcessor(self.store, self.worker, 30.0, POLICY)

    def test_a_real_screenshot_is_enriched_in_place(self):
        oid = self.store.ingest(pdraft(render_png(PLAIN, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica"), "r:0")).observation_id
        before = self.store.get(oid)
        self.assertEqual(before["evidence"]["ocr"]["status"], "pending")
        drain(self.proc, self.store, 1)
        after = self.store.get(oid)
        self.assertEqual(view(before), view(after))
        ocr = after["evidence"]["ocr"]
        self.assertEqual((ocr["status"], ocr["revision"]), ("succeeded", 2))
        self.assertEqual(ocr["text"].split("\n"), PLAIN)
        self.assertEqual(after["observation_id"], oid)

    def test_a_real_secret_is_redacted_and_the_screenshot_deleted(self):
        oid = self.store.ingest(pdraft(render_png(SECRET, WIDTH, HEIGHT, font_size=44.0), "r:1")).observation_id
        drain(self.proc, self.store, 1)
        o = self.store.get(oid)
        self.assertIn("[REDACTED:aws_access_key]", o["evidence"]["ocr"]["text"])
        self.assertNotIn(FAKE_KEY, json.dumps(o["evidence"]["ocr"]))
        self.assertEqual(o["evidence"]["screenshot"]["deletion"]["reason"], "secret_on_screen_late")
        self.assertFalse(o["evidence"]["screenshot"]["available"])
        self.assertEqual(self.store.assets.all_relative_paths(), set())

    def test_a_real_timeout_is_retried_and_then_succeeds(self):
        oid = self.store.ingest(pdraft(render_png(PLAIN, WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica"), "r:2")).observation_id
        self.worker.timeout_s = 0.001
        self.assertTrue(self.proc.run_once())
        self.assertEqual(self.store.ocr_queue_stats()["queued"], 1)
        self.worker.timeout_s = 30.0
        self.assertTrue(self.worker.wait_ready(120))
        drain(self.proc, self.store, 1)
        self.assertEqual(self.store.get(oid)["evidence"]["ocr"]["status"], "succeeded")
        self.assertEqual(self.store.db.execute("SELECT attempts FROM ocr_jobs").fetchone()[0], 2)


CHILD = r"""
import sys, time
sys.path[:0] = [{root!r}, {tests!r}]
from trace_v2.macos.ocr_client import OcrWorkerClient
from trace_v2.observations import ObservationStore
from trace_v2.observations.ocr_processor import OcrProcessor
from trace_v2.observations.ocr_queue import QueuePolicy
from test_v2_async_ocr_real import pdraft, PLAIN, WIDTH, HEIGHT
from v2_macos_render import render_png
store = ObservationStore({data!r}, {legacy!r})
worker = OcrWorkerClient(timeout_s=2.0)
worker.wait_ready(300)
for i in range(8):
    store.ingest(pdraft(render_png(PLAIN + [str(i)], WIDTH, HEIGHT, font_size=40.0, font_name="Helvetica"), f"c:{{i}}"))
print("ingested", flush=True)
OcrProcessor(store, worker, 2.0, QueuePolicy(lease_slack_s=1.0), idle_s=0.01).start()
time.sleep(600)
"""


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class KillAndResume(unittest.TestCase):
    def test_killing_the_process_mid_queue_and_restarting_resumes_the_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            data, legacy = base / "data", base / "Legacy-V1"
            code = CHILD.format(root=str(ROOT), tests=str(ROOT / "tests"), data=str(data), legacy=str(legacy))
            child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ingested")
                store = ObservationStore(data, legacy)
                end = time.monotonic() + 120
                while time.monotonic() < end and store.ocr_queue_stats()["done"] < 2:
                    time.sleep(0.05)
                store.close()
                child.send_signal(signal.SIGKILL)
                child.wait(10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(10)
                child.stdout.close()
            store = ObservationStore(data, legacy)
            worker = OcrWorkerClient(timeout_s=30.0)
            try:
                self.assertTrue(worker.wait_ready(300))
                before = store.ocr_queue_stats()
                self.assertGreaterEqual(before["done"], 2)
                self.assertLess(before["done"], 8)
                proc = OcrProcessor(store, worker, 2.0, QueuePolicy(lease_slack_s=1.0))
                time.sleep(3.5)                                  # a job left processing by the killed process: its lease runs out
                proc.recover()
                drain(proc, store, 8)
                states = [store.get(o["observation_id"])["evidence"]["ocr"]["status"] for o in store.list()]
                self.assertEqual(states, ["succeeded"] * 8)
                self.assertEqual(len({o["observation_id"] for o in store.list()}), 8)
            finally:
                worker.close()
                store.close()


if __name__ == "__main__":
    unittest.main()

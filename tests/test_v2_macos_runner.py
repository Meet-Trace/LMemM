"""A real `python -m trace_v2.run` on this Mac: OCR, accessibility and input events through the real pipeline. macOS only.

Skipped, with the reason, when the screen is locked (captures would only be withheld) or Screen Recording, Accessibility
or Input Monitoring is not granted to the process running the tests. Nothing is prompted. The harmless cursor wiggle is
posted by this test so there are events without a user."""

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import ApplicationServices
    import Quartz
    from trace_v2.observations import ObservationStore
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False

REPO = Path(__file__).resolve().parents[1]


def blocker():
    if not Quartz.CGPreflightScreenCaptureAccess():
        return "Screen Recording is not granted to the process running the tests"
    if not ApplicationServices.AXIsProcessTrusted():
        return "Accessibility is not granted to the process running the tests"
    if not (Quartz.CGPreflightListenEventAccess() and Quartz.CGPreflightPostEventAccess()):
        return "Input Monitoring is not granted to the process running the tests"
    session = Quartz.CGSessionCopyCurrentDictionary()
    if session is None or session.get("CGSSessionScreenIsLocked"):
        return "the screen is locked: every capture would be withheld"
    return None


def wiggle(stop):
    here = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    i = 0
    while not stop.is_set():
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, Quartz.CGPointMake(here.x + (i % 3), here.y), 0))
        i += 1
        stop.wait(0.02 if (i // 100) % 2 == 0 else 0.5)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, here, 0))


def start(data, *args):
    env = {**os.environ, "TRACE_V2_DATA_DIR": data, "PYTHONPATH": str(REPO)}
    return subprocess.Popen([sys.executable, "-m", "trace_v2.run", *args], cwd=REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@unittest.skipUnless(ON_MAC, "macOS only")
class RealRun(unittest.TestCase):
    def setUp(self):
        reason = blocker()
        if reason:
            self.skipTest(reason)
        self.dir = tempfile.TemporaryDirectory(prefix="trace-v2-run-")
        self.addCleanup(self.dir.cleanup)
        self.data = str(Path(self.dir.name).resolve())

    def observations(self):
        store = ObservationStore(Path(self.data))
        try:
            return [store.get(o["observation_id"]) for o in store.list(limit=1000)]
        finally:
            store.close()

    def test_thirty_seconds_give_about_ten_observations_with_ocr_ax_and_events(self):
        stop = threading.Event()
        threading.Thread(target=wiggle, args=(stop,), daemon=True).start()
        try:
            proc = start(self.data, "--duration", "30")
            out, err = proc.communicate(timeout=240)
        finally:
            stop.set()
        self.assertEqual(proc.returncode, 0, err[-2000:])
        observations = self.observations()
        self.assertTrue(9 <= len(observations) <= 12, len(observations))
        times = [o["timestamp_ns"] for o in observations]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), len(times))
        withheld = [o for o in observations if o["metadata"]["privacy"].get("withheld")]
        for o in observations:
            if o not in withheld:
                status = o["metadata"]["extraction_status"]
                self.assertEqual(status["ocr"]["status"], "succeeded", status)
                self.assertIn(status["accessibility"]["status"], ("succeeded", "failed"))      # failed: app without a window
        self.assertGreaterEqual(sum(o["metadata"]["extraction_status"]["ocr"]["status"] == "succeeded"
                                    for o in observations), len(observations) - len(withheld))
        self.assertGreater(sum(len(o["interaction_events"]) for o in observations), 0)
        stats = __import__("json").loads((Path(self.data) / "runner_stats.json").read_text())
        self.assertEqual((stats["final"], stats["missed_ticks"], stats["failures"]), (True, 0, 0))
        self.assertEqual(stats["input"]["state"], "stopped")

    def test_sigterm_stops_cleanly_with_a_final_observation(self):
        proc = start(self.data, "--duration", "120")
        time.sleep(25)                                # startup (imports, warm-up) plus a few 3 s captures
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err[-2000:])
        stats = __import__("json").loads((Path(self.data) / "runner_stats.json").read_text())
        self.assertTrue(stats["final"])
        self.assertGreaterEqual(len(self.observations()), 3)

    def test_second_runner_in_the_same_data_dir_is_refused(self):
        first = start(self.data, "--duration", "20")
        try:
            time.sleep(4)
            second = start(self.data, "--once")
            out, err = second.communicate(timeout=60)
            self.assertEqual(second.returncode, 3)
            self.assertIn("already using this data directory", err)
        finally:
            first.send_signal(signal.SIGTERM)
            first.communicate(timeout=60)


if __name__ == "__main__":
    unittest.main()

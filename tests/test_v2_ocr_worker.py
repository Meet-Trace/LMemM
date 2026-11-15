"""The OCR worker client against a fake worker that hangs, crashes, exits, lies and runs slowly (tests/v2_fake_ocr_worker.py,
the real worker loop with a misbehaving handler). Platform independent: no Vision, no pyobjc. The real-Vision tests are in
test_v2_macos_ocr_worker. Real processes, real pipes, real signals; timeouts are short but the bounds are generous because
the machine may be loaded."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import uuid
from pathlib import Path

from trace_v2.macos import ocr_client, ocr_protocol as proto
from trace_v2.macos.ocr_client import (ImageTooLarge, OcrWorkerClient, WorkerBusy, WorkerCircuitOpen, WorkerClosed,
                                       WorkerCrashed, WorkerProtocolError, WorkerStarting)
from trace_v2.macos.ocr import READER_VERSION, Timeout
from trace_v2.observations import Context, ScreenshotCapture
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import build_draft, run_reader
from v2_observation_helpers import T0, TempStore

FAKE = str(Path(__file__).with_name("v2_fake_ocr_worker.py"))
ROOT = str(Path(__file__).resolve().parents[1])


def processes(tag):
    """Pids of live processes whose command line carries `tag` (zombies show as <defunct> and are not counted)."""
    out = subprocess.run(["ps", "-ax", "-o", "pid=,stat=,command="], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and tag in parts[2] and not parts[1].startswith("Z") and int(parts[0]) != os.getpid() \
                and not parts[2].startswith("ps "):
            found.append(int(parts[0]))
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def until(condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def shot(*actions, size=0, at=None):
    """A PNG-shaped screenshot whose bytes tell the fake worker what to do. Every call is a different capture."""
    body = b"\x89PNG\r\n\x1a\n" + " ".join(f"ACTION:{a}" for a in actions).encode() + b" " + uuid.uuid4().bytes + bytes(size)
    return ScreenshotCapture(body, "png", 10, 10, at or time.time_ns())


def expected_text(screenshot):
    return f"fake:{hashlib.sha256(screenshot.data).hexdigest()}"


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tag = f"v2ocr-{uuid.uuid4().hex[:12]}"
        self.env = {**os.environ}
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close(grace_s=0.5)
        self.assertEqual(processes(self.tag), [], "an OCR worker process was leaked")

    def client(self, **kw):
        kw.setdefault("timeout_s", 1.5)
        kw.setdefault("grace_s", 0.3)
        client = OcrWorkerClient(command=[sys.executable, FAKE, "--tag", self.tag], env=self.env, **kw)
        self.clients.append(client)
        return client

    def ready_client(self, **kw):
        client = self.client(**kw)
        self.assertTrue(client.wait_ready(30), client.stats())
        return client


class Reading(ClientTest):
    def test_a_result_has_the_in_process_shape_and_belongs_to_its_screenshot(self):
        client = self.ready_client()
        screenshot = shot()
        raw, text = client.read(screenshot)
        self.assertEqual(text, expected_text(screenshot))
        self.assertEqual(set(raw), {"lines", "image_size", "recognition_level", "languages", "os_version"})
        self.assertEqual(client.version, READER_VERSION)
        self.assertEqual(client.stats()["succeeded"], 1)

    def test_only_png_is_read_like_the_in_process_reader(self):
        client = self.ready_client()
        with self.assertRaises(ValueError):
            client.read(ScreenshotCapture(b"x", "jpeg", 1, 1, T0))

    def test_a_worker_side_failure_keeps_its_error_class_and_unavailable_stays_unavailable(self):
        client = self.ready_client()
        result = run_reader(client, time.time_ns, shot("error:ValueError"), None)
        self.assertEqual((result.status, result.error_code, result.reader_version),
                         ("failed", "ValueError", READER_VERSION))
        result = run_reader(client, time.time_ns, shot("unavailable"), None)
        self.assertEqual((result.status, result.error_code), ("unavailable", "ReaderUnavailable"))
        self.assertEqual(run_reader(client, time.time_ns, shot(), None).status, "succeeded")     # nothing was disturbed

    def test_the_same_capture_read_again_gives_the_first_result_without_running_vision_again(self):
        client = self.ready_client()
        screenshot = shot()
        first = client.read(screenshot)
        self.assertEqual(client.read(screenshot), first)
        stats = client.stats()
        self.assertEqual((stats["jobs"], stats["idempotent_hits"]), (1, 1))


class WarmUp(ClientTest):
    def test_ocr_is_unavailable_until_the_worker_is_ready_and_the_first_capture_is_not_delayed(self):
        self.env["FAKE_OCR_WARMUP"] = "slow:1.5"
        client = self.client()
        started = time.monotonic()
        with self.assertRaises(WorkerStarting):
            client.read(shot())
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(client.wait_ready(30))
        self.assertEqual(client.read(shot())[1][:5], "fake:")

    def test_through_build_draft_the_extraction_is_recorded_unavailable_then_succeeded(self):
        self.env["FAKE_OCR_WARMUP"] = "slow:1.0"
        client = self.client()
        sources = DraftSources(client, shot())
        t = time.monotonic()
        draft, batch = build_draft(sources, EventBuffer(start_ns=T0 - 10), "k:0", clock_ns=lambda: T0)
        self.assertLess(time.monotonic() - t, 0.5)
        self.assertEqual((draft.ocr.status, draft.ocr.error_code), ("unavailable", "WorkerStarting"))
        self.assertTrue(client.wait_ready(30))
        sources.screenshot_value = shot()
        draft, batch = build_draft(sources, EventBuffer(start_ns=T0 - 10), "k:1", clock_ns=lambda: T0)
        self.assertEqual((draft.ocr.status, draft.ocr.reader_version), ("succeeded", READER_VERSION))
        self.assertEqual(draft.ocr.text, expected_text(sources.screenshot_value))

    def test_a_failed_warm_up_is_reported_but_jobs_are_still_accepted(self):
        self.env["FAKE_OCR_WARMUP"] = "fail"
        client = self.ready_client()
        self.assertEqual(client.read(shot())[1][:5], "fake:")


class DraftSources:
    capture_version = "test/1"
    screenshot_scope = "front_app"

    def __init__(self, ocr, screenshot_value, app="com.example.editor", ax_text="plain"):
        self.ocr, self.screenshot_value, self.app, self.ax_text = ocr, screenshot_value, app, ax_text
        outer = self

        class AX:
            version = "ax-tree/1"

            def read(self, context):
                return {"role": "AXWindow"}, outer.ax_text
        self.accessibility = AX()

    def context(self):
        return Context({"bundle_id": self.app, "name": "Editor", "pid": 7}, {"title": "notes.txt", "id": 3},
                       {"id": 1, "bounds": [0, 0, 10, 10], "scale": 1.0})

    def secure_input(self):
        return False

    def screenshot(self):
        return self.screenshot_value


class Timeouts(ClientTest):
    def test_a_hung_job_fails_as_timeout_at_the_timeout_and_the_next_job_succeeds_on_a_new_worker(self):
        client = self.ready_client(timeout_s=1.0)
        old = client.worker_pid()
        started = time.monotonic()
        result = run_reader(client, time.time_ns, shot("hang"), None)
        took = time.monotonic() - started
        self.assertEqual((result.status, result.error_code), ("failed", "Timeout"))
        self.assertTrue(1.0 <= took < 1.5, took)
        self.assertTrue(until(lambda: not alive(old), 5), "the hung worker was not killed")
        self.assertTrue(client.wait_ready(30))
        self.assertNotEqual(client.worker_pid(), old)
        self.assertEqual(client.read(shot())[1][:5], "fake:")
        self.assertEqual(client.stats()["restarts"], {"timeout": 1})

    def test_five_timeouts_in_a_row_are_each_recovered_without_leaking_processes(self):
        client = self.ready_client(timeout_s=0.5)
        pids = set()
        for _ in range(5):
            pids.add(client.worker_pid())
            with self.assertRaises(Timeout):
                client.read(shot("hang"))
            self.assertTrue(client.wait_ready(30))
            self.assertEqual(client.read(shot())[1][:5], "fake:")
            self.assertLessEqual(len(processes(self.tag)), 2)             # the new worker (and an old one being reaped)
        stats = client.stats()
        self.assertEqual((stats["timeouts"], stats["restarts"]["timeout"], stats["circuit_opens"], stats["spawns"]),
                         (5, 5, 0, 6))
        self.assertEqual(len(pids), 5)
        self.assertTrue(until(lambda: len(processes(self.tag)) == 1, 5), processes(self.tag))

    def test_a_late_answer_of_a_replaced_worker_is_never_attached_to_another_capture(self):
        client = self.ready_client(timeout_s=0.6)
        slow, other = shot("slow:1.5"), shot()
        with self.assertRaises(Timeout):
            client.read(slow)
        self.assertTrue(client.wait_ready(30))
        time.sleep(1.2)                                                    # the old worker would have answered by now
        raw, text = client.read(other)
        self.assertEqual(text, expected_text(other))
        self.assertNotEqual(text, expected_text(slow))

    def test_read_returns_within_the_timeout_even_if_the_worker_never_reads_its_pipe(self):
        client = self.ready_client(timeout_s=1.0)
        started = time.monotonic()
        with self.assertRaises(Timeout):
            client.read(shot("hang", size=8 * 1024 * 1024))                # bigger than the pipe buffer
        self.assertLess(time.monotonic() - started, 1.6)


class Crashes(ClientTest):
    def test_a_crash_mid_job_is_a_failed_read_then_a_restart_and_recovery(self):
        client = self.ready_client()
        old = client.worker_pid()
        result = run_reader(client, time.time_ns, shot("crash"), None)
        self.assertEqual((result.status, result.error_code), ("failed", "WorkerCrashed"))
        self.assertTrue(client.wait_ready(30))
        self.assertNotEqual(client.worker_pid(), old)
        self.assertEqual(client.read(shot())[1][:5], "fake:")
        self.assertEqual(client.stats()["crashes"], 1)

    def test_an_exit_mid_job_and_an_external_sigkill_are_recovered_from(self):
        client = self.ready_client()
        with self.assertRaises(WorkerCrashed):
            client.read(shot("exit"))
        self.assertTrue(client.wait_ready(30))
        victim = client.worker_pid()
        os.kill(victim, signal.SIGKILL)                                    # dies while idle
        self.assertTrue(until(lambda: client.worker_pid() != victim, 10))
        self.assertTrue(client.wait_ready(30))
        self.assertEqual(client.read(shot())[1][:5], "fake:")
        self.assertEqual(client.stats()["crashes"], 2)

    def test_a_job_after_a_crash_never_waits_longer_than_the_timeout_even_while_restarting(self):
        client = self.ready_client(timeout_s=1.0)
        with self.assertRaises(WorkerCrashed):
            client.read(shot("crash"))
        started = time.monotonic()
        try:
            client.read(shot())
        except WorkerStarting:
            pass
        self.assertLess(time.monotonic() - started, 1.5)

    def test_a_crash_at_startup_backs_off_then_opens_the_circuit_instead_of_looping(self):
        flag = Path(tempfile.mkdtemp()) / "crash"
        flag.write_text("x")
        self.env["FAKE_OCR_CRASH_FILE"] = str(flag)
        client = self.client(breaker=(3, 30.0, 2.0))
        self.assertTrue(until(lambda: client.stats()["circuit_opens"] == 1, 15), client.stats())
        spawns = client.stats()["spawns"]
        self.assertEqual(spawns, 3)
        with self.assertRaises(WorkerCircuitOpen):
            client.read(shot())
        result = run_reader(client, time.time_ns, shot(), None)
        self.assertEqual((result.status, result.error_code), ("unavailable", "WorkerCircuitOpen"))
        time.sleep(1.0)
        self.assertEqual(client.stats()["spawns"], spawns)                 # no crash loop while cooling off
        flag.unlink()                                                      # the cause went away; the cool-off ends
        self.assertTrue(client.wait_ready(30), client.stats())
        self.assertEqual(client.read(shot())[1][:5], "fake:")
        self.assertFalse(client.stats()["circuit_open"])

    def test_a_worker_that_never_becomes_ready_is_replaced(self):
        self.env["FAKE_OCR_WARMUP"] = "hang"
        client = self.client(ready_timeout_s=0.6, breaker=(50, 60.0, 60.0))
        self.assertTrue(until(lambda: client.stats()["restarts"].get("ready_timeout", 0) >= 2, 10), client.stats())


class BadAnswers(ClientTest):
    def test_garbage_is_a_protocol_error_and_the_worker_is_replaced(self):
        client = self.ready_client()
        old = client.worker_pid()
        result = run_reader(client, time.time_ns, shot("garbage"), None)
        self.assertEqual((result.status, result.error_code), ("failed", "WorkerProtocolError"))
        self.assertTrue(client.wait_ready(30))
        self.assertNotEqual(client.worker_pid(), old)
        self.assertEqual(client.read(shot())[1][:5], "fake:")
        self.assertTrue(until(lambda: not alive(old), 5))

    def test_a_frame_cut_off_by_a_crash_and_an_absurd_length_are_errors_not_hangs(self):
        client = self.ready_client()
        for action in ("truncate", "bigheader"):
            started = time.monotonic()
            with self.assertRaises((WorkerProtocolError, WorkerCrashed)):
                client.read(shot(action))
            self.assertLess(time.monotonic() - started, 1.6)
            self.assertTrue(client.wait_ready(30))
        self.assertEqual(client.read(shot())[1][:5], "fake:")

    def test_garbage_instead_of_hello_is_replaced(self):
        self.env["FAKE_OCR_START"] = "garbage"
        client = self.client(breaker=(50, 60.0, 60.0))
        self.assertTrue(until(lambda: client.stats()["restarts"].get("protocol", 0) >= 1, 10), client.stats())

    def test_stale_duplicate_and_foreign_results_are_discarded_and_never_double_attached(self):
        client = self.ready_client()
        a = shot("stale_then_ok")
        self.assertEqual(client.read(a)[1], expected_text(a))
        b = shot("wrongkey")
        self.assertEqual(client.read(b)[1], expected_text(b))
        c = shot("dup")
        self.assertEqual(client.read(c)[1], expected_text(c))
        d = shot()
        self.assertEqual(client.read(d)[1], expected_text(d))              # the duplicate of c is not d's answer
        stats = client.stats()
        self.assertEqual(stats["late_results_discarded"], 3)
        self.assertEqual((stats["jobs"], stats["succeeded"]), (4, 4))

    def test_only_a_stale_answer_is_a_timeout_not_a_result(self):
        client = self.ready_client(timeout_s=0.6)
        with self.assertRaises(Timeout):
            client.read(shot("stale"))
        self.assertEqual(client.stats()["late_results_discarded"], 1)


class Bounds(ClientTest):
    def test_a_second_job_while_one_runs_is_refused_at_once_and_bytes_in_flight_stay_one_screenshot(self):
        client = self.ready_client(timeout_s=5.0, max_image_bytes=3_000_000)
        running = shot("slow:1.0", size=2_000_000)
        box = {}
        thread = threading.Thread(target=lambda: box.setdefault("r", client.read(running)))
        thread.start()
        self.assertTrue(until(lambda: client.stats()["queue_depth"] == 1, 5))
        refused = []
        for _ in range(20):
            started = time.monotonic()
            with self.assertRaises(WorkerBusy):
                client.read(shot(size=2_000_000))
            refused.append(time.monotonic() - started)
        self.assertLess(max(refused), 0.1)
        stats = client.stats()
        self.assertEqual((stats["in_flight_bytes"], stats["queue_depth_max"]), (len(running.data), 1))
        thread.join(10)
        self.assertEqual(box["r"][1], expected_text(running))
        stats = client.stats()
        self.assertEqual((stats["busy_refused"], stats["jobs"], stats["in_flight_bytes"]), (20, 1, 0))
        self.assertEqual(stats["peak_in_flight_bytes"], len(running.data))
        result = run_reader(client, time.time_ns, shot(size=3_000_001), None)
        self.assertEqual((result.status, result.error_code), ("failed", "ImageTooLarge"))
        with self.assertRaises(ImageTooLarge):
            client.read(shot(size=3_000_001))
        self.assertEqual(client.stats()["peak_in_flight_bytes"], len(running.data))

    def test_a_worker_over_the_memory_limit_is_replaced_between_jobs(self):
        client = self.ready_client(max_rss_mb=120, rss_check_s=0.2)
        old = client.worker_pid()
        client.read(shot("balloon:200"))
        self.assertTrue(until(lambda: client.stats()["restarts"].get("rss", 0) == 1, 10), client.stats())
        self.assertTrue(client.wait_ready(30))
        self.assertNotEqual(client.worker_pid(), old)
        self.assertGreater(client.stats()["peak_worker_rss_mb"], 120)
        self.assertEqual(client.read(shot())[1][:5], "fake:")

    def test_the_worker_is_replaced_after_n_jobs_without_losing_one(self):
        client = self.ready_client(max_jobs=3)
        first = client.worker_pid()
        texts = []
        for _ in range(8):
            if not client.wait_ready(30):
                self.fail("no worker")
            screenshot = shot()
            texts.append(client.read(screenshot)[1] == expected_text(screenshot))
        self.assertTrue(all(texts))
        self.assertNotEqual(client.worker_pid(), first)
        self.assertGreaterEqual(client.stats()["restarts"]["max_jobs"], 2)


class Shutdown(ClientTest):
    def test_close_lets_the_job_in_flight_finish_then_stops_everything(self):
        client = self.ready_client(timeout_s=5.0)
        box = {}
        job = shot("slow:0.6")
        thread = threading.Thread(target=lambda: box.setdefault("r", client.read(job)))
        thread.start()
        self.assertTrue(until(lambda: client.stats()["queue_depth"] == 1, 5))
        started = time.monotonic()
        client.close(grace_s=3.0)
        self.assertLess(time.monotonic() - started, 3.0)
        thread.join(5)
        self.assertEqual(box["r"][1], expected_text(job))
        self.assertEqual(processes(self.tag), [])
        with self.assertRaises(WorkerClosed):
            client.read(shot())
        client.close()                                                     # idempotent

    def test_close_gives_up_on_a_wedged_job_after_the_grace_and_leaves_no_process(self):
        client = self.ready_client(timeout_s=30.0)
        thread = threading.Thread(target=lambda: self._swallow(client))
        thread.start()
        self.assertTrue(until(lambda: client.stats()["queue_depth"] == 1, 5))
        started = time.monotonic()
        client.close(grace_s=0.5)
        self.assertLess(time.monotonic() - started, 3.0)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(processes(self.tag), [])

    @staticmethod
    def _swallow(client):
        try:
            client.read(shot("hang"))
        except Exception:                    # noqa: BLE001 - the job fails when the worker is taken away
            pass

    def test_the_worker_exits_by_itself_when_the_parent_is_killed(self):
        script = textwrap.dedent(f"""
            import sys, time
            sys.path[:0] = [{ROOT!r}]
            from trace_v2.macos.ocr_client import OcrWorkerClient
            client = OcrWorkerClient(5.0, command=[sys.executable, {FAKE!r}, "--tag", {self.tag!r}])
            assert client.wait_ready(30)
            print(client.worker_pid(), flush=True)
            time.sleep(600)
        """)
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            pid = int(parent.stdout.readline())
            self.assertTrue(alive(pid))
            parent.kill()                                                  # SIGKILL: no cleanup of any kind runs
            parent.wait(10)
            self.assertTrue(until(lambda: not alive(pid), 5), "the OCR worker outlived its parent")
        finally:
            parent.kill()
            parent.wait(10)
            parent.stdout.close()

    def test_the_worker_exits_when_the_parent_is_killed_while_it_is_stuck_in_a_job(self):
        script = textwrap.dedent(f"""
            import sys, threading, time
            sys.path[:0] = [{ROOT!r}, {str(Path(FAKE).parent)!r}]
            from trace_v2.macos.ocr_client import OcrWorkerClient
            from test_v2_ocr_worker import shot
            client = OcrWorkerClient(600.0, command=[sys.executable, {FAKE!r}, "--tag", {self.tag!r}])
            assert client.wait_ready(30)
            print(client.worker_pid(), flush=True)
            client.read(shot("hang"))
        """)
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            pid = int(parent.stdout.readline())
            time.sleep(0.5)
            parent.kill()
            parent.wait(10)
            self.assertTrue(until(lambda: not alive(pid), 5), "a worker stuck in a job outlived its parent")
        finally:
            parent.kill()
            parent.wait(10)
            parent.stdout.close()


class Privacy(ClientTest):
    def test_a_withheld_capture_never_reaches_the_worker(self):
        client = self.ready_client()
        sources = DraftSources(client, shot(), app="com.example.vault")
        from trace_v2.observations import privacy
        policy = privacy.PrivacyPolicy(excluded_apps=frozenset({"com.example.vault"}))
        draft, _ = build_draft(sources, EventBuffer(start_ns=T0 - 10), "k:0", policy=policy, clock_ns=lambda: T0)
        self.assertEqual((draft.ocr.status, draft.ocr.error_code), ("withheld", "excluded_app"))
        self.assertEqual(client.stats()["jobs"], 0)

    def test_redaction_and_secret_on_screen_work_on_the_returned_text_in_the_main_process(self):
        client = self.ready_client()
        # The fake echoes a hash, so inject a secret through a reader wrapper that post-processes like Vision's text would.
        key = "AKIAIOSFODNN7EXAMPLE"

        class WithSecret:
            version = client.version

            def read(self, screenshot, context):
                raw, text = client.read(screenshot, context)
                raw["lines"][0]["text"] = text = f"aws_access_key_id = {key}"
                return raw, text
        sources = DraftSources(WithSecret(), shot())
        draft, _ = build_draft(sources, EventBuffer(start_ns=T0 - 10), "k:0", clock_ns=lambda: T0)
        self.assertEqual(draft.ocr.status, "succeeded")
        self.assertNotIn(key, draft.ocr.text)
        self.assertNotIn(key, json.dumps(draft.ocr.raw))
        self.assertEqual(draft.screenshot.reason, "secret_on_screen")


class ThroughTheRunner(ClientTest):
    """run.main with a fake sources object whose OCR is the client: stats, SIGTERM shutdown and the flags."""

    def parts(self, client, seen):
        from test_v2_observation_pipeline import Sources
        from test_v2_integration_review import FakeNotifications, FakeTap

        def parts(store, ocr=None):
            seen.append(ocr)
            sources = Sources()
            sources.ocr = client
            return sources, (lambda buffer, cursor_ms, gate: FakeTap(buffer, cursor_ms, gate)), None, None, FakeNotifications
        return parts

    def test_sigterm_stops_the_worker_and_the_stats_carry_its_counters(self):
        from trace_v2 import run as run_module
        client = self.ready_client()
        pid, seen = client.worker_pid(), []
        with tempfile.TemporaryDirectory() as d:
            timer = threading.Timer(2.2, os.kill, (os.getpid(), signal.SIGTERM))
            timer.start()
            code = run_module.main(["--data-dir", d, "--interval", "0.5"], parts=self.parts(client, seen))
            timer.cancel()
            stats = json.loads((Path(d) / "runner_stats.json").read_text())
        self.assertEqual((code, seen), (0, [None]))
        counters = stats["ocr_worker"]               # the fake screen repeats one capture time, so repeats are idempotent hits
        self.assertGreaterEqual(counters["succeeded"], 1)
        self.assertGreaterEqual(counters["jobs"] + counters["idempotent_hits"], 2)
        self.assertEqual(stats["ocr_worker"]["isolation"], "process")
        self.assertTrue(until(lambda: not alive(pid), 5))
        self.assertTrue(client.stats()["closed"])

    def test_the_ocr_flags_reach_the_sources_factory_and_are_validated(self):
        from trace_v2 import run as run_module
        client = self.ready_client()
        seen = []
        with tempfile.TemporaryDirectory() as d:
            code = run_module.main(["--data-dir", d, "--once", "--ocr-isolation", "inprocess", "--ocr-timeout", "7"],
                                   parts=self.parts(client, seen))
            self.assertEqual((code, seen), (0, [{"isolation": "inprocess", "timeout_s": 7.0}]))
            with self.assertRaises(ValueError):
                run_module.main(["--data-dir", d, "--once", "--ocr-timeout", "0.1"], parts=self.parts(client, seen))
            with self.assertRaises(SystemExit):
                run_module.main(["--data-dir", d, "--once", "--ocr-isolation", "thread"], parts=self.parts(client, seen))


class Protocol(unittest.TestCase):
    def pipe_with(self, data):
        r, w = os.pipe()
        os.write(w, data)
        os.close(w)
        self.addCleanup(os.close, r)
        return r

    def test_a_frame_round_trips(self):
        fd = self.pipe_with(proto.encode({"type": "job", "job_id": 4}, b"\x00png\xff"))
        self.assertEqual(proto.read_frame(fd), ({"type": "job", "job_id": 4}, b"\x00png\xff"))
        with self.assertRaises(proto.EndOfStream) as e:
            proto.read_frame(fd)
        self.assertFalse(e.exception.partial)

    def test_bad_frames_are_refused(self):
        frame = proto.encode({"type": "result", "job_id": 1})
        for data, error in ((frame[:-3], proto.EndOfStream), (b"\xff" * 8 + b"x" * 8, proto.ProtocolError),
                            (proto.encode({"no": "type"}), proto.ProtocolError),
                            (proto.PREFIX.pack(3, 0) + b"{{{", proto.ProtocolError)):
            with self.assertRaises(error):
                proto.read_frame(self.pipe_with(data))
        self.assertTrue(proto.EndOfStream(partial=True).partial)

    def test_the_default_command_runs_the_worker_module(self):
        self.assertEqual(ocr_client.worker_command()[1:], ["-m", "trace_v2.macos.ocr_worker"])


class Settings(unittest.TestCase):
    def test_defaults_bounds_and_history(self):
        from trace_v2.observations import settings
        with TempStore() as t:
            s = t.store
            self.assertEqual((settings.get_ocr_isolation(s), settings.get_ocr_timeout(s),
                              settings.get_ocr_worker_max_rss_mb(s), settings.get_ocr_worker_max_jobs(s)),
                             ("process", 10.0, 800, 500))
            settings.set_ocr_isolation(s, "inprocess")
            settings.set_ocr_timeout(s, 30)
            settings.set_ocr_worker_max_rss_mb(s, 1200)
            settings.set_ocr_worker_max_jobs(s, 50)
            self.assertEqual((settings.get_ocr_isolation(s), settings.get_ocr_timeout(s),
                              settings.get_ocr_worker_max_rss_mb(s), settings.get_ocr_worker_max_jobs(s)),
                             ("inprocess", 30.0, 1200, 50))
            for setter, bad in ((settings.set_ocr_isolation, "thread"), (settings.set_ocr_timeout, 0.5),
                                (settings.set_ocr_timeout, 121), (settings.set_ocr_timeout, float("nan")),
                                (settings.set_ocr_timeout, True), (settings.set_ocr_worker_max_rss_mb, 100),
                                (settings.set_ocr_worker_max_rss_mb, 800.5), (settings.set_ocr_worker_max_jobs, 5)):
                with self.assertRaises(ValueError):
                    setter(s, bad)
            self.assertEqual(settings.get_ocr_timeout(s), 30.0)
            history = t.store.db.execute("SELECT count(*) FROM settings_history WHERE key LIKE 'ocr_%'").fetchone()[0]
            self.assertEqual(history, 4)


if __name__ == "__main__":
    unittest.main()

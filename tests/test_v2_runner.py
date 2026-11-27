"""Runner and lifecycle with fakes: pause/resume, lock, signals, single instance, interval precedence, warm-up, stats.
Platform independent (fcntl is POSIX); the real macOS run is in test_v2_macos_runner."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from trace_v2 import run as run_module
from trace_v2.observations import Context, ObservationStore, settings
from trace_v2 import runner as runner_module
from trace_v2.runner import (AlreadyRunning, InstanceLock, Lifecycle, Runner, read_status, resolve_interval)
from test_v2_observation_pipeline import Sources
from v2_observation_helpers import TempStore, click, context


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeTap:
    def __init__(self, buffer, cursor_ms, gate, state="running"):
        self.buffer, self.cursor_ms, self.gate, self._state = buffer, cursor_ms, gate, state
        self.reason = None if state == "running" else "input_monitoring_not_granted"
        self.started = self.stopped = False
        self.resyncs = 0

    def start(self):
        self.started = True
        return self._state

    def stop(self):
        self.stopped = True

    def resync_clock(self):
        self.resyncs += 1

    def stats(self):
        return {"state": self._state, "reason": self.reason, "raw": {}, "stored": {}}

    def event(self):
        self.buffer.add(click(time.time_ns()))


def make_runner(store, sources=None, interval=0.1, tap_state="running", **kwargs):
    taps = []

    def factory(buffer, cursor_ms, gate):
        taps.append(FakeTap(buffer, cursor_ms, gate, tap_state))
        return taps[-1]
    runner = Runner(store, sources or Sources(), interval, tap_factory=factory, cursor_sample_ms=150,
                    stats_every_s=kwargs.pop("stats_every_s", 3600), **kwargs)
    runner.taps = taps
    return runner


class InFlight:
    """Runs runner.run() on a thread so the test can drive notifications and stop it."""

    def __init__(self, runner, **kwargs):
        self.runner, self.result = runner, {}
        self.thread = threading.Thread(target=lambda: self.result.update(stats=runner.run(**kwargs)))

    def __enter__(self):
        self.thread.start()
        assert wait_for(lambda: self.runner.observer is not None and self.runner.lifecycle is not None)
        return self

    def observations(self):
        return self.runner.observer.stats["observations"]

    def __exit__(self, *exc):
        self.runner.request_stop()
        self.thread.join(10)


class IntervalPrecedence(unittest.TestCase):
    def test_override_beats_setting_beats_default(self):
        with TempStore() as t:
            self.assertEqual(resolve_interval(t.store), 3.0)
            settings.set_capture_interval(t.store, 7)
            self.assertEqual(resolve_interval(t.store), 7.0)
            self.assertEqual(resolve_interval(t.store, 1.5), 1.5)
            self.assertEqual(settings.get_capture_interval(t.store), 7.0)           # the override is for this run only

    def test_invalid_override_is_refused(self):
        with TempStore() as t:
            for bad in (0.1, 0, -1, 7200, float("nan")):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    resolve_interval(t.store, bad)


class SingleInstance(unittest.TestCase):
    def test_second_runner_is_refused_with_the_holders_pid_until_released(self):
        with tempfile.TemporaryDirectory() as d:
            first = InstanceLock(d).acquire()
            self.assertEqual(InstanceLock.held(d)[0], True)
            with self.assertRaises(AlreadyRunning) as raised:
                InstanceLock(d).acquire()
            self.assertEqual(raised.exception.pid, InstanceLock.held(d)[1])
            self.assertIn("already using this data directory", str(raised.exception))
            first.release()
            self.assertEqual(InstanceLock.held(d), (False, None))
            InstanceLock(d).acquire().release()

    def test_main_exits_3_when_another_runner_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as d, InstanceLock(d):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(run_module.main(["--data-dir", d]), 3)
            self.assertIn("already using this data directory", err.getvalue())


class FakeObserver:
    def __init__(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


class LifecycleStateMachine(unittest.TestCase):
    def setUp(self):
        self.observer, self.resyncs = FakeObserver(), []
        self.lifecycle = Lifecycle(self.observer, lambda: self.resyncs.append(1))

    def test_sleep_pauses_and_wake_resumes_and_resyncs(self):
        self.lifecycle.handle("sleep")
        self.assertTrue(self.observer.paused)
        self.lifecycle.handle("wake")
        self.assertFalse(self.observer.paused)
        self.assertEqual(self.resyncs, [1])

    def test_pauses_are_by_reason(self):
        self.lifecycle.handle("session_resign")
        self.lifecycle.handle("sleep")
        self.lifecycle.handle("wake")
        self.assertTrue(self.observer.paused)                                  # still switched out
        self.lifecycle.handle("session_active")
        self.assertFalse(self.observer.paused)
        self.assertEqual(self.resyncs, [1])

    def test_lock_and_unlock_keep_ticking(self):
        self.lifecycle.handle("screen_locked")
        self.assertFalse(self.observer.paused)
        self.assertTrue(self.lifecycle.snapshot()["screen_locked"])
        self.lifecycle.handle("screen_unlocked")
        self.assertFalse(self.lifecycle.snapshot()["screen_locked"])

    def test_unknown_event_changes_nothing(self):
        self.lifecycle.handle("mystery")
        self.assertEqual((self.observer.paused, self.lifecycle.snapshot()["events"]), (False, {}))


class RunnerEndToEnd(unittest.TestCase):
    def test_runs_captures_with_events_and_stops_cleanly_with_a_final_observation(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.15)
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: flight.observations() >= 2))
                self.assertTrue(runner.taps[0].started)
                runner.taps[0].event()
                runner.request_stop()                                          # what SIGINT/SIGTERM do
                flight.thread.join(10)
            stats = flight.result["stats"]
            self.assertTrue(runner.taps[0].stopped)
            self.assertTrue(stats["final"])
            self.assertEqual(stats["failures"], 0)
            self.assertEqual(stats["events_left_at_stop"], 0)                  # the event went into a final observation
            self.assertEqual(len(t.store.list(limit=1000)), stats["observations"])
            self.assertEqual(sum(len(t.store.get(o["observation_id"])["interaction_events"])
                                 for o in t.store.list(limit=1000)), 1)
            self.assertEqual(json.loads((Path(t.store.root) / "runner_stats.json").read_text())["final"], True)

    def test_sleep_pauses_captures_and_a_stop_while_asleep_takes_no_final_capture(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1)
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: flight.observations() >= 2))
                runner.notify("sleep")
                time.sleep(0.4)                                                # let an in-flight tick finish
                frozen = flight.observations()
                runner.taps[0].event()
                time.sleep(0.5)
                self.assertEqual(flight.observations(), frozen)                # nothing while asleep
                runner.request_stop()
                flight.thread.join(10)
            stats = flight.result["stats"]
            self.assertEqual((stats["observations"], stats["events_left_at_stop"]), (frozen, 0))
            self.assertEqual(runner.taps[0].resyncs, 0)
            self.assertEqual(runner.buffer.take(time.time_ns()).stats["discarded_paused"], 1)   # refused and counted

    def test_wake_resumes_and_resyncs_the_clock(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1)
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: flight.observations() >= 1))
                runner.notify("sleep")
                time.sleep(0.4)
                frozen = flight.observations()
                runner.notify("wake")
                self.assertTrue(wait_for(lambda: flight.observations() > frozen))
                self.assertEqual(runner.taps[0].resyncs, 1)
                self.assertEqual(flight.result.get("stats"), None)
            self.assertEqual(flight.result["stats"]["lifecycle"]["events"], {"sleep": 1, "wake": 1})

    def test_missed_ticks_are_not_counted_for_paused_time(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1)
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: flight.observations() >= 1))
                runner.notify("session_resign")
                time.sleep(0.8)
                runner.notify("session_active")
                self.assertTrue(wait_for(lambda: flight.observations() >= 3))
            self.assertEqual(flight.result["stats"]["missed_ticks"], 0)

    def test_screen_lock_keeps_capturing(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1)
            with InFlight(runner) as flight:
                runner.notify("screen_locked")
                before = flight.observations()
                self.assertTrue(wait_for(lambda: flight.observations() >= before + 2))
            self.assertTrue(flight.result["stats"]["lifecycle"]["screen_locked"])

    def test_duration_stops_the_run(self):
        with TempStore() as t:
            started = time.monotonic()
            stats = make_runner(t.store, interval=0.1).run(duration_s=0.5)
            self.assertLess(time.monotonic() - started, 5)
            self.assertGreaterEqual(stats["observations"], 3)

    def test_once_takes_exactly_one_observation(self):
        with TempStore() as t:
            stats = make_runner(t.store).run(once=True)
            self.assertEqual((stats["observations"], len(t.store.list(limit=10))), (1, 1))

    def test_unavailable_tap_is_logged_once_and_capture_continues(self):
        with TempStore() as t, self.assertLogs("trace_v2.runner", "WARNING") as logs:
            stats = make_runner(t.store, tap_state="unavailable").run(duration_s=0.4)
        self.assertEqual([m for m in logs.output if "input events unavailable" in m].__len__(), 1)
        self.assertGreaterEqual(stats["observations"], 2)
        self.assertEqual(stats["input"]["state"], "unavailable")

    def test_no_tap_factory_means_no_events_and_no_error(self):
        with TempStore() as t:
            stats = Runner(t.store, Sources(), 0.1).run(duration_s=0.3)
            self.assertEqual(stats["input"], {"state": "disabled"})


class Privacy(unittest.TestCase):
    EXCLUDED = Context({"bundle_id": "com.1password.1password", "name": "1Password", "pid": 9}, {"title": "v", "id": 1},
                       {"id": 1, "bounds": [0, 0, 1, 1], "scale": 1.0})

    def test_keyboard_follows_the_gate_decision_and_withheld_reasons_are_counted(self):
        with TempStore() as t:
            sources = Sources(ctx=self.EXCLUDED)
            runner = make_runner(t.store, sources, interval=0.1)
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: runner.taps and runner.taps[0].gate.blocked))
                sources.ctx = context()
                self.assertTrue(wait_for(lambda: not runner.taps[0].gate.blocked))
            stats = flight.result["stats"]
            self.assertGreaterEqual(stats["withheld_by_reason"]["excluded_app"], 1)
            self.assertEqual(sources.screenshots, stats["observations"] - stats["withheld_by_reason"]["excluded_app"])


class StartupSteps(unittest.TestCase):
    def test_warmup_runs_before_the_first_capture_and_a_failure_does_not_stop_the_run(self):
        order = []

        class Recording(Sources):
            def context(self):
                order.append("capture")
                return super().context()

        def warmup():
            order.append("warmup")
            raise TimeoutError("text recognition did not finish in 120s")
        with TempStore() as t, self.assertLogs("trace_v2.runner", "WARNING"):
            stats = make_runner(t.store, Recording(), warmup=warmup).run(duration_s=0.3)
        self.assertEqual(order[0], "warmup")
        self.assertEqual((stats["warmup"]["ok"], stats["warmup"]["error"]), (False, "TimeoutError"))
        self.assertGreaterEqual(stats["observations"], 1)

    def test_permissions_report_is_stored_and_in_the_stats(self):
        report = {"screen_recording": True, "accessibility": False, "input_monitoring": True, "automation": "unknown"}
        with TempStore() as t:
            stats = make_runner(t.store, permissions=lambda: dict(report)).run(once=True)
            stored = settings.get(t.store, "permissions_report", None)
        for key, value in report.items():
            self.assertEqual((stored[key], stats["permissions"][key]), (value, value))

    def test_retention_runs_at_startup_and_changes_nothing_by_default(self):
        with TempStore() as t:
            runner = make_runner(t.store)
            runner.run(once=True)
            self.assertEqual(runner.retention_runs, 1)
            self.assertEqual(len(t.store.list(limit=10)), 1)

    def test_trim_stops_after_its_time_budget_and_continues_next_run(self):
        from unittest import mock
        clock = iter(range(0, 1000, 1))                       # each _monotonic() call is one second later
        with TempStore() as t:
            runner = make_runner(t.store, monotonic=lambda: next(clock))
            calls = []
            with mock.patch("trace_v2.runner.retention.run"), mock.patch(
                    "trace_v2.runner.trim.run", lambda *a, **k: calls.append(1) or {"trimmed": 5}):
                runner._run_retention()
            self.assertEqual(len(calls), 3)                   # deadline = 1 + 2 s; stops once past it, not after "all rows"


class StatsRecord(unittest.TestCase):
    def test_periodic_stats_are_written_outside_the_evidence_tables(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1, stats_every_s=0.2)
            with InFlight(runner):
                path = Path(t.store.root) / "runner_stats.json"
                self.assertTrue(wait_for(path.exists))
                record = json.loads(path.read_text())
                self.assertFalse(record["final"])
            for key in ("uptime_s", "cpu_s", "cpu_pct_avg", "rss_mb", "peak_rss_mb", "observations", "failures",
                        "missed_ticks", "withheld_by_reason", "input", "lifecycle", "interval_s"):
                self.assertIn(key, record)
            self.assertEqual(read_status(t.store.root)["stats"]["final"], True)
            self.assertEqual(read_status(t.store.root)["running"], False)

    def test_stats_survive_a_missing_ps_and_report_no_rss(self):
        denied = PermissionError(1, "Operation not permitted")
        with TempStore() as t:
            runner = make_runner(t.store)
            with mock.patch.object(runner_module, "native_rss_mb", return_value=None), \
                    mock.patch.object(subprocess, "run", side_effect=denied):
                self.assertIsNone(runner_module._current_rss_mb())
                runner.write_stats()
            self.assertIsNone(json.loads((Path(t.store.root) / "runner_stats.json").read_text())["rss_mb"])

    @unittest.skipUnless(sys.platform == "darwin", "proc_pidinfo is macOS only")
    def test_own_rss_is_read_without_ps_on_macos(self):
        with mock.patch.object(subprocess, "run", side_effect=PermissionError(1, "Operation not permitted")):
            self.assertGreater(runner_module._current_rss_mb(), 1)

    def test_status_prints_without_changing_anything(self):
        with TempStore() as t:
            make_runner(t.store).run(once=True)
            out = io.StringIO()
            run_module.print_status(t.store.root, out)
            self.assertIn("running: False", out.getvalue())
            self.assertIn("observations", out.getvalue())


class MainEntryPoint(unittest.TestCase):
    def parts(self, store):
        sources = Sources()
        return sources, None, None, None, lambda notify: type("N", (), {"pump": lambda s, secs: time.sleep(secs),
                                                                         "close": lambda s: None})()

    def test_once_with_interval_override_leaves_the_stored_setting_alone(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_module.main(["--data-dir", d, "--once", "--interval", "1.5"], parts=self.parts), 0)
            store = ObservationStore(Path(d))
            self.assertEqual(settings.get_capture_interval(store), 3.0)
            self.assertEqual(len(store.list(limit=10)), 1)
            store.close()
            self.assertEqual(InstanceLock.held(d), (False, None))                  # the lock is released on exit

    def test_a_v1_data_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "trail").mkdir()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(run_module.main(["--data-dir", d, "--once"], parts=self.parts), 2)
            self.assertIn("V1", err.getvalue())


if __name__ == "__main__":
    unittest.main()

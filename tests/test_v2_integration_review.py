"""PR E integration review of PRs A-D: each test pins one fix. Platform independent (fakes, real store and buffer).

1 privacy gate order   2 input gate (current app, mouse/scroll too)   3 events during a locked screen
4 sleep/wake           5 shutdown and the instance lock               6 event attribution through the whole stack
7 reader failure and recovery, scope fail-closed"""

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from trace_v2 import run as run_module
from trace_v2.inputs import InputGate
from trace_v2.observations import (Context, ObservationStore, ScopeUnavailable, ScreenshotCapture,
                                   privacy, settings)
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import ContinuousObserver, ReaderUnavailable, observe_once
from trace_v2.runner import AlreadyRunning, InstanceLock, Lifecycle, Runner
from test_v2_observation_pipeline import Sources
from test_v2_runner import FakeTap, make_runner, wait_for
from v2_observation_helpers import PNG, SEC, T0, TempStore, click, context

LOCKED = Context({"bundle_id": "com.apple.loginwindow", "name": "loginwindow", "pid": 1}, {"locked": True},
                 {"id": 1, "bounds": [0, 0, 1, 1], "scale": 1.0})
EXCLUDED = Context({"bundle_id": "com.1password.1password", "name": "1Password", "pid": 9}, {"title": "v", "id": 1},
                   {"id": 1, "bounds": [0, 0, 1, 1], "scale": 1.0})


# ------------------------------------------------------------------ 1. gate order

class GateOrder(unittest.TestCase):
    policy = privacy.PrivacyPolicy()

    def test_a_locked_screen_is_screen_locked_even_though_the_lock_screen_turns_secure_input_on(self):
        self.assertEqual(privacy.gate(LOCKED, self.policy, secure_input=True).reason, "screen_locked")
        self.assertEqual(privacy.gate(LOCKED, self.policy, secure_input=False).reason, "screen_locked")

    def test_lock_screen_in_front_with_secure_input_counts_as_locked_even_if_the_adapter_did_not_flag_it(self):
        unflagged = Context(LOCKED.application, {}, {})
        self.assertEqual(privacy.gate(unflagged, self.policy, secure_input=True).reason, "screen_locked")

    def test_an_unreadable_context_still_fails_closed_first_of_all(self):
        broken = Context({}, {"locked": True}, {}, error_code="OSError")
        self.assertEqual(privacy.gate(broken, self.policy, secure_input=True).reason, "context_unavailable")

    def test_locked_beats_excluded_app_and_secure_input_beats_nothing_else_above_it(self):
        both = Context(EXCLUDED.application, {"locked": True}, {})
        self.assertEqual(privacy.gate(both, self.policy, True).reason, "screen_locked")
        self.assertEqual(privacy.gate(EXCLUDED, self.policy, True).reason, "excluded_app")
        self.assertEqual(privacy.gate(context(), self.policy, True).reason, "secure_input")
        self.assertFalse(privacy.gate(context(), self.policy, False).withheld)

    def test_through_the_pipeline_a_locked_screen_is_recorded_as_screen_locked(self):
        with TempStore() as t:
            sources = Sources(ctx=LOCKED, secure=True)
            oid = observe_once(t.store, sources, EventBuffer(T0 - SEC), "k:0").observation_id
            self.assertEqual(t.store.get(oid)["metadata"]["privacy"]["reason"], "screen_locked")
            self.assertEqual(sources.screenshots, 0)


# ------------------------------------------------------------------ 2. input gate

class InputGateRules(unittest.TestCase):
    def test_starts_blocked_and_opens_on_the_first_clean_decision(self):
        gate = InputGate()
        self.assertTrue(gate.blocked)
        gate.capture_decision(False, "com.example.editor")
        self.assertFalse(gate.blocked)

    def test_switching_to_an_excluded_app_blocks_at_once_without_waiting_for_a_capture(self):
        gate = InputGate({"com.example.vault"})
        gate.capture_decision(False, "com.example.editor")
        gate.app_activated("com.example.vault")
        self.assertTrue(gate.blocked)
        self.assertEqual(gate.reason(), "app_activated")
        gate.app_activated("com.example.editor")                     # and open again when the user comes back
        self.assertFalse(gate.blocked)

    def test_builtin_password_managers_need_no_setting_of_their_own(self):
        gate = InputGate(privacy.PrivacyPolicy().all_excluded_apps())
        gate.capture_decision(False, "x")
        gate.app_activated("com.1password.1password")
        self.assertTrue(gate.blocked)

    def test_a_capture_verdict_about_another_app_is_dropped_when_the_front_app_changes(self):
        gate = InputGate()
        gate.capture_decision(True, "com.apple.Safari")              # a private window in Safari
        self.assertTrue(gate.blocked)
        gate.app_activated("com.example.editor")
        self.assertFalse(gate.blocked)

    def test_a_capture_taken_just_before_a_switch_cannot_undo_the_switchs_block(self):
        gate = InputGate({"com.example.vault"})
        gate.app_activated("com.example.vault")
        gate.capture_decision(False, "com.example.editor")           # read its context a moment before the switch
        self.assertTrue(gate.blocked)

    def test_screen_lock_blocks_until_unlock(self):
        gate = InputGate()
        gate.capture_decision(False, "a")
        gate.screen_locked(True)
        self.assertEqual(gate.reason(), "screen_locked")
        gate.screen_locked(False)
        self.assertFalse(gate.blocked)

    def test_an_activation_without_a_bundle_id_and_the_lock_screen_apps_block(self):
        gate = InputGate()
        gate.capture_decision(False, "a")
        gate.app_activated(None)
        self.assertTrue(gate.blocked)
        gate.app_activated("com.apple.loginwindow")
        self.assertTrue(gate.blocked)

    def test_excluded_list_changes_apply_to_the_current_app(self):
        gate = InputGate()
        gate.app_activated("com.example.bank")
        self.assertFalse(gate.blocked)
        gate.set_excluded({"com.example.bank"})
        self.assertTrue(gate.blocked)


class RunnerFeedsTheInputGate(unittest.TestCase):
    def test_activation_notifications_and_lock_notifications_reach_the_gate(self):
        with TempStore() as t:
            settings.put(t.store, privacy.POLICY_KEY, {"excluded_apps": ["com.example.vault"]})
            runner = make_runner(t.store, interval=0.05)
            thread = threading.Thread(target=runner.run)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: runner.lifecycle is not None and runner.taps))
                gate = runner.taps[0].gate
                self.assertTrue(wait_for(lambda: not gate.blocked))              # clean capture opened it
                runner.notify("app_activated", "com.example.vault")              # the user user switches to the vault
                self.assertTrue(gate.blocked)                                    # immediately, no capture needed
                runner.notify("app_activated", "com.example.editor")
                self.assertFalse(gate.blocked)
                runner.notify("screen_locked")
                self.assertTrue(gate.blocked)
                runner.notify("screen_unlocked")
                self.assertFalse(gate.blocked)
            finally:
                runner.request_stop()
                thread.join(10)

    def test_the_front_app_is_read_at_start_and_reconciled_so_a_missed_notification_is_repaired(self):
        front = ["com.1password.1password"]
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.05, settings_refresh_s=0.1, front_app=lambda: front[0])
            thread = threading.Thread(target=runner.run)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: runner.taps and runner.gate and runner.gate.reason() == "app_activated"))
                front[0] = "com.example.editor"                                  # the notification for this was "lost"
                self.assertTrue(wait_for(lambda: not runner.gate.blocked))
            finally:
                runner.request_stop()
                thread.join(10)

    def test_a_front_app_probe_that_fails_blocks_input(self):
        def broken():
            raise OSError("no front app")
        with TempStore() as t, self.assertLogs("trace_v2.runner", "ERROR"):
            runner = make_runner(t.store, interval=5, front_app=broken)
            runner.run(once=True)
            self.assertTrue(runner.gate.blocked)

    def test_user_policy_changes_apply_while_running(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.05, settings_refresh_s=0.1)
            thread = threading.Thread(target=runner.run)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: runner.observer is not None and runner.gate is not None))
                runner.notify("app_activated", "com.example.bank")
                self.assertTrue(wait_for(lambda: not runner.gate.blocked))
                privacy.save_policy(t.store, excluded_apps=["com.example.bank"])
                self.assertTrue(wait_for(lambda: runner.gate.blocked))
                self.assertIn("com.example.bank", runner.observer.policy.excluded_apps)
            finally:
                runner.request_stop()
                thread.join(10)


# ------------------------------------------------------------------ 3 + 4. lock and sleep

class LockedScreenEvents(unittest.TestCase):
    def test_events_of_a_locked_screen_are_counted_not_stored_and_the_window_moves_on(self):
        with TempStore() as t:
            buffer, sources = EventBuffer(T0), Sources(ctx=LOCKED, secure=True)
            clock = iter(range(T0 + 3 * SEC, T0 + 100 * SEC, 3 * SEC))
            buffer.add(click(T0 + 1 * SEC))
            buffer.add(click(T0 + 2 * SEC))
            a = observe_once(t.store, sources, buffer, "k:0", clock_ns=lambda: next(clock)).observation_id
            sources.ctx, sources.secure = context(), False
            buffer.add(click(T0 + 4 * SEC))
            b = observe_once(t.store, sources, buffer, "k:1", clock_ns=lambda: next(clock)).observation_id
            first, second = t.store.get(a), t.store.get(b)
            self.assertEqual(first["interaction_events"], [])
            self.assertEqual(first["metadata"]["extraction_status"]["events"]["discarded_privacy"], 2)
            self.assertEqual(first["metadata"]["events_window_ns"], [T0, T0 + 3 * SEC])
            self.assertEqual(second["metadata"]["events_window_ns"], [T0 + 3 * SEC, T0 + 6 * SEC])   # advanced, no overlap
            self.assertEqual([e["timestamp_ns"] for e in second["interaction_events"]], [T0 + 4 * SEC])


class SleepWake(unittest.TestCase):
    def test_no_observation_and_no_failed_one_while_paused_and_no_catch_up_burst_after_wake(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1)
            thread = threading.Thread(target=runner.run)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: runner.observer is not None and runner.observer.stats["observations"] >= 2))
                runner.notify("sleep")
                time.sleep(0.3)
                frozen = dict(runner.observer.stats)
                time.sleep(1.0)                                                  # ten intervals pass asleep
                self.assertEqual(runner.observer.stats["observations"], frozen["observations"])
                self.assertEqual(runner.observer.stats["failures"], frozen["failures"])
                runner.notify("wake")
                time.sleep(0.35)
                burst = runner.observer.stats["observations"] - frozen["observations"]
                self.assertTrue(1 <= burst <= 5, burst)                          # ~4 at 0.1 s; a replay would be ~14
                self.assertEqual(runner.observer.stats["missed_ticks"], 0)       # paused time is not "missed"
            finally:
                runner.request_stop()
                thread.join(10)

    def test_events_arriving_while_paused_are_refused_and_counted_and_the_window_spans_the_gap(self):
        with TempStore() as t:
            buffer, now = EventBuffer(T0), [T0 + 3 * SEC]
            observer = ContinuousObserver(t.store, Sources(), buffer, clock_ns=lambda: now[0])
            lifecycle = Lifecycle(observer, buffer=buffer)
            buffer.add(click(T0 + 1 * SEC))
            observer.tick()
            now[0] = T0 + 600 * SEC
            lifecycle.handle("sleep")
            self.assertFalse(buffer.add(click(T0 + 5 * SEC)))                    # arrives while asleep
            lifecycle.handle("wake")
            self.assertTrue(buffer.add(click(T0 + 599 * SEC)))
            observer.tick()
            second = t.store.get(t.store.list()[1]["observation_id"])
            self.assertEqual(second["metadata"]["events_window_ns"], [T0 + 3 * SEC, T0 + 600 * SEC])
            self.assertEqual(len(second["interaction_events"]), 1)
            self.assertEqual(second["metadata"]["extraction_status"]["events"]["discarded_paused"], 1)

    def test_wake_resyncs_the_input_clock_before_events_are_accepted_again(self):
        order = []
        buffer = EventBuffer(T0)
        buffer.end_pause = lambda: order.append("accepting")
        lifecycle = Lifecycle(type("O", (), {"pause": lambda s: None, "resume": lambda s: None})(),
                              lambda: order.append("resync"), buffer)
        lifecycle.handle("sleep")
        lifecycle.handle("wake")
        self.assertEqual(order, ["resync", "accepting"])


# ------------------------------------------------------------------ 5. shutdown and the lock

class FakeNotifications:
    def __init__(self, notify):
        self.closed = False

    def pump(self, seconds):
        time.sleep(seconds)

    def close(self):
        self.closed = True


def fake_parts(taps, sources=None, **tap_kwargs):
    def parts(store):
        def factory(buffer, cursor_ms, gate):
            taps.append(FakeTap(buffer, cursor_ms, gate, **tap_kwargs))
            return taps[-1]
        return (sources or Sources()), factory, None, None, FakeNotifications
    return parts


class Shutdown(unittest.TestCase):
    def run_with_signal(self, signum):
        taps = []
        with tempfile.TemporaryDirectory() as d:
            timer = threading.Timer(1.0, os.kill, (os.getpid(), signum))
            timer.start()
            with contextlib.redirect_stderr(io.StringIO()):
                code = run_module.main(["--data-dir", d, "--interval", "0.5"], parts=fake_parts(taps))
            timer.cancel()
            store = ObservationStore(Path(d))
            times = [o["timestamp_ns"] for o in store.list(limit=100)]
            stats = json.loads((Path(d) / "runner_stats.json").read_text())
            store.close()
            return code, taps[0], times, stats, (Path(d) / "runner.lock").exists(), InstanceLock.held(d)

    def check(self, signum):
        started = time.time_ns()
        code, tap, times, stats, lock_file, held = self.run_with_signal(signum)
        self.assertEqual(code, 0)
        self.assertTrue(tap.stopped)                                             # tap stopped
        self.assertFalse(lock_file)                                              # lock file removed
        self.assertEqual(held, (False, None))
        self.assertTrue(stats["final"])
        self.assertGreaterEqual(len(times), 2)
        self.assertGreater(times[-1], started + int(0.9 * SEC))                  # the last one is the shutdown capture
        self.assertEqual(len(set(times)), len(times))

    def test_sigint_gives_a_final_observation_stops_the_tap_and_removes_the_lock(self):
        self.check(signal.SIGINT)

    def test_sigterm_gives_a_final_observation_stops_the_tap_and_removes_the_lock(self):
        self.check(signal.SIGTERM)

    def test_a_final_observation_is_taken_even_when_no_event_is_pending(self):
        with TempStore() as t:
            runner = make_runner(t.store, interval=30)                           # one tick at start, then asleep
            thread = threading.Thread(target=runner.run)
            thread.start()
            self.assertTrue(wait_for(lambda: runner.observer is not None and runner.observer.stats["observations"] == 1))
            runner.request_stop()
            thread.join(10)
            self.assertEqual(len(t.store.list()), 2)

    def test_a_tap_that_fails_to_stop_does_not_hang_exit_or_keep_the_lock(self):
        class Broken(FakeTap):
            def stop(self):
                raise RuntimeError("tap thread blew up")
        with tempfile.TemporaryDirectory() as d, self.assertLogs("trace_v2.runner", "ERROR"):
            def parts(store):
                return Sources(), (lambda b, c, g: Broken(b, c, g)), None, None, FakeNotifications
            self.assertEqual(run_module.main(["--data-dir", d, "--once"], parts=parts), 0)
            self.assertEqual(InstanceLock.held(d), (False, None))

    def test_a_dead_observer_thread_is_reported_exits_1_and_still_releases_everything(self):
        class Dying(ContinuousObserver):
            def run(self, *a, **k):
                raise RuntimeError("observer died")
        taps = []
        with tempfile.TemporaryDirectory() as d, self.assertLogs("trace_v2.runner", "ERROR"):
            def parts(store):
                return Sources(), (lambda b, c, g: taps.append(FakeTap(b, c, g)) or taps[-1]), None, None, FakeNotifications
            original = Runner.__init__

            def init(self, *a, **k):
                original(self, *a, observer_factory=Dying, **k)
            Runner.__init__ = init
            try:
                self.assertEqual(run_module.main(["--data-dir", d], parts=parts), 1)
            finally:
                Runner.__init__ = original
            self.assertTrue(taps[0].stopped)
            self.assertEqual(InstanceLock.held(d), (False, None))
            self.assertEqual(json.loads((Path(d) / "runner_stats.json").read_text())["observer_error"], "RuntimeError")

    def test_stopping_during_a_slow_warmup_does_not_wait_for_it(self):
        release = threading.Event()
        with TempStore() as t:
            runner = make_runner(t.store, interval=0.1, warmup=lambda: release.wait(30))
            thread = threading.Thread(target=runner.run)
            thread.start()
            time.sleep(0.4)
            started = time.monotonic()
            runner.request_stop()
            thread.join(10)
            release.set()
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(runner.warmup_result["error"], "interrupted")


class InstanceLockRules(unittest.TestCase):
    def test_a_stale_lock_file_without_a_holder_never_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "runner.lock").write_text("999999\n")                     # what a crashed runner leaves behind
            self.assertEqual(InstanceLock.held(d), (False, None))
            with InstanceLock(d):
                self.assertEqual(int((Path(d) / "runner.lock").read_text()), os.getpid())

    def test_a_killed_holder_releases_the_lock_because_it_is_an_flock(self):
        with tempfile.TemporaryDirectory() as d:
            child = subprocess.Popen([sys.executable, "-c", (
                "import sys,time; sys.path.insert(0, %r); from trace_v2.runner import InstanceLock; "
                "InstanceLock(%r).acquire(); print('held', flush=True); time.sleep(60)") % (str(Path(__file__).parents[1]), d)],
                stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "held")
                with self.assertRaises(AlreadyRunning) as raised:
                    InstanceLock(d).acquire()
                self.assertEqual(raised.exception.pid, child.pid)
            finally:
                child.kill()                                                     # SIGKILL: no cleanup runs
                child.wait()
            self.assertTrue((Path(d) / "runner.lock").exists())                  # the stale file is still there ...
            InstanceLock(d).acquire().release()                                  # ... and does not matter

    def test_release_removes_the_file_and_a_replaced_file_is_relocked(self):
        with tempfile.TemporaryDirectory() as d:
            lock = InstanceLock(d).acquire()
            lock.release()
            self.assertFalse((Path(d) / "runner.lock").exists())
            lock.release()                                                       # idempotent


# ------------------------------------------------------------------ 6. event attribution, whole stack

class Produced:
    """Events with a unique x so each can be found again: x = its index."""

    def __init__(self):
        self.n, self.made = 0, []

    def event(self, t):
        self.n += 1
        self.made.append(self.n)
        return click(t, x=float(self.n))


class FailingOnce:
    def __init__(self, store):
        self.store, self.armed = store, False

    def __getattr__(self, name):
        return getattr(self.store, name)

    def ingest(self, draft):
        if self.armed:
            self.armed = False
            raise OSError("disk full")
        return self.store.ingest(draft)


class EventAttribution(unittest.TestCase):
    def test_every_event_is_in_exactly_one_observation_or_in_a_counted_drop(self):
        with TempStore() as t:
            now = [T0]
            clock = lambda: now[0]
            buffer, gate = EventBuffer(T0), InputGate({"com.example.vault"}, start_blocked=False)
            sources = Sources()
            store = FailingOnce(t.store)
            observer = ContinuousObserver(store, sources, buffer, clock_ns=clock)
            lifecycle = Lifecycle(observer, buffer=buffer, gate=gate)
            made = Produced()

            def at(seconds, n=1, tap_blocked=False):
                for i in range(n):
                    event = made.event(T0 + int((seconds + i / 10) * SEC))
                    if gate.blocked or tap_blocked:
                        buffer.note_dropped("gated")
                    else:
                        buffer.add(event)

            def tick(seconds):
                now[0] = T0 + int(seconds * SEC)
                return observer.tick()

            at(1, 2)                                                             # -> obs 0
            tick(3)
            sources.ctx = EXCLUDED                                               # a password manager comes to the front
            at(4, 3)                                                             # stored in the buffer ...
            tick(6)                                                              # ... then withheld: discarded_privacy
            sources.ctx = context()
            at(7, 2)
            store.armed = True
            self.assertFalse(tick(9))                                            # failed capture: batch restored
            at(10, 2)
            self.assertTrue(tick(12))                                            # stores 4 events with a wider window
            gate.app_activated("com.example.vault")                              # tap drops these at the source
            at(12.5, 3)
            gate.app_activated("com.example.editor")
            lifecycle.handle("sleep")
            at(13, 2)                                                            # arrive while paused
            lifecycle.handle("wake")
            at(16, 2)
            tick(18)
            at(19, 2)                                                            # the shutdown observation
            tick(20)
            self.assertEqual(buffer.pending(), 0)

            stored, drops, windows = [], {}, []
            for row in t.store.list(limit=100):
                o = t.store.get(row["observation_id"])
                stored += [int(e["payload"]["x"]) for e in o["interaction_events"]]
                windows.append(o["metadata"]["events_window_ns"])
                for key, value in o["metadata"]["extraction_status"]["events"].items():
                    if key.startswith(("discarded_", "dropped_", "suppressed_", "rejected", "before_start")):
                        drops[key] = drops.get(key, 0) + value
            self.assertEqual(len(stored), len(set(stored)), "an event was stored twice")
            self.assertEqual({k: v for k, v in drops.items() if v},
                             {"discarded_paused": 2, "dropped_gated": 3, "discarded_privacy": 3})
            self.assertEqual(len(stored) + sum(drops.values()), len(made.made), (stored, drops))
            self.assertEqual(sorted(stored), [1, 2, 6, 7, 8, 9, 15, 16, 17, 18])  # nothing lost, nothing duplicated
            for (a0, a1), (b0, b1) in zip(windows, windows[1:]):                 # contiguous, no gaps, no overlaps
                self.assertEqual(a1, b0)
            self.assertEqual(len(windows), 5)                                    # 5 stored: 0, 1(withheld), 3 (after failure), 4, 5


# ------------------------------------------------------------------ 7. reader failure, scope

class FlakyReader:
    version = "flaky/1"

    def __init__(self, fail_times, error):
        self.fail_times, self.error, self.calls = fail_times, error, 0

    def read(self, *args):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return {"lines": []}, "ok"


class ReaderFailureAndRecovery(unittest.TestCase):
    def test_a_reader_failure_is_recorded_and_the_next_capture_retries_normally(self):
        with TempStore() as t:
            sources = Sources()
            sources.ocr = FlakyReader(1, TimeoutError("text recognition did not finish in 10s"))
            sources.accessibility = FlakyReader(1, ReaderUnavailable("accessibility not trusted"))
            runner = make_runner(t.store, sources, interval=0.1)
            runner.run(duration_s=0.6)
            rows = [t.store.get(o["observation_id"]) for o in t.store.list(limit=100)]
            first, rest = rows[0]["metadata"]["extraction_status"], [r["metadata"]["extraction_status"] for r in rows[1:]]
            self.assertEqual((first["ocr"]["status"], first["ocr"]["error_code"]), ("failed", "TimeoutError"))
            self.assertEqual((first["accessibility"]["status"], first["accessibility"]["error_code"]),
                             ("unavailable", "ReaderUnavailable"))
            self.assertTrue(rest and all(s["ocr"]["status"] == s["accessibility"]["status"] == "succeeded" for s in rest))
            self.assertEqual(sources.ocr.calls, len(rows))                       # asked again every capture
            self.assertEqual(runner.stats()["evidence"]["reader_failures"],
                             {"ocr:failed:TimeoutError": 1, "accessibility:unavailable:ReaderUnavailable": 1})


class ScreenshotScope(unittest.TestCase):
    def test_default_is_front_app_and_only_the_two_values_are_accepted(self):
        with TempStore() as t:
            self.assertEqual(settings.get_screenshot_scope(t.store), "front_app")
            settings.set_screenshot_scope(t.store, "display")
            self.assertEqual(settings.get_screenshot_scope(t.store), "display")
            for bad in ("window", "", None, True, "Display"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    settings.set_screenshot_scope(t.store, bad)

    def test_an_unmatched_front_app_withholds_the_screenshot_as_scope_unavailable_and_never_widens(self):
        class NoMatch(Sources):
            screenshot_scope = "front_app"

            def screenshot(self):
                self.screenshots += 1
                raise ScopeUnavailable("no running application for pid 42")
        with TempStore() as t:
            sources = NoMatch()
            oid = observe_once(t.store, sources, EventBuffer(T0 - SEC), "k:0").observation_id
            o = t.store.get(oid)
            self.assertEqual(sources.screenshots, 1)                             # asked once, not retried wider
            self.assertEqual(o["evidence"]["screenshot"]["status"], "withheld")
            self.assertEqual(o["evidence"]["screenshot"]["error_code"], "scope_unavailable")
            self.assertEqual(o["metadata"]["extraction_status"]["ocr"]["error_code"], "no_screenshot")
            self.assertEqual(o["metadata"]["extraction_status"]["accessibility"]["status"], "succeeded")
            self.assertEqual(o["metadata"]["privacy"]["screenshot_scope"], "front_app")
            self.assertEqual(t.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_each_observation_records_the_scope_it_was_taken_with(self):
        class Scoped(Sources):
            screenshot_scope = "display"

            def screenshot(self):
                return ScreenshotCapture(PNG, "png", 32, 32, T0, scope=self.screenshot_scope)
        with TempStore() as t:
            sources = Scoped()
            a = observe_once(t.store, sources, EventBuffer(T0 - SEC), "k:0").observation_id
            sources.screenshot_scope = "front_app"
            b = observe_once(t.store, sources, EventBuffer(T0 - SEC), "k:1").observation_id
            self.assertEqual([t.store.get(i)["metadata"]["privacy"]["screenshot_scope"] for i in (a, b)],
                             ["display", "front_app"])

    def test_the_runner_applies_a_changed_scope_setting_to_the_sources_while_running(self):
        class Scoped(Sources):
            screenshot_scope = "front_app"
        with TempStore() as t:
            sources = Scoped()
            runner = make_runner(t.store, sources, interval=0.05, settings_refresh_s=0.1)
            thread = threading.Thread(target=runner.run)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: runner.observer is not None and runner.gate is not None))
                settings.set_screenshot_scope(t.store, "display")
                self.assertTrue(wait_for(lambda: sources.screenshot_scope == "display"))
            finally:
                runner.request_stop()
                thread.join(10)
            stats = runner.write_stats(final=True)
            self.assertEqual(set(stats["evidence"]["screenshot_scopes"]), {"front_app", "display"})


if __name__ == "__main__":
    unittest.main()

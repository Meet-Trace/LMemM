"""Trace V2 capture pipeline: independent readers, event attribution, continuous capture."""

import threading
import unittest
from unittest import mock

from trace_v2.observations import InteractionEvent, ScreenshotCapture
from trace_v2.observations.events import EventBuffer, InvalidEvent, validate
from trace_v2.observations.pipeline import ContinuousObserver, ReaderUnavailable, build_draft, observe_once
from v2_observation_helpers import PNG, SEC, T0, TempStore, click, context


class Clock:
    """Advances 1 µs per read, like a real clock."""

    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += 1000
        return self.t


class Reader:
    def __init__(self, version, result=None, error=None):
        self.version, self.result, self.error = version, result, error

    def read(self, *args):
        if self.error:
            raise self.error
        return self.result


class Sources:
    capture_version = "fake-capture/1"

    def __init__(self, ocr_error=None, ax_error=None, shot_error=None, context_error=None, ctx=None,
                 ocr_text="a", secure=False):
        self.ocr = Reader("ocr/1", ({"lines": [{"text": ocr_text, "box": [0, 0, 1, 1]}]}, ocr_text), ocr_error)
        self.accessibility = Reader("ax/1", ({"role": "AXWindow", "children": []}, ""), ax_error)
        self.shot_error, self.context_error, self.ctx, self.secure = shot_error, context_error, ctx, secure
        self.screenshots = 0

    def context(self):
        if self.context_error:
            raise self.context_error
        return self.ctx or context()

    def secure_input(self):
        return self.secure

    def screenshot(self):
        self.screenshots += 1
        if self.shot_error:
            raise self.shot_error
        return ScreenshotCapture(PNG, "png", 32, 32, T0)


def buffer():
    return EventBuffer(start_ns=T0 - 10 * SEC)


class ReaderIsolationTest(unittest.TestCase):
    def status(self, ts, sources, key):
        oid = observe_once(ts.store, sources, buffer(), key, clock_ns=Clock()).observation_id
        st = ts.store.get(oid)["metadata"]["extraction_status"]
        return st["screenshot"]["status"], st["ocr"]["status"], st["accessibility"]["status"], st

    def test_every_failure_combination_still_records_an_observation(self):
        cases = [
            ("screenshot ok, ocr ok, ax fails", Sources(ax_error=RuntimeError("ax")), ("stored", "succeeded", "failed")),
            ("screenshot ok, ocr fails, ax ok", Sources(ocr_error=ValueError("ocr")), ("stored", "failed", "succeeded")),
            ("both readers fail", Sources(ocr_error=OSError(), ax_error=OSError()), ("stored", "failed", "failed")),
            ("ax not permitted", Sources(ax_error=ReaderUnavailable("no permission")), ("stored", "succeeded", "unavailable")),
            ("screenshot fails", Sources(shot_error=PermissionError("denied")), ("failed", "skipped", "succeeded")),
        ]
        with TempStore() as ts:
            for i, (name, sources, expect) in enumerate(cases):
                with self.subTest(name):
                    self.assertEqual(self.status(ts, sources, f"s:{i}")[:3], expect)
            self.assertEqual(len(ts.store.list()), len(cases))

    def test_context_failure_is_recorded_not_fatal(self):
        with TempStore() as ts:
            st = self.status(ts, Sources(context_error=RuntimeError("no front app")), "s:0")[3]
            self.assertEqual(st["context"], {"status": "failed", "error_code": "RuntimeError"})

    def test_unserialisable_reader_output_is_a_recorded_failure(self):
        with TempStore() as ts:
            sources = Sources()
            sources.ocr.result = ({"bad": object()}, "x")
            self.assertEqual(self.status(ts, sources, "s:0")[1], "failed")


class EventValidationTest(unittest.TestCase):
    def test_keypress_never_carries_characters_or_keycodes(self):
        for payload in ({"phase": "down", "key_class": "character", "characters": "p"},
                        {"phase": "down", "key_class": "character", "keycode": 35},
                        {"phase": "down", "key_class": "p"}):
            with self.subTest(payload), self.assertRaises(InvalidEvent):
                validate(InteractionEvent("keypress", T0, payload))

    def test_buffer_rejects_and_counts_without_storing(self):
        buf = buffer()
        self.assertTrue(buf.add(click(T0)))
        self.assertFalse(buf.add(InteractionEvent("keypress", T0, {"phase": "down", "key_class": "character",
                                                                    "characters": "s"})))
        self.assertFalse(buf.add(InteractionEvent("keypress", T0, {"phase": "down", "key_class": "character"}),
                                 secure_input=True))
        self.assertFalse(buf.add(InteractionEvent("drag", T0, {})))
        batch = buf.take(T0 + 1)
        self.assertEqual(len(batch.events), 1)
        self.assertEqual({k: batch.stats[k] for k in ("received", "rejected", "suppressed_secure_input", "stored")},
                         {"received": 4, "rejected": 2, "suppressed_secure_input": 1, "stored": 1})

    def test_overflow_is_counted(self):
        buf = EventBuffer(start_ns=T0, max_events=2)
        for t in range(3):
            buf.add(click(T0 + t))
        self.assertEqual(buf.take(T0 + 10).stats["dropped_overflow"], 1)


class AttributionTest(unittest.TestCase):
    def test_startup_events_before_the_session_are_not_attributed(self):
        buf = EventBuffer(start_ns=T0)
        self.assertFalse(buf.add(click(T0 - 1)))
        self.assertTrue(buf.add(click(T0)))
        batch = buf.take(T0 + SEC)
        self.assertEqual((len(batch.events), batch.window, batch.stats["before_start"]), (1, (T0, T0 + SEC), 1))

    def test_boundaries_are_exclusive_at_start_and_inclusive_at_capture_time(self):
        buf = EventBuffer(start_ns=T0)
        for t in (T0, T0 + SEC, T0 + SEC + 1):
            buf.add(click(t))
        first = buf.take(T0 + SEC)
        first.commit()
        second = buf.take(T0 + 2 * SEC)
        self.assertEqual([e.occurred_at_ns for e in first.events], [T0, T0 + SEC])
        self.assertEqual([e.occurred_at_ns for e in second.events], [T0 + SEC + 1])
        self.assertEqual(second.window, (T0 + SEC, T0 + 2 * SEC))

    def test_late_arrivals_go_to_the_next_observation_and_are_counted(self):
        buf = EventBuffer(start_ns=T0)
        buf.take(T0 + SEC).commit()
        buf.add(click(T0 + 10))                    # happened before the first capture, delivered after it
        batch = buf.take(T0 + 2 * SEC)
        self.assertEqual((len(batch.events), batch.stats["late"]), (1, 1))
        self.assertEqual(batch.events[0].occurred_at_ns, T0 + 10)

    def test_clock_stepping_back_never_produces_an_inverted_window(self):
        buf = EventBuffer(start_ns=T0)
        buf.take(T0 + 5 * SEC).commit()
        buf.add(click(T0 + 6 * SEC))
        batch = buf.take(T0 + 2 * SEC)             # wall clock moved backwards
        self.assertEqual((batch.window, batch.stats["clock_regression"], len(batch.events)),
                         ((T0 + 5 * SEC, T0 + 5 * SEC), 1, 0))

    def test_a_failed_capture_loses_no_events(self):
        with TempStore() as ts:
            buf = buffer()
            for t in (T0 - 3, T0 - 2):
                buf.add(click(t))
            with mock.patch.object(ts.store, "ingest", side_effect=OSError("disk full")), self.assertRaises(OSError):
                observe_once(ts.store, Sources(), buf, "s:0", clock_ns=Clock())
            self.assertEqual(buf.pending(), 2)
            oid = observe_once(ts.store, Sources(), buf, "s:1", clock_ns=Clock()).observation_id
            stored = ts.store.get(oid)
            self.assertEqual([e["timestamp_ns"] for e in stored["interaction_events"]], [T0 - 3, T0 - 2])
            self.assertEqual(stored["metadata"]["events_window_ns"][0], T0 - 10 * SEC)   # window did not advance
            self.assertEqual(buf.pending(), 0)

    def test_consecutive_observations_cover_time_without_gaps_or_overlaps(self):
        with TempStore() as ts:
            buf, clock = buffer(), Clock()
            obs = ContinuousObserver(ts.store, Sources(), buf, clock_ns=clock)
            for i in range(4):
                buf.add(click(clock.t + 1))
                obs.tick()
            windows = [ts.store.get(o["observation_id"])["metadata"]["events_window_ns"] for o in ts.store.list()]
            self.assertEqual(windows[0][0], T0 - 10 * SEC)
            for prev, nxt in zip(windows, windows[1:]):
                self.assertEqual(prev[1], nxt[0])
            self.assertEqual([len(ts.store.events(o["observation_id"])) for o in ts.store.list()], [1, 1, 1, 1])
            buf.add(click(clock.t + 1))            # after the last capture: waits for the next one
            self.assertEqual(buf.pending(), 1)


class ContinuousTest(unittest.TestCase):
    def fake_time(self):
        fake = {"t": 0.0}

        class Stop(threading.Event):
            def wait(self, timeout=None):
                fake["t"] += timeout or 0
                return False

        return fake, Stop()

    def test_captures_on_schedule_with_nothing_happening(self):
        with TempStore() as ts:
            fake, stop = self.fake_time()
            obs = ContinuousObserver(ts.store, Sources(), buffer(), interval_s=2.0, clock_ns=Clock(),
                                     monotonic=lambda: fake["t"])
            stats = obs.run(stop=stop, max_ticks=5)
            self.assertEqual(stats["observations"], 5)
            self.assertEqual(len(ts.store.list()), 5)
            self.assertEqual(fake["t"], 8.0)

    def test_stopping_takes_a_final_observation_for_pending_events(self):
        with TempStore() as ts:
            buf, clock = buffer(), Clock()
            stop = threading.Event()
            obs = ContinuousObserver(ts.store, Sources(), buf, clock_ns=clock)
            obs.tick()
            buf.add(click(clock.t + 1))
            stop.set()
            stats = obs.run(stop=stop)
            self.assertEqual((stats["observations"], stats["events_left_at_stop"]), (2, 0))
            last = ts.store.list()[-1]["observation_id"]
            self.assertEqual(len(ts.store.events(last)), 1)

    def test_missed_ticks_are_skipped_and_counted(self):
        with TempStore() as ts:
            fake, stop = self.fake_time()
            sources = Sources()
            real = sources.screenshot

            def slow():
                fake["t"] += 5.0                   # one capture takes 2.5 intervals
                return real()

            sources.screenshot = slow
            obs = ContinuousObserver(ts.store, sources, buffer(), interval_s=2.0, clock_ns=Clock(),
                                     monotonic=lambda: fake["t"])
            stats = obs.run(stop=stop, max_ticks=3)
            self.assertEqual((stats["observations"], stats["missed_ticks"]), (3, 4))

    def test_a_failing_capture_does_not_stop_observing(self):
        with TempStore() as ts:
            obs = ContinuousObserver(ts.store, Sources(), buffer(), clock_ns=Clock())
            draft, batch = build_draft(Sources(), buffer(), f"{obs.session_id}:0", clock_ns=Clock(T0 + 10 ** 12))
            ts.store.ingest(draft)
            obs.tick()                            # same key, different content -> refused, counted
            obs.tick()
            self.assertEqual((obs.stats["observations"], obs.stats["failures"]), (1, 1))


if __name__ == "__main__":
    unittest.main()

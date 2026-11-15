"""Trace V2 capture pipeline: independent readers, privacy-safe events, continuous capture."""

import threading
import unittest

from trace_v2.observations import InteractionEvent, ScreenshotCapture
from trace_v2.observations.events import EventBuffer, InvalidEvent, validate
from trace_v2.observations.pipeline import ContinuousObserver, ReaderUnavailable, build_draft, observe_once
from v2_observation_helpers import PNG, SEC, T0, TempStore, click, context


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += 1000
        return self.t


class Reader:
    def __init__(self, version, result=None, error=None):
        self.version, self.result, self.error, self.calls = version, result, error, 0

    def read(self, *args):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Sources:
    capture_version = "fake-capture/1"

    def __init__(self, ocr_error=None, ax_error=None, shot_error=None, context_error=None):
        self.ocr = Reader("ocr/1", ({"lines": [{"text": "a", "box": [0, 0, 1, 1]}]}, "a"), ocr_error)
        self.accessibility = Reader("ax/1", ({"role": "AXWindow", "children": []}, ""), ax_error)
        self.shot_error, self.context_error = shot_error, context_error

    def context(self):
        if self.context_error:
            raise self.context_error
        return context()

    def screenshot(self):
        if self.shot_error:
            raise self.shot_error
        return ScreenshotCapture(PNG, "png", 32, 32, T0)


class ReaderIsolationTest(unittest.TestCase):
    def status(self, ts, sources, key):
        oid = observe_once(ts.store, sources, EventBuffer(), key, Clock()).observation_id
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


class EventPrivacyTest(unittest.TestCase):
    def test_keypress_never_carries_characters_or_keycodes(self):
        for payload in ({"phase": "down", "key_class": "character", "characters": "p"},
                        {"phase": "down", "key_class": "character", "keycode": 35},
                        {"phase": "down", "key_class": "p"}):
            with self.subTest(payload), self.assertRaises(InvalidEvent):
                validate(InteractionEvent("keypress", T0, payload))

    def test_buffer_rejects_and_counts_without_storing(self):
        buf = EventBuffer()
        self.assertTrue(buf.add(click(T0)))
        self.assertFalse(buf.add(InteractionEvent("keypress", T0, {"phase": "down", "key_class": "character",
                                                                    "characters": "s"})))
        self.assertFalse(buf.add(InteractionEvent("keypress", T0, {"phase": "down", "key_class": "character"}),
                                 secure_input=True))
        self.assertFalse(buf.add(InteractionEvent("drag", T0, {})))
        events, window, stats = buf.drain(T0 + 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(stats, {"received": 4, "rejected": 2, "suppressed_secure_input": 1, "dropped_overflow": 0,
                                 "stored": 1})

    def test_drain_splits_events_at_the_capture_time(self):
        buf = EventBuffer(start_ns=T0 - SEC)
        for t in (T0 - 2, T0, T0 + 5):
            buf.add(click(t))
        events, window, _ = buf.drain(T0)
        self.assertEqual(([e.occurred_at_ns for e in events], window), ([T0 - 2, T0], (T0 - SEC, T0)))
        events, window, _ = buf.drain(T0 + SEC)
        self.assertEqual(([e.occurred_at_ns for e in events], window), ([T0 + 5], (T0, T0 + SEC)))

    def test_overflow_is_counted(self):
        buf = EventBuffer(max_events=2)
        for t in range(3):
            buf.add(click(T0 + t))
        self.assertEqual(buf.drain(T0 + 10)[2]["dropped_overflow"], 1)


class ContinuousTest(unittest.TestCase):
    def test_captures_on_schedule_with_nothing_happening(self):
        with TempStore() as ts:
            fake = {"t": 0.0}

            class Stop(threading.Event):
                def wait(self, timeout=None):
                    fake["t"] += timeout or 0
                    return False

            obs = ContinuousObserver(ts.store, Sources(), EventBuffer(), interval_s=2.0, clock_ns=Clock(),
                                     monotonic=lambda: fake["t"])
            stats = obs.run(stop=Stop(), max_ticks=5)
            self.assertEqual(stats["observations"], 5)
            self.assertEqual(len(ts.store.list()), 5)
            self.assertEqual(fake["t"], 8.0)     # 5 captures, 4 waits of 2 s, no events needed

    def test_a_failing_capture_does_not_stop_observing(self):
        with TempStore() as ts:
            obs = ContinuousObserver(ts.store, Sources(), EventBuffer(), clock_ns=Clock())
            obs.seq = 0
            ts.store.ingest(build_draft(Sources(), EventBuffer(), f"{obs.session_id}:0", Clock(T0 + 10 ** 12)))
            obs.tick()                            # same key, different content -> refused, counted
            obs.tick()
            self.assertEqual(obs.stats, {"observations": 1, "failures": 1, "missed_ticks": 0})


if __name__ == "__main__":
    unittest.main()

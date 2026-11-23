"""Change-triggered capture: a tick is a full capture only when something happened (or the heartbeat is due)."""

import io
import unittest

from PIL import Image

from trace_v2.observations import InteractionEvent
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import ContinuousObserver, build_draft, observe_once
from trace_v2.observations.trigger import CaptureTrigger, get_params, normalise, set_params
from v2_observation_helpers import SEC, T0, TempStore, click, context
from test_v2_observation_pipeline import Sources


def png(shade=100, box=None, size=(96, 60)):
    image = Image.new("L", size, shade)
    if box:
        image.paste(255, box)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class ProbeSources(Sources):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.frame, self.probe_error, self.probes = png(), None, 0

    def probe(self):
        self.probes += 1
        if self.probe_error:
            raise self.probe_error
        return self.frame


def move(t):
    return InteractionEvent("cursor_move", t, {"x": 1.0, "y": 2.0})


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, params=None, sources=None):
        self.clock, self.sources = Clock(), sources or ProbeSources()
        self.buffer = EventBuffer(start_ns=T0 - 10 * SEC)
        self.trigger = CaptureTrigger(params)
        self.n = 0

    def tick(self, advance=3):
        self.clock.t += advance * SEC
        self.n += 1
        draft, batch = build_draft(self.sources, self.buffer, f"k:{self.n}", clock_ns=self.clock, trigger=self.trigger)
        if draft is not None:
            batch.commit()
        return draft


class Decide(unittest.TestCase):
    def test_first_tick_is_captured_and_a_quiet_second_tick_is_skipped(self):
        rig = Rig()
        self.assertIsNotNone(rig.tick())
        self.assertIsNone(rig.tick())
        self.assertEqual(rig.sources.screenshots, 1)
        self.assertEqual(rig.trigger.counts, {"first": 1, "skipped": 1})

    def test_a_context_change_is_captured(self):
        rig = Rig()
        rig.tick()
        rig.sources.ctx = context(app="com.example.browser")
        self.assertIsNotNone(rig.tick())
        self.assertEqual(rig.trigger.counts["context"], 1)

    def test_a_click_is_captured_but_cursor_movement_alone_is_not(self):
        rig = Rig()
        rig.tick()
        rig.buffer.add(move(rig.clock.t + SEC))
        self.assertIsNone(rig.tick())
        self.assertEqual(rig.buffer.pending(), 1, "skipped movement stays buffered for the next observation")
        rig.buffer.add(click(rig.clock.t + SEC))
        draft = rig.tick()
        self.assertIsNotNone(draft)
        self.assertEqual(sorted(e.type for e in draft.events), ["click", "cursor_move"])
        self.assertEqual(rig.buffer.pending(), 0)

    def test_a_screen_change_is_captured_and_noise_is_not(self):
        rig = Rig()
        rig.tick()
        rig.sources.frame = png(box=(0, 0, 1, 1))              # 1 of 5760 pixels: below the threshold
        self.assertIsNone(rig.tick())
        rig.sources.frame = png(box=(0, 0, 40, 30))
        self.assertIsNotNone(rig.tick())
        self.assertEqual(rig.trigger.counts["screen"], 1)

    def test_slow_drift_adds_up_because_the_comparison_is_with_the_last_capture(self):
        rig = Rig({"change_fraction": 0.05})
        rig.tick()
        rig.sources.frame = png(box=(0, 0, 10, 20))            # 3.5 %: under the threshold
        self.assertIsNone(rig.tick())
        rig.sources.frame = png(box=(0, 0, 20, 20))            # 6.9 % against the last capture, 3.5 % against the last probe
        self.assertIsNotNone(rig.tick())

    def test_heartbeat_then_slower_idle_heartbeat(self):
        rig = Rig({"heartbeat_s": 30, "idle_after_s": 100, "idle_heartbeat_s": 90})
        rig.tick()
        for _ in range(9):
            self.assertIsNone(rig.tick(3))
        self.assertIsNotNone(rig.tick(3))                      # 30 s since the last capture
        self.assertIsNotNone(rig.tick(30))                     # still active: 30 s heartbeat
        self.assertIsNotNone(rig.tick(30))
        self.assertEqual(rig.trigger.counts["heartbeat"], 3)
        self.assertIsNone(rig.tick(30))                        # 120 s without activity: idle, so the heartbeat is 90 s
        self.assertIsNotNone(rig.tick(60))
        self.assertEqual(rig.trigger.counts["idle_heartbeat"], 1)

    def test_a_heartbeat_reuses_the_accessibility_read_and_a_real_change_reads_again(self):
        rig = Rig({"heartbeat_s": 30})
        reads = []
        real = rig.sources.accessibility.read
        rig.sources.accessibility.read = lambda *a: (reads.append(1), real(*a))[1]
        rig.tick()
        for _ in range(9):
            rig.tick(3)
        beat = rig.tick(3)
        self.assertEqual((beat.capture_reason, len(reads)), ("heartbeat", 1))
        self.assertEqual(beat.accessibility.status, "succeeded")
        rig.sources.frame = png(shade=200)
        self.assertEqual(rig.tick(3).capture_reason, "screen")
        self.assertEqual(len(reads), 2)

    def test_the_draft_carries_the_reason_the_trigger_gave(self):
        rig = Rig({"heartbeat_s": 30})
        self.assertEqual(rig.tick().capture_reason, "first")
        for _ in range(9):
            rig.tick(3)
        self.assertEqual(rig.tick(3).capture_reason, "heartbeat")

    def test_without_a_probe_or_with_a_failing_probe_every_tick_is_captured(self):
        plain = Rig(sources=Sources())
        self.assertIsNotNone(plain.tick())
        self.assertIsNotNone(plain.tick())
        broken = Rig()
        broken.tick()
        broken.sources.probe_error = RuntimeError("no screen recording")
        self.assertIsNotNone(broken.tick())
        self.assertEqual(broken.trigger.counts["no_probe"], 1)

    def test_disabled_captures_every_tick(self):
        rig = Rig({"enabled": False})
        self.assertIsNotNone(rig.tick())
        self.assertIsNotNone(rig.tick())

    def test_a_withheld_tick_is_never_skipped_and_never_probed(self):
        rig = Rig(sources=ProbeSources(secure=True))
        self.assertIsNotNone(rig.tick())
        self.assertIsNotNone(rig.tick())
        self.assertEqual(rig.sources.probes, 0)
        self.assertEqual(rig.sources.screenshots, 0)


class Settings(unittest.TestCase):
    def test_validation_and_stored_round_trip(self):
        with self.assertRaises(ValueError):
            normalise({"nope": 1})
        with self.assertRaises(ValueError):
            normalise({"heartbeat_s": 60, "idle_heartbeat_s": 30})
        with TempStore() as ts:
            self.assertTrue(get_params(ts.store)["enabled"])
            set_params(ts.store, {"heartbeat_s": 20})
            self.assertEqual(get_params(ts.store)["heartbeat_s"], 20)


class Observer(unittest.TestCase):
    def test_skipped_ticks_are_counted_and_the_shutdown_capture_is_forced(self):
        with TempStore() as ts:
            clock = Clock()
            sources = ProbeSources()
            observer = ContinuousObserver(ts.store, sources, EventBuffer(start_ns=T0 - 10 * SEC), interval_s=3,
                                          clock_ns=clock, trigger=CaptureTrigger(), final_always=True)
            for _ in range(4):
                clock.t += 3 * SEC
                observer.tick()
            self.assertEqual((observer.stats["observations"], observer.stats["skipped_ticks"]), (1, 3))
            clock.t += SEC
            observer.tick(force=True)
            self.assertEqual(observer.stats["observations"], 2)

    def test_observe_once_returns_none_for_a_skipped_tick(self):
        with TempStore() as ts:
            sources, buffer, trigger, clock = ProbeSources(), EventBuffer(start_ns=T0 - 10 * SEC), CaptureTrigger(), Clock()
            clock.t += SEC
            self.assertIsNotNone(observe_once(ts.store, sources, buffer, "a:0", clock_ns=clock, trigger=trigger))
            clock.t += SEC
            self.assertIsNone(observe_once(ts.store, sources, buffer, "a:1", clock_ns=clock, trigger=trigger))


if __name__ == "__main__":
    unittest.main()

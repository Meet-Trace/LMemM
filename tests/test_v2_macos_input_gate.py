"""The tap's per-event gate with real CGEvent objects handed straight to the handler (nothing is posted, so no permission
is needed and the user's own input is never involved). macOS only."""

import sys
import time
import unittest

from trace_v2.inputs import InputGate
from trace_v2.observations.events import EventBuffer
from v2_observation_helpers import SEC

try:
    import Quartz
    from trace_v2.macos import InputTap
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False


def stamp(event):
    Quartz.CGEventSetTimestamp(event, time.clock_gettime_ns(time.CLOCK_UPTIME_RAW))
    return event


def all_event_kinds():
    here = Quartz.CGPointMake(100, 100)
    return [(Quartz.kCGEventLeftMouseDown, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, here, 0)),
            (Quartz.kCGEventLeftMouseUp, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, here, 0)),
            (Quartz.kCGEventMouseMoved, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, here, 0)),
            (Quartz.kCGEventScrollWheel, Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 3, 0)),
            (Quartz.kCGEventKeyDown, Quartz.CGEventCreateKeyboardEvent(None, 0x5A, True)),
            (Quartz.kCGEventKeyUp, Quartz.CGEventCreateKeyboardEvent(None, 0x5A, False))]


@unittest.skipUnless(ON_MAC, "macOS only")
class PerEventGate(unittest.TestCase):
    def setUp(self):
        self.buffer = EventBuffer(start_ns=time.time_ns() - SEC)

    def feed(self, tap):
        for kind, event in all_event_kinds():
            tap._handle(kind, stamp(event))
        tap.sampler.tick(time.time_ns() + SEC)
        return self.buffer.take(time.time_ns() + SEC)

    def test_while_blocked_keys_clicks_scrolls_and_cursor_positions_are_all_dropped_and_counted(self):
        gate = InputGate({"com.example.vault"}, start_blocked=False)
        tap = InputTap(self.buffer, secure_input=lambda: False, gate=gate)
        gate.app_activated("com.example.vault")
        batch = self.feed(tap)
        self.assertEqual(batch.events, ())
        self.assertEqual(batch.stats["dropped_gated"], 6)                  # shows up on the next Observation's event_stats
        self.assertEqual(tap.stats()["dropped_gated"], 6)
        self.assertEqual(tap.stats()["gate_blocked"], "app_activated")
        self.assertEqual(tap.stats()["stored"], {})
        self.assertEqual(tap.sampler.raw_moves, 0)                         # the position never reached the sampler

    def test_switching_back_to_a_normal_app_lets_every_kind_through_again(self):
        gate = InputGate({"com.example.vault"}, start_blocked=False)
        tap = InputTap(self.buffer, secure_input=lambda: False, gate=gate)
        gate.app_activated("com.example.vault")
        gate.app_activated("com.example.editor")
        batch = self.feed(tap)
        self.assertEqual({e.type for e in batch.events}, {"click", "cursor_move", "scroll", "keypress"})
        self.assertEqual(batch.stats["dropped_gated"], 0)

    def test_a_locked_screen_drops_everything_and_a_tap_without_a_gate_starts_open(self):
        gate = InputGate(start_blocked=False)
        gate.screen_locked(True)
        locked = self.feed(InputTap(self.buffer, secure_input=lambda: False, gate=gate))
        self.assertEqual(locked.events, ())
        locked.commit()
        self.assertTrue(len(self.feed(InputTap(self.buffer, secure_input=lambda: False)).events) > 0)

    def test_keyboard_blocked_still_works_as_the_old_switch(self):
        tap = InputTap(self.buffer, secure_input=lambda: False)
        tap.keyboard_blocked = True
        self.assertTrue(tap.keyboard_blocked)
        self.assertEqual(self.feed(tap).events, ())
        tap.keyboard_blocked = False
        self.assertFalse(tap.keyboard_blocked)

    def test_a_stored_keypress_has_no_keycode_or_character_field(self):
        keys = [e for e in self.feed(InputTap(self.buffer, secure_input=lambda: False)).events if e.type == "keypress"]
        self.assertEqual(len(keys), 2)
        for e in keys:
            self.assertLessEqual(set(e.payload), {"phase", "key_class", "modifiers", "is_repeat"})


if __name__ == "__main__":
    unittest.main()

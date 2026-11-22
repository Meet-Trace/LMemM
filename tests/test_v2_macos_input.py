"""Real CGEventTap with synthetic events posted by this test. macOS only.

Skipped, with the reason, unless the process running the tests has Input Monitoring (to listen) and permission to post
events; nothing is ever requested or prompted. The posted events are deliberately harmless: the cursor is moved a few
pixels and put back, the click is button 9 ("other"), the scroll has zero deltas, and the key is F20. Keyboard events are
not delivered to taps while secure keyboard entry is on (the terminal or app running the tests may enable it); that
case is reported by the keyboard test as a skip and the suppression itself is asserted instead."""

import json
import sys
import time
import unittest

from trace_v2.observations.events import EventBuffer
from v2_observation_helpers import SEC, TempStore, draft

try:
    import Quartz
    from trace_v2.macos import InputTap, session
    ON_MAC = sys.platform == "darwin"
    LISTEN, POST = bool(Quartz.CGPreflightListenEventAccess()), bool(Quartz.CGPreflightPostEventAccess())
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = LISTEN = POST = False
NEED = ("Input Monitoring (and permission to post events) is not granted to the process running the tests "
        "(System Settings > Privacy & Security > Input Monitoring)")


def post(event):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def wiggle(count=30, pause=0.01):
    here = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for i in range(count):
        post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
                                            Quartz.CGPointMake(here.x + (i % 3), here.y), 0))
        time.sleep(pause)
    post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, here, 0))
    return here


@unittest.skipUnless(ON_MAC, "macOS only")
class Unavailable(unittest.TestCase):
    def test_without_input_monitoring_the_tap_is_unavailable_and_does_not_prompt(self):
        originals = Quartz.CGPreflightListenEventAccess, Quartz.CGRequestListenEventAccess
        Quartz.CGPreflightListenEventAccess = lambda: False
        Quartz.CGRequestListenEventAccess = None                        # would raise TypeError if it were ever called
        try:
            tap = InputTap(EventBuffer(start_ns=0))
            self.assertEqual(tap.start(), "unavailable")
            self.assertEqual(tap.reason, "input_monitoring_not_granted")
            tap.stop()
        finally:
            Quartz.CGPreflightListenEventAccess, Quartz.CGRequestListenEventAccess = originals


def key_event(keycode, down, flags=0):
    event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    Quartz.CGEventSetFlags(event, flags)
    Quartz.CGEventSetTimestamp(event, time.clock_gettime_ns(time.CLOCK_UPTIME_RAW))
    return event


@unittest.skipUnless(ON_MAC, "macOS only")
class KeyFieldExtraction(unittest.TestCase):
    """Real CGEvent objects handed straight to the tap's handler (not posted, so no permission and no secure-input
    interference): proves the CGEvent -> allow-listed event path on this Mac, including what is dropped."""

    def tap(self, secure):
        self.buffer = EventBuffer(start_ns=time.time_ns() - SEC)
        return InputTap(self.buffer, secure_input=lambda: secure)

    def stored(self):
        return list(self.buffer.take(time.time_ns() + SEC).events)

    def test_a_character_key_with_modifiers_becomes_class_and_modifiers_only(self):
        tap = self.tap(secure=False)
        flags = Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift
        tap._handle(Quartz.kCGEventKeyDown, key_event(0x00, True, flags))     # the "a" key
        tap._handle(Quartz.kCGEventKeyUp, key_event(0x00, False))
        down, up = self.stored()
        self.assertEqual(down.payload, {"phase": "down", "key_class": "character", "modifiers": ["command", "shift"],
                                        "is_repeat": False})
        self.assertEqual(up.payload, {"phase": "up", "key_class": "character", "modifiers": []})
        self.assertAlmostEqual(down.occurred_at_ns / SEC, time.time(), delta=2)
        self.assertLessEqual(set(down.payload) | set(up.payload), {"phase", "key_class", "modifiers", "is_repeat"})

    def test_secure_input_suppresses_and_counts_keypresses(self):
        tap = self.tap(secure=True)
        tap._handle(Quartz.kCGEventKeyDown, key_event(0x00, True))
        batch = self.buffer.take(time.time_ns() + SEC)
        self.assertEqual((batch.events, batch.stats["suppressed_secure_input"]), ((), 1))

    def test_gated_app_drops_keyboard_events(self):
        tap = self.tap(secure=False)
        tap.keyboard_blocked = True
        tap._handle(Quartz.kCGEventKeyDown, key_event(0x00, True))
        self.assertEqual((self.stored(), tap.stats()["dropped_gated"]), ([], 1))


@unittest.skipUnless(ON_MAC, "macOS only")
@unittest.skipUnless(LISTEN and POST, NEED)
class RealTap(unittest.TestCase):
    def setUp(self):
        self.now = time.time_ns()
        self.buffer = EventBuffer(start_ns=self.now - SEC)
        self.tap = InputTap(self.buffer, cursor_sample_ms=100)
        self.assertEqual(self.tap.start(), "running", self.tap.reason)
        time.sleep(0.2)
        self.addCleanup(self.tap.stop)

    def events(self):
        time.sleep(0.5)
        self.tap.stop()
        return list(self.buffer.take(time.time_ns() + SEC).events)

    def test_moves_click_and_scroll_arrive_sampled_with_wall_clock_timestamps(self):
        here = wiggle()
        post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventOtherMouseDown, here, 9))
        post(Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventOtherMouseUp, here, 9))
        post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, 0, 0))
        events = self.events()
        after = time.time_ns()
        by_type = {}
        for e in events:
            by_type.setdefault(e.type, []).append(e)
            self.assertLess(abs(e.occurred_at_ns - after), 5 * SEC)           # sane wall time (the run itself ~1 s)
            self.assertGreater(e.occurred_at_ns, self.now - SEC)
        self.assertEqual([c.payload["phase"] for c in by_type["click"]], ["down", "up"])
        self.assertEqual(by_type["click"][0].payload["button"], "other")
        self.assertEqual(len(by_type["scroll"]), 1)
        moves = by_type["cursor_move"]
        self.assertGreaterEqual(len(moves), 2)
        self.assertLess(len(moves), 31)                                         # 31 raw moves were posted
        self.assertTrue(all(b.occurred_at_ns - a.occurred_at_ns >= 100_000_000 for a, b in zip(moves, moves[1:])))
        self.assertGreaterEqual(sum(m.payload["coalesced"] for m in moves), 31)
        self.assertTrue(moves[-1].payload.get("final"))
        stats = self.tap.stats()
        self.assertEqual((stats["raw"]["mouse_moved"], stats["raw"]["scroll"]), (31, 1))
        self.assertEqual(stats["callback_errors"], 0)

    def test_keys_store_class_and_modifiers_only_never_a_keycode_or_character(self):
        if session.secure_input():
            self.skipTest("secure keyboard entry is on in this session: macOS does not deliver key events to taps")
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        for down in (True, False):
            post(Quartz.CGEventCreateKeyboardEvent(source, 0x5A, down))        # F20: no character, no default action
        events = [e for e in self.events() if e.type == "keypress"]
        self.assertEqual([(e.payload["phase"], e.payload["key_class"]) for e in events], [("down", "function"),
                                                                                           ("up", "function")])
        self.assert_no_keycodes_or_characters(events)

    def test_stored_rows_hold_no_keycodes_or_characters(self):
        wiggle(5)
        events = self.events()
        self.assert_no_keycodes_or_characters(events)

    def assert_no_keycodes_or_characters(self, events):
        with TempStore() as t:
            result = t.store.ingest(draft(events=events, t=max([e.occurred_at_ns for e in events] + [self.now]) + 1,
                                          key="tap:0"))
            rows = json.dumps(t.store.events(result.observation_id))
        for word in ("keycode", "key_code", "unicode", "\"text\"", "\"characters\""):
            self.assertNotIn(word, rows)

    def test_keyboard_blocked_drops_key_events_and_counts_them(self):
        if session.secure_input():
            self.skipTest("secure keyboard entry is on in this session: key events are not delivered to taps")
        self.tap.keyboard_blocked = True
        for down in (True, False):
            post(Quartz.CGEventCreateKeyboardEvent(None, 0x5A, down))
        events = self.events()
        self.assertEqual([e for e in events if e.type == "keypress"], [])


if __name__ == "__main__":
    unittest.main()

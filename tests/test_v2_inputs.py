"""Platform-independent input logic: cursor sampling, mach-to-wall time, key classes, raw-event decoding, schema."""

import json
import unittest

from trace_v2.inputs import CursorSampler, MachClock, decode, key_class, modifiers_from_flags
from trace_v2.inputs.keys import FLAG_MASKS, modifier_phase
from trace_v2.observations import InteractionEvent, settings
from trace_v2.observations.events import EventBuffer, InvalidEvent, validate
from v2_observation_helpers import SEC, T0, TempStore

MS = 1_000_000


def sampler(interval_ms=150):
    out = []
    return CursorSampler(lambda t, payload: out.append((t, payload)), interval_ms), out


class Sampling(unittest.TestCase):
    def test_first_move_is_emitted_immediately(self):
        s, out = sampler()
        s.move(1000 * MS, 5, 6)
        self.assertEqual(out, [(1000 * MS, {"x": 5, "y": 6, "coalesced": 1})])

    def test_continuous_movement_is_at_most_one_sample_per_interval(self):
        s, out = sampler(150)
        for i in range(0, 1001, 8):                            # ~125 Hz for one second
            s.move(T0 + i * MS, i, i)
        times = [t for t, _ in out]
        self.assertTrue(all(b - a >= 150 * MS for a, b in zip(times, times[1:])))
        self.assertLessEqual(len(out), 7)
        self.assertEqual(sum(p["coalesced"] for _, p in out) + s._coalesced, s.raw_moves)   # every raw move is accounted for

    def test_coalesced_counts_the_raw_moves_a_sample_stands_for(self):
        s, out = sampler(100)
        for i in range(10):
            s.move(T0 + i * 20 * MS, i, 0)                    # 0, 20, ... 180 ms
        self.assertEqual([p["coalesced"] for _, p in out], [1, 5, 4][:len(out)])
        self.assertEqual(out[0][1]["x"], 0)
        self.assertEqual(out[1][1]["x"], 5)                   # emitted at t=100 ms with the position at that time

    def test_final_position_is_emitted_once_the_cursor_stops(self):
        s, out = sampler(150)
        s.move(T0, 0, 0)
        s.move(T0 + 40 * MS, 10, 10)
        s.move(T0 + 70 * MS, 20, 20)
        s.tick(T0 + 100 * MS)                                  # still within an interval of the last move: nothing
        self.assertEqual(len(out), 1)
        s.tick(T0 + 70 * MS + 150 * MS)
        self.assertEqual(len(out), 2)
        t, payload = out[1]
        self.assertEqual((payload["x"], payload["y"], payload["final"], payload["coalesced"]), (20, 20, True, 2))
        self.assertGreaterEqual(t - out[0][0], 150 * MS)       # never closer than the interval
        self.assertLessEqual(t, T0 + 220 * MS)                 # and never in the future of the tick that emitted it
        s.tick(T0 + 5 * SEC)
        self.assertEqual(len(out), 2)                          # a final is emitted once

    def test_a_stationary_cursor_emits_nothing(self):
        s, out = sampler()
        s.tick(T0)
        s.tick(T0 + SEC)
        self.assertEqual(out, [])

    def test_a_single_move_after_a_pause_has_no_trailing_duplicate(self):
        s, out = sampler()
        s.move(T0, 1, 1)
        s.tick(T0 + SEC)
        self.assertEqual(len(out), 1)

    def test_movement_resumes_after_a_final(self):
        s, out = sampler(100)
        s.move(T0, 0, 0)
        s.move(T0 + 30 * MS, 1, 1)
        s.tick(T0 + 200 * MS)
        s.move(T0 + 300 * MS, 2, 2)
        self.assertEqual([p.get("final", False) for _, p in out], [False, True, False])
        self.assertEqual(out[2][1]["coalesced"], 1)

    def test_interval_is_validated(self):
        for bad in (99, 201, 150.0, "150", True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                CursorSampler(lambda *a: None, bad)
        CursorSampler(lambda *a: None, 100)
        CursorSampler(lambda *a: None, 200)

    def test_samples_validate_and_stay_under_the_rate_limit_end_to_end(self):
        buffer = EventBuffer(start_ns=T0 - SEC)
        s = CursorSampler(lambda t, p: buffer.add(InteractionEvent("cursor_move", t, p)), 100)
        for i in range(0, 2001, 4):                            # 250 Hz for two seconds
            s.move(T0 + i * MS, i, i)
        s.tick(T0 + 5 * SEC)
        events = buffer.take(T0 + 10 * SEC).events
        self.assertTrue(all(b.occurred_at_ns - a.occurred_at_ns >= 100 * MS for a, b in zip(events, events[1:])))


SEC = SEC


class Clock(unittest.TestCase):
    def test_event_timestamps_are_uptime_ns_plus_the_offset(self):
        clock = MachClock(lambda: 24 * SEC, lambda: 1_700_000_000 * SEC)
        self.assertEqual(clock.to_wall_ns(24 * SEC), 1_700_000_000 * SEC)
        self.assertEqual(clock.to_wall_ns(26 * SEC + 5), 1_700_000_002 * SEC + 5)

    def test_offset_is_captured_once_so_conversion_is_stable(self):
        now = {"mach": 1000, "wall": 5 * SEC}
        clock = MachClock(lambda: now["mach"], lambda: now["wall"])
        now["wall"] += 9 * SEC
        self.assertEqual(clock.to_wall_ns(1000), 5 * SEC)

    def test_resync_after_sleep(self):
        now = {"mach": 100 * SEC, "wall": 2_000 * SEC}
        clock = MachClock(lambda: now["mach"], lambda: now["wall"])
        now["mach"] += 10 * SEC
        now["wall"] += 10 * SEC
        self.assertFalse(clock.check())                        # both advanced equally: no drift
        now["wall"] += 3600 * SEC                              # slept for an hour: mach stood still
        self.assertTrue(clock.check())
        self.assertEqual(clock.resyncs, 1)
        self.assertEqual(clock.to_wall_ns(now["mach"]), now["wall"])
        self.assertFalse(clock.check())

    def test_small_drift_is_tolerated(self):
        now = {"mach": 0, "wall": 10 * SEC}
        clock = MachClock(lambda: now["mach"], lambda: now["wall"])
        now["wall"] += SEC // 2
        self.assertFalse(clock.check())


class Keys(unittest.TestCase):
    TABLE = {0x00: "character", 0x2F: "character", 0x32: "character", 0x24: "return", 0x4C: "return", 0x30: "tab",
             0x31: "whitespace", 0x33: "delete", 0x75: "delete", 0x35: "escape", 0x37: "modifier", 0x3A: "modifier",
             0x3F: "modifier", 0x7B: "navigation", 0x7E: "navigation", 0x73: "navigation", 0x7A: "function",
             0x6F: "function", 0x5A: "function", 0x52: "character", 0x41: "character", 0x47: "other", 0x6E: "other",
             0x80: "other"}

    def test_classes(self):
        for code, expected in self.TABLE.items():
            with self.subTest(code=hex(code)):
                self.assertEqual(key_class(code), expected)

    def test_modifier_flags_in_a_stable_order(self):
        flags = FLAG_MASKS["shift"] | FLAG_MASKS["command"] | FLAG_MASKS["caps_lock"]
        self.assertEqual(modifiers_from_flags(flags), ["command", "shift", "caps_lock"])
        self.assertEqual(modifiers_from_flags(0), [])

    def test_implicit_fn_flag_is_dropped_for_arrows_and_function_keys_only(self):
        self.assertEqual(modifiers_from_flags(FLAG_MASKS["fn"], "navigation"), [])
        self.assertEqual(modifiers_from_flags(FLAG_MASKS["fn"], "function"), [])
        self.assertEqual(modifiers_from_flags(FLAG_MASKS["fn"], "character"), ["fn"])

    def test_modifier_phase_comes_from_whether_its_flag_is_set(self):
        self.assertEqual(modifier_phase(0x37, FLAG_MASKS["command"]), "down")
        self.assertEqual(modifier_phase(0x37, 0), "up")
        self.assertIsNone(modifier_phase(0x00, 0))


class Decoding(unittest.TestCase):
    def setUp(self):
        self.clock = MachClock(lambda: 0, lambda: T0)

    def decode(self, **raw):
        return decode({"ticks": 5 * SEC, "x": 1.5, "y": 2.5, **raw}, self.clock)

    def test_click(self):
        event = self.decode(type="mouse_down", button=1, click_count=2)
        validate(event)
        self.assertEqual((event.type, event.occurred_at_ns, event.payload),
                         ("click", T0 + 5 * SEC, {"x": 1.5, "y": 2.5, "button": "right", "phase": "down", "click_count": 2}))
        self.assertEqual(self.decode(type="mouse_up", button=7, click_count=0).payload["button"], "other")
        self.assertNotIn("click_count", self.decode(type="mouse_up", button=0, click_count=0).payload)

    def test_scroll_units_and_phases(self):
        trackpad = self.decode(type="scroll", dx=0, dy=-12, continuous=True, scroll_phase=2, momentum_phase=0)
        wheel = self.decode(type="scroll", dx=0, dy=3, continuous=False, scroll_phase=0, momentum_phase=0)
        momentum = self.decode(type="scroll", dx=0, dy=-1, continuous=True, scroll_phase=0, momentum_phase=2)
        for event in (trackpad, wheel, momentum):
            validate(event)
        self.assertEqual((trackpad.payload["unit"], trackpad.payload["phase"]), ("pixel", "changed"))
        self.assertEqual((wheel.payload["unit"], wheel.payload["phase"]), ("line", "none"))
        self.assertEqual(momentum.payload["phase"], "momentum")

    def test_keypress_never_carries_a_keycode_or_character(self):
        down = self.decode(type="key_down", keycode=0x00, flags=FLAG_MASKS["command"], autorepeat=True)
        up = self.decode(type="key_up", keycode=0x00, flags=0)
        validate(down)
        validate(up)
        self.assertEqual(down.payload, {"phase": "down", "key_class": "character", "modifiers": ["command"],
                                        "is_repeat": True})
        self.assertEqual(up.payload, {"phase": "up", "key_class": "character", "modifiers": []})
        self.assertNotIn("keycode", json.dumps([down.payload, up.payload]))

    def test_every_keycode_decodes_to_a_class_only(self):
        seen = set()
        for code in range(0, 128):
            event = self.decode(type="key_down", keycode=code, flags=0, autorepeat=False)
            validate(event)
            self.assertEqual(set(event.payload), {"phase", "key_class", "modifiers", "is_repeat"})
            seen.add(event.payload["key_class"])
        self.assertEqual(seen, {"character", "whitespace", "delete", "return", "tab", "escape", "navigation",
                                "function", "modifier", "other"})

    def test_modifier_key_changes(self):
        down = self.decode(type="flags_changed", keycode=0x38, flags=FLAG_MASKS["shift"])
        up = self.decode(type="flags_changed", keycode=0x38, flags=0)
        self.assertEqual((down.payload["phase"], down.payload["key_class"], down.payload["modifiers"]),
                         ("down", "modifier", ["shift"]))
        self.assertEqual((up.payload["phase"], up.payload["modifiers"]), ("up", []))
        self.assertIsNone(self.decode(type="flags_changed", keycode=0x00, flags=0))

    def test_unknown_type_is_ignored(self):
        self.assertIsNone(self.decode(type="mystery"))


class SchemaAndSetting(unittest.TestCase):
    def test_cursor_move_accepts_coalesced_and_final_only_in_range(self):
        validate(InteractionEvent("cursor_move", T0, {"x": 1, "y": 2, "coalesced": 40, "final": True}))
        for bad in ({"coalesced": 0}, {"coalesced": True}, {"coalesced": 1_000_001}, {"final": False}, {"final": 1},
                    {"keycode": 5}):
            with self.subTest(bad=bad), self.assertRaises(InvalidEvent):
                validate(InteractionEvent("cursor_move", T0, {"x": 1, "y": 2, **bad}))

    def test_cursor_sample_setting(self):
        with TempStore() as t:
            self.assertEqual(settings.get_cursor_sample_ms(t.store), 150)
            self.assertEqual(settings.set_cursor_sample_ms(t.store, 100), 100)
            self.assertEqual(settings.get_cursor_sample_ms(t.reopen()), 100)
            for bad in (99, 201, 150.0, True, "150", None):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    settings.set_cursor_sample_ms(t.store, bad)
            self.assertEqual(settings.get_cursor_sample_ms(t.store), 100)


if __name__ == "__main__":
    unittest.main()

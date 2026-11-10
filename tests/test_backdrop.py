import os
import unittest

import backdrop


class PlanTests(unittest.TestCase):
    def test_weights_sum_to_one_every_minute(self):
        for m in range(1440):
            w = backdrop.plan(m)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=3, msg=m)
            self.assertTrue(set(w) <= set(backdrop.KEYS))

    def test_the_schedule(self):
        for hour, key in ((7, "dawn"), (13, "day"), (18, "evening"), (22, "night"), (2, "night")):
            self.assertEqual(backdrop.plan(hour * 60), {key: 1.0}, hour)

    def test_boundary_is_halfway_and_neighbours_blend(self):
        w = backdrop.plan(10 * 60)                         # dawn -> day
        self.assertAlmostEqual(w["dawn"], 0.5, places=2)
        self.assertAlmostEqual(w["day"], 0.5, places=2)
        w = backdrop.plan(5 * 60)                          # night -> dawn, across the day's edge
        self.assertEqual(set(w), {"night", "dawn"})

    def test_no_jump_from_minute_to_minute(self):
        last = None
        for m in range(1441):
            w = backdrop.plan(m)
            if last is not None:
                for k in backdrop.KEYS:
                    self.assertLess(abs(w.get(k, 0) - last.get(k, 0)), 0.12, (m, k))
            last = w

    def test_a_pinned_choice_overrides_the_clock(self):
        self.assertEqual(backdrop.plan(13 * 60, "night"), {"night": 1.0})
        self.assertEqual(backdrop.plan(13 * 60, "nonsense"), {"day": 1.0})

    def test_choices(self):
        rows = backdrop.choices("evening")
        self.assertEqual(rows[0]["key"], "auto")
        self.assertEqual([r["key"] for r in rows if r["on"]], ["evening"])
        self.assertEqual([r["key"] for r in backdrop.choices("zzz") if r["on"]], ["auto"])

    def test_every_background_is_complete(self):
        for b in backdrop.BACKGROUNDS:
            self.assertEqual(len(b["base"]), 3)
            self.assertTrue(all(0 <= v <= 1 for v in b["base"]), b["key"])
            self.assertTrue(0 < b["grain"] <= 0.12, b["key"])
            for x, y, r, color, strength in b["lights"]:
                self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and 0 < r <= 1 and 0 < strength <= 1.0, b["key"])
                self.assertTrue(all(0 <= v <= 1 for v in color))
            self.assertLess(max(b["base"]), 0.2, "the ground stays graphite-dark")


class GrainTests(unittest.TestCase):
    def test_tile_is_a_valid_png_of_the_asked_size(self):
        import struct, zlib
        import grain
        png = grain.tile_png(0.05, size=32)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (32, 32))
        self.assertEqual(png[-12:-8], b"\x00\x00\x00\x00")             # IEND
        self.assertEqual(grain.tile_png(0.05, size=32), png)           # the same grain every time


if __name__ == "__main__":
    unittest.main()

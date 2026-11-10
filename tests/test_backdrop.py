import os
import unittest

import backdrop


class PlanTests(unittest.TestCase):
    def test_exactly_one_period_every_minute(self):
        for m in range(1440):
            w = backdrop.plan(m)
            self.assertEqual(list(w.values()), [1.0], m)
            self.assertTrue(set(w) <= set(backdrop.KEYS))

    def test_the_schedule(self):
        # morning 05:00-11:59, afternoon 12:00-16:59, evening 17:00-20:59, night 21:00-04:59
        for hhmm, key in (("04:59", "night"), ("05:00", "morning"), ("11:59", "morning"), ("12:00", "afternoon"),
                          ("16:59", "afternoon"), ("17:00", "evening"), ("20:59", "evening"), ("21:00", "night"),
                          ("00:00", "night"), ("02:30", "night")):
            h, m = map(int, hhmm.split(":"))
            self.assertEqual(backdrop.plan(h * 60 + m), {key: 1.0}, hhmm)

    def test_legacy_choices_still_mean_something(self):
        self.assertEqual(backdrop.plan(0, "dawn"), {"morning": 1.0})
        self.assertEqual(backdrop.plan(0, "day"), {"afternoon": 1.0})

    def test_a_pinned_choice_overrides_the_clock(self):
        self.assertEqual(backdrop.plan(13 * 60, "night"), {"night": 1.0})
        self.assertEqual(backdrop.plan(13 * 60, "nonsense"), {"afternoon": 1.0})

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
                self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and 0 < r <= 1 and 0 < strength <= 0.5, b["key"])
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

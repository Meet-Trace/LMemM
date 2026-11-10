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

    def test_every_asset_exists(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for k in backdrop.KEYS:
            self.assertTrue(os.path.exists(backdrop.path(k, root)), k)


if __name__ == "__main__":
    unittest.main()

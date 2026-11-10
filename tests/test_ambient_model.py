import os
import tempfile
import unittest

import ambient_model as m
import config


class CropTests(unittest.TestCase):
    def test_same_shape_shows_everything(self):
        self.assertEqual(m.crop(1600, 900, 3200, 1800), (0.0, 0.0, 1.0, 1.0))

    def test_never_stretches(self):
        for vw, vh in ((600, 800), (1200, 700), (2560, 1080), (900, 900), (400, 1400)):
            x, y, w, h = m.crop(vw, vh, 3200, 1800, (0.6, 0.3))
            self.assertAlmostEqual((w * 3200) / (h * 1800), vw / vh, places=6, msg=(vw, vh))   # the cropped picture has the window's shape
            self.assertTrue(0 <= x and x + w <= 1.0000001 and 0 <= y and y + h <= 1.0000001)
            self.assertTrue(w == 1.0 or h == 1.0)                                              # it fills the window, one axis whole

    def test_the_lit_region_stays_in_view(self):
        x, y, w, h = m.crop(600, 800, 3200, 1800, (0.62, 0.30))     # a tall window cuts the sides
        self.assertTrue(x <= 0.62 <= x + w)
        x, y, w, h = m.crop(2560, 800, 3200, 1800, (0.62, 0.30))    # a wide one cuts top and bottom
        self.assertTrue(y <= 0.30 <= y + h)

    def test_degenerate_sizes_are_safe(self):
        self.assertEqual(m.crop(0, 0, 100, 100), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(m.crop(100, 100, 0, 0), (0.0, 0.0, 1.0, 1.0))


class ArtTests(unittest.TestCase):
    def test_missing_until_all_four_are_in(self):
        with tempfile.TemporaryDirectory() as root:
            art = os.path.join(root, config.AMBIENT_ART_DIR)
            os.makedirs(art)
            self.assertEqual(m.missing(root), list(m.PERIODS))
            for p in m.PERIODS[:3]:
                open(os.path.join(art, p + ".webp"), "wb").close()
            self.assertEqual(m.missing(root), ["night"])
            open(os.path.join(art, "night.png"), "wb").close()          # another format of the same name counts
            self.assertEqual(m.missing(root), [])
            self.assertTrue(m.art_path("night", root).endswith("night.png"))

    def test_webp_wins_over_png(self):
        with tempfile.TemporaryDirectory() as root:
            art = os.path.join(root, config.AMBIENT_ART_DIR)
            os.makedirs(art)
            for ext in (".png", ".webp"):
                open(os.path.join(art, "morning" + ext), "wb").close()
            self.assertTrue(m.art_path("morning", root).endswith("morning.webp"))


class MotionDataTests(unittest.TestCase):
    def test_every_period_has_lights_and_a_focus(self):
        for p in m.PERIODS:
            self.assertIn(p, m.FOCUS)
            self.assertTrue(0 <= m.FOCUS[p][0] <= 1 and 0 <= m.FOCUS[p][1] <= 1)
            self.assertTrue(m.LIGHTS[p])
            for x, y, size, color, strength, dx, dy, pa, pb in m.LIGHTS[p]:
                self.assertTrue(0 <= x <= 1 and 0 <= y <= 1 and 0 < size <= 1)
                self.assertTrue(0 < strength <= 0.15, "lights stay faint: the artwork carries the light")
                self.assertTrue(abs(dx) <= 0.05 and abs(dy) <= 0.05, "the drift is small")
                self.assertTrue(0 <= pa <= 1 and 0 <= pb <= 1)
                self.assertTrue(color[2] >= color[0] and color[2] >= color[1], "blue-indigo-violet family")

    def test_timings_are_inside_the_asked_ranges(self):
        lo, hi = config.AMBIENT_MOVE_SECONDS
        self.assertTrue(45 <= lo <= hi <= 90)
        lo, hi = config.AMBIENT_PULSE_SECONDS
        self.assertTrue(30 <= lo <= hi <= 60)
        self.assertTrue(10 <= config.AMBIENT_FADE_SECONDS <= 20)
        for phase in (0, 0.3, 1):
            self.assertTrue(config.AMBIENT_MOVE_SECONDS[0] <= m.seconds_between(config.AMBIENT_MOVE_SECONDS, phase) <= config.AMBIENT_MOVE_SECONDS[1])

    def test_schedule_is_in_config_and_covers_the_day(self):
        self.assertEqual([k for _, k in config.AMBIENT_SCHEDULE], list(m.PERIODS))
        self.assertEqual([h for h, _ in config.AMBIENT_SCHEDULE], [5, 12, 17, 21])


if __name__ == "__main__":
    unittest.main()

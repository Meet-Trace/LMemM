"""The hard-coded colours keep their contrast (audit: the orange on the light pill was 2.1-2.8:1)."""
import unittest

import palette as p


class PaletteContrast(unittest.TestCase):
    def test_pill_marks_read_on_both_pills(self):            # marks: 3:1
        self.assertGreaterEqual(p.contrast(p.ACCENT_ON_DARK_PILL, p.PILL_DARK), 3.0)
        self.assertGreaterEqual(p.contrast(p.ACCENT_ON_LIGHT_PILL, p.PILL_LIGHT), 3.0)

    def test_pill_warning_reads_on_both_pills(self):         # it is text-like: 4.5:1
        self.assertGreaterEqual(p.contrast(p.RED_ON_DARK_PILL, p.PILL_DARK), 4.5)
        self.assertGreaterEqual(p.contrast(p.RED_ON_LIGHT_PILL, p.PILL_LIGHT), 4.5)

    def test_accent_buttons_and_words(self):
        self.assertGreaterEqual(p.contrast(p.ACCENT, (1, 1, 1)), 4.5)
        self.assertGreaterEqual(p.contrast(p.ACCENT_TEXT_ON_LIGHT, p.CARD_LIGHT), 4.5)
        self.assertGreaterEqual(p.contrast(p.ACCENT_TEXT_ON_DARK, p.CARD_DARK), 4.5)

    def test_orange_words_on_the_note_card(self):
        self.assertGreaterEqual(p.contrast(p.ACCENT_TEXT_ON_LIGHT, p.CARD_LIGHT), 4.5)
        self.assertGreaterEqual(p.contrast(p.ACCENT_TEXT_ON_DARK, p.CARD_DARK), 4.5)

    def test_ticks_and_status_words_on_the_light_card(self):
        white = (1, 1, 1)
        self.assertGreaterEqual(p.contrast(white, p.ACCENT_TEXT_ON_LIGHT), 3.0)       # the pill card's tick and badge
        self.assertGreaterEqual(p.contrast(white, p.ACCENT), 3.0)                     # the window's blue tick
        for words in (p.GREEN_TEXT_ON_LIGHT, p.ORANGE_STATUS_ON_LIGHT, p.RED_TEXT_ON_LIGHT):
            self.assertGreaterEqual(p.contrast(words, p.CARD_LIGHT), 4.5)

    def test_the_toast_has_an_edge_in_dark_mode(self):
        self.assertGreaterEqual(p.contrast((p.TOAST_DARK,) * 3, p.CARD_DARK), 1.2)

    def test_both_graphite_treatments_keep_text_readable(self):
        for name, t in p.THEMES.items():
            for ground in ("canvas", "sidebar", "surface", "inset"):
                self.assertGreaterEqual(p.contrast(t["ink"], t[ground]), 7.0, (name, ground))
                self.assertGreaterEqual(p.contrast(t["mute"], t[ground]), 4.5, (name, ground))
                self.assertGreaterEqual(p.contrast(p.ACCENT_TEXT_ON_DARK, t[ground]), 4.5, (name, ground))

    def test_graphite_is_neutral_not_brown_and_not_navy(self):
        for name, t in p.THEMES.items():
            r, g, b = t["canvas"]
            self.assertLessEqual(b - r, 0.045, name)                            # at most a whisper cool
            self.assertGreaterEqual(b, r, name)                                 # never warm-brown
            self.assertLess(r, 0.12, name)                                      # dark

    def test_surfaces_step_up_and_edges_show(self):
        for name, t in p.THEMES.items():
            lum = [p.luminance(t[k]) for k in ("sidebar", "canvas", "surface")]
            self.assertEqual(lum, sorted(lum), name)
            self.assertLess(p.luminance(t["inset"]), p.luminance(t["surface"]), name)
            self.assertGreaterEqual(p.contrast(t["edge"], t["surface"]), 1.15, name)
        self.assertGreater(p.contrast(p.THEMES["b"]["surface"], p.THEMES["b"]["canvas"]) + 0.0, 1.0)

    def test_use_switches_and_ignores_unknown_names(self):
        p.use("a")
        self.assertEqual(p.WARM["canvas"], p.THEMES["a"]["canvas"])
        p.use("nope")
        self.assertEqual(p.WARM["canvas"], p.THEMES["a"]["canvas"])
        p.use("b")

    def test_contrast_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(p.contrast((0, 0, 0), (1, 1, 1)), 21.0, places=1)
        self.assertEqual(p.contrast(p.ACCENT, (1, 1, 1)), p.contrast((1, 1, 1), p.ACCENT))


if __name__ == "__main__":
    unittest.main()

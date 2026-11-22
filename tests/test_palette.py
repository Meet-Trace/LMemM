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

    def test_every_treatment_has_the_same_keys(self):
        self.assertEqual(set(p.THEMES), {"a", "b", "ivory"})
        keys = set(p.THEMES["a"])
        for name, t in p.THEMES.items():
            self.assertEqual(set(t), keys, name)
        for key in ("soft", "accent", "accent_text", "error", "success", "success_text"):
            self.assertIn(key, keys)

    def test_ivory_is_the_websites_palette(self):            # trace-landing/hero.css: --canvas, --ink, --muted, --soft, --blue
        def hexed(rgb):
            return tuple(round(c * 255) for c in rgb)
        ivory = p.THEMES["ivory"]
        self.assertEqual(hexed(ivory["canvas"]), (0xf4, 0xed, 0xdf))
        self.assertEqual(hexed(ivory["ink"]), (0x19, 0x1a, 0x20))
        self.assertEqual(hexed(ivory["mute"]), (0x4a, 0x4d, 0x58))
        for got, want in zip(hexed(p.ACCENT), (0x28, 0x56, 0xd9)):
            self.assertLessEqual(abs(got - want), 1)
        for got, want in zip(hexed(p.ACCENT_TEXT_ON_LIGHT), (0x1f, 0x43, 0xb0)):
            self.assertLessEqual(abs(got - want), 1)
        # --soft is #6b6e79 on the site (4.4:1 on the canvas). The app's metadata text is a little deeper so that it reaches 4.5:1.
        self.assertGreaterEqual(p.contrast(ivory["soft"], ivory["canvas"]), 4.5)

    def test_every_treatment_keeps_text_readable(self):
        for name, t in p.THEMES.items():
            for ground in ("canvas", "sidebar", "surface", "inset"):
                self.assertGreaterEqual(p.contrast(t["ink"], t[ground]), 7.0, (name, ground))
                self.assertGreaterEqual(p.contrast(t["mute"], t[ground]), 4.5, (name, ground))
                self.assertGreaterEqual(p.contrast(t["soft"], t[ground]), 4.5, (name, ground))
                for words in ("accent_text", "error", "success_text"):
                    self.assertGreaterEqual(p.contrast(t[words], t[ground]), 4.5, (name, ground, words))
            self.assertGreaterEqual(p.contrast((1, 1, 1), t["accent"]), 4.5, name)             # white words on the accent fill

    def test_graphite_is_neutral_not_brown_and_not_navy(self):
        for name in ("a", "b"):
            r, g, b = p.THEMES[name]["canvas"]
            self.assertLessEqual(b - r, 0.045, name)                            # at most a whisper cool
            self.assertGreaterEqual(b, r, name)                                 # never warm-brown
            self.assertLess(r, 0.12, name)                                      # dark

    def test_ivory_is_a_warm_light_ground(self):
        r, g, b = p.THEMES["ivory"]["canvas"]
        self.assertGreaterEqual(r, g)
        self.assertGreaterEqual(g, b)
        self.assertGreater(r, 0.9)

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

    def test_use_ivory_and_the_appearance_picks_the_treatment(self):
        p.use("ivory")
        self.assertEqual(p.WARM["canvas"], p.THEMES["ivory"]["canvas"])
        self.assertTrue(p.is_light())
        self.assertEqual(p.theme_for(False, "b"), "ivory")
        self.assertEqual(p.theme_for(True, "a"), "a")
        p.use("b")
        self.assertFalse(p.is_light())

    def test_contrast_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(p.contrast((0, 0, 0), (1, 1, 1)), 21.0, places=1)
        self.assertEqual(p.contrast(p.ACCENT, (1, 1, 1)), p.contrast((1, 1, 1), p.ACCENT))


if __name__ == "__main__":
    unittest.main()

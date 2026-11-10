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

    def test_the_light_surfaces_keep_text_readable(self):
        ink, mute, accent = p.WARM["ink"], p.WARM["mute"], p.ACCENT_TEXT_ON_LIGHT
        for ground in ("canvas", "sidebar", "surface", "inset"):
            self.assertGreaterEqual(p.contrast(ink, p.WARM[ground]), 7.0, ground)
            self.assertGreaterEqual(p.contrast(mute, p.WARM[ground]), 4.5, ground)
        for ground in ("canvas", "surface", "inset"):                           # blue words are set on these
            self.assertGreaterEqual(p.contrast(accent, p.WARM[ground]), 4.5, ground)

    def test_the_page_is_warm_light_and_not_brown(self):
        r, g, b = p.WARM["canvas"]
        self.assertGreater(r, 0.93)                                             # light
        self.assertLess(r - b, 0.04)                                            # a touch warm, nowhere near beige
        self.assertGreater(p.luminance(p.WARM["surface"]), p.luminance(p.WARM["canvas"]))
        self.assertGreater(p.luminance(p.WARM["canvas"]), p.luminance(p.WARM["sidebar"]))
        self.assertGreaterEqual(p.contrast(p.WARM["surface"], p.WARM["edge"]), 1.1)       # the hairline can be seen

    def test_contrast_is_symmetric_and_bounded(self):
        self.assertAlmostEqual(p.contrast((0, 0, 0), (1, 1, 1)), 21.0, places=1)
        self.assertEqual(p.contrast(p.ACCENT, (1, 1, 1)), p.contrast((1, 1, 1), p.ACCENT))


if __name__ == "__main__":
    unittest.main()

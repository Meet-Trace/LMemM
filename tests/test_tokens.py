"""The non-colour design tokens: spacing, radii, type and motion are ordered and bounded (no Mac needed)."""
import unittest

import motion
import tokens


class Tokens(unittest.TestCase):
    def test_scales_ascend(self):
        self.assertEqual(list(tokens.SPACE), sorted(set(tokens.SPACE)))
        radii = [tokens.RADIUS[k] for k in ("s", "m", "l", "full")]
        self.assertEqual(radii, sorted(set(radii)))
        sizes = [tokens.DISPLAY[k] for k in ("m", "l", "xl")]
        self.assertEqual(sizes, sorted(set(sizes)))
        self.assertLess(tokens.TEXT["meta"], tokens.TEXT["label"])
        self.assertLess(tokens.TEXT["label"], tokens.TEXT["body"])
        self.assertLess(tokens.TEXT["body"], tokens.DISPLAY["m"])

    def test_no_window_heading_is_above_34(self):
        self.assertLessEqual(max(tokens.DISPLAY.values()), tokens.WINDOW_HEADING_MAX)
        self.assertEqual(tokens.WINDOW_HEADING_MAX, 34)

    def test_layout_widths_make_sense(self):
        self.assertLess(tokens.CONTENT_MIN, tokens.CONTENT_MAX)
        self.assertIn(tokens.PAD, tokens.SPACE)

    def test_motion_reads_the_tokens(self):
        self.assertEqual(motion.EASE, tokens.EASE)
        for name, seconds in tokens.DURATION.items():
            self.assertGreater(seconds, 0, name)
            self.assertLess(seconds, 0.3, name)
        self.assertEqual(motion.POP_SECONDS, tokens.DURATION["pop"])
        self.assertEqual(motion.MODAL_SECONDS, tokens.DURATION["modal"])

    def test_the_curve_is_the_websites(self):                  # cubic-bezier(.2, .7, .1, 1) in trace-landing/hero.css
        self.assertEqual(tokens.EASE, (0.2, 0.7, 0.1, 1.0))

    def test_reduced_motion_still_drops_movement(self):
        self.assertIsInstance(motion.reduced(), bool)            # whatever this Mac says, asking never raises
        motion.pop(object())                                     # and a stand-in view is harmless


if __name__ == "__main__":
    unittest.main()

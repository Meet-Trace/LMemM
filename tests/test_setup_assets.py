"""The first-run window's type and look: Figtree ships with the app (with its licence), every
symbol and weight the window asks for is named, and the accent is blue. Source checks only."""
import ast
import os
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class SetupAssets(unittest.TestCase):
    def test_figtree_ships_with_its_licence(self):
        fonts = os.path.join(ROOT, "assets", "fonts")
        self.assertGreater(os.path.getsize(os.path.join(fonts, "Figtree[wght].ttf")), 20000)
        self.assertIn("SIL Open Font License", open(os.path.join(fonts, "OFL.txt")).read())

    def test_instrument_serif_ships_with_its_licence(self):
        fonts = os.path.join(ROOT, "assets", "fonts")
        for name in ("InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf"):
            self.assertGreater(os.path.getsize(os.path.join(fonts, name)), 20000, name)
        licence = open(os.path.join(fonts, "OFL-InstrumentSerif.txt")).read()
        self.assertIn("SIL Open Font License", licence)
        self.assertIn("Instrument Serif", licence)

    def test_the_kit_registers_the_serif_and_falls_back_without_it(self):
        kit = open(os.path.join(ROOT, "src", "setup_kit.py")).read()
        self.assertIn("InstrumentSerif-Regular.ttf", kit)
        self.assertIn("InstrumentSerif-Italic.ttf", kit)
        for fallback in ('"NewYork-Regular"', '"Georgia"', "NSFont.systemFontOfSize_(size)"):
            self.assertIn(fallback, kit)                      # no face -> the system serif -> the system font, never a broken glyph

    def test_the_accent_is_the_websites_blue(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import palette
        r, g, b = palette.ACCENT
        self.assertEqual((round(r * 255), round(g * 255), round(b * 255)), (0x28, 0x56, 0xd9))      # #2856d9 (was #3563E9 before the identity refactor)
        self.assertGreater(b, r + 0.4)                    # clearly blue
        self.assertGreater(b, g)

    def test_the_kit_accent_is_the_palette_accent(self):
        kit = open(os.path.join(ROOT, "src", "setup_kit.py")).read()
        self.assertIn("ACCENT = palette.ACCENT", kit)      # one accent, not a copy of its numbers

    def test_alignment_values_match_the_existing_widget_code(self):
        """widget.py (running on a Mac) uses setAlignment_(1) for centre; the kit must agree, or every
        line of text lands on the wrong side."""
        import re
        kit = open(os.path.join(ROOT, "src", "setup_kit.py")).read()
        self.assertIn("LEFT, CENTER, RIGHT = 0, 1, 2", kit)
        widget = open(os.path.join(ROOT, "src", "widget.py")).read()
        self.assertRegex(widget, r"setAlignment_\(1\)")
        self.assertIn("# centre", widget)

    def test_every_role_has_an_icon(self):
        import re
        ui = open(os.path.join(ROOT, "src", "onboarding_ui.py")).read()
        ob = open(os.path.join(ROOT, "src", "onboarding.py")).read()
        roles = re.search(r"ROLES = \(([^)]*)\)", ob).group(1)
        for role in re.findall(r'"(\w+)"', roles):
            self.assertIn(f'"{role}":', ui.split("ROLE_SYMBOLS")[1].split("}")[0], role)

    def test_labels_are_drawn_by_the_kit_not_by_text_fields(self):
        ui = open(os.path.join(ROOT, "src", "onboarding_ui.py")).read()
        self.assertNotIn("_label(", ui)                   # NSTextField labels sit high in a button
        self.assertNotIn("systemOrange", ui.replace("NSColor.systemOrangeColor() if state == onboarding.RESTART", ""))

    def test_setup_window_resizes_and_never_lets_the_body_cover_the_button(self):
        ui = open(os.path.join(ROOT, "src", "onboarding_ui.py")).read()
        self.assertIn("1 | 2 | 8", ui)                    # resizable
        self.assertIn("setContentMinSize_", ui)
        self.assertIn("windowDidResize_", ui)
        self.assertIn("NSScrollView.alloc().initWithFrame_(NSMakeRect(X, TOP, CW, room))", ui)   # a tall body scrolls above the foot


if __name__ == "__main__":
    unittest.main()

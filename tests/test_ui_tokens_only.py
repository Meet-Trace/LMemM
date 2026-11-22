"""The UI files take their colours, fonts and radii from palette.py, tokens.py and setup_kit.py (source checks, no Mac needed).

Colour calls that build a colour from numbers (colorWithRed, colorWithWhite, colorWithSRGBRed) are allowed only where the
numbers come from the palette or are a neutral that has no token; the count per file may go down, never up. Numeric font
sizes are not scanned: the sizes are still passed to `put_text` and `font` as plain numbers (the type scale is in
tokens.py, and the window's headings are set by tokens.DISPLAY, which test_tokens.py holds at 34 or less).
"""
import os
import re
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
FILES = ("window.py", "memory_pane.py", "widget.py", "notecard.py", "onboarding_ui.py", "menubar.py")
BUILDERS = re.compile(r"colorWithRed_|colorWithWhite_|colorWithSRGBRed_")
TUPLE = re.compile(r"\(\s*-?\d*\.\d+\s*,\s*-?\d*\.\d+\s*,\s*-?\d*\.\d+\s*[,)]")
# files -> how many colour-building calls remain, and why
ALLOWED_BUILDERS = {
    "window.py": 5,          # the code-drawn backdrop (colours from backdrop.py), the toast and scrim greys, the Undo link (palette values)
    "memory_pane.py": 1,     # rgb(): turns a palette colour into an NSColor
    "widget.py": 15,         # the pill's own inversion (palette.PILL_*), _rgb(), mid-grey blends
    "notecard.py": 1,        # _accent(): palette values
    "onboarding_ui.py": 0,
    "menubar.py": 0,         # system colours only: the menu bar is the OS's
}


def read(name):
    with open(os.path.join(SRC, name)) as handle:
        return handle.read()


class UiUsesTokens(unittest.TestCase):
    def test_no_literal_colour_tuples_in_the_ui_files(self):
        for name in FILES:
            self.assertEqual(TUPLE.findall(read(name)), [], name + " holds a literal colour: move it to palette.py")

    def test_colour_building_calls_do_not_grow(self):
        for name in FILES:
            self.assertLessEqual(len(BUILDERS.findall(read(name))), ALLOWED_BUILDERS[name], name)

    def test_no_design_asset_is_fetched_over_the_network(self):
        for name in FILES + ("setup_kit.py", "palette.py", "tokens.py"):
            self.assertNotRegex(read(name), r"https?://\S+\.(?:woff2?|ttf|otf|css|png|jpe?g|svg|webp)", name)
            self.assertNotIn("hey-trace.app", read(name), name)
            self.assertNotIn("NSURLSession", read(name), name)

    def test_titles_use_the_display_face_and_the_scale(self):
        window = read("window.py")
        self.assertIn('kit.display(doc, page["title"]', window)
        self.assertIn('kit.display(host, page["title"]', window)
        self.assertNotRegex(window, r"kit\.display\([^)]*size=(3[5-9]|[4-9]\d)")


if __name__ == "__main__":
    unittest.main()

"""The UI files take their colours, fonts and radii from palette.py, tokens.py and setup_kit.py (source checks, no Mac needed).

Colour calls that build a colour from numbers (colorWithRed, colorWithWhite, colorWithSRGBRed) are allowed only where the
numbers come from the palette or are a neutral that has no token; the count per file may go down, never up. Font sizes
are read from tokens.py (SZ["label"], tokens.DISPLAY["m"]): a number written in a text call's size slot fails the test.
"""
import ast
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


# the calls whose size argument is a font size: name -> (position of the size, keyword)
SIZE_ARG = {"put_text": (5, "size"), "text_height": (1, "size"), "text_width": (1, "size"), "font": (0, "size"),
            "_font": (0, "size"), "_field": (2, "size"), "button": (None, "size"), "_link": (None, "size"),
            "segmented": (None, "size"), "_segmented": (None, "size"), "mono_text": (None, "size"), "_label": (None, "size")}


def literal_sizes(source):
    """The (line, number) of every font size written as a number in a text call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
        if name not in SIZE_ARG:
            continue
        pos, kw = SIZE_ARG[name]
        if name == "_label":                       # window's self._label(parent, text, x, y, w, size) vs widget's _label(text, size)
            pos = 5 if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "self" else 1
        args = ([node.args[pos]] if pos is not None and len(node.args) > pos else []) + [k.value for k in node.keywords if k.arg == kw]
        found += [(a.lineno, a.value) for a in args if isinstance(a, ast.Constant) and isinstance(a.value, (int, float))
                  and not isinstance(a.value, bool)]
    return found


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

    def test_font_sizes_come_from_the_type_scale(self):
        for name in FILES:
            self.assertEqual(literal_sizes(read(name)), [], name + " sets a font size as a number: use tokens.SIZE / tokens.DISPLAY")

    def test_every_size_in_the_scale_is_known_and_headings_stay_small(self):
        import tokens
        self.assertEqual(list(tokens.SIZE.values()), sorted(tokens.SIZE.values()))
        self.assertLessEqual(max(tokens.DISPLAY.values()), tokens.WINDOW_HEADING_MAX)

    def test_dialog_titles_are_serif(self):
        window = read("window.py")
        self.assertNotRegex(window, r"kit\.put_text\(card, .*cw - 48, SZ\[\"(head|title)\"\], 700")

    def test_one_field_style(self):
        pane = read("memory_pane.py")
        self.assertNotIn("setBezelStyle_", read("window.py"))
        self.assertIn("_KitBox", pane)
        self.assertIn("input_field", read("window.py"))

    def test_the_shared_primitives_are_used_where_their_concept_appears(self):
        pane, window = read("memory_pane.py"), read("window.py")
        self.assertIn("kit.source_line(", pane)                 # observed text: a mono source line with the words marked
        self.assertIn("kit.tag(host,'decided'", pane)           # a decision
        self.assertIn("kit.segmented(", pane)                   # the period control
        self.assertIn("kit.segmented(doc", window)              # Settings sections
        self.assertGreaterEqual(window.count("kit.empty_state("), 2)
        self.assertGreaterEqual(window.count('kit.surface(doc, x, y, w, ') , 2)   # notes (thing page and search) share one surface

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

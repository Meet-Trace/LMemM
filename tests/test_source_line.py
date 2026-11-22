"""source_line (the shared evidence line): the quote is drawn at the height the row reserved, so a long quote cannot
run past the row into the next one. Needs AppKit (a Mac); the choice of face is checked everywhere."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from AppKit import NSMakeRect
    import setup_kit as kit
    from widget import _Flipped
except Exception:                                   # not a Mac, or no PyObjC
    kit = None

LONG = "one very long line of observed text that keeps going " * 12


@unittest.skipIf(kit is None, "needs AppKit")
class SourceLine(unittest.TestCase):
    def host(self):
        return _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 400))

    def test_a_long_quote_stays_inside_the_height_the_row_reserved(self):
        host, cap, top = self.host(), 36, 10
        kit.source_line(host, "main.py", 20, top, 300, quote=LONG, quote_height=cap)
        bottom = max(v.frame().origin.y + v.frame().size.height for v in host.subviews())
        self.assertLessEqual(bottom, top + kit.SOURCE_HEAD_H + kit.SOURCE_GAP + cap)

    def test_the_quote_starts_where_the_row_budgets_it(self):
        host = self.host()
        kit.source_line(host, "main.py", 20, 10, 300, quote="short", quote_height=18)
        quote = host.subviews()[-1]
        self.assertEqual(quote.frame().origin.y, 10 + kit.SOURCE_HEAD_H + kit.SOURCE_GAP)

    def test_without_a_cap_the_quote_takes_its_own_height(self):
        host = self.host()
        used = kit.source_line(host, "main.py", 20, 10, 300, quote=LONG)
        self.assertGreater(used, kit.SOURCE_HEAD_H + kit.SOURCE_GAP + 36)


class SourceFace(unittest.TestCase):
    def test_mono_is_for_files_and_addresses_not_page_titles(self):
        if kit is None:
            self.skipTest("needs AppKit")
        for literal in ("main.py", "src/window.py", "https://example.com/a"):
            self.assertTrue(kit.literal_source(literal), literal)
        for prose in ("Fix login bug · GitHub", "Design review notes"):
            self.assertFalse(kit.literal_source(prose), prose)

    def test_the_pane_sizes_its_row_from_the_same_numbers_the_primitive_draws_with(self):
        pane = open(os.path.join(os.path.dirname(__file__), "..", "src", "memory_pane.py")).read()
        self.assertIn("kit.SOURCE_HEAD_H", pane)
        self.assertIn("kit.SOURCE_GAP", pane)
        self.assertIn("quote_height=bh_", pane)


SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def _read(name):
    with open(os.path.join(SRC, name)) as handle:
        return handle.read()


class OneImplementationEach(unittest.TestCase):
    """The same component is drawn by one primitive wherever it appears (spec criteria 5, 6, 9)."""

    def test_the_source_button_and_decided_tag_are_shared_by_memory_and_resume(self):
        for name in ("memory_pane.py", "window.py"):
            self.assertIn("kit.source_button(", _read(name), name)
            self.assertNotIn('"Source  \\u203a"', _read(name), name + " draws its own Source button")
        self.assertIn('kit.tag(host, "decided"', _read("window.py"))
        self.assertIn("kit.tag(host,'decided'", _read("memory_pane.py"))

    def test_no_window_field_is_a_bare_native_text_field(self):
        window = _read("window.py")
        self.assertNotIn("NSTextField.alloc()", window)
        self.assertIn("kit._KitInput.alloc()", window)
        self.assertIn("kit.input_field(accessory", window)

    def test_every_memory_field_is_drawn_on_the_one_outline(self):
        pane = _read("memory_pane.py")
        self.assertNotIn("box.fill=kit.faint(0.06);box.border=kit.line()", pane)     # the composer's old hand-drawn box
        self.assertGreaterEqual(pane.count("kit._KitBox.alloc().initWithFocus_("), 2)  # query/field_box and the composer
        self.assertEqual(pane.count("self.note.box=box"), 1)

    def test_the_pill_card_buttons_come_from_the_shared_button(self):
        widget = _read("widget.py")
        body = widget[widget.index("    def _button(self"):widget.index("    def _suggest(self")]
        self.assertIn("kit.button(", body)
        self.assertNotIn("_Tap.alloc()", body)

    def test_memory_with_nothing_kept_uses_the_empty_state(self):
        self.assertIn("Nothing kept for this period yet", _read("memory_pane.py"))
        self.assertNotIn("para(view['empty']", _read("memory_pane.py"))


@unittest.skipIf(kit is None, "needs AppKit")
class SourceButton(unittest.TestCase):
    def test_it_sits_at_the_right_edge_and_names_what_it_opens(self):
        host = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 100))
        width = kit.source_button(host, 0, 10, "main.py", lambda: None, right=380)
        button = host.subviews()[0]
        self.assertEqual(button.frame().origin.x + button.frame().size.width, 380)
        self.assertEqual(button.frame().size.width, width)
        self.assertEqual(button.ax_label, "Source for: main.py")


if __name__ == "__main__":
    unittest.main()

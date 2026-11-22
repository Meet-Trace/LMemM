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


if __name__ == "__main__":
    unittest.main()

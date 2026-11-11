"""The window's shell: it can be resized, minimised and made full screen, and it stays reachable from other apps.
AppKit cannot run here, so these read window.py and widget.py for the wiring (tests/window_smoke.py draws it)."""
import os, re, unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
WINDOW = open(os.path.join(SRC, "window.py")).read()
WIDGET = open(os.path.join(SRC, "widget.py")).read()


class ShellTests(unittest.TestCase):
    def test_style_has_resize_and_minimise(self):
        self.assertIn("style = 1 | 2 | 4 | 8", WINDOW)                 # titled, close, minimise, resize

    def test_full_screen_button(self):
        self.assertIn("setCollectionBehavior_(1 << 7)", WINDOW)

    def test_stays_when_you_leave(self):
        self.assertIn("setHidesOnDeactivate_(False)", WINDOW)

    def test_regular_app_while_open(self):
        self.assertIn("setActivationPolicy_(0 if on else 1)", WINDOW)
        self.assertRegex(WINDOW, r"def show\(self, banner\):\n\s+self\._regular\(True\)")
        self.assertIn("lambda: self._regular(False)", WINDOW)

    def test_minimum_size_and_remembered_frame(self):
        self.assertIn("setContentMinSize_((MIN_W, MIN_H))", WINDOW)
        self.assertIn('setFrameAutosaveName_("LMemMMain")', WINDOW)

    def test_no_fixed_layout_left(self):
        self.assertNotIn("MAIN_W", WINDOW)
        body = WINDOW.split("def _render(self)")[1].split("def _fill")[0]
        self.assertIsNone(re.search(r"(?<![\w.])[WH](?![\w])\s*[-,)]", body.replace("MIN_W", "")), body)

    def test_one_column_width_for_every_page(self):
        self.assertNotIn("MAX_WIDE", WINDOW)
        self.assertEqual(WINDOW.count("min(MAX_MAIN, room)"), 1)

    def test_selection_has_clear_and_done(self):
        bar = WINDOW.split("def _select_bar")[1].split("def _mini_card")[0]
        self.assertIn('"Clear", 14 + tw + 12', bar)
        self.assertIn("self.clear_select", bar)
        self.assertIn('("Done", "primary", self.stop_select', bar)
        clear = WINDOW.split("def clear_select")[1].split("def pick_thing")[0]
        self.assertIn("self.sel = []", clear)                              # stays in selection mode

    def test_panes_keep_their_scroll_place(self):
        self.assertEqual(WINDOW.count("setDocumentView_(self.memory_pane.host)"), 0)     # never re-set on every draw: that snaps to the top
        self.assertEqual(WINDOW.count("setDocumentView_(self.resume_pane.host)"), 0)
        place = WINDOW.split("def _place_pane")[1].split("def _fill")[0]
        self.assertIn("if scroll.documentView() is not host", place)
        self.assertIn("clip.scrollToPoint_", place)

    def test_keys(self):
        for key in ('"k"', '","', '"["', '"q"', '"w"', '"m"'):
            self.assertIn(key, WINDOW)
        self.assertIn("cancelOperation_", WINDOW)
        self.assertIn("self.window.on_escape = self.escape", WINDOW)

    def test_arrow_keys_reach_keynav(self):
        self.assertIn("def keyDown_(self, event)", WINDOW)
        self.assertIn("self.window.on_key = self.key", WINDOW)
        self.assertIn("keynav.press(self.cur, rows, code)", WINDOW)
        for rid in ('"n" + row["id"]', '"p" + row["id"]', '"t" + t["id"]'):
            self.assertIn(rid, WINDOW)

    def test_voiceover(self):
        for name in ("isAccessibilityElement", "accessibilityRole", "accessibilityLabel", "accessibilityPerformPress"):
            self.assertIn(name, WIDGET)


if __name__ == "__main__":
    unittest.main()

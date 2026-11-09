import unittest

import numpy as np

import trail_ax
import trail_cover
from trail_ax import Snapshot, collect

DISPLAY = {"X": 0, "Y": 0, "Width": 1000, "Height": 800}
WIN = (100, 100, 600, 500)


def screen():
    return np.full((800, 1000), 255, np.int16)


def ink(g, x, y, w, h):
    """Draw 'text' (high-contrast stripes) at a rectangle."""
    for i in range(y, y + h, 4):
        g[i:i + 2, x:x + w] = 0


class Node:
    def __init__(self, role, children=(), **attrs):
        self._a = {"AXRole": role, **{"AX" + k: v for k, v in attrs.items()}}
        self._kids = list(children)

    def attrs(self):
        return self._a

    def children(self):
        return self._kids


def text(t, x, y, w=300, h=20):
    return Node("AXStaticText", Value=t, Position=(x, y), Size=(w, h))


def window(*kids):
    return Node("AXWindow", list(kids), Title="App", Position=WIN[:2], Size=WIN[2:])


class JudgeTests(unittest.TestCase):
    def test_a_screen_the_tree_describes_is_not_read(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        ink(g, 150, 300, 300, 20)
        snap = collect(window(text("hello there", 150, 200), text("second line", 150, 300)))
        v = trail_cover.judge(snap, g, DISPLAY, 1.0)
        self.assertTrue(v["ok"], v)
        self.assertGreater(v["ink"], 0)

    def test_ink_nothing_accounts_for_goes_to_the_screen_read(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        ink(g, 150, 350, 400, 120)                      # drawn, but no node says so
        snap = collect(window(text("hello there", 150, 200), text("x", 150, 560)))
        v = trail_cover.judge(snap, g, DISPLAY, 1.0)
        self.assertFalse(v["ok"], v)
        self.assertEqual(v["why"], "ink the tree does not account for")

    def test_a_terminal_sized_text_box_is_content_we_did_not_read(self):
        g = screen()
        ink(g, 120, 160, 500, 400)
        body = Node("AXTextArea", Position=(110, 140), Size=(580, 450), Value="secret")
        snap = collect(window(text("Terminal", 150, 120), body))
        v = trail_cover.judge(snap, g, DISPLAY, 1.0)
        self.assertFalse(v["ok"])
        self.assertIn("large text box", v["why"])

    def test_a_small_input_box_is_withheld_not_a_gap(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        ink(g, 150, 520, 300, 20)                       # what you are typing
        box = Node("AXTextField", Position=(140, 510), Size=(400, 40), Value="draft")
        snap = collect(window(text("hello there", 150, 200), box))
        self.assertTrue(trail_cover.judge(snap, g, DISPLAY, 1.0)["ok"])

    def test_images_and_toolbars_we_skip_on_purpose_are_not_a_gap(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        ink(g, 150, 300, 200, 150)                      # a picture
        ink(g, 150, 150, 400, 20)                       # a toolbar
        snap = collect(window(text("hello there", 150, 200),
                              Node("AXImage", Position=(150, 300), Size=(200, 150)),
                              Node("AXToolbar", Position=(140, 140), Size=(500, 40))))
        self.assertTrue(trail_cover.judge(snap, g, DISPLAY, 1.0)["ok"])

    def test_a_truncated_or_empty_tree_is_never_trusted(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        snap = collect(window(text("hello there", 150, 200)), max_nodes=1)
        self.assertFalse(trail_cover.judge(snap, g, DISPLAY, 1.0)["ok"])
        self.assertFalse(trail_cover.judge(Snapshot(), g, DISPLAY, 1.0)["ok"])

    def test_ink_with_no_text_from_the_tree_goes_to_the_screen_read(self):
        g = screen()
        ink(g, 150, 200, 300, 200)
        snap = collect(window(Node("AXGroup", [Node("AXGroup")])))
        self.assertFalse(trail_cover.judge(snap, g, DISPLAY, 1.0)["ok"])

    def test_retina_scale(self):
        g = np.full((1600, 2000), 255, np.int16)
        for i in range(400, 440, 8):
            g[i:i + 4, 300:900] = 0
        snap = collect(window(text("hello there", 150, 200, 300, 20)))
        self.assertTrue(trail_cover.judge(snap, g, DISPLAY, 2.0)["ok"])


class MapTests(unittest.TestCase):
    def test_ascii_map_marks_explained_and_unexplained_ink(self):
        g = screen()
        ink(g, 150, 200, 300, 20)
        ink(g, 150, 400, 300, 40)
        snap = collect(window(text("hello there", 150, 200)))
        out = trail_cover.ascii_map(snap, g, DISPLAY, 1.0)
        self.assertIn("#", out)
        self.assertIn("X", out)

    def test_size_and_point_are_read_from_their_printed_form(self):
        self.assertEqual(trail_ax._from_text("<AXValue 0x1 {value = x:12.5 y:34.0 type = kAXValueCGPointType}>"), (12.5, 34.0))
        self.assertEqual(trail_ax._from_text("<AXValue 0x1 {value = w:600.0 h:500.0 type = kAXValueCGSizeType}>"), (600.0, 500.0))
        self.assertIsNone(trail_ax._from_text("nothing here"))


class ResultTests(unittest.TestCase):
    META = {"ts": "20261009-120000", "image": "x.jpg", "app": "App", "window": "App"}

    def test_it_is_shaped_like_an_ocr_result(self):
        snap = collect(window(text("Plan for Monday", 150, 200, 200, 30),
                              text("we agreed to ship the report on Friday morning", 150, 260, 500, 20)))
        snap.headings.append("Plan for Monday")
        res = trail_cover.to_res(snap, self.META, (1000, 800), DISPLAY, 1.0)
        self.assertEqual(res["image_size"], {"w": 1000, "h": 800})
        kinds = {o["text"]: o["kind"] for o in res["objects"]}
        self.assertEqual(kinds["Plan for Monday"], "heading")
        self.assertEqual(kinds["we agreed to ship the report on Friday morning"], "paragraph")
        self.assertEqual(res["resolver"]["engine"], "accessibility")
        self.assertEqual([o["id"] for o in res["objects"]], [0, 1])

    def test_secret_looking_lines_and_off_screen_text_are_dropped(self):
        snap = collect(window(text("password: hunter2hunter2", 150, 200),
                              text("hidden below", 150, 5000), text("visible", 150, 300)))
        res = trail_cover.to_res(snap, self.META, (1000, 800), DISPLAY, 1.0)
        self.assertEqual([o["text"] for o in res["objects"]], ["visible"])


if __name__ == "__main__":
    unittest.main()

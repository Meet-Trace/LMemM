import unittest

import ocr_regions


def obj(text, x, y, w=100, h=14):
    return {"kind": "text", "text": text, "box": [x, y, w, h], "conf": 1.0}


class PlanTests(unittest.TestCase):
    SIZE = (1440, 900)

    def test_one_small_change_is_one_box(self):
        boxes = ocr_regions.plan({"regions": [[400, 800, 300, 32]], "scroll": 0}, self.SIZE)
        self.assertEqual(len(boxes), 1)
        x, y, w, h = boxes[0]
        self.assertTrue(x <= 400 and y <= 800 and x + w >= 700 and y + h >= 832)

    def test_a_scroll_or_nothing_to_compare_reads_everything(self):
        self.assertIsNone(ocr_regions.plan({"regions": [[0, 0, 50, 50]], "scroll": 120}, self.SIZE))
        self.assertIsNone(ocr_regions.plan(None, self.SIZE))
        self.assertIsNone(ocr_regions.plan({"regions": [], "scroll": 0}, self.SIZE))

    def test_a_big_change_reads_everything(self):
        self.assertIsNone(ocr_regions.plan({"regions": [[0, 0, 1440, 600]], "scroll": 0}, self.SIZE))

    def test_touching_boxes_merge_and_scattered_ones_give_up(self):
        two = ocr_regions.plan({"regions": [[100, 100, 100, 20], [100, 130, 100, 20]], "scroll": 0}, self.SIZE)
        self.assertEqual(len(two), 1)
        far = [[i * 300, 40 + (i % 2) * 400, 40, 20] for i in range(5)]
        self.assertIsNone(ocr_regions.plan({"regions": far, "scroll": 0}, self.SIZE))

    def test_boxes_stay_inside_the_screen(self):
        x, y, w, h = ocr_regions.plan({"regions": [[1420, 880, 20, 20]], "scroll": 0}, self.SIZE)[0]
        self.assertTrue(x >= 0 and y >= 0 and x + w <= 1440 and y + h <= 900)


class MergeTests(unittest.TestCase):
    def test_old_text_outside_the_box_is_kept_and_inside_is_replaced(self):
        old = [obj("Inbox", 10, 20), obj("hello", 400, 810), obj("Sent", 10, 60)]
        new = [obj("hello there", 400, 810, 160)]
        out = ocr_regions.merge(old, [[380, 790, 400, 60]], new)
        self.assertEqual([o["text"] for o in out], ["Inbox", "Sent", "hello there"])

    def test_the_result_is_in_reading_order(self):
        out = ocr_regions.merge([obj("b", 10, 200)], [[0, 0, 50, 50]], [obj("a", 10, 10)])
        self.assertEqual([o["text"] for o in out], ["a", "b"])


if __name__ == "__main__":
    unittest.main()

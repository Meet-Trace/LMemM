"""real_days.py: exporting your own days for labelling, read-only, keeping labels on re-export."""

import json
import os
import tempfile
import unittest

import project_grouping_eval as pge
import real_days


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(doc, fh)


class RealDaysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem = os.path.join(self.tmp, "memory")
        write(os.path.join(self.mem, "memory.json"), {"items": [
            {"id": "a", "app": "Excel", "kind": "spreadsheet", "title": "Model.xlsx", "screenshot": "x.png"},
            {"id": "b", "app": "Slack", "kind": "chat", "title": "#pricing"}]})
        write(os.path.join(self.mem, "sessions", "s1.json"), {"timeline": [
            {"item": "a", "from": "2026-11-03T09:00:00", "to": "2026-11-03T09:10:00"},
            {"item": "b", "from": "2026-11-03T09:10:00", "to": "2026-11-03T09:12:00"},
            {"item": "a", "from": "2026-11-04T10:00:00", "to": "2026-11-04T10:05:00"},
            {"item": "gone", "from": "2026-11-04T10:05:00", "to": "2026-11-04T10:06:00"}]})
        self.out = os.path.join(self.tmp, "days")

    def test_one_file_per_day_without_screenshots(self):
        paths = real_days.export(self.mem, self.out)
        self.assertEqual([os.path.basename(p) for p in paths], ["2026-11-03.json", "2026-11-04.json"])
        with open(paths[0]) as fh:
            day = json.load(fh)
        self.assertEqual(set(day["items"]), {"a", "b"})
        self.assertNotIn("screenshot", day["items"]["a"])
        self.assertEqual(day["items"]["a"]["label_status"], "todo")
        with open(paths[1]) as fh:
            self.assertEqual([v["item"] for v in json.load(fh)["timeline"]], ["a"])

    def test_unlabelled_days_cannot_be_scored_and_labels_survive_re_export(self):
        path = real_days.export(self.mem, self.out, "2026-11-03", "2026-11-03")[0]
        with self.assertRaises(ValueError):
            pge.load_day(path)
        with open(path) as fh:
            day = json.load(fh)
        for item in day["items"].values():
            item["label"], item["label_status"] = ["Pricing"], "done"
        write(path, day)
        real_days.export(self.mem, self.out, "2026-11-03", "2026-11-03")
        items = pge.load_day(path)
        self.assertEqual(items["a"]["label"], ["Pricing"])

    def test_summary_lists_days(self):
        rows = real_days.summary(self.mem)
        self.assertEqual(rows[0], ("2026-11-03", 2, 2, 12))

    def test_memory_is_never_written(self):
        before = os.path.getmtime(os.path.join(self.mem, "memory.json"))
        real_days.export(self.mem, self.out)
        self.assertEqual(before, os.path.getmtime(os.path.join(self.mem, "memory.json")))


class BcubedTest(unittest.TestCase):
    def test_perfect_partial_and_unfiled(self):
        truth = {"a": {"P"}, "b": {"P"}, "c": {"Q"}, "d": set()}
        self.assertEqual(pge.bcubed(truth, {"a": ["P"], "b": ["P"], "c": ["Q"]})["f1"], 1.0)
        mixed = pge.bcubed(truth, {"a": ["X"], "b": ["X"], "c": ["X"]})
        self.assertAlmostEqual(mixed["wrong_mix"], 1.0)
        none = pge.bcubed(truth, {})
        self.assertEqual((none["precision"], none["filed"], none["wrong_mix"]), (1.0, 0.0, None))

    def test_summarise_gives_mean_low_high(self):
        self.assertEqual(pge.summarise([{"f": 0.5}, {"f": 1.0}])["f"], (0.75, 0.5, 1.0))



class LabelPageTest(unittest.TestCase):
    def test_labels_are_cleaned_and_can_be_reset(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "label_days.py")
        spec = importlib.util.spec_from_file_location("label_days", path)
        lab = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lab)
        doc = {"items": {"a": {"id": "a", "title": "x", "label": [], "label_status": "todo"}},
               "timeline": [{"item": "a", "from": "2026-11-03T09:00:00", "to": "2026-11-03T09:05:00"}]}
        self.assertEqual(lab.apply_labels(doc, {"a": ["Kestrel / Market  sizing", "Kestrel/Market sizing"]}), 1)
        self.assertEqual(doc["items"]["a"]["label"], ["Kestrel/Market sizing"])
        self.assertEqual(lab.day_view(doc)[0]["times"], ["09:00-09:05"])
        lab.apply_labels(doc, {"a": None, "missing": ["X"]})
        self.assertEqual(doc["items"]["a"]["label_status"], "todo")


if __name__ == "__main__":
    unittest.main()

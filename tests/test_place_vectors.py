"""place_vectors.py and the meaning step of project_reasoner_assign.run(vectors=...)."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

import place_vectors as pv
import project_reasoner_assign as A
import project_reasoner_graph as rg

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")


def load(name):
    with open(os.path.join(HERE, name)) as fh:
        doc = json.load(fh)
    return doc["items"], doc["timeline"]


def visit(item, start, end):
    return {"item": item, "from": f"2026-11-05T10:{start:02d}:00", "to": f"2026-11-05T10:{end:02d}:00"}


class VectorsTest(unittest.TestCase):
    def test_cache_embeds_each_card_once(self):
        items, _ = load("sample_day.json")
        calls = []

        class Counting(pv.WordHashEmbedder):
            def __call__(self, texts):
                calls.append(len(texts))
                return super().__call__(texts)
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "v.json")
            first = pv.vectors(items, Counting(), cache=cache)
            second = pv.vectors(items, Counting(), cache=cache)
        self.assertEqual(first, second)
        self.assertEqual(sum(calls), len({pv.card_text(i, screen=False) for i in items.values()}))

    def test_screen_text_is_left_out_by_default(self):
        item = {"title": "Kestrel", "content": {"excerpts": [{"text": "Spice Route catering"}]}}
        self.assertNotIn("Spice", pv.card_text(item, screen=False))
        self.assertIn("Spice", pv.card_text(item))


class MeaningTest(unittest.TestCase):
    def test_without_vectors_nothing_changes(self):
        items, timeline = load("sample_day.json")
        self.assertEqual(A.run(items, timeline, rg.CapitalisedNames())[0],
                         A.run(items, timeline, rg.CapitalisedNames(), vectors={})[0])

    def test_similar_cards_switched_between_form_a_task(self):
        items = {"a": {"app": "Preview", "title": "market report"}, "b": {"app": "Word", "title": "draft notes"}}
        timeline = [visit("a", 0, 5), visit("b", 5, 10), visit("a", 10, 15), visit("b", 15, 20)]
        same = {"a": [1.0, 0.0], "b": [1.0, 0.0]}
        apart = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
        log = A.run(items, timeline, None, vectors=same)[2]
        self.assertTrue(any(e.get("step") == "assign" and len(e["task"]) == 2 for e in log))
        log = A.run(items, timeline, None, vectors=apart)[2]
        self.assertFalse(any(e.get("step") == "assign" and len(e["task"]) == 2 for e in log))

    def test_conflicting_anchors_block_meaning(self):
        items = {"a": {"title": "tam", "state": {"file": "~/Clients/Kestrel/tam.xlsx"}},
                 "b": {"title": "tam", "state": {"file": "~/Clients/Northwind/tam.xlsx"}}}
        timeline = [visit("a", 0, 5), visit("b", 5, 10)]
        log = A.run(items, timeline, None, vectors={"a": [1.0, 0.0], "b": [1.0, 0.0]})[2]
        self.assertFalse(any(e.get("step") == "assign" and len(e["task"]) == 2 for e in log))


class ScriptTest(unittest.TestCase):
    def test_tunes_with_the_hash_stand_in(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "embed_projects.py")
        spec = importlib.util.spec_from_file_location("embed_projects", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out):
            mod.main(["--embed", "hash", "--projects", "12", "--seeds", "4", "--report", "0.25,4,1",
                      "--cache", os.path.join(d, "v.json")])
        self.assertIn("off", out.getvalue())
        self.assertIn("0.25,4,1", out.getvalue())


class RealCompareTest(unittest.TestCase):
    def test_prints_titles_and_counts_only(self):
        import synthetic_days as sd
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "compare_real_grouping.py")
        spec = importlib.util.spec_from_file_location("compare_real_grouping", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        doc = sd.generate(6, seed=4)
        lines = []
        with tempfile.TemporaryDirectory() as d:
            mod.real_days.read_items = lambda m: doc["items"]
            mod.real_days.read_visits = lambda m: doc["timeline"]
            mod.finder.recent = lambda items, visits, now, days: (items, visits)
            mod.main(["--memory", d, "--cache", os.path.join(d, "v.json")], out=lines.append,
                     embed=pv.WordHashEmbedder(), name_model=lambda s, u, schema: {"name": "unsure"})
        text = "\n".join(lines)
        self.assertIn("off (no vectors)", text)
        self.assertIn("things changed project", text)
        self.assertIn("== names for the", text)
        for item in doc["items"].values():
            for line in (item.get("activity") or {}).get("typing", {}).get("text", []):
                self.assertNotIn(line, text)


if __name__ == "__main__":
    unittest.main()

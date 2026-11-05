"""project_reasoner_assign.py: lasting projects that tasks are assigned to, measured on generated streams."""

import contextlib
import io
import json
import os
import unittest

import project_grouping_eval as pge
import project_reasoner_assign as A
import project_reasoner_graph as rg
import synthetic_days as sd

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")


def load(name):
    with open(os.path.join(HERE, name)) as fh:
        doc = json.load(fh)
    return doc["items"], doc["timeline"]


class SyntheticTest(unittest.TestCase):
    def test_same_seed_same_stream(self):
        self.assertEqual(sd.generate(20, seed=5), sd.generate(20, seed=5))
        self.assertNotEqual(sd.generate(20, seed=5)["items"].keys(), sd.generate(20, seed=6)["items"].keys())

    def test_hard_cases_are_in_the_stream(self):
        doc = sd.generate(60, seed=7)
        self.assertTrue(doc["world"]["hard_pairs"]["same workspace"])
        self.assertTrue(all(i.get("label") is not None for i in doc["items"].values()))
        ids = {v["item"] for v in doc["timeline"]}
        self.assertLessEqual(ids, set(doc["items"]))


class EntitiesTest(unittest.TestCase):
    def test_typed_priors(self):
        e = A.entities({"title": "Kestrel TAM", "state": {
            "workspace": "180DC", "file": "~/Clients/Kestrel/tam.xlsx", "chat": "#kestrel-sizing",
            "url": "https://github.com/acme/lmemm/pull/26", "to": ["Meera Shah <meera@kestrelhealth.in>"]}})
        self.assertEqual(e["180dc"], A.PRIOR["workspace"])
        self.assertEqual(e["kestrel"], A.PRIOR["folder"])
        self.assertEqual(e["lmemm"], A.PRIOR["repo"])
        self.assertEqual(e["acme"], A.PRIOR["org"])
        self.assertEqual(e["kestrelhealth"], A.PRIOR["domain"])
        self.assertEqual(e["meera shah"], A.PRIOR["person"])
        self.assertEqual(e["sizing"], A.PRIOR["channel"])
        self.assertNotIn("clients", e)

    def test_a_name_inside_a_workspace_stays_weak(self):
        e = A.entities({"state": {"workspace": "Northstar"}}, names=["Northstar"])
        self.assertEqual(e["northstar"], A.PRIOR["workspace"])

    def test_conflicting_anchors(self):
        a = {"kestrel": 0.8, "tam": 0.15}
        self.assertTrue(A._conflict(a, {"northwind": 0.8, "tam": 0.15}))
        self.assertFalse(A._conflict(a, {"kestrelhealth": 0.8}))
        self.assertFalse(A._conflict(a, {"tam": 0.15}))


class AssignTest(unittest.TestCase):
    def test_no_wrong_merges_on_the_invented_days(self):
        for day in ("sample_day.json", "held_out_day.json"):
            items, timeline = load(day)
            assigned, _, _ = A.run(items, timeline, rg.CapitalisedNames())
            s = pge.bcubed(pge.gold(items, "project"), assigned, "project")
            self.assertEqual(s["wrong_mix"], 0.0, day)
            self.assertGreater(s["f1"], 0.7, day)

    def test_hard_cases_rarely_merge_on_a_generated_stream(self):
        doc = sd.generate(100, seed=101)
        assigned, _, _ = A.run(doc["items"], doc["timeline"], rg.CapitalisedNames())
        b = pge.bcubed(pge.gold(doc["items"], "project"), assigned, "project")
        self.assertGreater(b["precision"], 0.9)
        self.assertGreater(b["recall"], 0.75)
        for kind, (bad, total) in sd.hard_case_merges(doc, assigned).items():
            self.assertLessEqual(bad, max(2, 0.05 * total), kind)

    def test_beats_the_graph_method_at_scale(self):
        doc = sd.generate(100, seed=102)
        gold = pge.gold(doc["items"], "project")
        new, _, _ = A.run(doc["items"], doc["timeline"], None)
        old, _, _ = rg.run(doc["items"], doc["timeline"], None)
        self.assertGreater(pge.bcubed(gold, new, "project")["precision"],
                           pge.bcubed(gold, old, "project")["precision"] + 0.5)

    def test_inbox_and_one_off_pages_stay_unfiled(self):
        items, timeline = load("sample_day.json")
        assigned, _, log = A.run(items, timeline, None)
        self.assertNotIn("gmail-inbox", assigned)
        self.assertNotIn("slack-general", assigned)
        self.assertTrue(any("not filed" in e.get("decision", "") for e in log))

    def test_failed_model_calls_fall_back_to_code(self):
        def boom(system, user, schema):
            raise OSError("ollama is down")
        items, timeline = load("sample_day.json")
        self.assertEqual(A.run(items, timeline, boom)[0], A.run(items, timeline, None)[0])


class StressCliTest(unittest.TestCase):
    def test_runs_and_prints_hard_cases(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "stress_projects.py")
        spec = importlib.util.spec_from_file_location("stress_projects", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.main(["--projects", "20", "--seed", "3", "--model", "none"])
        self.assertIn("Hard cases", out.getvalue())
        self.assertIn("same workspace", out.getvalue())


if __name__ == "__main__":
    unittest.main()

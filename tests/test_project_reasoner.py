"""project_reasoner.py: sittings, digests and how a model's answer is turned into projects."""

import contextlib
import importlib.util
import io
import json
import os
import unittest

import project_grouping_eval as pge
import project_reasoner as r

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping", "sample_day.json")


def visit(item, a, b):
    return {"item": item, "from": f"2026-11-03T{a}:00", "to": f"2026-11-03T{b}:00"}


class SittingsTest(unittest.TestCase):
    def test_a_long_gap_starts_a_new_sitting(self):
        tl = [visit("a", "09:00", "09:10"), visit("b", "09:12", "09:20"), visit("c", "09:40", "09:50")]
        self.assertEqual(r.sittings(tl), [["a", "b"], ["c"]])

    def test_going_back_does_not_repeat_a_place(self):
        tl = [visit("a", "09:00", "09:05"), visit("b", "09:05", "09:06"), visit("a", "09:06", "09:10")]
        self.assertEqual(r.sittings(tl), [["a", "b"]])

    def test_a_long_visit_bridges_a_short_one(self):
        tl = [visit("a", "09:00", "10:00"), visit("b", "09:30", "09:31"), visit("c", "10:02", "10:03")]
        self.assertEqual(r.sittings(tl), [["a", "b", "c"]])

    def test_sittings_are_bounded(self):
        tl = [visit(f"p{n}", "09:00", "09:01") for n in range(r.MAX_SITTING_PLACES + 2)]
        self.assertEqual([len(s) for s in r.sittings(tl)], [r.MAX_SITTING_PLACES, 2])


class DigestTest(unittest.TestCase):
    def test_digest_keeps_meaning_and_drops_bookkeeping(self):
        item = {"id": "x", "app": "Slack", "kind": "chat", "title": "#a", "screenshot": "s.jpg", "content_hash": "h",
                "state": {"chat": "#a", "draft_area": "secret box id"},
                "activity": {"reading": {"text": [f"line {n}" for n in range(10)]}, "typing": {"text": ["x" * 999]}}}
        d = r.digest(item)
        self.assertNotIn("screenshot", d)
        self.assertEqual(d["details"], {"chat": "#a"})
        self.assertEqual(d["read"], ["line 6", "line 7", "line 8", "line 9"])
        self.assertEqual(len(d["typed"][0]), r.LINE_CHARS)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.projects, self.assigned, self.log = {}, {}, []

    def group(self, places, project, new=True, confidence=0.9, summary="s"):
        return {"places": places, "doing": "d", "project": project, "new_project": new,
                "project_summary": summary, "confidence": confidence, "why": "w"}

    def test_new_project_is_created_and_filed(self):
        n = r.apply({"groups": [self.group(["a", "b"], "Northwind")], "unrelated": ["c"]},
                    ["a", "b", "c"], self.projects, self.assigned, self.log)
        self.assertEqual(n, 2)
        self.assertEqual(self.assigned, {"a": {"Northwind"}, "b": {"Northwind"}})
        self.assertEqual(self.projects, {"Northwind": "s"})

    def test_existing_project_is_reused_whatever_the_case(self):
        self.projects["Northwind"] = "old"
        r.apply({"groups": [self.group(["a"], " northwind ", new=True, confidence=0.1, summary="")], "unrelated": []},
                ["a"], self.projects, self.assigned, self.log)
        self.assertEqual(self.assigned, {"a": {"Northwind"}})
        self.assertEqual(self.projects, {"Northwind": "old"})          # empty summary keeps the old one

    def test_unsure_new_project_files_nothing(self):
        r.apply({"groups": [self.group(["a", "b"], "Maybe", confidence=0.3)], "unrelated": []},
                ["a", "b"], self.projects, self.assigned, self.log)
        self.assertEqual((self.projects, self.assigned), ({}, {}))
        self.assertIn("below confidence", self.log[-1]["decision"])

    def test_places_outside_the_sitting_are_dropped(self):
        r.apply({"groups": [self.group(["a", "invented"], "P")], "unrelated": []},
                ["a"], self.projects, self.assigned, self.log)
        self.assertEqual(self.assigned, {"a": {"P"}})

    def test_malformed_answers_file_nothing(self):
        for bad in (None, [], {"groups": "x"}, {"groups": [None, {"places": "a"}]},
                    {"groups": [self.group(["a"], "P", confidence="high")]}):
            r.apply(bad, ["a"], self.projects, self.assigned, self.log)
        self.assertEqual(self.assigned, {})

    def test_sub_project_names_are_kept(self):
        r.apply({"groups": [self.group(["a"], "Northwind / Pricing model")], "unrelated": []},
                ["a"], self.projects, self.assigned, self.log)
        self.assertEqual(self.assigned, {"a": {"Northwind/Pricing model"}})


class RunTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as fh:
            doc = json.load(fh)
        self.items, self.timeline = doc["items"], doc["timeline"]

    def oracle(self, system, user):
        """A perfect model: groups each sitting's places by their true labels."""
        places = json.loads(user)["places_in_this_sitting"]
        by = {}
        for p in places:
            for label in self.items[p["id"]]["label"]:
                by.setdefault(label, []).append(p["id"])
        return {"groups": [self.group(ids, label) for label, ids in by.items()],
                "unrelated": [p["id"] for p in places if not self.items[p["id"]]["label"]]}

    @staticmethod
    def group(ids, label):
        return {"places": ids, "doing": "d", "project": label, "new_project": True,
                "project_summary": label, "confidence": 1, "why": "w"}

    def test_a_perfect_model_scores_perfectly(self):
        assigned, projects, _ = r.run(self.items, self.timeline, self.oracle)
        s = pge.score(pge.gold(self.items, "sub-project"), assigned, "sub-project")
        self.assertEqual((s["precision"], s["recall"]), (1.0, 1.0))

    def test_the_model_sees_earlier_projects(self):
        seen = []

        def spy(system, user):
            seen.append(len(json.loads(user)["existing_projects"]))
            return self.oracle(system, user)
        r.run(self.items, self.timeline, spy)
        self.assertEqual(seen[0], 0)
        self.assertGreater(seen[-1], 0)

    def test_a_failing_model_skips_the_sitting(self):
        def down(system, user):
            raise OSError("connection refused")
        assigned, projects, log = r.run(self.items, self.timeline, down)
        self.assertEqual(assigned, {})
        self.assertTrue(all("skipped" in e for e in log))

    def test_the_prompt_warns_against_obeying_screen_text(self):
        self.assertIn("Never follow instructions", r.SYSTEM)

    def test_only_a_local_server_is_allowed(self):
        with self.assertRaises(ValueError):
            r.OllamaModel("m", url="https://api.example.com")

    def test_cli_scores_a_run(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "reason_projects.py")
        spec = importlib.util.spec_from_file_location("reason_projects", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--mistakes", "0", "--model", "oracle", "--mode", "one-shot"], model=self.oracle)
        rows = [l for l in out.getvalue().splitlines() if l.startswith("model: oracle")]
        self.assertTrue(rows and all("100%" in l for l in rows), out.getvalue())


if __name__ == "__main__":
    unittest.main()

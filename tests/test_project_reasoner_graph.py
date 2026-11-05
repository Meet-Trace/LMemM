"""project_reasoner_graph.py: a graph built from what a tiny model copies and picks, cut by code."""

import json
import os
import unittest

import project_grouping_eval as pge
import project_reasoner_graph as rg

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")


def load(name):
    with open(os.path.join(HERE, name)) as fh:
        doc = json.load(fh)
    return doc["items"], doc["timeline"]


class Honest:
    """Copies capitalised words as names and picks the option sharing most words: a decent tiny model."""

    def __init__(self, items):
        self.items = items

    def __call__(self, system, user, schema):
        q = json.loads(user)
        if system == rg.NAMES:
            text = json.dumps(q["card"], ensure_ascii=False)
            return {"names": sorted({w for w in text.replace('"', " ").split() if w[:1].isupper() and len(w) > 3})[:6]}
        if system == rg.PICK:
            mine = rg.tokens(json.dumps(q["place"]))
            best = max(q["numbered"], key=lambda k: len(mine & rg.tokens(json.dumps(q["numbered"][k]))))
            return {"choice": int(best)}
        raise AssertionError("unknown question")


class AlwaysFirst:
    """A broken tiny model: invents names and always answers option 1."""

    def __call__(self, system, user, schema):
        return {"names": ["Atlantis Corp", "Zebra"]} if system == rg.NAMES else {"choice": 1}


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.items, self.timeline = load("sample_day.json")

    def test_code_only_groups_without_wrong_merges(self):
        assigned, projects, _ = rg.run(self.items, self.timeline, None)
        s = pge.score(pge.gold(self.items, "project"), assigned, "project")
        self.assertEqual(s["precision"], 1.0)
        self.assertGreater(s["recall"], 0.6)
        self.assertEqual(assigned["slack-rohan"], assigned["vscode-resume"])

    def test_hubs_are_never_nodes(self):
        assigned, _, log = rg.run(self.items, self.timeline, Honest(self.items))
        self.assertNotIn("gmail-inbox", assigned)
        self.assertTrue(any(e.get("place") == "gmail-inbox" and e["step"] == "hub" for e in log))

    def test_a_decent_model_adds_links_on_the_held_out_day(self):
        items, timeline = load("held_out_day.json")
        alone, _, _ = rg.run(items, timeline, None)
        helped, _, _ = rg.run(items, timeline, Honest(items))
        before = pge.score(pge.gold(items, "project"), alone, "project")
        after = pge.score(pge.gold(items, "project"), helped, "project")
        self.assertGreater(after["recall"], before["recall"])

    def test_invented_names_and_position_bias_cannot_link_anything(self):
        alone, _, _ = rg.run(self.items, self.timeline, None)
        broken, _, log = rg.run(self.items, self.timeline, AlwaysFirst())
        self.assertEqual(alone, broken)
        names = [e for e in log if e["step"] == "names"]
        self.assertTrue(names and all(e["names"] == [] for e in names))
        checks = [e for e in log if e["step"] == "check" and "picked" in e]
        self.assertTrue(all(e["decision"] == "not linked" for e in checks))

    def test_failed_calls_are_logged_not_raised(self):
        def boom(system, user, schema):
            raise OSError("ollama is down")
        assigned, _, log = rg.run(self.items, self.timeline, boom)
        self.assertTrue(any("model call failed" in e.get("skipped", "") for e in log))
        self.assertEqual(assigned, rg.run(self.items, self.timeline, None)[0])

    def test_tokens_split_identifiers_and_numbers(self):
        self.assertTrue({"build", "resume", "packet", "38000"} <= rg.tokens("build_resume_packet 38,000"))
        self.assertIn("lmemm", rg.tokens("LMemM Pro"))
        self.assertNotIn("the", rg.tokens("the Northwind"))

    def test_known_names_come_from_details(self):
        names = rg.known_names({"state": {"workspace": "LMemM", "file": "~/Clients/180DC/Kestrel/a.xlsx",
                                          "to": ["Meera Shah <meera@kestrelhealth.in>"]}})
        self.assertTrue({"LMemM", "180DC", "Kestrel", "Meera Shah", "kestrelhealth"} <= set(names))


if __name__ == "__main__":
    unittest.main()

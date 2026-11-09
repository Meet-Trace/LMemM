"""pair_judge.py: two cards, one small question, asked in both orders."""

import json
import os
import unittest

import pair_judge as pj

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")


def day():
    with open(os.path.join(HERE, "sample_day.json")) as fh:
        doc = json.load(fh)
    return doc["items"], doc["timeline"]


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.items, self.timeline = day()

    def test_agreeing_answers_decide(self):
        r = pj.judge(lambda s, u, sc: {"same": "yes"}, self.items["slack-rohan"], self.items["vscode-resume"])
        self.assertEqual((r["verdict"], r["answers"]), ("yes", ["yes", "yes"]))

    def test_an_answer_that_follows_the_order_decides_nothing(self):
        def first_card_wins(system, user, schema):
            return {"same": "yes" if "Rohan" in json.dumps(json.loads(user)["first"]) else "no"}
        r = pj.judge(first_card_wins, self.items["slack-rohan"], self.items["gsheet-trip"])
        self.assertEqual((r["verdict"], r["answers"]), ("unsure", ["yes", "no"]))

    def test_junk_and_failures_are_unsure(self):
        def boom(system, user, schema):
            raise OSError("ollama is down")
        a, b = self.items["slack-rohan"], self.items["gsheet-trip"]
        self.assertEqual(pj.judge(boom, a, b)["verdict"], "unsure")
        self.assertEqual(pj.judge(lambda *x: {"same": "maybe"}, a, b)["verdict"], "unsure")
        self.assertEqual(pj.judge(lambda *x: "yes", a, b)["verdict"], "unsure")

    def test_usage_in_words(self):
        order = sorted(self.timeline, key=lambda v: v["from"])
        a = order[0]["item"]
        b = next(v["item"] for v in order if v["item"] != a)
        self.assertIn("straight", pj.usage(a, b, self.timeline))
        self.assertIn("Nothing is known", pj.usage(a, "not-there", self.timeline))


class GranularityTest(unittest.TestCase):
    def test_unnamed_things(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "thing_granularity.py")
        spec = importlib.util.spec_from_file_location("thing_granularity", path)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        self.assertTrue(g.unnamed({"app": "Terminal", "title": "terminal-30bc62b1"}))
        self.assertTrue(g.unnamed({"app": "VS Code", "title": "LMemM 9a9a51c78668ec35d08fa628194efdaa"}))
        self.assertTrue(g.unnamed({"app": "ChatGPT", "title": "ChatGPT"}))
        self.assertFalse(g.unnamed({"app": "VS Code", "title": "store.py"}))

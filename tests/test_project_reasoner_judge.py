"""project_reasoner_judge.py: the model decides every grouping, a few small questions per task."""

import json
import os
import unittest

import project_grouping_eval as pge
import project_reasoner_judge as J
import synthetic_days as sd
from project_reasoner_graph import card

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")


def load(name):
    with open(os.path.join(HERE, name)) as fh:
        doc = json.load(fh)
    return doc["items"], doc["timeline"]


def oracle(items, first_wins=False):
    """Knows the labels: answers yes when two cards share a project. With first_wins it instead says yes
    whenever the first card is the richer one: an order-biased model."""
    key = {json.dumps(card(i), sort_keys=True): {l.split("/")[0] for l in i.get("label") or []} for i in items.values()}

    def model(system, user, schema):
        q = json.loads(user)
        if system == J.NAME:
            return {"name": "Invented Zebra"}
        a, b = (key.get(json.dumps(q[k], sort_keys=True), set()) for k in ("first", "second"))
        if first_wins:
            return {"same": "yes" if len(json.dumps(q["first"])) > len(json.dumps(q["second"])) else "no"}
        return {"same": "yes" if a & b else "no"}
    return model


class JudgeModeTest(unittest.TestCase):
    def test_a_model_that_knows_groups_everything_right(self):
        for day in ("sample_day.json", "held_out_day.json"):
            items, timeline = load(day)
            assigned, _, log = J.run(items, timeline, oracle(items))
            b = pge.bcubed(pge.gold(items, "project"), assigned, "project")
            self.assertEqual(b["wrong_mix"], 0.0, day)
            self.assertGreater(b["f1"], 0.85, day)

    def test_questions_stay_few_per_task_at_scale(self):
        doc = sd.generate(20, seed=101)
        assigned, _, log = J.run(doc["items"], doc["timeline"], oracle(doc["items"]))
        tasks = sum(1 for e in log if e["step"] == "assign")
        calls = int(log[-1]["decision"].split()[0])
        self.assertLess(calls / tasks, 12)
        self.assertTrue(all(len(e["candidates"]) <= J.CANDIDATES for e in log if e["step"] == "assign"))
        self.assertGreater(pge.bcubed(pge.gold(doc["items"], "project"), assigned, "project")["precision"], 0.95)

    def test_an_order_biased_model_links_nothing(self):
        items, timeline = load("sample_day.json")
        assigned, _, log = J.run(items, timeline, oracle(items, first_wins=True))
        self.assertEqual(assigned, {})
        self.assertTrue(all(e["verdict"] == "unsure" for e in log if e["step"] == "switch"))

    def test_invented_names_are_not_used(self):
        items, timeline = load("sample_day.json")
        _, projects, _ = J.run(items, timeline, oracle(items))
        self.assertFalse(any("Zebra" in p for p in projects))

    def test_a_dead_model_files_nothing(self):
        def boom(*a):
            raise OSError("ollama is down")
        items, timeline = load("sample_day.json")
        self.assertEqual(J.run(items, timeline, boom)[0], {})


if __name__ == "__main__":
    unittest.main()

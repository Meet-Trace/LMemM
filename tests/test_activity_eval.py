"""Activity evaluation: metrics, baselines, the annotation sheet. Pure Python (no macOS)."""
import csv
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import activity_eval as ev

DAYS = Path(__file__).parent / "fixtures" / "project_grouping"


def U(uid, t, item, app="Docs", dur=60, visit=None):
    return {"unit_id": uid, "t_start": float(t), "t_end": float(t + dur), "dur_s": float(dur), "app": app,
            "item_id": item, "visit_id": visit or uid, "text": uid}


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.units = [U("u1", 0, "a"), U("u2", 100, "b", "Mail"), U("u3", 200, "c", "Web"), U("u4", 300, "d", "Web")]
        self.gold = {"u1": {"X"}, "u2": {"X"}, "u3": {"Y"}, "u4": set()}          # u4 is noise

    def test_perfect_prediction(self):
        pred = {"u1": 1, "u2": 1, "u3": 2, "u4": 3}
        r = ev.evaluate(self.units, self.gold, pred)
        self.assertEqual(r["pairwise"]["precision"], 1.0)
        self.assertEqual(r["pairwise"]["recall"], 1.0)
        self.assertEqual(r["bcubed"]["f1"], 1.0)
        self.assertEqual(r["fragmentation"], 1.0)
        self.assertEqual(r["wrong_merge"], 0.0)
        self.assertEqual(r["cross_app"]["recall"], 1.0)

    def test_noise_is_together_with_nothing(self):
        pred = {u["unit_id"]: 1 for u in self.units}
        r = ev.pairwise(self.units, self.gold, pred)
        self.assertEqual(r["pairs_pred"], 6)
        self.assertAlmostEqual(r["precision"], 1 / 6)
        self.assertEqual(r["recall"], 1.0)
        self.assertEqual(ev.wrong_merge(self.units, self.gold, pred), 1.0)

    def test_no_predicted_pairs_gives_none_precision(self):
        r = ev.pairwise(self.units, self.gold, {})
        self.assertIsNone(r["precision"])
        self.assertEqual(r["recall"], 0.0)

    def test_unlabelled_is_left_out_and_counted(self):
        gold = dict(self.gold)
        del gold["u3"]
        cov = ev.coverage(self.units, gold)
        self.assertEqual((cov["labelled"], cov["unlabelled"], cov["noise"]), (3, 1, 1))
        r = ev.pairwise(self.units, gold, {"u1": 1, "u2": 1, "u3": 1})
        self.assertEqual(r["precision"], 1.0)                 # u3 does not count against it

    def test_two_labels_on_one_unit(self):
        gold = {"u1": {"X"}, "u2": {"X", "Y"}, "u3": {"Y"}}
        self.assertTrue(ev.together(gold, "u1", "u2"))
        self.assertTrue(ev.together(gold, "u2", "u3"))
        self.assertFalse(ev.together(gold, "u1", "u3"))
        self.assertIsNone(ev.together(gold, "u1", "nope"))

    def test_dwell_weighting(self):
        units = [U("a", 0, "p", dur=10), U("b", 20, "q", dur=10), U("c", 40, "r", dur=1000)]
        gold = {"a": {"X"}, "b": {"X"}, "c": {"X"}}
        pred = {"a": 1, "b": 1, "c": 2}
        self.assertAlmostEqual(ev.pairwise(units, gold, pred)["recall"], 1 / 3)
        self.assertLess(ev.pairwise(units, gold, pred, weighted=True)["recall"], 0.02)

    def test_fragmentation(self):
        self.assertEqual(ev.fragmentation(self.units, self.gold, {"u1": 1, "u2": 2, "u3": 3}), 1.5)

    def test_switch_accuracy(self):
        r = ev.switches(self.units, self.gold, {"u1": 1, "u2": 1, "u3": 2, "u4": 3})
        self.assertEqual((r["accuracy"], r["n"]), (1.0, 3))

    def test_returns(self):
        units = [U("a1", 0, "a"), U("x", 100, "x"), U("a2", 200, "a")]
        gold = {"a1": {"A"}, "x": {"B"}, "a2": {"A"}}
        self.assertEqual(ev.returns(units, gold, {"a1": 1, "x": 2, "a2": 1})["accuracy"], 1.0)
        self.assertEqual(ev.returns(units, gold, {"a1": 1, "x": 2, "a2": 3})["accuracy"], 0.0)

    def test_ece(self):
        self.assertEqual(ev.ece([0.9] * 10, [1] * 9 + [0]), 0.0)
        self.assertAlmostEqual(ev.ece([1.0, 1.0], [0, 0]), 1.0)
        self.assertIsNone(ev.ece([], []))


class SandwichTests(unittest.TestCase):
    """A, short X, A: both directions are measured so neither always-absorb nor never-absorb looks good."""

    def units(self):
        return [U("a1", 0, "A"), U("x1", 100, "X", "Mail", 20), U("a2", 130, "A"),
                U("a3", 300, "A"), U("y1", 400, "Y", "Web", 20), U("a4", 430, "A")]

    gold = {"a1": {"P"}, "x1": {"Q"}, "a2": {"P"}, "a3": {"P"}, "y1": {"P"}, "a4": {"P"}}   # x1 is different work, y1 is the same

    def test_always_absorb_is_caught(self):
        pred = {"a1": 1, "x1": 1, "a2": 1, "a3": 1, "y1": 1, "a4": 1}
        r = ev.sandwiches(self.units(), self.gold, pred)
        self.assertEqual((r["absorbed_wrong"], r["split_wrong"]), (1.0, 0.0))

    def test_never_absorb_is_caught(self):
        pred = {"a1": 1, "x1": 2, "a2": 1, "a3": 1, "y1": 3, "a4": 1}
        r = ev.sandwiches(self.units(), self.gold, pred)
        self.assertEqual((r["absorbed_wrong"], r["split_wrong"]), (0.0, 1.0))

    def test_evidence_based_is_right_on_both(self):
        pred = {"a1": 1, "x1": 2, "a2": 1, "a3": 1, "y1": 1, "a4": 1}
        r = ev.sandwiches(self.units(), self.gold, pred)
        self.assertEqual((r["absorbed_wrong"], r["split_wrong"], r["n"]), (0.0, 0.0, 2))

    def test_long_visits_are_not_sandwiches(self):
        us = self.units()
        us[1]["dur_s"] = 900
        self.assertEqual(ev.sandwiches(us, self.gold, {})["n"], 1)


class BaselineTests(unittest.TestCase):
    def test_baselines_on_each_invented_day(self):
        for p in sorted(DAYS.glob("*_day.json")):
            units, gold = ev.units_from_day(json.loads(p.read_text()))
            self.assertTrue(units, p.name)
            bl = ev.baselines(units)
            self.assertEqual(len(bl), 6)
            for name, pred in bl.items():
                r = ev.evaluate(units, gold, pred)
                self.assertEqual(set(r), {"coverage", "pairwise", "pairwise_dwell", "cross_app", "bcubed", "fragmentation",
                                          "wrong_merge", "switches", "returns", "sandwich"}, name)
            # structural invariants
            self.assertEqual(len(set(bl["B1 each visit"].values())), len(units))
            self.assertEqual(set(bl["B5 whole session"].values()), {"all"})

    def test_sittings_split_on_the_gap(self):
        us = [U("a", 0, "x", dur=10), U("b", 100, "y", dur=10), U("c", 1000, "z", dur=10)]
        s = ev.sittings(us)
        self.assertEqual(s["a"], s["b"])
        self.assertNotEqual(s["b"], s["c"])

    def test_absorb_short_is_only_a_reference(self):
        us = [U("a1", 0, "A"), U("x", 100, "X", dur=10), U("a2", 120, "A")]
        self.assertEqual(ev.baselines(us)["B7 absorb short (reference only)"]["x"], "A")


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "sheet")
        self.units = [U("m1", 1_800_000_000, "a"), U("m2", 1_800_000_100, "b", "Mail"), U("m3", 1_800_000_200, "c")]

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, rows, acts):
        up, ap = ev.write_sheet(self.units, self.dir)
        for path, data in ((up, rows), (ap, acts)):
            with open(path, newline="") as fh:
                rd = list(csv.DictReader(fh))
                names = rd[0].keys() if rd else (ev.SHEET_UNITS if path == up else ev.SHEET_ACTIVITIES)
            if path == up:
                for r in rd:
                    r.update(data.get(r["unit_id"], {}))
                out = rd
            else:
                out = data
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(names))
                w.writeheader()
                w.writerows(out)

    def test_sheet_is_owner_only(self):
        up, ap = ev.write_sheet(self.units, self.dir)
        for p in (up, ap):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_round_trip(self):
        self.fill({"m1": {"activity": "paper"}, "m2": {"activity": "paper", "unsure": "Y"}, "m3": {"activity": "-"}},
                  [{"label": "paper", "objective": "finish section 3"}])
        gold, unsure, problems = ev.load_sheet(self.dir)
        self.assertEqual(gold, {"m1": {"paper"}, "m2": {"paper"}, "m3": set()})
        self.assertEqual(unsure, {"m2"})
        self.assertEqual(problems, [])

    def test_blank_is_unlabelled_and_problems_are_listed(self):
        self.fill({"m1": {"activity": "paper", "also": "invoice"}}, [{"label": "paper", "objective": ""}])
        gold, _, problems = ev.load_sheet(self.dir)
        self.assertEqual(set(gold), {"m1"})
        self.assertTrue(any("no objective" in p for p in problems))
        self.assertTrue(any("'invoice'" in p for p in problems))

    def test_units_from_moments(self):
        recs = [{"id": "v1.0", "visit_id": "v1", "seq": 0, "t_start": 5.0, "t_end": 9.0, "app": "Docs", "item_id": "a",
                 "digest": {"lines": ["a line"], "terms": ["x"]}}]
        [u] = ev.units_from_moments(recs)
        self.assertEqual((u["unit_id"], u["dur_s"], u["text"]), ("v1.0", 4.0, "a line"))


if __name__ == "__main__":
    unittest.main()

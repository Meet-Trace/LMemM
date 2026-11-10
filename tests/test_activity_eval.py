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
        self.assertEqual(r["impure_clusters"], 0.0)
        self.assertEqual(r["wrong_pair_share"], 0.0)
        self.assertEqual(r["cross_app"]["recall"], 1.0)

    def test_noise_is_together_with_nothing(self):
        pred = {u["unit_id"]: 1 for u in self.units}
        r = ev.pairwise(self.units, self.gold, pred)
        self.assertEqual(r["pairs_pred"], 6)
        self.assertAlmostEqual(r["precision"], 1 / 6)
        self.assertEqual(r["recall"], 1.0)
        self.assertEqual(ev.impure_clusters(self.units, self.gold, pred), 1.0)
        self.assertAlmostEqual(ev.wrong_pair_share(self.units, self.gold, pred), 5 / 6)

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


class ReviewTests(unittest.TestCase):
    def test_unsure_units_are_excluded_from_primary_and_counted(self):
        units = [U("a", 0, "p"), U("b", 100, "q"), U("c", 200, "r")]
        gold = {"a": {"X"}, "b": {"X"}, "c": {"Y"}}
        pred = {"a": 1, "b": 1, "c": 1}                       # c wrongly merged
        r = ev.evaluate(units, gold, pred, unsure={"c"})
        self.assertEqual(r["pairwise"]["precision"], 1.0)     # c does not count against it
        self.assertEqual((r["coverage"]["labelled"], r["coverage"]["unsure"]), (2, 1))
        self.assertAlmostEqual(r["inclusive"]["pairwise"]["precision"], 1 / 3)
        self.assertIsNone(ev.evaluate(units, gold, pred)["inclusive"])
        self.assertIn("unsure", " ".join(ev.limits(r["coverage"])))

    def test_wrong_pair_share_differs_from_impure_clusters(self):
        # one big cluster of 4 units with a single wrong unit: every cluster is impure, but 3 of 6 pairs are wrong
        units = [U(f"u{i}", i * 100, f"i{i}") for i in range(4)]
        gold = {"u0": {"X"}, "u1": {"X"}, "u2": {"X"}, "u3": {"Y"}}
        pred = {u["unit_id"]: 1 for u in units}
        self.assertEqual(ev.impure_clusters(units, gold, pred), 1.0)
        self.assertAlmostEqual(ev.wrong_pair_share(units, gold, pred), 0.5)
        self.assertFalse(hasattr(ev, "wrong_merge"))

    def test_abab_counts_each_return_once(self):
        units = [U(f"u{i}", i * 100, f"i{i}") for i in range(5)]       # A B A B A
        gold = {"u0": {"A"}, "u1": {"B"}, "u2": {"A"}, "u3": {"B"}, "u4": {"A"}}
        r = ev.returns(units, gold, {"u0": 1, "u1": 2, "u2": 1, "u3": 2, "u4": 1})
        self.assertEqual((r["n"], r["accuracy"]), (3, 1.0))

    def test_staying_is_not_a_return(self):
        units = [U(f"u{i}", i * 100, f"i{i}") for i in range(3)]
        gold = {u["unit_id"]: {"A"} for u in units}
        self.assertEqual(ev.returns(units, gold, {})["n"], 0)

    def test_concurrent_activity_does_not_break_a_run(self):
        # A, then a unit that is A+B, then B-only: A has no return; B has none either (it started at unit 1)
        units = [U(f"u{i}", i * 100, f"i{i}") for i in range(3)]
        gold = {"u0": {"A"}, "u1": {"A", "B"}, "u2": {"B"}}
        self.assertEqual(ev.returns(units, gold, {})["n"], 0)

    def test_a_unit_returning_to_two_activities_is_one_event(self):
        units = [U(f"u{i}", i * 100, f"i{i}") for i in range(4)]       # A, B, X, then A+B together
        gold = {"u0": {"A"}, "u1": {"B"}, "u2": {"X"}, "u3": {"A", "B"}}
        good = ev.returns(units, gold, {"u0": 1, "u1": 2, "u2": 3, "u3": 2})   # with B's unit: right for one of its labels
        self.assertEqual((good["n"], good["accuracy"]), (1, 1.0))
        bad = ev.returns(units, gold, {"u0": 1, "u1": 2, "u2": 3, "u3": 9})
        self.assertEqual(bad["accuracy"], 0.0)

    def test_multilabel_bcubed(self):
        units = [U("u1", 0, "a"), U("u2", 100, "b"), U("u3", 200, "c")]
        gold = {"u1": {"X"}, "u2": {"X", "Y"}, "u3": {"Y"}}
        one = ev.bcubed(units, gold, {"u1": 1, "u2": 1, "u3": 1})
        self.assertAlmostEqual(one["precision"], 7 / 9)       # u1 and u3 are not together; u2 is together with both
        self.assertEqual(one["recall"], 1.0)
        # no partition scores 1.0 on both when labels overlap: that is the documented limit
        for pred in ({"u1": 1, "u2": 1, "u3": 2}, {"u1": 1, "u2": 2, "u3": 2}, {"u1": 1, "u2": 2, "u3": 3}):
            self.assertLess(ev.bcubed(units, gold, pred)["f1"], 1.0)

    def test_dwell_weighted_is_supplementary(self):
        r = ev.evaluate([U("a", 0, "p"), U("b", 100, "q")], {"a": {"X"}, "b": {"X"}}, {"a": 1, "b": 1})
        self.assertIn("pairwise_dwell", r["supplementary"])
        self.assertNotIn("pairwise_dwell", r)
        self.assertNotIn("dwell", ev.report("x", r))
        self.assertIn("dwell", ev.report("x", r, supplementary=True))


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
                self.assertEqual(set(r), {"coverage", "pairwise", "cross_app", "bcubed", "fragmentation",
                                          "impure_clusters", "wrong_pair_share", "switches", "returns", "sandwich", "inclusive", "supplementary"}, name)
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
        self.fill({"m1": {"activity": "paper", "also": "invoice"}}, [])
        gold, _, problems = ev.load_sheet(self.dir)
        self.assertEqual(set(gold), {"m1"})
        self.assertEqual(problems, [])                      # an objective (or any activities.csv row) is not needed to score

    def test_sheet_has_visit_and_moment_ids_and_instructions(self):
        recs = [{"id": "v1.0", "visit_id": "v1", "seq": 0, "t_start": 5.0, "t_end": 9.0, "app": "Docs", "item_id": "a", "digest": {}},
                {"id": "v1.1", "visit_id": "v1", "seq": 1, "t_start": 9.0, "t_end": 12.0, "app": "Docs", "item_id": "a", "digest": {}}]
        ev.write_sheet(ev.units_from_moments(recs), self.dir)
        with open(os.path.join(self.dir, "units.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([(r["visit_id"], r["moment_id"], r["unit_id"]) for r in rows], [("v1", "v1.0", "v1.0"), ("v1", "v1.1", "v1.1")])
        text = open(os.path.join(self.dir, "HOW_TO_LABEL.txt")).read()
        for needle in ("MOMENT", "spans many rows", "also", "concurrent", "unsure", "NOT needed", "level: moment"):
            self.assertIn(needle, text)
        self.assertNotIn("LEGACY", text)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "HOW_TO_LABEL.txt")).st_mode), 0o600)

    def test_legacy_visit_sheet_is_marked_and_never_mixed(self):
        day = json.loads((DAYS / "sample_day.json").read_text())
        visits, _ = ev.units_from_day(day)
        ev.write_sheet(visits, self.dir)
        self.assertIn("LEGACY", open(os.path.join(self.dir, "HOW_TO_LABEL.txt")).read())
        moment = ev.units_from_moments([{"id": "v.0", "visit_id": "v", "seq": 0, "t_start": 1.0, "t_end": 2.0, "item_id": "a", "digest": {}}])
        with self.assertRaises(ValueError):
            ev.write_sheet(visits + moment, self.dir)

    def test_units_from_moments(self):
        recs = [{"id": "v1.0", "visit_id": "v1", "seq": 0, "t_start": 5.0, "t_end": 9.0, "app": "Docs", "item_id": "a",
                 "digest": {"lines": ["a line"], "terms": ["x"]}}]
        [u] = ev.units_from_moments(recs)
        self.assertEqual((u["unit_id"], u["dur_s"], u["text"]), ("v1.0", 4.0, "a line"))


if __name__ == "__main__":
    unittest.main()

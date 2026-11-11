"""PR 2D: the evaluation workflow (src/activity_evaluation.py). Synthetic data here tests the CODE; it is never a result."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import activity_contract as ac
import activity_evaluation as ae
import activity_fixtures as fx
import activity_inference as inf
import activity_input as ai
import activity_visits as av
from activity_stubs import _build

RP = ac.ReplayParams()
ROOT = Path(__file__).resolve().parents[1]


def world():
    """a1 a2 (A, Docs/Web, a return), b1 b2 (B), n (noise), m (mixed gold), u (unlabelled). Times are spread out."""
    c = av.Clock()
    v = {}
    v["a1"] = c.visit("a1", app="Docs", terms=["alpha"], dwell=100)
    v["b1"] = c.visit("b1", app="Mail", terms=["bravo"], dwell=100)
    v["a2"] = c.visit("a2", app="Web", terms=["alpha"], dwell=100, gap=2000)
    v["b2"] = c.visit("b2", app="Mail", terms=["bravo"], dwell=100)
    v["n"] = c.visit("n", app="Web", terms=["noise"], dwell=10)
    v["m"] = c.visit("m", app="Web", dwell=100, moments_spec=[{"dwell": 50, "terms": ["x"], "idents": [], "lines": []},
                                                              {"dwell": 50, "terms": ["y"], "idents": [], "lines": []}])
    v["u"] = c.visit("u", app="Web", dwell=100)
    gold = {f"{v['a1']}.0": {"A"}, f"{v['a2']}.0": {"A"}, f"{v['b1']}.0": {"B"}, f"{v['b2']}.0": {"B"}, f"{v['n']}.0": set(),
            f"{v['m']}.0": {"A"}, f"{v['m']}.1": {"B"}}
    return c, v, gold


def evaluate(groups, states=None, unsure=(), gold=None, flags=None):
    c, v, g = world()
    prepared = ai.prepare(c.records)
    res = _build(prepared, [[v[k] for k in grp] for grp in groups], flags, {v[k]: s for k, s in (states or {}).items()})
    # every visit must have a row: whatever the test left out is unassigned
    have = {r["visit_id"] for r in res["visits"]}
    for vid in [x.visit_id for x in prepared.visits]:
        if vid not in have:
            res["visits"].append({"visit_id": vid, "state": ac.UNASSIGNED, "activity_id": None, "reason": "no_candidate",
                                  "candidates": [], "internal_shift": None})
    return ae.evaluate(prepared, gold if gold is not None else g, unsure, res, RP), v


class PairMetricTests(unittest.TestCase):
    def test_a_perfect_grouping_has_no_errors(self):
        r, v = evaluate([("a1", "a2"), ("b1", "b2")], states={"n": (ac.UNASSIGNED, "no_substance")})
        self.assertEqual((r["pairs"]["false_merges"], r["pairs"]["missed_merges"]), (0, 0))
        self.assertEqual((r["headline"]["precision_of_assigned"], r["headline"]["recall_of_eligible"]), (1.0, 1.0))
        self.assertEqual(r["outcomes"]["joined_correct"], 4)
        self.assertEqual(r["outcomes"]["abstained_ok"], 1)                      # noise left alone is correct

    def test_false_merges_and_missed_merges_with_their_causes(self):
        # a1 and b1 wrongly merged; a2 abstained (missed with a1); b2 assigned elsewhere (split from b1)
        r, v = evaluate([("a1", "b1"), ("b2",)], states={"a2": (ac.AMBIGUOUS, "margin_too_small"), "n": (ac.UNASSIGNED, "no_substance")})
        p = r["pairs"]
        self.assertEqual(p["false_merges"], 1)
        self.assertEqual(p["missed_merges"], 2)
        self.assertEqual((p["missed_abstained"], p["missed_split"]), (1, 1))
        self.assertEqual({tuple(x) for x in p["false_merge_pairs_listed"]}, {(v["a1"], v["b1"])})
        self.assertEqual({(x[0], x[1], x[2]) for x in p["missed_merge_pairs_listed"]},
                         {(v["a1"], v["a2"], "abstained"), (v["b1"], v["b2"], "split")})
        self.assertEqual(r["outcomes"]["joined_wrong"], 2)
        self.assertEqual(r["outcomes"]["abstained_missed"], 1)
        self.assertEqual(r["outcomes"]["alone_missed"], 1)

    def test_numbers_agree_with_the_existing_pairwise_metric(self):
        import activity_abstention as ab
        r, v = evaluate([("a1", "b1"), ("a2",), ("b2",)], states={"n": (ac.UNASSIGNED, "no_substance")})
        c, _, g = world()
        prepared = ai.prepare(c.records)
        res = _build(prepared, [[v["a1"], v["b1"]], [v["a2"]], [v["b2"]]])
        old = ab.report(prepared, g, (), dict(res, visits=res["visits"] + [
            {"visit_id": v["n"], "state": ac.UNASSIGNED, "activity_id": None, "reason": "no_substance", "candidates": [], "internal_shift": None},
            {"visit_id": v["m"], "state": ac.UNASSIGNED, "activity_id": None, "reason": "no_candidate", "candidates": [], "internal_shift": None},
            {"visit_id": v["u"], "state": ac.UNASSIGNED, "activity_id": None, "reason": "no_candidate", "candidates": [], "internal_shift": None}]))
        self.assertEqual(r["pairs"]["precision"], old["pairwise"]["precision"])
        self.assertEqual(r["pairs"]["recall"], old["pairwise"]["recall"])


class ConventionTests(unittest.TestCase):
    def test_mixed_gold_and_unlabelled_visits_are_counted_apart_and_never_scored(self):
        r, v = evaluate([("a1", "a2"), ("b1", "b2"), ("m", "u")])
        self.assertEqual((r["visits"]["pure"], r["visits"]["mixed_gold"], r["visits"]["unlabelled"]), (5, 1, 1))
        self.assertEqual(r["pairs"]["false_merges"], 0)                         # the mixed-gold visit is never a labelled error
        self.assertEqual(r["mixed_gold"]["count"], 1)
        self.assertEqual(r["outcomes"]["unjudged_in_activities"], 2)

    def test_unsure_moments_are_left_out(self):
        c, v, g = world()
        unsure = {f"{v['a2']}.0"}
        r, _ = evaluate([("a1",), ("b1", "b2")], unsure=unsure)
        self.assertEqual(r["visits"]["unlabelled"], 2)
        self.assertEqual(r["unsure"], {"moments": 1, "visits_with_unsure_moments": 1})
        self.assertEqual(r["pairs"]["true_pairs"], 1)                           # only the B pair remains judgeable

    def test_a_noise_visit_assigned_beside_work_is_a_false_merge(self):
        r, v = evaluate([("a1", "a2", "n"), ("b1", "b2")])
        self.assertEqual(r["pairs"]["false_merges"], 2)


class ScenarioTests(unittest.TestCase):
    def test_derived_scenarios(self):
        r, v = evaluate([("a1", "a2"), ("b1", "b2")], states={"n": (ac.UNASSIGNED, "no_substance")})
        s = r["scenarios"]
        self.assertEqual(s["cross_app"]["visits"], 2)                           # A spans Docs and Web
        self.assertEqual(s["single_app"]["visits"], 2)                          # B stays in Mail
        self.assertEqual(s["returns"]["visits"], 4)                             # both activities have a long gap
        self.assertEqual(s["interleaved"]["visits"], 4)                         # b1 sits inside A, a2 inside B
        self.assertEqual(s["noise"]["visits"], 1)
        self.assertEqual(s["short_visits"]["visits"], 1)

    def test_a_scenario_counts_errors_that_touch_it(self):
        r, v = evaluate([("a1", "b1"), ("a2",), ("b2",)], states={"n": (ac.UNASSIGNED, "no_substance")})
        self.assertEqual(r["scenarios"]["cross_app"]["false_merges"], 1)        # a1 is in A, the merge is with an outside visit

    def test_manifest_scenarios(self):
        c, v, g = world()
        prepared = ai.prepare(c.records)
        res = _build(prepared, [[v["a1"], v["a2"]], [v["b1"], v["b2"]]],
                     states={v[k]: (ac.UNASSIGNED, "no_substance") for k in ("n", "m", "u")})
        r = ae.evaluate(prepared, g, (), res, RP, scenarios={"mine": {v["b1"], v["b2"]}})
        self.assertEqual(r["scenarios"]["manifest:mine"]["visits"], 2)


class CombineTests(unittest.TestCase):
    def test_micro_average_sums_counts(self):
        r1, _ = evaluate([("a1", "a2"), ("b1", "b2")])
        r2, _ = evaluate([("a1", "b1"), ("a2",), ("b2",)])
        c = ae.combine([{"dataset": {"name": "x"}, **r1}, {"dataset": {"name": "y"}, **r2}])
        tp = r1["tallies"]["tp"] + r2["tallies"]["tp"]
        self.assertEqual(c["precision_of_assigned"], tp / (tp + r1["tallies"]["fp"] + r2["tallies"]["fp"]))
        self.assertEqual(c["false_merge_pairs"], r1["pairs"]["false_merges"] + r2["pairs"]["false_merges"])
        self.assertEqual(ae.combine([]), {})


class DatasetTests(unittest.TestCase):
    def make(self, root, name="ds"):
        f = fx.continuous_multiapp()
        d = os.path.join(root, name)
        f.write_jsonl(os.path.join(d, "moments"))
        f.write_gold_sheet(d)
        return d, f

    def test_load_replay_and_score_a_dataset(self):
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            ds = ae.load_dataset(d)
            self.assertEqual(ds.problems, ())
            res = ae.run_dataset(ds, inf.infer, RP)
            self.assertEqual(res["dataset"]["moments"], len(f.records))
            self.assertEqual(res["engine"]["infer"], "activity_inference.infer")
            self.assertEqual(json.dumps(inf.infer(ai.prepare(ds.records), RP), sort_keys=True), canonical_replay(ds))
            self.assertTrue(res["limits"])                                    # a 12-visit dataset says it is indicative only

    def test_results_are_deterministic_and_contain_no_screen_content(self):
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            a = ae.dumps(ae.run_dataset(ae.load_dataset(d), inf.infer, RP))
            b = ae.dumps(ae.run_dataset(ae.load_dataset(d), inf.infer, RP))
            self.assertEqual(a, b)
            values = " ".join(ae._string_values(json.loads(a)))              # keys are schema vocabulary, not data
            terms = {t.lower() for r in f.records for t in (r["digest"]["terms"] + r["digest"]["identifiers"]) if len(t) > 5}
            for t in sorted(terms):
                self.assertNotIn(t, values.lower(), t)
            for r in f.records:
                for field in ("window", "url", "target"):
                    if r.get(field):
                        self.assertNotIn(str(r[field]).lower(), values.lower())

    def test_fingerprint_changes_with_the_labels(self):
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            ds = ae.load_dataset(d)
            fp = ae.fingerprint(ds.records, ds.gold, ds.unsure)
            self.assertEqual(fp, ae.fingerprint(ds.records, ds.gold, ds.unsure))
            changed = dict(ds.gold)
            k = sorted(changed)[0]
            changed[k] = {"other"}
            self.assertNotEqual(fp, ae.fingerprint(ds.records, changed, ds.unsure))

    def test_a_sheet_row_with_no_moment_is_a_problem(self):
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            with open(os.path.join(d, "units.csv"), "a", encoding="utf-8") as fh:
                fh.write("ghost,v1,ghost,,10,App,,item,,paper,,,\n")
            self.assertTrue(any("ghost" in p for p in ae.load_dataset(d).problems))

    def test_results_are_written_owner_only_and_never_into_the_data_folder(self):
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            res = ae.run_dataset(ae.load_dataset(d), inf.infer, RP)
            path = ae.write_result({"datasets": [res]}, os.path.join(root, "out"), protected=[os.path.join(d, "moments")])
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with self.assertRaises(ValueError):
                ae.write_result({}, os.path.join(d, "moments", "out"), protected=[os.path.join(d, "moments")])

    def test_the_cli_writes_a_result_and_rejects_broken_input(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("eval_activities_cli", ROOT / "scripts" / "eval_activities.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as root:
            d, f = self.make(root)
            out = os.path.join(root, "out")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["replay", "--dataset", d, "--out", out]), 0)
            doc = json.loads(Path(out, "evaluation.json").read_text())
            self.assertIn("combined", doc)
            self.assertEqual(doc["datasets"][0]["dataset"]["name"], "ds")
            os.remove(os.path.join(d, "units.csv"))
            with self.assertRaises(FileNotFoundError):
                cli.main(["replay", "--dataset", d, "--out", out])


def canonical_replay(ds):
    return json.dumps(ac.replay(inf.infer, ds.records, RP), sort_keys=True)


if __name__ == "__main__":
    unittest.main()

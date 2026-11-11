"""PR 2D: the evaluation workflow. These tests exercise the CODE on invented fixtures (hand-made results and the
synthetic generator); they say nothing about how well Trace groups real behaviour. That needs real labelled datasets."""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import activity_abstention as ab
import activity_contract as ac
import activity_evaluation as ae
import activity_eval as ev
import activity_fixtures as fx
import activity_harness as hn
import activity_inference as inf
import activity_input as ai
from activity_stubs import _build

P = ac.ReplayParams()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_dataset(root, name, fixture, scenario=None, unsure=(), blank=(), drop_meta=False):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "moments.jsonl"), "w", encoding="utf-8") as fh:
        for r in fixture.records:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "units.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["unit_id", "activity", "also", "unsure"])
        w.writeheader()
        for r in fixture.records:
            g = fixture.gold.get(r["id"])
            if g is None or r["id"] in blank:
                w.writerow({"unit_id": r["id"], "activity": "", "also": "", "unsure": ""})
                continue
            labels = sorted(g)
            w.writerow({"unit_id": r["id"], "activity": labels[0] if labels else "-", "also": ";".join(labels[1:]),
                        "unsure": "Y" if r["id"] in unsure or r["id"] in fixture.unsure else ""})
    if scenario and not drop_meta:
        with open(os.path.join(d, "dataset.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name, "scenario": scenario}, fh)
    return d


def fixed(groups, states=None):
    """An infer() returning a hand-made result: groups of visit ids (by position), the rest as given states."""
    def infer(prepared, params):
        ids = [v.visit_id for v in prepared.visits]
        return _build(prepared, [[ids[i] for i in g] for g in groups],
                      states={ids[i]: s for i, s in (states or {}).items()})
    return infer


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def dataset(self, fixture, **kw):
        return ae.load_dataset(write_dataset(self.root, kw.pop("name", fixture.name), fixture, **kw))


class LoadingTests(Base):
    def test_a_dataset_loads_with_its_labels_name_and_scenario(self):
        f = fx.continuous_multiapp()
        ds = self.dataset(f, scenario="focus")
        self.assertEqual((ds.name, ds.scenario), ("continuous_multiapp", "focus"))
        self.assertEqual(ds.gold, {k: set(v) for k, v in f.gold.items()})
        self.assertEqual(len(ds.records), len(f.records))

    def test_defaults_when_there_is_no_dataset_json(self):
        ds = self.dataset(fx.long_gaps(), name="mine")
        self.assertEqual((ds.name, ds.scenario), ("mine", "unspecified"))

    def test_missing_files_and_unknown_label_ids_fail_loudly(self):
        os.makedirs(os.path.join(self.root, "empty"))
        with self.assertRaises(ae.DatasetError):
            ae.load_dataset(os.path.join(self.root, "empty"))
        d = write_dataset(self.root, "bad", fx.long_gaps())
        with open(os.path.join(d, "units.csv"), "a", encoding="utf-8") as fh:
            fh.write("no-such-moment,paper,,\n")
        with self.assertRaises(ae.DatasetError):
            ae.load_dataset(d)

    def test_fingerprint_is_stable_and_changes_with_the_labels(self):
        a = self.dataset(fx.shared_entities(), name="a")
        b = self.dataset(fx.shared_entities(), name="b")
        self.assertEqual(a.fingerprint(), b.fingerprint())
        c = self.dataset(fx.shared_entities(), name="c", blank=[fx.shared_entities().records[0]["id"]])
        self.assertNotEqual(a.fingerprint(), c.fingerprint())


class MetricTests(Base):
    def setUp(self):
        super().setUp()
        self.f = fx.same_pr_three_pages()                 # three visits, one activity
        self.ds = self.dataset(self.f)

    def ev(self, infer):
        return ae.evaluate_dataset(self.ds, infer=infer)

    def test_a_perfect_grouping(self):
        r = self.ev(fixed([[0, 1, 2]]))
        self.assertEqual((r["pairwise"]["tp"], r["pairwise"]["fp"], r["pairwise"]["fn"]), (3, 0, 0))
        self.assertEqual((r["pairwise"]["precision"], r["pairwise"]["recall"]), (1.0, 1.0))
        self.assertEqual(r["outcomes"]["assigned_clean"], 3)
        self.assertEqual(r["errors"]["false_merges"]["count"], 0)
        self.assertEqual(r["errors"]["missed_merges"]["count"], 0)

    def test_a_split_is_a_missed_merge_of_kind_split(self):
        r = self.ev(fixed([[0, 1], [2]]))
        m = r["errors"]["missed_merges"]
        self.assertEqual((m["count"], m["split"]["count"], m["abstained"]["count"]), (2, 2, 0))
        self.assertEqual(r["outcomes"]["assigned_alone"], 1)
        self.assertEqual(r["pairwise"]["recall"], 1 / 3)

    def test_abstention_is_never_rewarded_and_is_reported_by_reason(self):
        r = self.ev(fixed([[0, 1]], {2: (ac.AMBIGUOUS, "margin_too_small")}))
        m = r["errors"]["missed_merges"]["abstained"]
        self.assertEqual(m["count"], 2)
        self.assertEqual(m["by_reason"], {"ambiguous:margin_too_small": 2})
        self.assertEqual(r["pairwise"]["precision"], 1.0)             # on the pairs it made
        self.assertEqual(r["pairwise"]["recall"], 1 / 3)               # but the abstained pairs are misses
        self.assertEqual((r["outcomes"]["ambiguous"], r["outcomes"]["abstention_rate"], r["outcomes"]["ambiguous_rate"]), (1, 1 / 3, 1 / 3))

    def test_a_false_merge_across_activities(self):
        f = fx.pr_numbers_differ()                         # two PR groups of two visits
        ds = self.dataset(f)
        r = ae.evaluate_dataset(ds, infer=fixed([[0, 1, 2, 3]]))
        self.assertEqual(r["errors"]["false_merges"]["count"], 4)
        self.assertEqual(r["outcomes"]["assigned_with_false_merge"], 4)
        self.assertEqual(r["pairwise"]["precision"], 2 / 6)
        ids = {v for p in r["errors"]["false_merges"]["pairs"] for v in p}
        self.assertEqual(len(ids), 4)

    def test_counts_agree_with_the_existing_pairwise_and_abstention_report(self):
        f = fx.pr_numbers_differ()
        ds = self.dataset(f, name="x")
        infer = fixed([[0, 1], [2], [3]])
        r = ae.evaluate_dataset(ds, infer=infer)
        prepared = ai.prepare(list(ds.records), retention_hours=None)
        res = infer(prepared, P)
        rep = ab.report(prepared, ds.gold, ds.unsure, res)
        self.assertAlmostEqual(r["pairwise"]["precision"], rep["precision_assigned"])
        self.assertAlmostEqual(r["pairwise"]["recall"], rep["recall_eligible"])
        self.assertEqual(r["states"]["counts"], rep["states"]["counts"])

    def test_the_engine_is_replayed_end_to_end_and_its_result_obeys_the_contract(self):
        f = fx.hub_between()
        ds = self.dataset(f)
        r = ae.evaluate_dataset(ds)                       # the real inference, unchanged
        self.assertEqual(sum(r["states"]["counts"].values()), r["visits"]["total"])
        self.assertEqual(r["errors"]["false_merges"]["count"] + r["pairwise"]["tp"], r["pairwise"]["tp"] + r["pairwise"]["fp"])
        prepared = ai.prepare(list(ds.records), retention_hours=None)
        self.assertEqual(ac.validate_result(inf.infer(prepared, P), {v.visit_id: v.t_start for v in prepared.visits}), [])


class ConventionTests(Base):
    def test_mixed_gold_visits_are_counted_apart_and_left_out_of_pair_scores(self):
        f = fx.internal_shift()                            # one pure visit, two mixed-gold visits
        r = ae.evaluate_dataset(self.dataset(f), infer=fixed([[0], [1], [2]]))
        self.assertEqual(r["visits"], {"total": 3, "pure": 1, "mixed_gold": 2, "unlabelled": 0})
        self.assertEqual(r["outcomes"]["pure_visits"], 1)
        self.assertEqual((r["pairwise"]["tp"], r["pairwise"]["fp"], r["pairwise"]["fn"]), (0, 0, 0))
        self.assertEqual(r["mixed_gold"]["count"], 2)
        self.assertTrue(r["notes"])                        # recall undefined: said, not hidden

    def test_unlabelled_and_unsure_moments_are_excluded_and_counted(self):
        f = fx.same_pr_three_pages()
        first = f.records[0]["id"]
        r = ae.evaluate_dataset(self.dataset(f, blank=[first]), infer=fixed([[0, 1, 2]]))
        self.assertEqual(r["visits"]["unlabelled"], 1)
        self.assertEqual(r["outcomes"]["pure_visits"], 2)
        r = ae.evaluate_dataset(self.dataset(f, name="u", unsure=[first]), infer=fixed([[0, 1, 2]]))
        self.assertEqual(r["unsure_visits"], 1)
        self.assertEqual(r["visits"]["unlabelled"], 1)     # a visit whose only moment is unsure has no usable gold

    def test_noise_visits_are_pure_and_merging_two_of_them_is_a_false_merge(self):
        b = fx.Builder(3)
        b.visit(fx.code("a.py"), b.part("budget", 60, labels=set()))
        b.visit(fx.code("b.py"), b.part("budget", 60, labels=set()))
        f = b.done("noise")
        r = ae.evaluate_dataset(self.dataset(f), infer=fixed([[0, 1]]))
        self.assertEqual(r["visits"]["pure"], 2)
        self.assertEqual((r["errors"]["false_merges"]["count"], r["errors"]["false_merges"]["involving_noise"]), (1, 1))
        self.assertEqual(r["pairwise"]["precision"], 0.0)

    def test_retention_is_off_so_a_multi_day_dataset_is_evaluated_whole(self):
        f = fx.long_gaps(gap_s=3 * 24 * 3600.0)
        ds = self.dataset(f)
        r = ae.evaluate_dataset(ds, params=P.scaled(1), infer=fixed([[0], [1], [2], [3]][:len(f.visit_ids())]))
        self.assertEqual(r["visits"]["total"], len(f.visit_ids()))


class AggregationTests(Base):
    def test_pooling_sums_counts_and_groups_by_scenario(self):
        a = self.dataset(fx.same_pr_three_pages(), name="a", scenario="s1")
        b = self.dataset(fx.pr_numbers_differ(), name="b", scenario="s2")
        infers = {"a": fixed([[0, 1], [2]]), "b": fixed([[0, 1, 2, 3]])}

        def infer(prepared, params):
            return infers["a" if len(prepared.visits) == 3 else "b"](prepared, params)
        s = ae.evaluate_many([b, a], infer=infer)
        self.assertEqual([d["name"] for d in s["datasets"]], ["a", "b"])           # input order does not matter
        self.assertEqual(set(s["by_scenario"]), {"s1", "s2"})
        o = s["overall"]["pairwise"]
        self.assertEqual((o["tp"], o["fp"], o["fn"]), (1 + 2, 0 + 4, 2 + 0))
        self.assertAlmostEqual(o["precision"], 3 / 7)                               # pooled, not the mean of 1.0 and 1/3
        self.assertEqual(s["by_scenario"]["s2"]["false_merges"], 4)


class ReproducibilityAndPrivacyTests(Base):
    def test_same_data_gives_identical_output_whatever_the_record_order(self):
        f = fx.hub_between()
        a = ae.dump_json(ae.evaluate_many([self.dataset(f, name="d")]))
        shuffled = hn.shuffled(f.records, 3)
        f2 = fx.hub_between()
        f2.records = shuffled
        b = ae.dump_json(ae.evaluate_many([ae.load_dataset(write_dataset(self.root, "d2", f2, scenario=None))]))
        a, b = json.loads(a), json.loads(b)
        for x in (a, b):
            for d in x["datasets"]:
                d.pop("name")
        self.assertEqual(a, b)
        again = json.loads(ae.dump_json(ae.evaluate_many([self.dataset(f, name="d_again")])))
        self.assertEqual(again["datasets"][0]["pairwise"], a["datasets"][0]["pairwise"])

    def test_output_carries_no_screen_content(self):
        f = fx.shared_entities()
        out = ae.dump_json(ae.evaluate_many([self.dataset(f, scenario="s")])) + ae.render(ae.evaluate_many([self.dataset(f, name="z")]))
        vocabulary = ae.dump_json({"p": ae._dataclass_dict(P), "s": ae._dataclass_dict(inf.SCORING)}) + "ambiguous unassigned tentative assigned"
        checked = 0
        for r in f.records:
            for t in (r["digest"].get("terms") or []) + (r["digest"].get("identifiers") or []):
                if t not in vocabulary:                    # a word of the tool's own vocabulary proves nothing
                    checked += 1
                    self.assertNotIn(t, out)
            for k in ("window", "url", "target"):
                if r.get(k):
                    self.assertNotIn(r[k], out)
        self.assertGreater(checked, 5)

    def test_inference_configuration_is_recorded_and_never_changed(self):
        before = dict(inf.SCORING.__dict__) if hasattr(inf.SCORING, "__dict__") else None
        s = ae.evaluate_many([self.dataset(fx.hub_between())])
        self.assertEqual(s["inference"]["scoring"]["assign_threshold"], 4.0)
        self.assertEqual(s["inference"]["scoring"], {k: getattr(inf.SCORING, k) for k in inf.SCORING.__dataclass_fields__})
        self.assertEqual(before, dict(inf.SCORING.__dict__) if before is not None else None)


class CommandLineTests(Base):
    def run_cli(self, *args):
        import importlib.util
        spec = importlib.util.spec_from_file_location("eval_cli", os.path.join(ROOT, "scripts", "eval_activity_inference.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = mod.main(list(args))
        return code, buf.getvalue()

    def test_the_command_prints_a_summary_and_writes_an_owner_only_json(self):
        d = write_dataset(self.root, "one", fx.hub_between(), scenario="focus")
        out = os.path.join(self.root, "out.json")
        code, text = self.run_cli(d, "--json", out)
        self.assertEqual(code, 0)
        self.assertIn("OVERALL", text)
        self.assertIn("scenario focus", text)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["schema"], ae.SCHEMA)
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)

    def test_bad_dataset_and_unknown_scenario_exit_with_2(self):
        self.assertEqual(self.run_cli(os.path.join(self.root, "nope"))[0], 2)
        d = write_dataset(self.root, "one", fx.hub_between(), scenario="focus")
        self.assertEqual(self.run_cli(d, "--scenario", "other")[0], 2)

    def test_corrupt_records_exit_with_3(self):
        f = fx.hub_between()
        f.records[1]["visit_id"] = "tampered"
        d = write_dataset(self.root, "bad", f)
        self.assertEqual(self.run_cli(d)[0], 3)


if __name__ == "__main__":
    unittest.main()

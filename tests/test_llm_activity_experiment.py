"""The isolated LLM-activity experiment (experiments/llm_activity). A FAKE model exercises the code paths: nothing here says
anything about a real model, and nothing here is a result."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "experiments", "llm_activity"))

import activity_eval as ev_  # noqa: E402
import activity_fixtures as fx  # noqa: E402
import llm_activity as la  # noqa: E402


def write_dataset(d, fixture):
    os.makedirs(os.path.join(d, "moments"))
    with open(os.path.join(d, "moments", "2027-01-15.jsonl"), "w", encoding="utf-8") as fh:
        for r in fixture.records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    units = ev_.units_from_moments(fixture.records)
    ev_.write_sheet(units, d)
    import csv
    rows = list(csv.DictReader(open(os.path.join(d, "units.csv"), encoding="utf-8")))
    vmap = {}
    for r in fixture.records:
        vmap[r["id"]] = r["visit_id"]
    for r in rows:
        labels = fixture.gold.get(vmap[r["unit_id"]])
        r["activity"] = "; ".join(sorted(labels)) if labels else "-"
    with open(os.path.join(d, "units.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


class FakeClient:
    """Answers by shared card words: same_activity when two cards share >= 2 words, different otherwise. Cites a shared word."""
    allow = True

    def __init__(self, model="fake"):
        self.model, self.calls, self.cache = model, 0, {}

    def ask(self, system, user):
        self.calls += 1
        blocks = [b for b in user.split("\n\n") if b.startswith(("Visit", "Group"))]
        words = []
        for b in blocks[:2]:
            for line in b.splitlines():
                if "words seen:" in line:
                    words.append(set(w.strip() for w in line.split(":", 1)[1].split(",")))
        shared = (words[0] & words[1]) if len(words) == 2 else set()
        d = {"decision": "same_activity" if len(shared) >= 2 else "different_activity", "evidence": ", ".join(sorted(shared)[:2]),
             "explanation": "fake", "confidence": "high"}
        return {"content": json.dumps(d), "wall_s": 0.01, "prompt_tokens": 100, "output_tokens": 20}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ds = os.path.join(self.tmp.name, "day")
        write_dataset(self.ds, fx.two_in_one_app())
        self.day = la.Day(self.ds)
        self.pool, self.potential = la.retrieve(self.day)


class TestRetrieval(Base):
    def test_pool_is_deterministic_and_reports_recall(self):
        again, _ = la.retrieve(self.day)
        self.assertEqual(sorted(self.pool), sorted(again))
        text = la.recall_report(self.day, self.pool, self.potential)
        self.assertIn("CANDIDATE RECALL", text)
        self.assertIn("recall by time gap", text)

    def test_every_pool_pair_has_a_named_source(self):
        for k, v in self.pool.items():
            self.assertTrue(set(v["sources"]) <= set(la.SOURCES) and v["sources"])
            self.assertLess(self.day.pos[k[0]], self.day.pos[k[1]])

    def test_far_apart_pairs_can_be_presented_through_text(self):
        far = [k for k in self.pool if self.day.gap(*k) > 1800]
        self.assertTrue(all(k in self.potential for k in far))     # not excluded by time alone

    def test_subset_is_deterministic_and_bounded(self):
        a = la.stratified_subset(self.day, self.pool, 15)
        self.assertEqual(a, la.stratified_subset(self.day, self.pool, 15))
        self.assertLessEqual(len(a), 15)


class TestPipeline(Base):
    def run_pairs(self):
        c = FakeClient()
        pairs = {k: la.judge_pair(self.day, c, *k) for k in self.pool}
        return c, pairs

    def test_second_order_only_when_first_is_not_different(self):
        c = FakeClient()
        for k in list(self.pool)[:20]:
            before = c.calls
            p = la.judge_pair(self.day, c, *k)
            self.assertEqual(c.calls - before, 1 if p["orders"][0] == "different_activity" else 2)

    def test_reconstruction_results_satisfy_the_contract_and_evaluate(self):
        c, pairs = self.run_pairs()
        for mode in ("B", "C"):
            res, log = la.reconstruct(self.day, pairs, c, mode)
            line, out, pred = la.recon_eval(self.day, res, mode, log)
            self.assertIn("precision", line)
            self.assertEqual(len(res["visits"]), len(self.day.order))

    def test_hybrid_rejects_cannot_linked_links_that_llm_only_accepts(self):
        c, pairs = self.run_pairs()
        k = next(k for k, p in pairs.items() if p["verdict"] == "same")
        cannot = self.day.cannot
        self.day.cannot = lambda a, b: self.day.key(a, b) == k or cannot(a, b)
        _, logB = la.reconstruct(self.day, pairs, c, "B")
        _, logC = la.reconstruct(self.day, pairs, c, "C")
        self.assertIn(k, logB["links"])
        self.assertNotIn(k, logC["links"])
        self.assertIn((k, "cannot_link"), logC["rejected"])

    def test_invented_evidence_is_not_verifiable(self):
        a, b = list(self.pool)[0]
        self.assertFalse(la.phrase_ok(self.day, a, b, ["zebra quantum"]) and not self.day.pair_ev.get(self.day.key(a, b)))

    def test_a_singleton_linked_equally_to_two_clusters_does_not_bridge_them(self):
        d = self.day
        v = d.order[:7]
        pairs = {}

        def same(x, y):
            k = d.key(x, y)
            pairs[k] = {"pair": list(k), "verdict": "same", "orders": [], "evidence": ["x"], "explanation": [], "confidence": "high", "valid": True}
        same(v[0], v[1]); same(v[0], v[2]); same(v[3], v[4]); same(v[3], v[5])
        same(v[6], v[0]); same(v[6], v[3])
        res, log = la.reconstruct(d, pairs, FakeClient(), "B", group_oracle=lambda sa, sb: False)
        part = {frozenset(a["members"]) for a in res["activities"]}
        self.assertFalse(any(v[0] in g and v[3] in g for g in part))
        self.assertIn(v[6], log["ties"])

    def test_group_question_decides_bridges_with_one_link(self):
        d = self.day
        v = d.order[:6]
        pairs = {}

        def same(x, y):
            k = d.key(x, y)
            pairs[k] = {"pair": list(k), "verdict": "same", "orders": [], "evidence": ["x"], "explanation": [], "confidence": "high", "valid": True}
        same(v[1], v[2]); same(v[0], v[3]); same(v[2], v[3])           # one link between two established pairs
        no, _ = la.reconstruct(d, pairs, FakeClient(), "B", group_oracle=lambda sa, sb: False)
        yes, log = la.reconstruct(d, pairs, FakeClient(), "B", group_oracle=lambda sa, sb: True)
        self.assertEqual(max(len(a["members"]) for a in no["activities"]), 2)
        self.assertEqual(max(len(a["members"]) for a in yes["activities"]), 4)
        self.assertEqual(log["merge"][0]["route"], "group_confirmed")

    def test_cache_makes_the_run_reproducible_without_the_model(self):
        class Counting(la.Client):
            def ask(self, system, user):
                return super().ask(system, user)
        c = la.Client(self.tmp.name + "/out", "m1", allow_calls=True)
        c.cache.clear()
        rec = {"key": c.key(la.SYSTEM, "u"), "model": "m1", "content": json.dumps({"decision": "uncertain", "evidence": "", "explanation": "", "confidence": "low"}),
               "wall_s": 1.0, "prompt_tokens": 5, "output_tokens": 2}
        c.cache[rec["key"]] = rec
        with open(c.path, "w") as fh:
            fh.write(json.dumps(rec) + "\n")
        off = la.Client(self.tmp.name + "/out", "m1", allow_calls=False)
        self.assertEqual(off.ask(la.SYSTEM, "u")["content"], rec["content"])
        with self.assertRaises(la.Missing):
            off.ask(la.SYSTEM, "not cached")

    def test_unparseable_replies_abstain(self):
        self.assertEqual(la.parse({"content": "not json"})["decision"], "uncertain")
        self.assertFalse(la.parse({"content": "{}"})["valid"])

    def test_oracle_upper_bound_runs(self):
        orc = la.gold_pair_verdicts(self.day, self.pool)
        res, log = la.reconstruct(self.day, orc, None, "oracle", group_oracle=la.gold_group_oracle(self.day))
        line, _, _ = la.recon_eval(self.day, res, "UB", log)
        self.assertIn("UB", line)

    def test_pair_eval_and_examples_render(self):
        c, pairs = self.run_pairs()
        text, rows = la.pair_eval(self.day, self.pool, pairs)
        self.assertIn("DESCRIPTIVE ONLY", text)
        preds, logs = {}, {}
        for name, res in (("A", la.build_result(self.day, [], {v: ("unassigned", "no_candidate") for v in self.day.order})),):
            preds[name] = {}
        for mode in ("B", "C"):
            res, logs[mode] = la.reconstruct(self.day, pairs, c, mode)
            preds[mode] = {r["visit_id"]: r["activity_id"] for r in res["visits"] if r["state"] == "assigned"}
        self.assertIn("EXAMPLES", la.examples(self.day, preds, pairs, logs, private=True))


if __name__ == "__main__":
    unittest.main()

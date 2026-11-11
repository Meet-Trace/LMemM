"""PR 2B: deterministic evidence between visits (src/activity_evidence.py)."""
import copy
import dataclasses
import json
import unittest

import activity_evidence as ev
import activity_features as af
import activity_fixtures as fx
import activity_harness as hn
import activity_input as ai
import activity_visits as av
from activity_contract import ReplayParams

RP = ReplayParams()
SECONDS = {"t_start", "t_end", "dwell_s", "gap_s"}


def run(clock, rp=RP, ep=None):
    return ev.analyse(ai.prepare(clock.records, max_intra_visit_gap_s=rp.max_intra_visit_gap_s), None, rp, ep)


def types(es):
    return sorted({e.type for e in es.items})


def canon(es):
    return ev.canonical_json(es.to_dict())


def strip(d):
    if isinstance(d, dict):
        return {k: strip(v) for k, v in d.items() if k not in SECONDS}
    if isinstance(d, list):
        return [strip(x) for x in d]
    return d


class ArtifactEvidenceTests(unittest.TestCase):
    def test_same_item_same_artifact_and_symmetry_of_source_and_target(self):
        c = av.Clock()
        a = c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha", "bravo", "charlie", "delta"])
        b = c.visit(2, url="other.example.com/x/y", terms=["echo"], gap=500.0)
        d = c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha", "bravo", "charlie", "delta"], gap=500.0)
        c.fillers(20)
        fs, es = run(c)
        e = {x.type: x for x in es.items if x.type in ("same_item", "same_artifact")}
        self.assertEqual((e["same_item"].source, e["same_item"].target), (a, d))      # earlier first
        self.assertEqual(e["same_artifact"].subject, {"artifact_type": "document", "artifact": "docs.example.com/d/1"})
        self.assertEqual(e["same_artifact"].family, "artifact")
        self.assertEqual(e["same_artifact"].polarity, "support")
        self.assertEqual(es.for_pair(d, a), es.for_pair(a, d))
        self.assertEqual(es.for_pair(a, b), ())

    def test_gap_is_the_difference_from_the_earlier_end_to_the_later_start(self):
        c = av.Clock()
        c.visit(1, url="docs.example.com/d/1", kind="document", dwell=60.0, terms=["alpha"])
        c.visit(1, url="docs.example.com/d/1", kind="document", gap=100.0, terms=["alpha"])
        fs, es = run(c)
        e = es.by_type("same_item")[0]
        self.assertAlmostEqual(e.provenance["gap_s"], 100.0)

    def test_strength_carries_specificity_and_band_not_a_verdict(self):
        c = av.Clock()
        for _ in range(2):
            c.visit(1, url="mail.example.com/mail/u/0", kind="mail")
        fs, es = run(c)
        e = es.by_type("same_artifact")[0]
        self.assertEqual(e.strength["band"], "low")
        self.assertTrue(e.strength["hub_pattern"])
        self.assertIn("needs_purpose_check", e.restrictions)
        for forbidden in ("score", "tier", "decision", "assign", "merge", "confidence"):
            self.assertNotIn(forbidden, json.dumps(e.to_dict()).lower().replace("assignment", ""))

    def test_pr_identity_and_conflict_between_different_numbers(self):
        c = av.Clock()
        a = c.visit(1, url="github.com/o/r/pull/12", window="x")
        b = c.visit(2, url="github.com/o/r/pull/12/files", window="y")
        d = c.visit(3, url="github.com/o/r/pull/14", window="z")
        e_ = c.visit(4, url="github.com/o/other/pull/12", window="w")
        fs, es = run(c)
        same = es.by_type("same_pr_issue")
        self.assertEqual([(x.source, x.target) for x in same], [(a, b)])
        self.assertIn("needs_corroboration_other_than_time", same[0].restrictions)
        conf = es.by_type("different_pr_issue")
        self.assertEqual({frozenset(x.pair()) for x in conf}, {frozenset((a, d)), frozenset((b, d))})
        self.assertTrue(all(x.polarity == "conflict" and not x.lifted for x in conf))
        self.assertEqual(es.for_pair(a, e_), ())                       # same number, other repository: nothing
        self.assertEqual(es.cannot_links(), frozenset({frozenset((a, d)), frozenset((b, d))}))

    def test_files_same_name_known_and_unknown_folder(self):
        c = av.Clock()
        a = c.visit(1, kind="code_file", app="Code", target="util.py", window="util.py - alpha")
        b = c.visit(2, kind="code_file", app="Code", target="util.py", window="util.py - beta")
        d = c.visit(3, kind="code_file", app="Code", target="util.py", window="no workspace here")
        fs, es = run(c)
        conf = es.by_type("file_in_different_folders")
        self.assertEqual([frozenset(x.pair()) for x in conf], [frozenset((a, b))])
        weak = {frozenset(x.pair()) for x in es.by_type("same_file_name")}
        self.assertEqual(weak, {frozenset((a, d)), frozenset((b, d))})
        self.assertIn("folder_unverified", es.by_type("same_file_name")[0].restrictions)
        self.assertEqual(es.by_type("same_artifact"), ())               # different keys are not the same artifact


class GitEvidenceTests(unittest.TestCase):
    def pages(self, *branches):
        c = av.Clock()
        ids = [c.visit(i, url=f"github.com/acme/billing/tree/{b}", window=f"billing at {b}") for i, b in enumerate(branches)]
        return c, ids

    def test_same_branch_is_support_with_restrictions(self):
        c, (a, b) = self.pages("feature-retry", "feature-retry")
        fs, es = run(c)
        e = es.by_type("same_branch")[0]
        self.assertEqual(e.family, "exact")
        self.assertEqual(e.subject, {"repo": "github.com/acme/billing", "branch": "feature-retry"})
        self.assertEqual(set(e.restrictions), {"cannot_assign_alone", "cannot_tip_margin_alone"})

    def test_different_branch_same_repo_is_a_cannot_link(self):
        c, (a, b) = self.pages("feature-retry", "fix-pagination")
        fs, es = run(c)
        e = es.by_type("different_branch")[0]
        self.assertEqual(e.subject["branches"], ["feature-retry", "fix-pagination"])
        self.assertEqual(es.cannot_links(), frozenset({frozenset((a, b))}))

    def test_missing_branch_and_different_repositories_give_no_signal(self):
        c = av.Clock()
        c.visit(1, url="github.com/acme/billing/tree/feature-retry", window="a")
        c.visit(2, url="github.com/acme/billing", window="b")
        c.visit(3, url="github.com/acme/other/tree/fix-pagination", window="c")
        fs, es = run(c)
        self.assertEqual([t for t in types(es) if "branch" in t], [])
        self.assertEqual(es.cannot_links(), frozenset())

    def test_a_terminal_branch_without_a_repository_makes_no_pair_evidence(self):
        c = av.Clock()
        c.visit(1, kind="terminal", app="Terminal", target="zsh", window="git:(feature-x)")
        c.visit(2, kind="terminal", app="Terminal", target="zsh", window="git:(fix-y)", gap=500.0)
        fs, es = run(c)
        self.assertEqual(fs.visits[0].branch["repo"], None)
        self.assertEqual([t for t in types(es) if "branch" in t], [])

    def test_a_direct_shared_pr_identity_lifts_a_branch_conflict_but_keeps_it_visible(self):
        c = av.Clock()
        a = c.visit(1, url="github.com/o/r/pull/12", window="fix: a (#12) feature-a into main")
        b = c.visit(2, url="github.com/o/r/pull/12/files", window="x feature-b into main")
        fs, es = run(c)
        conf = es.by_type("different_branch")[0]
        self.assertTrue(conf.lifted)
        self.assertEqual(conf.lifted_by, "github.com/o/r#12")
        self.assertEqual(es.cannot_links(), frozenset())
        self.assertEqual(es.conflicting_pairs(), frozenset({frozenset((a, b))}))

    def test_workspace_branches_pair_within_a_workspace_only(self):
        c = av.Clock()
        a = c.visit(1, kind="code_file", app="Code", target="a.py", window="a.py - billing (dev-wip)")
        b = c.visit(2, kind="code_file", app="Code", target="b.py", window="b.py - billing (fix-x)")
        d = c.visit(3, kind="code_file", app="Code", target="c.py", window="c.py - other (dev-wip)")
        fs, es = run(c)
        self.assertEqual({frozenset(x.pair()) for x in es.by_type("different_branch")}, {frozenset((a, b))})
        self.assertEqual(es.by_type("same_branch"), ())


class LineageAndLocatorTests(unittest.TestCase):
    def test_version_lineage_is_measured_not_decided(self):
        c = av.Clock()
        shared = ["harbour", "berth", "tender", "dredging", "quay", "pilotage"]
        a = c.visit(1, kind="code_file", app="Keynote", target="Pitch_v1.key", window="Pitch_v1.key - Deck", terms=shared)
        b = c.visit(2, kind="code_file", app="Keynote", target="Pitch_v2.key", window="Pitch_v2.key - Deck", terms=shared, gap=3000.0)
        d = c.visit(3, kind="code_file", app="Keynote", target="Other_v2.key", window="Other_v2.key - Deck", terms=["zebra"])
        e_ = c.visit(4, kind="code_file", app="Keynote", target="Pitch_v3.key", window="Pitch_v3.key - Elsewhere", terms=shared)
        c.fillers(40)
        fs, es = run(c)
        lin = es.by_type("version_lineage")
        self.assertEqual([x.pair() for x in lin], [(a, b)])               # other base name, other folder: no link
        self.assertEqual(set(lin[0].restrictions), {"cannot_assign_alone", "needs_content_corroboration",
                                                    "cannot_override_cannot_link"})
        corr = lin[0].strength["corroboration"]
        self.assertEqual(set(corr), {"cos_idf", "rare_shared", "x_e"})
        self.assertGreater(corr["x_e"], 0.5)
        self.assertIn("pitch", lin[0].strength["excluded_tokens"])        # the lineage's own tokens never corroborate it

    def test_lineage_with_unrelated_content_has_low_corroboration(self):
        c = av.Clock()
        c.visit(1, kind="code_file", app="Keynote", target="Pitch_v1.key", window="Pitch_v1.key - Deck",
                terms=["harbour", "berth", "tender", "dredging"])
        c.visit(2, kind="code_file", app="Keynote", target="Pitch_v2.key", window="Pitch_v2.key - Deck",
                terms=["coffee", "espresso", "barista", "roast"], gap=3000.0)
        c.fillers(40)
        fs, es = run(c)
        self.assertEqual(es.by_type("version_lineage")[0].strength["corroboration"]["x_e"], 0.0)

    def test_same_name_is_the_same_artifact_not_a_version(self):
        c = av.Clock()
        for _ in range(2):
            c.visit(1, kind="code_file", app="Keynote", target="Pitch_v1.key", window="Pitch_v1.key - Deck")
        fs, es = run(c)
        self.assertEqual(es.by_type("version_lineage"), ())
        self.assertEqual(len(es.by_type("same_artifact")), 1)

    def test_locator_requires_a_shared_prefix_on_different_pages_and_never_a_bare_domain(self):
        c = av.Clock()
        a = c.visit(1, url="docs.example.com/guides/a", window="a")
        b = c.visit(2, url="docs.example.com/guides/b", window="b")
        d = c.visit(3, url="docs.example.com/other/c", window="c")
        e_ = c.visit(4, url="docs.example.com/guides/a", window="a again", gap=500.0)
        fs, es = run(c)
        loc = {frozenset(x.pair()) for x in es.by_type("same_locator")}
        self.assertEqual(loc, {frozenset((a, b)), frozenset((b, e_))})   # (a, e_) is the same page: artifact evidence
        self.assertEqual(es.by_type("same_locator")[0].subject["namespace"], "locator")
        self.assertFalse(any(x.subject.get("namespace") == "domain" for x in es.items))
        self.assertIn("never_bare_domain", es.by_type("same_locator")[0].restrictions)


class LexicalEvidenceTests(unittest.TestCase):
    def build(self):
        c = av.Clock()
        a = c.visit(1, url="a.example.com/p/1", window="a", terms=["gearbox", "torque", "flange", "bearing"],
                    idents=["drive_shaft.py", "common_util.py"])
        b = c.visit(2, url="b.example.com/p/2", window="b", terms=["gearbox", "torque", "unrelated", "lineup"],
                    idents=["drive_shaft.py", "other_thing.py"], gap=900.0)
        c.fillers(60)
        return c, a, b

    def test_shared_identifiers_list_each_identifier_with_its_class(self):
        c, a, b = self.build()
        fs, es = run(c)
        e = es.by_type("shared_identifiers")[0]
        self.assertEqual((e.source, e.target), (a, b))
        self.assertEqual(e.strength["count"], 1)
        item = e.strength["items"][0]
        self.assertEqual((item["token"], item["df"], item["class"]), ("drive_shaft.py", 2, "rare"))
        self.assertEqual(e.family, "lexical")

    def test_shared_terms_report_rare_tokens_and_the_content_measurement(self):
        c, a, b = self.build()
        fs, es = run(c)
        e = es.by_type("shared_terms")[0]
        sim = af.content_similarity(fs.visit(a), fs.visit(b), fs.corpus)
        self.assertEqual(e.strength["x_e"], sim["x_e"])
        self.assertEqual(e.strength["rare_shared"], sim["rare_shared"])
        self.assertIn("gearbox", e.subject["tokens"])
        self.assertTrue(all(i["df"] == 2 for i in e.strength["items"]))
        self.assertIn("needs_second_family", e.restrictions)

    def test_hub_like_tokens_never_become_evidence(self):
        c = av.Clock()
        for i in range(10):
            c.visit(i, url=f"s{i}.example.com/p/q", terms=["everywhere", f"own{i}word"], idents=["shared_everywhere.py"])
        fs, es = run(c)
        self.assertEqual(es.by_type("shared_identifiers"), ())
        self.assertFalse(any("everywhere" in json.dumps(e.subject) for e in es.items if e.type == "shared_terms"))

    def test_tokens_from_the_artifact_key_are_flagged_so_they_cannot_count_twice(self):
        c = av.Clock()
        c.visit(1, url="a.example.com/pricing-strategy/x", window="a", terms=["alpha"])
        c.visit(2, url="b.example.com/pricing-strategy/y", window="b", terms=["bravo"], gap=900.0)
        c.fillers(60)
        fs, es = run(c)
        e = es.by_type("shared_terms")[0]
        flagged = {i["token"]: i["in_key"] for i in e.strength["items"]}
        self.assertTrue(flagged["pricing"] and flagged["strategy"])


class SetLevelTests(unittest.TestCase):
    def test_duplicate_evidence_collapses_and_ids_are_unique(self):
        c = av.Clock()
        for _ in range(3):
            c.visit(1, url="docs.example.com/d/1", kind="document", target="docs.example.com/d/1",
                    terms=["alpha", "bravo", "charlie", "delta"])
        fs, es = run(c)
        ids = [e.id for e in es.items]
        self.assertEqual(len(ids), len(set(ids)))
        keys = [(e.type, e.pair(), json.dumps(e.subject, sort_keys=True)) for e in es.items]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(es.by_type("same_artifact")), 3)            # three visits, three pairs, one artifact

    def test_the_same_input_in_other_orders_with_duplicates_gives_identical_output(self):
        for name, build in fx.BUILDERS.items():
            recs = build().records
            a = ev.analyse(ai.prepare(recs), None, RP)[1]
            b = ev.analyse(ai.prepare(hn.with_duplicates(hn.shuffled(recs, 7), 8)), None, RP)[1]
            self.assertEqual(canon(a), canon(b), name)

    def test_output_is_sorted_and_ids_are_content_hashes(self):
        recs = fx.BUILDERS["hub_between"]().records
        fs, es = ev.analyse(ai.prepare(recs), None, RP)
        order = {f.visit_id: i for i, f in enumerate(fs.visits)}
        keys = [(order[e.source], order[e.target], e.type) for e in es.items]
        self.assertEqual(keys, sorted(keys))
        for e in es.items:
            self.assertLess(order[e.source], order[e.target])
            self.assertEqual(e.id, ev._ev_id(e.type, e.source, e.target, e.subject))

    def test_large_groups_are_reported_not_silently_dropped(self):
        c = av.Clock()
        for _ in range(5):
            c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha"])
        fs, es = run(c, ep=ev.EvidenceParams(max_group_visits=4))
        self.assertEqual(es.by_type("same_item"), ())
        self.assertTrue(any(k.startswith("artifact:") and v == 5 for k, v in es.skipped.items()))
        self.assertEqual(es.report["skipped_groups"], len(es.skipped))

    def test_evidence_objects_are_immutable_and_serialisable(self):
        recs = fx.BUILDERS["pr_numbers_differ"]().records
        fs, es = ev.analyse(ai.prepare(recs), None, RP)
        self.assertIsInstance(es.items, tuple)
        self.assertIsInstance(es.cannot_links(), frozenset)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            es.items[0].lifted = True
        json.dumps(es.to_dict())
        json.dumps(fs.to_dict())

    def test_the_output_can_be_consumed_without_the_raw_moments(self):
        recs = fx.BUILDERS["branches_alternating"]().records
        fs, es = ev.analyse(ai.prepare(recs), None, RP)
        text = json.dumps({"f": fs.to_dict(), "e": es.to_dict()})
        for raw_only in ('"digest"', '"interaction"', '"context"', '"obs_id"', '"text_hash"'):
            self.assertNotIn(raw_only, text)

    def test_every_fixture_produces_evidence_that_names_only_existing_visits(self):
        for name, build in fx.BUILDERS.items():
            fs, es = ev.analyse(ai.prepare(build().records), None, RP)
            ids = {f.visit_id for f in fs.visits}
            for e in es.items:
                self.assertTrue({e.source, e.target} <= ids, name)
                self.assertIn(e.type, ev.FAMILY)
                self.assertEqual(e.family, ev.FAMILY[e.type])

    def test_fixture_expectations(self):
        def get(name):
            recs = fx.BUILDERS[name]().records
            return ev.analyse(ai.prepare(recs), None, RP)[1]
        self.assertTrue(get("pr_numbers_differ").cannot_links())
        self.assertTrue(get("branches_alternating").cannot_links())
        self.assertFalse(get("same_branch_two_tasks").cannot_links())
        self.assertTrue(get("shared_doc_two_purposes").by_type("same_artifact"))


class TimeInvarianceTests(unittest.TestCase):
    NAMES = ("continuous_multiapp", "branches_alternating", "pr_numbers_differ", "hub_between", "interruptions_returns")

    def test_translation_changes_nothing_but_timestamps(self):
        for name in self.NAMES:
            recs = fx.BUILDERS[name]().records
            base = ev.analyse(ai.prepare(recs), None, RP)[1].to_dict()
            for off in (1234.5, -9.0e6):
                moved = ev.analyse(ai.prepare(hn.translate(recs, off), check_capture_clock=False), None, RP)[1].to_dict()
                self.assertEqual(strip(base), strip(moved), (name, off))
                for x, y in zip(base["items"], moved["items"]):
                    self.assertAlmostEqual(x["provenance"]["gap_s"], y["provenance"]["gap_s"], places=3)

    def test_joint_scaling_changes_only_gap_s_by_the_factor(self):
        for name in self.NAMES:
            recs = fx.BUILDERS[name]().records
            base = ev.analyse(ai.prepare(recs), None, RP)[1].to_dict()
            for k in (0.5, 2.0, 8.0):
                rp = RP.scaled(k)
                es = ev.analyse(ai.prepare(hn.scale(recs, k), max_intra_visit_gap_s=rp.max_intra_visit_gap_s,
                                           check_capture_clock=False), None, rp)[1].to_dict()
                self.assertEqual(strip(base), strip(es), (name, k))
                for x, y in zip(base["items"], es["items"]):
                    self.assertAlmostEqual(y["provenance"]["gap_s"], x["provenance"]["gap_s"] * k, places=6)

    def test_specificity_recurrence_follows_the_resume_parameter_under_scaling(self):
        c = av.Clock()
        for i in range(4):
            c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha", "bravo", "charlie", "delta"],
                    gap=5000.0 if i else 2.0, dwell=60.0)
        base = ev.analyse(ai.prepare(c.records), None, RP)[1]
        for k in (0.5, 4.0):
            rp = RP.scaled(k)
            es = ev.analyse(ai.prepare(hn.scale(c.records, k), max_intra_visit_gap_s=rp.max_intra_visit_gap_s,
                                       check_capture_clock=False), None, rp)[1]
            self.assertEqual([e.strength for e in es.items], [e.strength for e in base.items])

    def test_no_evidence_field_reads_absolute_time(self):
        recs = fx.BUILDERS["hub_between"]().records
        es = ev.analyse(ai.prepare(recs), None, RP)[1]
        text = json.dumps(es.to_dict())
        self.assertNotIn(repr(float(recs[0]["t_start"])), text)       # ids embed a capture second; timestamps never appear


if __name__ == "__main__":
    unittest.main()

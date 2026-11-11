"""PR 2C: scoring, candidate generation, assignment, consolidation and abstention of the activity inference.

Logic tests run on hand-built FeatureSet / EvidenceSet stand-ins (so each rule is exercised in isolation, with the exact
evidence it names); integration tests run the real pipeline and the PR 2A invariant harness on every fixture. The
weights are provisional and uncalibrated: these tests pin RULES (what may or may not decide), not the fixtures' truth.
"""
import unittest
from types import SimpleNamespace as NS

import activity_contract as ac
import activity_evidence as ev
import activity_features as af
import activity_fixtures as fx
import activity_harness as hn
import activity_inference as inf

P = ac.ReplayParams()
CFG = inf.ScoringConfig()
CONTRASTS = ("different_pr_issue", "different_workspace_branch")


# ---------------------------------------------------------------- stand-ins

def visit(i, t0, dwell=100.0, app="App", known=True, glance=False, ok=True, shift=None):
    return NS(visit_id=f"v{i:02d}", t_start=t0, t_end=t0 + dwell, dwell_s=dwell, app=app, known_kind=known, glance=glance,
              substance={"ok": ok, "reasons": []}, weak_evidence=False, bag=(), internal_shift=shift)


def E(type_, a, b, **strength):
    a, b = f"v{a:02d}", f"v{b:02d}"
    return NS(id=f"ev-{type_}-{a}-{b}", type=type_, polarity="contrast" if type_ in CONTRASTS else "support", source=a, target=b,
              strength=strength)


STRONG = [("same_pr_issue", {"band": "high"}), ("shared_identifiers", {"count": 1, "items": []})]      # 4.0 + 1.0 = 5.0


def strong(a, b):
    return [E(t, a, b, **s) for t, s in STRONG]


class Store:
    def __init__(self, cannot=(), complete=True):
        self.cannot, self.complete = {frozenset(p) for p in cannot}, complete

    def is_cannot_link(self, a, b):
        return frozenset((a, b)) in self.cannot


class Evidence:
    def __init__(self, items, store, skipped_members=None):
        self.items, self.constraints = tuple(items), store
        self.skipped_members = skipped_members or {}
        self.skipped = {k: len(v) for k, v in self.skipped_members.items()}

    @property
    def support_complete(self):
        return not self.skipped

    def require_complete(self):
        if not self.constraints.complete:
            raise ev.ConstraintsIncompleteError("incomplete")
        return self.constraints


def vid(i):
    return f"v{i:02d}"


def run(visits, items=(), cannot=(), omitted=None, complete=True, cfg=None):
    fs = NS(visits=tuple(visits), corpus=NS(), params=af.FeatureParams(), replay_params=P)
    members = {k: tuple(vid(i) for i in v) for k, v in (omitted or {}).items()}
    res = inf.infer_evidence(fs, Evidence(items, Store(cannot, complete), members), P, cfg)
    return res, {r["visit_id"]: r for r in res["visits"]}


def together(res, *ids):
    return any(all(vid(i) in g for i in ids) for g in ac.partition(res))


def row(rows, i):
    return rows[vid(i)]


# ---------------------------------------------------------------- scoring policy

class ConfigTests(unittest.TestCase):
    def test_the_design_thresholds_and_nothing_seconds_valued_live_in_the_config(self):
        self.assertEqual((CFG.assign_threshold, CFG.weak_floor, CFG.min_margin), (4.0, 2.0, 2.0))
        self.assertEqual([f for f in CFG.__dataclass_fields__ if f.endswith("_s")], [])
        self.assertEqual(ac.undeclared_time_params(), [])

    def test_context_alone_can_never_reach_the_assignment_threshold(self):
        self.assertLess(CFG.ctx_cap, CFG.assign_threshold)

    def test_correlated_signals_of_one_family_are_not_added(self):
        v = [visit(1, 0), visit(2, 200)]
        items = [E("same_pr_issue", 1, 2, band="high"), E("same_artifact", 1, 2, band="high"), E("same_item", 1, 2, band="high")]
        _, rows = run(v, items)
        self.assertEqual((row(rows, 2)["state"], row(rows, 2)["reason"]), (ac.TENTATIVE, "artifact_only"))
        en = inf._Engine(NS(visits=tuple(v), corpus=NS(), params=af.FeatureParams()), Evidence(items, Store()), P, CFG)
        s = en.score(vid(2), [vid(1)])
        self.assertEqual(s.E, CFG.w_same_pr_issue)                  # the strongest of the three, not their sum
        self.assertEqual(s.families, ["artifact"])


# ---------------------------------------------------------------- assignment rules

class AnchorTests(unittest.TestCase):
    def test_two_independent_families_with_enough_score_anchor_a_pair(self):
        res, rows = run([visit(1, 0), visit(2, 200)], strong(1, 2))
        self.assertTrue(together(res, 1, 2))
        self.assertEqual((row(rows, 1)["state"], row(rows, 2)["state"]), ("assigned", "assigned"))

    def test_a_shared_artifact_alone_is_a_candidate_not_a_merge(self):
        res, rows = run([visit(1, 0), visit(2, 200)], [E("same_pr_issue", 1, 2, band="high")])
        self.assertFalse(together(res, 1, 2))
        self.assertEqual((row(rows, 2)["state"], row(rows, 2)["reason"]), (ac.TENTATIVE, "artifact_only"))

    def test_version_lineage_alone_cannot_assign_and_needs_corroboration(self):
        res, rows = run([visit(1, 0), visit(2, 200)], [E("version_lineage", 1, 2, corroboration={"x_e": 1.0})])
        self.assertFalse(together(res, 1, 2))
        self.assertEqual(row(rows, 2)["reason"], "lineage_only")
        res, rows = run([visit(1, 0), visit(2, 200)], [E("version_lineage", 1, 2, corroboration={"x_e": 0.0}),
                                                       E("shared_identifiers", 1, 2, count=3, items=[])])
        self.assertFalse(together(res, 1, 2))                         # zero corroboration contributes nothing

    def test_same_branch_locator_and_file_name_never_assign_even_together(self):
        items = [E("same_branch", 1, 2), E("same_locator", 1, 2), E("same_file_name", 1, 2), E("same_workspace_branch", 1, 2)]
        res, rows = run([visit(1, 0), visit(2, 200)], items)
        self.assertFalse(together(res, 1, 2))

    def test_shared_terms_need_a_second_family(self):
        res, _ = run([visit(1, 0), visit(2, 200)], [E("shared_terms", 1, 2, x_e=1.0)])
        self.assertFalse(together(res, 1, 2))
        res, _ = run([visit(1, 0), visit(2, 200)], [E("shared_terms", 1, 2, x_e=1.0), E("same_artifact", 1, 2, band="high")])
        self.assertTrue(together(res, 1, 2))

    def test_below_the_threshold_with_a_primary_family_is_tentative_at_most(self):
        # identifier (1.0) + artifact at low band (4.0*0.5 = 2.0) -> 3.0: >= floor, < 4.0
        items = [E("shared_identifiers", 1, 2, count=1, items=[]), E("same_pr_issue", 1, 2, band="low")]
        res, rows = run([visit(1, 0), visit(2, 100000.0)], items)             # far apart: no context
        self.assertFalse(together(res, 1, 2))
        self.assertEqual(row(rows, 2)["state"], ac.TENTATIVE)

    def test_evidence_below_the_weak_floor_is_treated_as_absent(self):
        res, rows = run([visit(1, 0), visit(2, 200)], [E("same_locator", 1, 2)])
        self.assertEqual((row(rows, 1)["reason"], row(rows, 2)["reason"]), ("seed", "seed"))


class MarginTests(unittest.TestCase):
    def two_units(self, extra):
        """Units A = {1, 2} and B = {3, 4} anchored by strong evidence; visit 5 links to both."""
        v = [visit(1, 0), visit(2, 200), visit(3, 400), visit(4, 600), visit(5, 800)]
        return v, strong(1, 2) + strong(3, 4) + extra

    def test_equal_candidates_are_ambiguous_not_a_coin_flip(self):
        v, items = self.two_units(strong(2, 5) + strong(4, 5))
        res, rows = run(v, items)
        self.assertEqual((row(rows, 5)["state"], row(rows, 5)["reason"]), (ac.AMBIGUOUS, "margin_too_small"))
        self.assertFalse(any(vid(5) in g for g in ac.partition(res)))

    def test_a_clear_winner_is_assigned(self):
        v, items = self.two_units(strong(2, 5) + [E("same_locator", 4, 5)])
        res, rows = run(v, items)
        self.assertTrue(together(res, 1, 2, 5))
        self.assertEqual(row(rows, 5)["candidates"][0]["families"], ["artifact", "lexical"])

    def test_branch_and_locator_cannot_tip_a_margin(self):
        # A: 5.0 (tip 5.0). B: 5.0 + same_branch 1.0 + locator 1.0 = 7.0 in full but tip 5.0: the 2.0 full lead is not a margin.
        v, items = self.two_units(strong(2, 5) + strong(4, 5) + [E("same_branch", 4, 5), E("same_locator", 4, 5)])
        res, rows = run(v, items)
        self.assertEqual((row(rows, 5)["state"], row(rows, 5)["reason"]), (ac.AMBIGUOUS, "margin_too_small"))


class ContrastTests(unittest.TestCase):
    def test_different_pr_is_a_penalty_not_a_prohibition(self):
        items = [E("same_artifact", 1, 2, band="high"), E("shared_identifiers", 1, 2, count=1, items=[]), E("different_pr_issue", 1, 2)]
        res, rows = run([visit(1, 0), visit(2, 100000.0)], items)           # 3.0 + 1.0 - 2.0 = 2.0
        self.assertEqual(row(rows, 2)["state"], ac.TENTATIVE)
        self.assertNotEqual(row(rows, 2)["reason"], "cannot_link")

    def test_enough_other_evidence_still_merges_visits_of_different_prs(self):
        items = [E("same_artifact", 1, 2, band="high"), E("shared_identifiers", 1, 2, count=3, items=[]),
                 E("shared_terms", 1, 2, x_e=1.0), E("different_pr_issue", 1, 2)]       # 3.0 + 3.0 - 2.0 = 4.0
        res, _ = run([visit(1, 0), visit(2, 200)], items)
        self.assertTrue(together(res, 1, 2))

    def test_one_activity_may_span_two_prs_when_a_member_shares_the_pr(self):
        # 1: PR 12, 2: PR 12 (anchored), 3: PR 15 with a PR-15 link to 2 ... member 1 contrasts with 3 but 2 shares the PR.
        items = strong(1, 2) + strong(2, 3) + [E("different_pr_issue", 1, 3)]
        res, _ = run([visit(1, 0), visit(2, 200), visit(3, 50000.0)], items)        # far away: no context help
        self.assertTrue(together(res, 1, 2, 3))

    def test_the_pr_numbers_differ_fixture_keeps_distinct_prs_apart_without_a_cannot_link(self):
        f = fx.pr_numbers_differ()
        self.assertEqual(f.cannot_link, [])
        self.assertEqual(hn.check_must_pairs(inf.infer, f.records, P, not_merge=f.must_not_merge), [])
        es = ev.analyse(hn.prepared_for(f.records, P), None, P)[1]
        self.assertTrue(es.by_type("different_pr_issue"))
        self.assertFalse(es.cannot_links())


# ---------------------------------------------------------------- cannot-links

class CannotLinkTests(unittest.TestCase):
    def test_a_cannot_linked_candidate_is_never_assigned_even_with_strong_evidence(self):
        res, rows = run([visit(1, 0), visit(2, 200)], strong(1, 2), cannot=[(vid(1), vid(2))])
        self.assertFalse(together(res, 1, 2))
        self.assertEqual(row(rows, 2)["reason"], "cannot_link")

    def test_a_blocked_unit_does_not_compete_in_the_margin(self):
        v = [visit(1, 0), visit(2, 200), visit(3, 400), visit(4, 600), visit(5, 800)]
        items = strong(1, 2) + strong(3, 4) + strong(2, 5) + strong(4, 5)
        res, _ = run(v, items, cannot=[(vid(2), vid(5))])                  # unit A is blocked for 5: B wins unopposed
        self.assertTrue(together(res, 3, 4, 5))
        self.assertFalse(together(res, 1, 5))

    def test_consolidation_never_merges_across_a_cannot_link_and_rechecks_after_every_merge(self):
        v = [visit(i, 300.0 * i) for i in range(1, 4)]
        fs = NS(visits=tuple(v), corpus=NS(), params=af.FeatureParams(), replay_params=P)
        # 1-2 is the stronger link (tip 5.0); 2-3 carries E 4.0 but tip 3.0, so merging 1 and 2 first is margin-safe.
        items = strong(1, 2) + [E("same_artifact", 2, 3, band="high"), E("same_locator", 2, 3)]

        def groups(cannot):
            es = Evidence(items, Store(cannot=cannot))
            en = inf._Engine(fs, es, P, CFG)
            for i in (1, 2, 3):
                en.assigned.add(en.uid_of[vid(i)])
            en.pass3()
            return {frozenset(m) for u, m in en.members.items() if u in en.assigned}
        self.assertIn(frozenset((vid(1), vid(2), vid(3))), groups([]))                # without the constraint: all three
        blocked = groups([(vid(1), vid(3))])                                          # 1 and 3 may never share an activity
        self.assertEqual(blocked, {frozenset((vid(1), vid(2))), frozenset((vid(3),))})   # the second merge is refused

    def test_incomplete_constraints_fail_closed_and_produce_no_result(self):
        with self.assertRaises(ev.ConstraintsIncompleteError):
            run([visit(1, 0), visit(2, 200)], strong(1, 2), complete=False)

    def test_real_pipeline_rejects_an_incomplete_store(self):
        f = fx.branches_alternating()
        prepared = hn.prepared_for(f.records, P)
        fs, es = ev.analyse(prepared, None, P)
        bad = ev.EvidenceSet(es.items, es.skipped, es.report, ev.ConstraintStore(False, es.constraints.rules, es.constraints.branch_of,
                                                                               es.constraints.file_of, es.constraints.prs, es.constraints.order))
        with self.assertRaises(ev.ConstraintsIncompleteError):
            inf.infer_evidence(fs, bad, P)


# ---------------------------------------------------------------- context (Pass 2)

class ContextTests(unittest.TestCase):
    ANCHOR = strong(1, 2)

    def with_borderline(self, t3):
        # visit 3: low-band artifact 2.0 + locator 1.0 = 3.0 evidence (below 4.0, >= floor, two families)
        items = self.ANCHOR + [E("same_pr_issue", 2, 3, band="low"), E("same_locator", 2, 3)]
        return run([visit(1, 0), visit(2, 200), visit(3, t3)], items)

    def test_adjacent_context_lifts_borderline_evidence_over_the_threshold(self):
        res, rows = self.with_borderline(310.0)
        self.assertTrue(together(res, 1, 2, 3))
        self.assertEqual(row(rows, 3)["state"], ac.ASSIGNED)

    def test_the_same_evidence_without_adjacency_stays_tentative(self):
        res, rows = self.with_borderline(300.0 + P.candidate_horizon_s * 3)
        self.assertFalse(together(res, 1, 2, 3))
        self.assertEqual(row(rows, 3)["state"], ac.TENTATIVE)

    def test_context_without_evidence_never_assigns_it_seeds_instead(self):
        res, rows = run([visit(1, 0), visit(2, 200), visit(3, 310.0)], self.ANCHOR)
        self.assertFalse(together(res, 1, 2, 3))
        self.assertEqual(row(rows, 3)["reason"], "seed")

    def test_context_cannot_supply_the_missing_primary_family(self):
        items = self.ANCHOR + [E("same_branch", 2, 3), E("same_locator", 2, 3), E("same_file_name", 2, 3)]
        res, _ = run([visit(1, 0), visit(2, 200), visit(3, 310.0)], items)
        self.assertFalse(together(res, 1, 2, 3))

    def test_only_earlier_assignments_are_used(self):
        # visit 2 links strongly to the LATER visit 3, but 3 is not assigned when 2 is decided
        res, rows = run([visit(1, 0), visit(2, 200), visit(3, 400)], strong(2, 3))
        self.assertTrue(together(res, 2, 3))
        self.assertEqual(row(rows, 1)["reason"], "seed")


# ---------------------------------------------------------------- seeds, substance, flags

class SeedTests(unittest.TestCase):
    def test_glances_and_no_substance_are_not_assigned(self):
        _, rows = run([visit(1, 0, glance=True), visit(2, 200, ok=False), visit(3, 400)])
        self.assertEqual((row(rows, 1)["state"], row(rows, 1)["reason"]), (ac.UNASSIGNED, "no_substance"))
        self.assertEqual((row(rows, 2)["state"], row(rows, 2)["reason"]), (ac.UNASSIGNED, "no_substance"))
        self.assertEqual((row(rows, 3)["state"], row(rows, 3)["reason"]), (ac.ASSIGNED, "seed"))

    def test_seed_needs_dwell_and_unknown_apps_need_more(self):
        _, rows = run([visit(1, 0, dwell=P.seed_dwell_s - 1), visit(2, 5000, dwell=P.seed_dwell_s + 1, known=False),
                       visit(3, 9000, dwell=P.unknown_app_seed_dwell_s + 1, known=False)])
        self.assertEqual(row(rows, 1)["state"], ac.UNASSIGNED)
        self.assertEqual(row(rows, 2)["state"], ac.UNASSIGNED)
        self.assertEqual(row(rows, 3)["reason"], "seed")

    def test_internal_shift_lowers_confidence_and_never_splits_the_visit(self):
        flag = {"from_moment_id": "m1", "to_moment_id": "m2", "overlap": 0.0, "new_identifiers": ["secret"]}
        _, a = run([visit(1, 0), visit(2, 200)], strong(1, 2))
        _, b = run([visit(1, 0), visit(2, 200, shift=flag)], strong(1, 2))
        self.assertLess(row(b, 2)["confidence"], row(a, 2)["confidence"])
        self.assertEqual(row(b, 2)["internal_shift"], {"from_moment_id": "m1", "to_moment_id": "m2"})
        self.assertEqual(row(b, 2)["state"], ac.ASSIGNED)
        self.assertEqual(len([r for r in b.values()]), 2)                  # still exactly one row per visit

    def test_status_is_reporting_only_and_lives_outside_the_result(self):
        res, _ = run([visit(1, 0), visit(2, 200)], strong(1, 2))
        self.assertNotIn("status", str(res))
        prepared = NS(visits=[NS(visit_id=vid(1), t_end=100.0), NS(visit_id=vid(2), t_end=300.0)])
        self.assertEqual(set(inf.report_status(res, prepared, P).values()), {"active"})
        late = P.with_reporting(status_active_s=1.0, status_paused_s=2.0)
        before = [r["state"] for r in res["visits"]]
        inf.report_status(res, prepared, late)
        self.assertEqual(before, [r["state"] for r in res["visits"]])


# ---------------------------------------------------------------- incomplete support evidence

class IncompleteSupportTests(unittest.TestCase):
    OMIT = {"artifact:big": (1, 2, 3)}

    def test_complete_support_gives_a_confident_seed_for_a_visit_with_no_evidence(self):
        res, rows = run([visit(1, 0), visit(2, 200)])
        self.assertEqual((row(rows, 2)["state"], row(rows, 2)["reason"]), (ac.ASSIGNED, "seed"))
        self.assertTrue(res["relations"]["support_complete"])

    def test_a_missing_pair_listing_is_not_treated_as_unrelated(self):
        res, rows = run([visit(1, 0), visit(2, 200), visit(3, 400)], omitted=self.OMIT)
        self.assertEqual(row(rows, 1)["reason"], "seed")                     # nothing earlier could be related
        for i in (2, 3):
            self.assertEqual((row(rows, i)["state"], row(rows, i)["reason"]), (ac.AMBIGUOUS, "shared_artifact"))
        self.assertFalse(res["relations"]["support_complete"])
        for i in (2, 3):
            self.assertNotEqual(row(rows, i)["reason"], "no_candidate")
            self.assertIsNone(row(rows, i)["activity_id"])

    def test_explicit_positive_evidence_is_still_used_when_no_rival_could_be_hidden(self):
        res, rows = run([visit(1, 0), visit(2, 200)], strong(1, 2), omitted={"artifact:big": (1, 2)})
        self.assertTrue(together(res, 1, 2))
        self.assertEqual(row(rows, 2)["state"], ac.ASSIGNED)

    def test_a_hidden_rival_blocks_a_confident_choice(self):
        v = [visit(1, 0), visit(2, 200), visit(3, 400), visit(4, 600), visit(5, 800)]
        items = strong(1, 2) + strong(3, 4) + strong(2, 5)                  # clear winner on listed evidence
        res, rows = run(v, items, omitted={"identifier:x": (4, 5)})         # but 5 shares an omitted group with unit {3, 4}
        self.assertEqual((row(rows, 5)["state"], row(rows, 5)["reason"]), (ac.AMBIGUOUS, "shared_artifact"))
        res, rows = run(v, items)                                           # the same input with complete support
        self.assertTrue(together(res, 1, 2, 5))

    def test_consolidation_skips_a_merge_that_omitted_pairs_could_contest(self):
        v = [visit(i, 300.0 * i) for i in range(1, 5)]
        fs = NS(visits=tuple(v), corpus=NS(), params=af.FeatureParams(), replay_params=P)

        def merged(omitted):
            es = Evidence(strong(1, 2), Store(), {k: tuple(vid(i) for i in m) for k, m in omitted.items()})
            en = inf._Engine(fs, es, P, CFG)
            for i in (1, 2, 3):
                en.assigned.add(en.uid_of[vid(i)])
            en.pass3()
            return en.uid_of[vid(1)] == en.uid_of[vid(2)]
        self.assertTrue(merged({}))
        self.assertFalse(merged({"artifact:z": (2, 3)}))

    def test_an_omitted_group_never_creates_a_cannot_link_or_a_rejection(self):
        res, rows = run([visit(1, 0), visit(2, 200)], strong(1, 2), omitted={"artifact:big": (1, 2, 9)})
        self.assertNotEqual(row(rows, 2)["reason"], "cannot_link")

    def test_real_pipeline_with_a_tiny_group_limit_abstains_instead_of_guessing(self):
        f = fx.continuous_multiapp()
        prepared = hn.prepared_for(f.records, P)
        fs = af.extract(prepared, None, P)
        es = ev.build_evidence(fs, ev.EvidenceParams(max_group_visits=2))
        self.assertFalse(es.support_complete)
        res = inf.infer_evidence(fs, es, P)
        self.assertEqual(ac.validate_result(res, {v.visit_id: v.t_start for v in prepared.visits}), [])
        self.assertTrue(any(r["reason"] == "shared_artifact" for r in res["visits"]))
        full = inf.infer(prepared, P)
        self.assertFalse(any(r["reason"] == "shared_artifact" for r in full["visits"]))


# ---------------------------------------------------------------- the real pipeline and the PR 2A harness

class PipelineTests(unittest.TestCase):
    def test_every_invariant_holds_on_every_fixture(self):
        for f in fx.all_fixtures():
            with self.subTest(f.name):
                res = hn.run_all(inf.infer, f.records, P, forget_visits=f.visit_ids()[:2])
                self.assertEqual({k: v for k, v in res.items() if v}, {})

    def test_no_cannot_linked_pair_shares_an_activity_on_any_fixture(self):
        for f in fx.all_fixtures():
            with self.subTest(f.name):
                self.assertEqual(hn.check_cannot_links(inf.infer, f.records, P, f.cannot_link), [])
                prepared = hn.prepared_for(f.records, P)
                es = ev.analyse(prepared, None, P)[1]
                part = ac.partition(inf.infer(prepared, P))
                for a, b in es.constraints.pairs():
                    self.assertFalse(any(a in g and b in g for g in part))

    def test_must_not_merge_expectations_hold_on_every_fixture(self):
        for f in fx.all_fixtures():
            with self.subTest(f.name):
                self.assertEqual(hn.check_must_pairs(inf.infer, f.records, P, not_merge=f.must_not_merge), [])

    def test_exactly_one_row_per_visit_and_only_assigned_visits_have_ids(self):
        f = fx.hub_between()
        prepared = hn.prepared_for(f.records, P)
        res = inf.infer(prepared, P)
        self.assertEqual(sorted(r["visit_id"] for r in res["visits"]), sorted(v.visit_id for v in prepared.visits))
        for r in res["visits"]:
            self.assertEqual(r["activity_id"] is not None, r["state"] == ac.ASSIGNED)
            self.assertIn(r["state"], ac.STATES)

    def test_activity_ids_hash_the_earliest_final_member(self):
        f = fx.hub_between()
        self.assertEqual(hn.check_ids(inf.infer, f.records, P), [])

    def test_reporting_parameters_do_not_change_assignments(self):
        f = fx.interruptions_returns()
        self.assertEqual(hn.check_status_independence(inf.infer, f.records, P), [])

    def test_the_result_carries_no_screen_text_only_ids_counts_and_codes(self):
        f = fx.shared_entities()
        text = str(inf.infer(hn.prepared_for(f.records, P), P))
        for r in f.records:
            for t in (r["digest"].get("terms") or []):
                self.assertNotIn(f"'{t}'", text)


if __name__ == "__main__":
    unittest.main()

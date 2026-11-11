"""PR 2C: deterministic activity inference (src/activity_inference.py).

Scenarios are hand-built with tests/activity_visits.py and padded with filler visits so that token classes are meaningful.
The WEIGHTS under test are provisional: these tests pin RULES (what may assign alone, what may not, what abstains), not
calibrated numbers."""
import ast
import dataclasses
import os
import unittest

import activity_contract as ac
import activity_evidence as ev
import activity_fixtures as fx
import activity_harness as hn
import activity_inference as inf
import activity_input as ai
import activity_visits as av

RP = ac.ReplayParams()
LONG = 4 * 3600.0


def W(tag, n=4):
    """n distinct letters-only words that start with `tag` (so they survive tokenisation)."""
    return [f"{tag}{chr(97 + i)}{chr(98 + i)}x" for i in range(n)]


def prep(clock, rp=RP):
    return ai.prepare(clock.records, max_intra_visit_gap_s=rp.max_intra_visit_gap_s)


def go(clock, rp=RP, scoring=None, ep=None):
    p = prep(clock, rp)
    fs, es = ev.analyse(p, None, rp, ep)
    return inf.infer_from(fs, es, scoring), fs, es


def row(res, v):
    return next(r for r in res["visits"] if r["visit_id"] == v)


def together(res, a, b):
    ra, rb = row(res, a), row(res, b)
    return ra["state"] == rb["state"] == ac.ASSIGNED and ra["activity_id"] == rb["activity_id"]


def pair_visits(c, shared_terms=4, own=4, idents=(), url=None, kind="reading_page", gap=200.0, tag="pair", **kw):
    """Two visits that share `shared_terms` rare terms (and the given identifiers), separated by `gap` seconds."""
    sh = W(tag + "s", shared_terms)
    a = c.visit(f"{tag}1", url=url or f"{tag}a.example.com/p/{tag}", kind=kind, terms=sh + W(tag + "a", own), idents=list(idents), **kw)
    b = c.visit(f"{tag}2", url=(url or f"{tag}b.example.com/q/{tag}") if url is None else url, kind=kind,
                terms=sh + W(tag + "b", own), idents=list(idents), gap=gap, **kw)
    return a, b


class Base(unittest.TestCase):
    def padded(self, n=24):
        return av.Clock().fillers(n)


class GateAndCompleteness(Base):
    def test_incomplete_cannot_links_fail_closed_with_no_result(self):
        c = self.padded()
        pair_visits(c)
        fs, es = ev.analyse(prep(c), None, RP)
        broken = dataclasses.replace(es, constraints=dataclasses.replace(es.constraints, complete=False))
        with self.assertRaises(ev.ConstraintsIncompleteError):
            inf.infer_from(fs, broken)

    def test_require_complete_is_called_before_any_work(self):
        calls = []

        class Spy(ev.EvidenceSet):
            def require_complete(self):
                calls.append(1)
                return super().require_complete()
        c = self.padded(6)
        fs, es = ev.analyse(prep(c), None, RP)
        spy = Spy(es.items, es.skipped, es.report, es.constraints, es.skipped_visits)
        inf.infer_from(fs, spy)
        self.assertEqual(calls, [1])

    def test_incomplete_support_never_seeds_and_never_wins_on_a_margin(self):
        c = av.Clock()
        for i in range(6):                                        # six visits of one hub-like artifact: its pair listing is skipped
            c.visit("hubdoc", url="docs.example.com/d/shared", kind="document", terms=W(f"own{chr(97 + i)}", 5), gap=900.0)
        c.fillers(20)
        small = ev.EvidenceParams(max_group_visits=4)
        res, fs, es = go(c, ep=small)
        self.assertFalse(es.support_complete)
        hub_visits = [r for r in res["visits"] if r["visit_id"] in set(es.skipped_visits[next(iter(es.skipped_visits))])]
        self.assertTrue(hub_visits)
        for r in hub_visits:
            self.assertEqual((r["state"], r["reason"]), (ac.AMBIGUOUS, "insufficient_families"))
            self.assertTrue(r["explanation"]["incomplete_support"])
        self.assertGreater(res["relations"]["abstained_for_incomplete_support"], 0)
        full, _, es2 = go(c)                                       # with complete support the same visits are ordinary seeds
        self.assertTrue(es2.support_complete)
        self.assertEqual(res["relations"]["support_complete"], False)
        self.assertTrue(all(row(full, r["visit_id"])["state"] == ac.ASSIGNED for r in hub_visits))

    def test_a_missing_pair_listing_is_not_evidence_of_no_relation(self):
        c = av.Clock()
        a, b = pair_visits(c, idents=("quorum_alpha.py", "quorum_beta.py"), tag="hard")
        for i in range(6):
            c.visit("hubdoc", url="docs.example.com/d/shared", kind="document", terms=W(f"own{chr(97 + i)}", 5), gap=900.0)
        c.fillers(20)
        res, fs, es = go(c, ep=ev.EvidenceParams(max_group_visits=4))
        self.assertFalse(es.support_complete)
        # explicitly listed positive evidence that satisfies every rule alone still assigns
        self.assertTrue(together(res, a, b))
        # and no visit is told it has no candidate because a listing was omitted
        for r in res["visits"]:
            if r["explanation"].get("incomplete_support"):
                self.assertNotEqual(r["reason"], "no_candidate")

    def test_merge_does_not_use_an_absent_listing_as_a_reason_to_merge(self):
        c = av.Clock()
        for i in range(6):
            c.visit("hubdoc", url="docs.example.com/d/shared", kind="document", terms=W(f"own{chr(97 + i)}", 5), gap=900.0)
        c.fillers(20)
        res, fs, es = go(c, ep=ev.EvidenceParams(max_group_visits=4))
        groups = [a for a in res["activities"] if len(a["members"]) > 1]
        self.assertEqual(groups, [])


class AnchorsPass1(Base):
    def test_two_rare_shared_identifiers_anchor_without_any_time(self):
        c = av.Clock()
        a, b = pair_visits(c, shared_terms=0, idents=("quorum_alpha.py", "quorum_beta.py"), gap=2 * LONG)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a, b))
        self.assertEqual(row(res, b)["explanation"]["rule"], "H3")
        self.assertEqual(row(res, b)["explanation"]["pass"], 1)

    def test_a_cannot_link_is_checked_before_any_anchor(self):
        c = av.Clock()
        ids = ("quorum_alpha.py", "quorum_beta.py")
        a = c.visit("br1", url="github.com/acme/billing/tree/feature-retry", terms=W("brx", 4), idents=ids)
        b = c.visit("br2", url="github.com/acme/billing/tree/fix-paging", terms=W("bry", 4), idents=ids)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.constraints.is_cannot_link(a, b))
        self.assertFalse(together(res, a, b))

    def test_one_shared_identifier_is_soft_and_cannot_anchor(self):
        c = av.Clock()
        a, b = pair_visits(c, shared_terms=0, idents=("quorum_alpha.py",))
        c.fillers(24)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))

    def test_artifact_identity_needs_independent_content_corroboration(self):
        c = av.Clock()
        url = "github.com/acme/billing/pull/12"
        a = c.visit("p1", url=url, terms=W("pra", 8), window="Alpha review")
        b = c.visit("p2", url=url + "/files", terms=W("prb", 8), window="Omega review", gap=200.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("same_pr_issue"))
        self.assertFalse(together(res, a, b), "same PR with disjoint content must not merge on identity alone")
        c = av.Clock()
        sh = W("prs", 4)
        a = c.visit("p1", url=url, terms=sh + W("pra", 4), window="Alpha review")
        b = c.visit("p2", url=url + "/files", terms=sh + W("prb", 4), window="Omega review", gap=200.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a, b))

    def test_version_lineage_alone_cannot_assign_and_corroborated_lineage_can(self):
        def build(shared):
            c = av.Clock()
            sh = W("lns", 4)
            a = c.visit("f1", kind="code_file", app="Keynote", target="Pitch_v1.key", window="Pitch_v1.key - Deck",
                        terms=W("lna", 4) + (sh if shared else W("lnc", 4)))
            b = c.visit("f2", kind="code_file", app="Keynote", target="Pitch_v2.key", window="Pitch_v2.key - Deck",
                        terms=W("lnb", 4) + (sh if shared else W("lnd", 4)), gap=3000.0)
            c.fillers(24)
            return c, a, b
        c, a, b = build(False)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("version_lineage"))
        self.assertFalse(together(res, a, b))
        c, a, b = build(True)
        res, _, _ = go(c)
        self.assertTrue(together(res, a, b))
        self.assertEqual(row(res, b)["explanation"]["pass"], 1)


class SoftEvidence(Base):
    def test_lexical_overlap_alone_never_reaches_the_threshold(self):
        c = av.Clock()
        a, b = pair_visits(c, shared_terms=4)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))
        self.assertEqual(row(res, b)["state"], ac.AMBIGUOUS if row(res, b)["state"] != ac.ASSIGNED else ac.ASSIGNED)

    def test_two_families_reach_the_soft_threshold_and_decay_removes_them(self):
        def build(gap):
            c = av.Clock()
            sh = W("sfs", 4)
            url = "mail.example.com/inbox"                        # a hub page: its identity is demoted to soft
            a = c.visit("d1", url=url, kind="mail", terms=sh + W("sfa", 4))
            b = c.visit("d1", url=url, kind="mail", terms=sh + W("sfb", 4), gap=gap)
            c.fillers(24)
            return c, a, b
        c, a, b = build(200.0)                                     # past the handoff window, inside T_resume
        res, fs, es = go(c)
        self.assertTrue(together(res, a, b))
        self.assertEqual(row(res, b)["explanation"]["rule"], "soft")
        self.assertEqual(sorted(row(res, b)["explanation"]["families"]), ["artifact", "lexical"])
        self.assertEqual(row(res, b)["confidence"]["level"], "medium")
        c, a, b = build(1500.0)                                    # decay: both soft families have lost most of their weight
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b), "time alone never adds, and decay shrinks the soft signals")

    def test_same_artifact_without_a_second_family_stays_tentative_or_ambiguous_then_a_new_activity(self):
        def build(gap):
            c = av.Clock()
            url = "mail.example.com/inbox"
            a = c.visit("d1", url=url, kind="mail", terms=W("tba", 5))
            b = c.visit("d1", url=url, kind="mail", terms=W("tbb", 5), gap=gap)
            c.fillers(24)
            return c, a, b
        c, a, b = build(200.0)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))
        self.assertIn((row(res, b)["state"], row(res, b)["reason"]), {(ac.TENTATIVE, "artifact_only"), (ac.AMBIGUOUS, "shared_artifact")})
        self.assertEqual([c_["activity_id"] for c_ in row(res, b)["candidates"]], [row(res, a)["activity_id"]])
        c, a, b = build(3 * 3600.0)                                # long gap: a new activity that shares the artifact
        res, _, _ = go(c)
        self.assertEqual((row(res, b)["state"], row(res, b)["reason"]), (ac.ASSIGNED, "seed"))
        sharing = {x["id"]: x["shares_artifact_with"] for x in res["activities"]}
        self.assertEqual(sharing[row(res, b)["activity_id"]], [row(res, a)["activity_id"]])

    def test_a_hub_artifact_never_assigns_alone(self):
        c = av.Clock()
        a = c.visit("mail", url="mail.example.com/inbox", kind="mail", terms=W("hba", 5))
        b = c.visit("mail", url="mail.example.com/inbox", kind="mail", terms=W("hbb", 5), gap=30.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))

    def test_same_branch_is_contextual_and_cannot_establish_an_assignment(self):
        c = av.Clock()
        a = c.visit("b1", kind="code_file", app="Code", target="a.py", window="a.py - billing (dev-wip)", terms=W("sba", 5))
        b = c.visit("b2", kind="code_file", app="Code", target="b.py", window="b.py - billing (dev-wip)", terms=W("sbb", 5), gap=200.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("same_workspace_branch"))
        self.assertFalse(together(res, a, b))

    def test_repo_backed_same_branch_is_contextual_too(self):
        cfg = inf.SCORING
        s = inf.Sig()
        s.ctx["same_branch"] = cfg.w_same_branch
        self.assertLess(s.total(cfg), cfg.weak_floor)
        self.assertEqual(s.families(cfg)[0], set())

    def test_same_branch_cannot_tip_a_margin(self):
        cfg = inf.SCORING
        sig_a, sig_b = inf.Sig(), inf.Sig()
        sig_a.put("lexical", 3.0, "S2"), sig_a.put("artifact", 2.0, "S1")
        sig_b.put("lexical", 3.0, "S2"), sig_b.put("artifact", 1.5, "S1")
        sig_a.ctx["same_branch"] = cfg.w_same_branch
        # the contextual part is capped and excluded from the substantive margin that the decision tests
        self.assertLessEqual(sig_a.contextual(cfg), cfg.contextual_cap)
        self.assertAlmostEqual(sig_a.net_substantive() - sig_b.net_substantive(), 0.5)
        self.assertLess(sig_a.net_substantive() - sig_b.net_substantive(), cfg.margin)

    def test_same_locator_and_same_file_name_cannot_assign_alone(self):
        c = av.Clock()
        a = c.visit("l1", url="docs.example.com/guides/one", terms=W("lca", 5))
        b = c.visit("l2", url="docs.example.com/guides/two", terms=W("lcb", 5), gap=10.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("same_locator"))
        self.assertFalse(together(res, a, b))

    def test_adjacency_app_and_domain_never_score(self):
        c = av.Clock()
        a = c.visit("x1", url="site.example.com/a/one", terms=W("ada", 5), app="Safari")
        b = c.visit("x2", url="site.example.com/b/two", terms=W("adb", 5), app="Safari", gap=1.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))


class ContrastAndHandoff(Base):
    def test_different_prs_are_contrast_never_a_cannot_link_and_can_share_an_activity(self):
        c = av.Clock()
        t12, t15 = "Add retry - Pull Request #12 · acme/billing", "Fix paging - Pull Request #15 · acme/billing"
        ids = ("quorum_alpha.py", "quorum_beta.py")
        a = c.visit("p12", url="github.com/acme/billing/pull/12", terms=W("pca", 4), idents=ids, window=t12)
        b = c.visit("p15", url="github.com/acme/billing/pull/15", terms=W("pcb", 4), idents=ids, window=t15, gap=60.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("different_pr_issue"))
        self.assertFalse(es.constraints.is_cannot_link(a, b))
        self.assertTrue(together(res, a, b), "stronger corroborating evidence lets one Activity span two PRs")

    def test_different_prs_without_other_evidence_stay_apart(self):
        c = av.Clock()
        sh = W("pds", 4)
        a = c.visit("p12", url="github.com/acme/billing/pull/12", terms=sh + W("pda", 4), window="Add retry - Pull Request #12 · acme/billing")
        b = c.visit("p15", url="github.com/acme/billing/pull/15", terms=sh + W("pdb", 4), window="Fix paging - Pull Request #15 · acme/billing", gap=200.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertFalse(es.constraints.is_cannot_link(a, b))
        self.assertFalse(together(res, a, b))

    def test_handoff_needs_the_short_switch(self):
        def build(gap):
            c = av.Clock()
            a = c.visit("h1", url="docs.example.com/x/one", terms=W("hoa", 5), idents=("quorum_alpha.py",), dwell=60.0)
            b = c.visit("h2", app="Terminal", kind="terminal", url=None, window="shell", terms=W("hob", 5),
                        idents=("quorum_alpha.py",), gap=gap, dwell=60.0)
            c.fillers(24)
            return c, a, b
        c, a, b = build(20.0)
        res, _, _ = go(c)
        self.assertTrue(together(res, a, b))
        self.assertEqual(row(res, b)["explanation"]["rule"], "H4")
        c, a, b = build(600.0)
        res, _, _ = go(c)
        self.assertFalse(together(res, a, b))


class DecisionStates(Base):
    def test_a_visit_equally_close_to_two_activities_is_ambiguous(self):
        c = av.Clock()
        ids1, ids2 = ("quorum_alpha.py", "quorum_beta.py"), ("zephyr_alpha.py", "zephyr_beta.py")
        a1 = c.visit("m1", url="s1.example.com/a/1", terms=W("mma", 4), idents=ids1)
        a2 = c.visit("m2", url="s1.example.com/b/2", terms=W("mmb", 4), idents=ids1, gap=30.0)
        b1 = c.visit("m3", url="s2.example.com/a/3", terms=W("mmc", 4), idents=ids2, gap=30.0)
        b2 = c.visit("m4", url="s2.example.com/b/4", terms=W("mmd", 4), idents=ids2, gap=30.0)
        x = c.visit("m5", url="s3.example.com/a/5", terms=W("mme", 4), idents=("quorum_alpha.py", "zephyr_alpha.py", "quorum_beta.py", "zephyr_beta.py"), gap=30.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a1, a2))
        self.assertTrue(together(res, b1, b2))
        self.assertFalse(together(res, a1, b1))
        r = row(res, x)
        self.assertEqual((r["state"], r["reason"]), (ac.AMBIGUOUS, "margin_too_small"))
        self.assertIsNone(r["activity_id"])
        self.assertEqual(len(r["candidates"]), 2)

    def test_a_glance_with_no_evidence_is_not_placed(self):
        c = av.Clock()
        c.visit("g1", url="s1.example.com/a/1", terms=W("gla", 5), dwell=60.0)
        g = c.visit("g2", url="s9.example.com/z", terms=[], dwell=3.0, frames=1)
        c.fillers(24)
        res, _, _ = go(c)
        r = row(res, g)
        self.assertEqual(r["state"], ac.UNASSIGNED)
        self.assertIn(r["reason"], {"no_substance", "adjacency_only"})

    def test_a_substantive_visit_with_no_candidate_becomes_a_seed(self):
        c = self.padded(12)
        res, _, _ = go(c)
        for r in res["visits"]:
            self.assertEqual((r["state"], r["reason"]), (ac.ASSIGNED, "seed"))
            self.assertEqual(r["confidence"]["level"], "low")

    def test_every_assigned_visit_is_explained_and_every_non_assigned_has_a_reason(self):
        c = av.Clock()
        pair_visits(c, idents=("quorum_alpha.py", "quorum_beta.py"))
        c.fillers(24)
        res, _, _ = go(c)
        for r in res["visits"]:
            if r["state"] == ac.ASSIGNED:
                self.assertTrue(r["explanation"]["rule"])
                self.assertIn(r["confidence"]["level"], inf.LEVELS)
            else:
                self.assertIn(r["reason"], ac.REASONS)

    def test_interruption_does_not_join_without_its_own_evidence(self):
        c = av.Clock()
        ids = ("quorum_alpha.py", "quorum_beta.py")
        a1 = c.visit("i1", url="s1.example.com/a/1", terms=W("ina", 4), idents=ids)
        x = c.visit("slack", url="chat.example.com/room/x", kind="chat", terms=W("inx", 5), dwell=40.0, gap=10.0)
        a2 = c.visit("i2", url="s1.example.com/b/2", terms=W("inb", 4), idents=ids, gap=10.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a1, a2))
        self.assertFalse(together(res, a1, x))
        self.assertEqual([i["visit_id"] for i in res["relations"]["interruptions"]], [x])

    def test_a_return_is_recorded_not_merged_by_adjacency(self):
        c = av.Clock()
        ids = ("quorum_alpha.py", "quorum_beta.py")
        a1 = c.visit("r1", url="s1.example.com/a/1", terms=W("rta", 4), idents=ids)
        c.visit("o1", url="s5.example.com/o/1", terms=W("rto", 5))
        a2 = c.visit("r2", url="s1.example.com/b/2", terms=W("rtb", 4), idents=ids, gap=1200.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a1, a2))
        self.assertEqual([r["visit_id"] for r in res["relations"]["returns"]], [a2])


class Consolidation(Base):
    def test_pass3_merges_established_activities_only_on_links_that_do_not_pass_through_one_visit(self):
        def build(second_link):
            c = av.Clock()
            a1 = c.visit("c1", url="s1.example.com/a/1", terms=W("cna", 4), idents=("aa_one.py", "aa_two.py", "aa_three.py", "link_one.py", "link_two.py"))
            a2 = c.visit("c2", url="s1.example.com/b/2", terms=W("cnb", 4), idents=("aa_one.py", "aa_two.py", "aa_three.py") + (("link_three.py", "link_four.py") if second_link else ()), gap=200.0)
            b1 = c.visit("c3", url="s2.example.com/a/3", terms=W("cnc", 4), idents=("bb_one.py", "bb_two.py", "bb_three.py", "link_one.py", "link_two.py"), gap=200.0)
            b2 = c.visit("c4", url="s2.example.com/b/4", terms=W("cnd", 4), idents=("bb_one.py", "bb_two.py", "bb_three.py") + (("link_three.py", "link_four.py") if second_link else ()), gap=200.0)
            c.fillers(24)
            return c, a1, a2, b1, b2
        c, a1, a2, b1, b2 = build(True)
        res, _, _ = go(c)
        self.assertTrue(all(together(res, a1, x) for x in (a2, b1, b2)), "two independent hard links merge two established activities")
        c, a1, a2, b1, b2 = build(False)
        res, _, _ = go(c)
        self.assertFalse(together(res, a1, b1), "one link between two established activities is not enough")
        self.assertFalse(together(res, a2, b2))
        self.assertNotEqual(row(res, b1)["state"], ac.ASSIGNED if together(res, a1, b1) else None)

    def test_cannot_linked_activities_never_merge(self):
        c = av.Clock()
        ids = ("quorum_alpha.py", "quorum_beta.py")
        a1 = c.visit("c1", url="github.com/acme/billing/tree/feature-retry", terms=W("cxa", 4), idents=ids)
        a2 = c.visit("c2", url="github.com/acme/billing/tree/feature-retry/src", terms=W("cxb", 4), idents=ids, gap=20.0)
        b1 = c.visit("c3", url="github.com/acme/billing/tree/fix-paging", terms=W("cxc", 4), idents=ids, gap=20.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.constraints.is_cannot_link(a1, b1))
        self.assertFalse(together(res, a1, b1))
        self.assertFalse(together(res, a2, b1))

    def test_check_detects_a_withdrawn_assignment_a_split_and_a_cannot_linked_pair(self):
        c = av.Clock()
        a = c.visit("k1", url="github.com/acme/billing/tree/feature-retry", terms=W("cka", 4))
        b = c.visit("k2", url="github.com/acme/billing/tree/fix-paging", terms=W("ckb", 4), gap=20.0)
        c.fillers(10)
        fs, es = ev.analyse(prep(c), None, RP)
        ctx = inf._Ctx(fs, es, inf.SCORING)
        st = inf._State(ctx)
        k = st.new(a)
        before = st.snapshot()
        st.add(k, b)                                               # a cannot-linked pair forced into one activity
        with self.assertRaises(inf.ConstraintViolation):
            inf._check(ctx, st, before, "test")
        st2 = inf._State(ctx)
        st2.new(a)
        with self.assertRaises(inf.ConstraintViolation):
            inf._check(ctx, st2, {b: 0}, "test")                   # b was assigned before and is gone now
        st3 = inf._State(ctx)
        k1 = st3.new(a)
        k2 = st3.new(b)
        st3.add(k1, c.records[-1]["visit_id"])
        with self.assertRaises(inf.ConstraintViolation):
            inf._check(ctx, st3, {a: k1, b: k1}, "test")           # a group that was one is now two


class Corrections(Base):
    def _hub(self, c, idents=()):
        """Six visits of one page: with max_group_visits=4 its pair listing is omitted, so each of them is 'affected'."""
        out = []
        for i in range(6):
            out.append(c.visit("hubdoc", url="docs.example.com/d/shared", kind="document",
                               terms=W(f"hb{chr(97 + i)}", 5), idents=list(idents) if i < 2 else [], gap=900.0))
        return out

    def test_an_affected_visit_never_assigns_even_with_no_visible_runner_up(self):
        c = av.Clock()
        hub = self._hub(c, idents=("quorum_alpha.py", "quorum_beta.py"))
        c.fillers(20)
        small = ev.EvidenceParams(max_group_visits=4)
        res, fs, es = go(c, ep=small)
        self.assertFalse(es.support_complete)
        self.assertTrue(together(res, hub[0], hub[1]) is False)       # explicit H3 evidence, but no margin can be believed
        for v in hub[:2]:
            r = row(res, v)
            self.assertEqual((r["state"], r["reason"]), (ac.AMBIGUOUS, "insufficient_families"))
            self.assertTrue(r["explanation"]["incomplete_support"])
        res_full, _, es_full = go(c)                                   # complete support: the same pair assigns
        self.assertTrue(es_full.support_complete)
        self.assertTrue(together(res_full, hub[0], hub[1]))

    def test_a_merge_is_not_made_when_a_member_could_have_an_unlisted_rival(self):
        c = av.Clock()
        a1 = c.visit("c1", url="s1.example.com/a/1", terms=W("cna", 4), idents=("aa_one.py", "aa_two.py", "aa_three.py", "link_one.py", "link_two.py"))
        a2 = c.visit("c2", url="s1.example.com/b/2", terms=W("cnb", 4), idents=("aa_one.py", "aa_two.py", "aa_three.py", "link_three.py", "link_four.py"), gap=200.0)
        b1 = c.visit("c3", url="s2.example.com/a/3", terms=W("cnc", 4), idents=("bb_one.py", "bb_two.py", "bb_three.py", "link_one.py", "link_two.py"), gap=200.0)
        b2 = c.visit("c4", url="s2.example.com/b/4", terms=W("cnd", 4), idents=("bb_one.py", "bb_two.py", "bb_three.py", "link_three.py", "link_four.py"), gap=200.0)
        c.fillers(24)

        def merged(affected):
            fs, es = ev.analyse(prep(c), None, RP)
            ctx = inf._Ctx(fs, es, inf.SCORING)
            ctx.affected = set(affected)
            st = inf._State(ctx)
            for grp in ((a1, a2), (b1, b2)):
                k = st.new(grp[0])
                st.add(k, grp[1])
            inf._merge_pass(ctx, st)
            return len(st.members)
        self.assertEqual(merged([]), 1)
        self.assertEqual(merged([a1]), 2)

    def _bridge(self, flip):
        c = av.Clock()
        one, two = ("aa_one.py", "aa_two.py", "aa_three.py"), ("bb_one.py", "bb_two.py", "bb_three.py")
        link = ("link_one.py", "link_two.py")
        first, second = (one, two) if not flip else (two, one)
        a1 = c.visit("c1", url="s1.example.com/a/1", terms=W("cna", 4), idents=first + link)
        a2 = c.visit("c2", url="s1.example.com/b/2", terms=W("cnb", 4), idents=first, gap=200.0)
        b1 = c.visit("c3", url="s2.example.com/a/3", terms=W("cnc", 4), idents=second + link, gap=200.0)
        b2 = c.visit("c4", url="s2.example.com/b/4", terms=W("cnd", 4), idents=second, gap=200.0)
        c.fillers(24)
        return c, a1, a2, b1, b2

    def test_bridge_visits_stay_ambiguous_whatever_their_order(self):
        for flip in (False, True):
            c, a1, a2, b1, b2 = self._bridge(flip)
            res, _, _ = go(c)
            for v in (a1, b1):
                r = row(res, v)
                self.assertEqual((r["state"], r["reason"]), (ac.AMBIGUOUS, "margin_too_small"), (flip, v))
            self.assertFalse(together(res, a1, b1))
        # exchanging the two sides leaves the outcome of the corresponding visits unchanged
        c1, *v1 = self._bridge(False)
        c2, *v2 = self._bridge(True)
        r1, r2 = go(c1)[0], go(c2)[0]
        for x, y in zip(v1, v2):
            self.assertEqual((row(r1, x)["state"], row(r1, x)["reason"]), (row(r2, y)["state"], row(r2, y)["reason"]))

    def test_locator_is_never_a_corroborating_family_and_never_tips_a_margin(self):
        cfg = inf.SCORING
        s = inf.Sig()
        s.put("artifact", 3.0, "S1")
        s.locator = cfg.w_s3_locator
        self.assertLess(len(s.families(cfg)[0]), cfg.min_families)        # the locator is not counted as a second family
        c = av.Clock()
        ids1, ids2 = ("quorum_alpha.py", "quorum_beta.py"), ("zephyr_alpha.py", "zephyr_beta.py")
        c.visit("m1", url="s1.example.com/a/1", terms=W("mma", 4), idents=ids1)
        c.visit("m2", url="s1.example.com/b/2", terms=W("mmb", 4), idents=ids1, gap=200.0)
        c.visit("m3", url="s2.example.com/a/3", terms=W("mmc", 4), idents=ids2, gap=200.0)
        c.visit("m4", url="s2.example.com/b/4", terms=W("mmd", 4), idents=ids2, gap=200.0)
        x = c.visit("m5", url="s1.example.com/a/5", terms=W("mme", 4),         # same locator as the first group only
                    idents=("quorum_alpha.py", "zephyr_alpha.py", "quorum_beta.py", "zephyr_beta.py"), gap=200.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("same_locator"))
        r = row(res, x)
        self.assertEqual((r["state"], r["reason"]), (ac.AMBIGUOUS, "margin_too_small"))

    def test_locator_plus_one_family_below_threshold_does_not_assign(self):
        c = av.Clock()
        sh = W("lps", 4)
        a = c.visit("l1", url="docs.example.com/guides/one", terms=sh + W("lpa", 4))
        b = c.visit("l2", url="docs.example.com/guides/two", terms=sh + W("lpb", 4), gap=200.0)
        c.fillers(24)
        res, fs, es = go(c)
        self.assertTrue(es.by_type("same_locator") and es.by_type("shared_terms"))
        self.assertFalse(together(res, a, b))


class Scoring(unittest.TestCase):
    def test_all_weights_and_thresholds_live_in_one_config(self):
        cfg = inf.SCORING
        self.assertEqual((cfg.assign_threshold, cfg.weak_floor, cfg.margin, cfg.min_families), (4.0, 2.0, 2.0, 2))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.margin = 1.0
        self.assertTrue(dataclasses.is_dataclass(cfg))

    def test_no_scoring_numbers_are_scattered_in_the_module(self):
        src = open(os.path.join(os.path.dirname(inf.__file__), "activity_inference.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        cfg_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ScoringConfig")
        inside = {id(x) for x in ast.walk(cfg_class)}
        loose = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)
                 and id(n) not in inside and n.value not in (0.0, 0.5, 1.0)]
        self.assertEqual(loose, [], "a float scoring constant lives outside ScoringConfig")

    def test_family_scores_take_the_maximum_not_the_sum(self):
        s = inf.Sig()
        s.put("lexical", 3.0, "S2")
        s.put("lexical", 2.0, "S4")
        s.put("lexical", 2.5, "S2")
        self.assertEqual(s.substantive(), 3.0)
        t = inf.Sig()
        t.put("artifact", 2.0, "S1")
        s.absorb(t)
        self.assertEqual(s.substantive(), 5.0)
        self.assertEqual(s.families(inf.SCORING)[0], {"lexical", "artifact"})

    def test_contextual_evidence_is_capped_and_never_a_family(self):
        cfg = inf.SCORING
        s = inf.Sig()
        s.ctx.update({"same_branch": 0.5, "same_file_name": 0.5, "version_lineage": 1.0})
        s.locator = 1.0
        self.assertEqual(s.contextual(cfg), cfg.contextual_cap)
        self.assertEqual(s.families(cfg)[0], set())
        self.assertEqual(s.substantive(), 0.0)

    def test_contrast_and_age_subtract(self):
        cfg = inf.SCORING
        s = inf.Sig()
        s.put("lexical", 3.0, "S2")
        s.pen["different_pr_issue"] = cfg.w_diff_pr
        self.assertEqual(s.total(cfg), 3.0 + cfg.w_diff_pr)

    def test_a_threshold_comes_from_the_config(self):
        c = av.Clock()
        sh = W("cfs", 4)
        url = "mail.example.com/inbox"
        a = c.visit("d1", url=url, kind="mail", terms=sh + W("cfa", 4))
        b = c.visit("d1", url=url, kind="mail", terms=sh + W("cfb", 4), gap=200.0)
        c.fillers(24)
        res, _, _ = go(c)
        self.assertTrue(together(res, a, b))
        res, _, _ = go(c, scoring=dataclasses.replace(inf.SCORING, assign_threshold=50.0))
        self.assertFalse(together(res, a, b))


class InputsAndPurity(Base):
    def test_activity_profile_ignores_non_assigned_visits(self):
        def build(extra):
            c = av.Clock()
            a, b = pair_visits(c, idents=("quorum_alpha.py", "quorum_beta.py"))
            if extra:
                c.visit("empty", url="blank.example.com/x", terms=[], dwell=2.0, frames=1)   # a glance with an empty digest
            c.fillers(24)
            return c, a, b
        c1, a, b = build(False)
        c2, a2, b2 = build(True)
        r1, _, _ = go(c1)
        r2, _, _ = go(c2)
        self.assertEqual(sorted(len(x["members"]) for x in r1["activities"] if len(x["members"]) > 1),
                         sorted(len(x["members"]) for x in r2["activities"] if len(x["members"]) > 1))
        self.assertTrue(together(r1, a, b) and together(r2, a2, b2))

    def test_module_imports_only_what_the_pipeline_allows(self):
        src = open(os.path.join(os.path.dirname(inf.__file__), "activity_inference.py"), encoding="utf-8").read()
        mods = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module.split(".")[0])
        self.assertEqual(mods - {"math", "dataclasses", "activity_contract", "activity_evidence", "activity_features"}, set())

    def test_replay_is_the_public_entry_and_matches_infer(self):
        c = av.Clock()
        pair_visits(c, idents=("quorum_alpha.py", "quorum_beta.py"))
        c.fillers(24)
        via_replay = ac.replay(inf.infer, c.records, RP)
        direct = inf.infer(prep(c), RP)
        self.assertTrue(hn.same_outcome(via_replay, direct))
        self.assertEqual(ac.validate_result(via_replay, {v.visit_id: v.t_start for v in prep(c).visits}), [])


class FixturesAndHarness(unittest.TestCase):
    def test_every_fixture_is_valid_never_breaks_a_must_not_merge_or_a_cannot_link(self):
        for name, build in fx.BUILDERS.items():
            f = build()
            res = hn.run(inf.infer, f.records, RP)
            self.assertEqual(ac.validate_result(res, hn._starts(f.records)), [], name)
            self.assertEqual(hn.check_must_pairs(inf.infer, f.records, RP, not_merge=f.must_not_merge), [], name)
            self.assertEqual(hn.check_cannot_links(inf.infer, f.records, RP, f.cannot_link), [], name)

    def test_pr_numbers_differ_keeps_the_two_pr_groups_apart_without_a_cannot_link(self):
        f = fx.pr_numbers_differ()
        self.assertEqual(f.cannot_link, [])
        self.assertTrue(f.must_not_merge)
        fs, es = ev.analyse(ai.prepare(f.records), None, RP)
        self.assertFalse(es.cannot_links())
        self.assertTrue(es.by_type("different_pr_issue"))
        self.assertEqual(hn.check_must_pairs(inf.infer, f.records, RP, not_merge=f.must_not_merge), [])

    def test_the_invariant_harness_passes_on_every_fixture(self):
        for name, build in fx.BUILDERS.items():
            f = build()
            r = hn.run_all(inf.infer, f.records, RP, forget_visits=f.visit_ids()[:2])
            self.assertEqual({k: v for k, v in r.items() if v}, {}, name)

    def test_the_invariant_harness_passes_on_scenarios_that_actually_merge(self):
        c = av.Clock()
        ids = ("quorum_alpha.py", "quorum_beta.py")
        pair_visits(c, idents=ids, tag="hn")
        c.visit("slack", url="chat.example.com/room/x", kind="chat", terms=W("hnx", 5), dwell=40.0, gap=10.0)
        sh = W("mls", 4)
        url = "mail.example.com/inbox"
        c.visit("d1", url=url, kind="mail", terms=sh + W("mla", 4), gap=900.0)
        c.visit("d1", url=url, kind="mail", terms=sh + W("mlb", 4), gap=200.0)
        c.fillers(24)
        res = hn.run(inf.infer, c.records, RP)
        self.assertGreaterEqual(sum(1 for a in res["activities"] if len(a["members"]) > 1), 2)
        vids = list(dict.fromkeys(r["visit_id"] for r in c.records))
        r = hn.run_all(inf.infer, c.records, RP, forget_visits=vids[:2])
        self.assertEqual({k: v for k, v in r.items() if v}, {})

    def test_handoff_is_a_declared_and_scaled_time_parameter(self):
        self.assertIn("handoff_s", ac.ASSIGNMENT_TIME)
        self.assertEqual(ac.ReplayParams().scaled(3).handoff_s, 180.0)
        self.assertEqual(hn.check_time_params_declared(), [])


if __name__ == "__main__":
    unittest.main()

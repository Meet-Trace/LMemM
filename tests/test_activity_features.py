"""PR 2B: deterministic visit features (src/activity_features.py)."""
import copy
import json
import math
import re
import types
import unittest

import activity_features as af
import activity_fixtures as fx
import activity_harness as hn
import activity_input as ai
import activity_visits as av
from activity_contract import ReplayParams

RP = ReplayParams()
SECONDS_FIELDS = {"t_start", "t_end", "dwell_s"}


def feats(clock, rp=RP, p=None):
    return af.extract(ai.prepare(clock.records, max_intra_visit_gap_s=rp.max_intra_visit_gap_s), p, rp)


def padded(n=25):
    c = av.Clock()
    c.fillers(n)
    return c


def close(a, b, rel=1e-9):
    """Nested equality where floats match to a relative tolerance."""
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, rel) for x, y in zip(a, b))
    return a == b


class TokenClassTests(unittest.TestCase):
    def test_every_token_has_exactly_one_class_and_no_hub_is_rare(self):
        for n in range(1, 201):
            for df in range(1, n + 1):
                c = af.token_class(df, n)
                self.assertIn(c, ("hub", "single", "rare", "common"))
                self.assertEqual(c == "hub", df * 5 > n, (n, df))
                if c == "hub":
                    self.assertNotEqual(c, "rare")

    def test_the_documented_example_with_ten_visits(self):
        self.assertEqual([af.token_class(d, 10) for d in (1, 2, 3, 4)], ["single", "rare", "hub", "hub"])

    def test_rare_cutoff_follows_max_of_three_and_five_percent(self):
        self.assertEqual(af.token_class(3, 100), "rare")
        self.assertEqual(af.token_class(5, 100), "rare")
        self.assertEqual(af.token_class(6, 100), "common")
        self.assertEqual(af.token_class(7, 140), "rare")      # ceil(140 / 20) = 7
        self.assertEqual(af.token_class(8, 140), "common")
        self.assertEqual(af.token_class(20, 100), "common")   # exactly 0.2 N is not a hub
        self.assertEqual(af.token_class(21, 100), "hub")

    def test_idf_hand_computed(self):
        self.assertAlmostEqual(af.idf(2, 9), math.log(10 / 3) / math.log(10))
        self.assertAlmostEqual(af.idf(1, 9), math.log(5) / math.log(10))
        self.assertEqual(af.idf(9, 9), 0.0 + math.log(10 / 10) / math.log(10))
        self.assertEqual(af.idf(1, 0), 0.0)
        for n in (1, 5, 80):
            for df in range(1, n + 1):
                self.assertTrue(0.0 <= af.idf(df, n) <= 1.0)


class TokenisationTests(unittest.TestCase):
    def test_golden_tokens_sources_and_weights(self):
        c = av.Clock()
        c.visit(1, url="docs.example.com/guides/Quarterly-Planning?token=SECRETWORD#frag", window="Budget Review - 2026",
                target="docs.example.com/guides/Quarterly-Planning", kind="document",
                terms=["Forecast", "the", "ab"], idents=["Budget_Model.xlsx", "#42"], lines=["capex payroll plan"])
        c.fillers(30)
        f = af.extract(ai.prepare(c.records), None, RP).visits[0]
        bag = f.bag_map()
        self.assertEqual(bag.get("forecast"), 0.8)               # OCR only: q = 0.8, terms 1.0
        self.assertEqual(bag.get("budget_model.xlsx"), 0.8)
        self.assertEqual(bag.get("capex"), 0.8 * 0.5)            # words from lines count half
        self.assertIn("quarterly", bag)                          # URL path words
        self.assertIn("planning", bag)
        self.assertIn("review", bag)                             # title words
        self.assertNotIn("secretword", bag)                      # the query is never read
        self.assertNotIn("frag", bag)
        self.assertNotIn("docs", bag)                            # the host is a namespace, not content
        self.assertNotIn("the", bag)                             # stop word
        self.assertNotIn("ab", bag)                              # too short
        self.assertNotIn("2026", bag)                            # purely digits
        self.assertIn("#42", bag)
        self.assertTrue({"quarterly", "planning"} <= f.key_tokens)
        self.assertNotIn("forecast", f.key_tokens)

    def test_a_token_keeps_its_largest_weight_over_moments_and_sources(self):
        c = padded(30)
        c.visit(1, kind="code_file", target="alpha.py", window="alpha.py - proj", app="Code",
                moments_spec=[{"dwell": 60, "terms": [], "idents": [], "lines": ["gearbox torque"]},
                              {"dwell": 60, "terms": ["gearbox"], "idents": [], "lines": []}])
        f = af.extract(ai.prepare(c.records), None, RP).visits[-1]
        self.assertEqual(f.bag_map()["gearbox"], 0.8)

    def test_hub_like_tokens_are_dropped_and_do_not_count_as_eligible_only_when_empty(self):
        c = av.Clock()
        for i in range(10):
            c.visit(i, url=f"a{i}.example.com/p", terms=["common", "uniq" + "abcdefghij"[i] * 3], window="x")
        c.visit(99, url="blank.example.com/p", window="y")           # empty bag
        fs = af.extract(ai.prepare(c.records), None, RP)
        self.assertEqual(fs.corpus.n, 10)                             # the empty-bag visit is not eligible
        self.assertEqual(fs.corpus.df["common"], 10)
        self.assertEqual(fs.corpus.cls["common"], "hub")
        self.assertTrue(all("common" not in f.bag_map() for f in fs.visits))
        self.assertEqual(fs.corpus.cls["uniqaaa"], "single")

    def test_document_frequency_counts_distinct_visits_not_occurrences(self):
        c = padded(20)
        c.visit(1, terms=["rareword", "rareword", "other"], lines=["rareword rareword rareword"], window="a",
                url="x.example.com/a/b")
        c.visit(2, terms=["rareword"], window="b", url="y.example.com/a/b")
        fs = af.extract(ai.prepare(c.records), None, RP)
        self.assertEqual(fs.corpus.df["rareword"], 2)
        self.assertEqual(fs.corpus.cls["rareword"], "rare")


class QualityTests(unittest.TestCase):
    def q(self, **kw):
        c = padded(25)
        c.visit(1, url="x.example.com/a/b", terms=kw.pop("terms", ["alpha", "bravo", "charlie", "delta"]), **kw)
        return af.extract(ai.prepare(c.records), None, RP).visits[-1].quality

    def test_provenance_mix_sets_the_factor(self):
        self.assertEqual(self.q(prov={"ocr": 10})["q"], 0.8)
        self.assertEqual(self.q(prov={"ax": 10, "ocr": 10})["q"], 1.0)
        self.assertEqual(self.q(prov={"ax": 10})["q"], 1.0)
        self.assertEqual(self.q(prov={"ocr": 1}, frames=1)["q"], 0.8 * 0.6)
        self.assertEqual(self.q(prov={"ocr": 10})["source"], "ocr")
        self.assertEqual(self.q(prov={"ax": 3, "ocr": 4})["source"], "ax+ocr")

    def test_empty_digest_sets_half_and_content_richness(self):
        e = self.q(terms=[])
        self.assertEqual((e["q"], e["content"], e["digest_empty"]), (0.5, "empty", True))
        self.assertEqual(self.q(terms=["alphaword", "bravoword"])["content"], "thin")
        self.assertEqual(self.q()["content"], "ok")

    def test_missing_or_malformed_optional_fields_do_not_break_extraction(self):
        c = padded(25)
        c.visit(1, url=None, target=None, window=None, kind=None, app="Mystery", prov={})
        recs = copy.deepcopy(c.records)
        recs[-1]["provenance"] = None
        recs[-1]["digest"] = {}
        recs[-1]["interaction"] = {}
        recs[-1]["context"] = {}
        f = af.extract(ai.prepare(recs), None, RP).visits[-1]
        self.assertEqual(f.quality["source"], "none")
        self.assertEqual(f.bag, ())
        self.assertTrue(f.weak_evidence)
        self.assertIsNone(f.branch)
        json.dumps(f.to_dict())


class ArtifactTests(unittest.TestCase):
    def arts(self, **kw):
        c = padded(25)
        c.visit(1, **kw)
        return {(a["type"], a["key"]): a for a in af.extract(ai.prepare(c.records), None, RP).visits[-1].artifacts}

    def test_pull_request_page_gives_pr_page_and_item_artifacts(self):
        a = self.arts(url="GitHub.com/Acme/Billing/pull/12/files?diff=split#x", window="Fix - Pull Request #12")
        self.assertIn(("pr", "github.com/acme/billing#12"), a)
        self.assertEqual(a[("pr", "github.com/acme/billing#12")]["number"], 12)
        self.assertIn(("page", "github.com/acme/billing/pull/12/files"), a)
        self.assertIn(("item", "item-1"), a)
        self.assertNotIn("diff", json.dumps(sorted(map(str, a))))

    def test_issue_and_merge_request_and_dotgit(self):
        self.assertIn(("issue", "github.com/acme/billing#7"), self.arts(url="github.com/acme/billing/issues/7"))
        self.assertIn(("pr", "gitlab.com/acme/billing#3"), self.arts(url="gitlab.com/acme/billing/-/merge_requests/3"))
        self.assertIn(("pr", "github.com/acme/billing#5"), self.arts(url="github.com/acme/billing.git/pull/5"))

    def test_generic_pages_are_never_id_bearing(self):
        for url in ("github.com/acme/billing", "github.com/acme/billing/pulls", "github.com/acme/billing/issues"):
            a = [v for k, v in self.arts(url=url).items() if k[0] == "page"][0]
            self.assertFalse(a["id_bearing"], url)
            self.assertTrue(a["hub_pattern"], url)

    def test_hub_pattern_kinds_and_segments(self):
        self.assertTrue([v for k, v in self.arts(url="mail.example.com/mail/u/0", kind="mail").items() if k[0] == "page"][0]["hub_pattern"])
        self.assertTrue([v for k, v in self.arts(url="app.example.com/calendar/week").items() if k[0] == "page"][0]["hub_pattern"])
        self.assertTrue([v for k, v in self.arts(url="www.example.com").items() if k[0] == "page"][0]["hub_pattern"])
        a = [v for k, v in self.arts(url="arxiv.org/abs/2601.00123").items() if k[0] == "page"][0]
        self.assertTrue(a["id_bearing"] and not a["hub_pattern"])

    def test_document_url_is_a_document_artifact(self):
        a = self.arts(url="docs.example.com/cafe-budget", kind="document")
        self.assertTrue(a[("document", "docs.example.com/cafe-budget")]["id_bearing"])

    def test_file_artifact_with_workspace_and_branch_suffix(self):
        a = self.arts(kind="code_file", app="Visual Studio Code", target="Retry_Policy.py",
                      window="Retry_Policy.py - billing (feature-retry) - Visual Studio Code")
        f = a[("file", "billing/retry_policy.py")]
        self.assertEqual((f["name"], f["folder"], f["folder_known"]), ("retry_policy.py", "billing", True))

    def test_file_artifact_folder_from_target_and_unknown_folder(self):
        a = self.arts(kind="code_file", app="Code", target="src/pkg/mod.py", window="mod.py")
        self.assertEqual(a[("file", "src/pkg/mod.py")]["folder"], "src/pkg")
        b = self.arts(kind="code_file", app="Code", target="mod.py", window="something else")
        f = b[("file", "/mod.py")]
        self.assertFalse(f["folder_known"])

    def test_chat_artifact_is_a_hub_pattern(self):
        a = self.arts(kind="chat", app="Slack", target="c/42")
        self.assertTrue(a[("chat", "slack/c/42")]["hub_pattern"])

    def test_title_reference_with_repository(self):
        a = self.arts(kind="document", url=None, target=None, window="Fix the thing · Pull Request #99 · Acme/Billing")
        self.assertIn(("pr", "github.com/acme/billing#99"), a)

    def test_artifacts_are_sorted_and_unique(self):
        c = padded(25)
        c.visit(1, url="github.com/acme/billing/pull/12", window="Pull Request #12 · acme/billing")
        arts = af.extract(ai.prepare(c.records), None, RP).visits[-1].artifacts
        keys = [(a["type"], a["key"]) for a in arts]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual(sum(1 for k in keys if k[0] == "pr"), 1)    # URL and title name the same PR: one artifact


class NamespaceBranchLineageTests(unittest.TestCase):
    def one(self, **kw):
        c = padded(25)
        c.visit(1, **kw)
        return af.extract(ai.prepare(c.records), None, RP).visits[-1]

    def test_namespaces(self):
        f = self.one(url="github.com/acme/billing/pull/12")
        self.assertEqual(set(f.namespaces), {("domain", "github.com"), ("repo", "github.com/acme/billing")})
        g = self.one(url="docs.example.com/guides/a")
        self.assertEqual(set(g.namespaces), {("domain", "docs.example.com"), ("locator", "docs.example.com/guides")})
        h = self.one(kind="code_file", app="Code", target="mod.py", window="mod.py - proj - Code")
        self.assertIn(("folder", "proj"), h.namespaces)

    def test_branch_from_tree_page_workspace_suffix_and_terminal(self):
        self.assertEqual(self.one(url="github.com/acme/billing/tree/feature-retry").branch,
                         {"name": "feature-retry", "scope": "repo", "repo": "github.com/acme/billing", "workspace": None})
        self.assertEqual(self.one(kind="code_file", app="Code", target="m.py", window="m.py - billing (dev-wip)").branch,
                         {"name": "dev-wip", "scope": "workspace", "repo": None, "workspace": "billing"})
        t = self.one(kind="terminal", app="Terminal", target="zsh", window="~/billing git:(feature-x)")
        self.assertEqual((t.branch["name"], t.branch["scope"]), ("feature-x", "none"))
        self.assertIsNone(t.branch["repo"])                           # a Moment has no working directory

    def test_branch_absent_default_ambiguous_or_two_names(self):
        self.assertIsNone(self.one(url="github.com/acme/billing/tree/main").branch)
        self.assertIsNone(self.one(url="github.com/acme/billing/blob/feature/retry/a.py").branch)   # slash: ambiguous
        self.assertIsNone(self.one(url="github.com/acme/billing").branch)
        self.assertIsNone(self.one(kind="terminal", app="Terminal", target="zsh",
                                   window="git:(feature-x) [dev-wip]").branch)

    def test_lineage_keys(self):
        a = self.one(kind="code_file", app="Keynote", target="Pitch_v2.key", window="Pitch_v2.key - Deck - Keynote").lineage
        self.assertEqual((a["stem"], a["ext"], a["scope"], a["name"]), ("pitch", "key", "keynote|deck", "pitch_v2.key"))
        b = self.one(kind="code_file", app="Keynote", target="Pitch_final.key", window="Pitch_final.key - Deck - Keynote").lineage
        self.assertEqual((a["stem"], a["ext"], a["scope"]), (b["stem"], b["ext"], b["scope"]))
        d = self.one(kind="document", url="docs.example.com/d/1", window="Harbour proposal v3").lineage
        self.assertEqual((d["stem"], d["scope"]), ("harbour proposal", "google chrome|docs.example.com"))
        self.assertIsNone(self.one(kind="code_file", app="Code", target="ab.py", window="ab.py - p").lineage)  # stem too short


class GlanceSubstanceShiftTests(unittest.TestCase):
    def f(self, dwell=120.0, rp=RP, **kw):
        c = padded(25)
        c.visit(1, url="x.example.com/a/b", dwell=dwell, terms=["alpha", "bravo", "charlie", "delta"], **kw)
        return af.extract(ai.prepare(c.records), None, rp).visits[-1]

    def test_glance_boundary_at_exactly_the_parameter(self):
        self.assertTrue(self.f(dwell=29.999).glance)
        self.assertFalse(self.f(dwell=30.0).glance)
        self.assertTrue(self.f(dwell=30.0, rp=ReplayParams(glance_dwell_s=30.5)).glance)

    def test_substance_reasons(self):
        self.assertEqual(self.f(dwell=10.0).substance, {"ok": False, "reasons": ["glance", "short_dwell"]})
        self.assertEqual(self.f().substance, {"ok": True, "reasons": []})
        self.assertEqual(self.f(frames=1).substance["reasons"], ["few_frames"])

    def test_unknown_kind_needs_the_longer_dwell(self):
        c = padded(25)
        c.visit(1, kind="spreadsheet", url="x.example.com/a/b", dwell=100.0, terms=["alpha", "bravo", "charlie", "delta"])
        f = af.extract(ai.prepare(c.records), None, RP).visits[-1]
        self.assertFalse(f.known_kind)
        self.assertEqual(f.substance["reasons"], ["short_dwell"])      # 100 s < 120 s
        self.assertFalse(f.glance)

    def test_weak_evidence_is_empty_digest_without_an_identifying_artifact(self):
        c = padded(25)
        c.visit(1, kind="mail", url="mail.example.com/mail/u/0", window="Inbox")
        c.visit(2, kind="document", url="docs.example.com/d/9", window="Doc")
        a, b = af.extract(ai.prepare(c.records), None, RP).visits[-2:]
        self.assertTrue(a.weak_evidence)
        self.assertFalse(b.weak_evidence)                              # an identifying document artifact is evidence
        self.assertIn("empty_digest", a.substance["reasons"])

    def shift(self, spec, rp=RP):
        c = padded(25)
        c.visit(1, kind="code_file", app="Code", target="m.py", window="m.py - proj", moments_spec=spec)
        return af.extract(ai.prepare(c.records), None, rp).visits[-1]

    def test_internal_shift_is_preserved_without_splitting(self):
        f = self.shift([{"dwell": 200, "terms": ["alpha", "bravo"], "idents": [], "lines": []},
                        {"dwell": 200, "terms": ["gamma", "delta"], "idents": [], "lines": []}])
        self.assertEqual(len(f.moment_ids), 2)
        self.assertEqual(f.internal_shift["from_moment_id"], f.moment_ids[0])
        self.assertEqual(f.internal_shift["to_moment_id"], f.moment_ids[1])
        self.assertEqual(f.internal_shift["overlap"], 0.0)

    def test_internal_shift_needs_dwell_overlap_and_new_identifier_rules(self):
        same = [{"dwell": 200, "terms": ["alpha", "bravo"], "idents": [], "lines": []},
                {"dwell": 200, "terms": ["alpha", "bravo"], "idents": [], "lines": []}]
        self.assertIsNone(self.shift(same).internal_shift)
        short = [same[0], {"dwell": 60, "terms": ["gamma", "delta"], "idents": [], "lines": []}]
        self.assertIsNone(self.shift(short).internal_shift)            # the second moment is under 120 s
        self.assertIsNotNone(self.shift(short, ReplayParams(internal_shift_min_dwell_s=60.0)).internal_shift)
        newid = [{"dwell": 200, "terms": ["alpha", "bravo"], "idents": ["a_b.py"], "lines": []},
                 {"dwell": 200, "terms": ["alpha", "bravo"], "idents": ["a_b.py", "new_file.py"], "lines": []}]
        self.assertEqual(self.shift(newid).internal_shift["new_identifiers"], ["new_file.py"])
        self.assertIsNone(self.shift([{"dwell": 200, "terms": [], "idents": [], "lines": []}, same[1]]).internal_shift)


class SimilarityTests(unittest.TestCase):
    P = af.FeatureParams()

    def corpus(self, rare=(), idf=1.0):
        toks = set("abcdefghijklmnopq")
        return types.SimpleNamespace(cls={t: ("rare" if t in rare else "common") for t in toks},
                                     idf={t: idf for t in toks})

    def v(self, toks, w=1.0):
        return types.SimpleNamespace(bag=tuple((t, w) for t in sorted(toks)))

    def test_hand_computed_cosine_and_x_e(self):
        c = self.corpus(rare={"a", "b"})
        r = af.content_similarity(self.v("abcd"), self.v("abef"), c)
        self.assertAlmostEqual(r["cos_idf"], 0.5)
        self.assertEqual(r["rare_shared"], 2)
        self.assertAlmostEqual(r["x_e"], 1.0)                          # (0.5 - 0.15) / 0.35 = 1, R/2 = 1
        c1 = self.corpus(rare={"a"})
        r1 = af.content_similarity(self.v("acde"), self.v("afgh"), c1)
        self.assertAlmostEqual(r1["cos_idf"], 0.25)
        self.assertAlmostEqual(r1["x_e"], (0.25 - 0.15) / 0.35 * 0.5)

    def test_symmetry_short_bags_and_no_rare_token(self):
        c = self.corpus(rare={"a"})
        x, y = self.v("acde"), self.v("afgh")
        self.assertEqual(af.content_similarity(x, y, c), af.content_similarity(y, x, c))
        self.assertEqual(af.content_similarity(self.v("abc"), self.v("abc"), c)["cos_idf"], 0.0)   # fewer than 4 tokens
        self.assertEqual(af.content_similarity(self.v("cdef"), self.v("cdef"), c)["x_e"], 0.0)      # R = 0
        z = self.corpus(rare={"a"}, idf=0.0)
        self.assertEqual(af.content_similarity(self.v("acde"), self.v("acde"), z)["cos_idf"], 0.0)  # zero norm

    def test_exclude_removes_tokens_from_both_bags(self):
        c = self.corpus(rare={"a", "b"})
        r = af.content_similarity(self.v("abcd"), self.v("abef"), c, exclude={"a"})
        self.assertEqual(r["rare_tokens"], ["b"])
        self.assertEqual(r["cos_idf"], 0.0)                            # only 3 tokens left on each side

    def test_x_e_boundary_conditions(self):
        """x_E >= 0.5 exactly when R >= 2 and cos >= 0.325, or R == 1 and cos >= 0.5 (design 14.3)."""
        p = self.P
        for r in (0, 1, 2, 3):
            for cos in (0.0, 0.149999, 0.15, 0.325 - 1e-6, 0.325, 0.325 + 1e-6, 0.5 - 1e-6, 0.5, 0.9):
                x = min(1.0, max(0.0, (cos - p.cos_floor) / p.cos_span)) * min(1.0, r / p.rare_cap_r)
                expect = (r >= 2 and cos >= 0.325 - 1e-12) or (r == 1 and cos >= 0.5 - 1e-12)
                self.assertEqual(x >= 0.5 - 1e-12, expect, (r, cos))

    def test_order_and_duplicates_in_the_bag_do_not_change_the_value(self):
        c = self.corpus(rare={"a", "b"})
        a, b = self.v("abcd"), self.v("abef")
        shuffled = types.SimpleNamespace(bag=tuple(reversed(a.bag)))
        self.assertEqual(af.content_similarity(a, b, c), af.content_similarity(shuffled, b, c))


class SpecificityTests(unittest.TestCase):
    def spec(self, build, rp=RP):
        c = padded(25)
        build(c)
        return af.extract(ai.prepare(c.records, max_intra_visit_gap_s=rp.max_intra_visit_gap_s), None, rp).corpus.artifacts

    def test_specificity_is_prior_times_breadth(self):
        def build(c):
            c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha", "bravo", "charlie", "delta"])
        s = self.spec(build)[("document", "docs.example.com/d/1")]
        self.assertEqual((s["prior"], s["f_pattern"], s["f_volatility"], s["f_recurrence"]), (1.0, 1.0, 1.0, 1.0))
        self.assertEqual((s["specificity"], s["band"]), (1.0, "high"))

    def test_hub_pattern_demotes_below_the_low_threshold(self):
        def build(c):
            c.visit(1, url="mail.example.com/mail/u/0", kind="mail")
        s = self.spec(build)[("page", "mail.example.com/mail/u/0")]
        self.assertEqual(s["f_pattern"], 0.2)
        self.assertEqual(s["band"], "low")

    def test_recurrence_counts_sittings_by_the_resume_gap_only(self):
        def build(c):
            for i in range(4):
                c.visit(1, url="docs.example.com/d/1", kind="document", terms=["alpha", "bravo", "charlie", "delta"],
                        gap=5000.0 if i else 2.0, dwell=60.0)
        s = self.spec(build)[("document", "docs.example.com/d/1")]
        self.assertEqual(s["sittings"], 4)
        self.assertEqual(s["f_recurrence"], 0.5)
        s2 = self.spec(build, ReplayParams(t_resume_s=6000.0))[("document", "docs.example.com/d/1")]
        self.assertEqual(s2["sittings"], 1)

    def test_volatile_content_lowers_specificity(self):
        def build(c):
            for i in range(3):
                c.visit(1, url="docs.example.com/d/1", kind="document",
                        terms=[f"w{i}a{j}word" for j in range(5)], gap=2.0)
        s = self.spec(build)[("document", "docs.example.com/d/1")]
        self.assertEqual(s["f_volatility"], 0.1)                      # nothing in common between its own visits

    def test_specificity_cannot_depend_on_assignments(self):
        import inspect
        self.assertEqual(list(inspect.signature(af.extract).parameters), ["prepared", "params", "replay_params"])
        self.assertEqual(list(inspect.signature(af._specificity).parameters), ["feats", "corpus", "p", "rp"])


class HeuristicBoundaryTests(unittest.TestCase):
    """Volatility and recurrence are uncalibrated heuristics (docs/adr/0004); these tests pin their exact definitions,
    boundaries and sensitivity, not their correctness."""
    P = af.FeatureParams()

    def test_volatility_factor_is_clamped_linear_in_the_mean_similarity(self):
        p = self.P
        self.assertEqual(af.volatility_factor(0.0, p), p.vol_floor)
        self.assertEqual(af.volatility_factor(0.03, p), p.vol_floor)              # 0.03 / 0.3 = 0.1: the floor itself
        self.assertEqual(af.volatility_factor(0.03 - 1e-9, p), p.vol_floor)       # below it stays at the floor
        self.assertAlmostEqual(af.volatility_factor(0.15, p), 0.5)
        self.assertEqual(af.volatility_factor(0.3, p), 1.0)
        self.assertEqual(af.volatility_factor(0.3 + 1e-9, p), 1.0)
        self.assertLess(af.volatility_factor(0.3 - 1e-6, p), 1.0)
        self.assertEqual(af.volatility_factor(1.0, p), 1.0)

    def test_volatility_is_monotone_and_always_in_the_unit_interval(self):
        xs = [i / 200 for i in range(0, 201)]
        ys = [af.volatility_factor(x) for x in xs]
        self.assertEqual(ys, sorted(ys))
        self.assertTrue(all(0 < y <= 1 for y in ys))

    def test_volatility_sensitivity_to_its_parameters(self):
        base = af.volatility_factor(0.1, self.P)
        self.assertGreater(af.volatility_factor(0.1, af.FeatureParams(vol_ref=0.15)), base)     # lower reference: gentler
        self.assertLess(af.volatility_factor(0.1, af.FeatureParams(vol_ref=0.6)), base)
        self.assertGreater(af.volatility_factor(0.0, af.FeatureParams(vol_floor=0.3)), af.volatility_factor(0.0, self.P))

    def test_recurrence_factor_values_and_monotonicity(self):
        self.assertEqual([af.recurrence_factor(n) for n in (1, 2)], [1.0, 1.0])
        self.assertAlmostEqual(af.recurrence_factor(3), 2 / 3)
        self.assertEqual(af.recurrence_factor(4), 0.5)
        self.assertEqual(af.recurrence_factor(10), 0.2)
        ys = [af.recurrence_factor(n) for n in range(1, 60)]
        self.assertEqual(ys, sorted(ys, reverse=True))
        self.assertTrue(all(0 < y <= 1 for y in ys))

    def test_sittings_split_only_when_the_gap_exceeds_the_resume_parameter(self):
        mk = lambda s, e: types.SimpleNamespace(t_start=s, t_end=e)
        vs = [mk(0.0, 10.0), mk(910.0, 920.0)]                      # gap exactly 900
        self.assertEqual(af.sittings_of(vs, 900.0), 1)
        self.assertEqual(af.sittings_of(vs, 899.999), 2)
        self.assertEqual(af.sittings_of([mk(0, 10), mk(5, 8), mk(915, 920)], 900.0), 2)      # nested visit does not shorten the gap
        self.assertEqual(af.sittings_of([mk(0, 1000), mk(1500, 1600)], 900.0), 1)            # gap is from the latest end
        for k in (0.5, 8.0, 1e-3):
            scaled = [mk(v.t_start * k, v.t_end * k) for v in vs]
            self.assertEqual(af.sittings_of(scaled, 900.0 * k), 1)
            self.assertEqual(af.sittings_of(scaled, 899.999 * k), 2)

    def test_specificity_bands_at_their_thresholds(self):
        c = padded(25)
        for kind_url, expect in (("docs.example.com/d/1", "high"),):
            c.visit(1, url=kind_url, kind="document", terms=["alpha", "bravo", "charlie", "delta"])
        s = af.extract(ai.prepare(c.records), None, RP).corpus.artifacts[("document", "docs.example.com/d/1")]
        self.assertEqual(s["band"], "high")
        p = af.FeatureParams(spec_high=1.0000001)
        s2 = af.extract(ai.prepare(c.records), p, RP).corpus.artifacts[("document", "docs.example.com/d/1")]
        self.assertEqual(s2["band"], "mid")
        p3 = af.FeatureParams(spec_low=1.0000001, spec_high=2.0)
        s3 = af.extract(ai.prepare(c.records), p3, RP).corpus.artifacts[("document", "docs.example.com/d/1")]
        self.assertEqual(s3["band"], "low")


class InvarianceAndDeterminismTests(unittest.TestCase):
    def test_every_fixture_extracts_serialises_and_is_order_independent(self):
        for name, build in fx.BUILDERS.items():
            recs = build().records
            a = af.extract(ai.prepare(recs), None, RP)
            b = af.extract(ai.prepare(hn.with_duplicates(hn.shuffled(recs, 3), 4)), None, RP)
            self.assertEqual(json.dumps(a.to_dict(), sort_keys=True), json.dumps(b.to_dict(), sort_keys=True), name)

    def test_translation_changes_only_the_timestamps(self):
        for name in ("continuous_multiapp", "hub_between", "internal_shift", "interruptions_returns"):
            recs = fx.BUILDERS[name]().records
            base = af.extract(ai.prepare(recs), None, RP).to_dict()
            for off in (1234.5, -9.0e6):
                moved = af.extract(ai.prepare(hn.translate(recs, off), check_capture_clock=False), None, RP).to_dict()
                self.assertTrue(close(strip(base), strip(moved)), (name, off))
                for v0, v1 in zip(base["visits"], moved["visits"]):
                    self.assertAlmostEqual(v1["dwell_s"], v0["dwell_s"], places=3)

    def test_joint_scaling_changes_only_seconds_valued_fields(self):
        for name in ("continuous_multiapp", "hub_between", "internal_shift", "interruptions_returns"):
            recs = fx.BUILDERS[name]().records
            base = af.extract(ai.prepare(recs), None, RP)
            for k in (0.5, 2.0, 8.0):
                rp = RP.scaled(k)
                scaled = af.extract(ai.prepare(hn.scale(recs, k), max_intra_visit_gap_s=rp.max_intra_visit_gap_s,
                                               check_capture_clock=False), None, rp)
                self.assertTrue(close(strip(base.to_dict()), strip(scaled.to_dict())), (name, k))
                for v0, v1 in zip(base.visits, scaled.visits):
                    self.assertAlmostEqual(v1.dwell_s, v0.dwell_s * k, places=6)
                    self.assertEqual((v0.glance, v0.substance), (v1.glance, v1.substance))

    def test_flags_at_a_threshold_keep_their_outcome_under_scaling(self):
        c = padded(25)
        c.visit(1, url="x.example.com/a/b", dwell=30.0, terms=["alpha", "bravo", "charlie", "delta"])
        recs = c.records
        for k in (0.5, 2.0, 8.0, 1024.0):
            rp = RP.scaled(k)
            f = af.extract(ai.prepare(hn.scale(recs, k), max_intra_visit_gap_s=rp.max_intra_visit_gap_s,
                                      check_capture_clock=False), None, rp).visits[-1]
            self.assertFalse(f.glance, k)

    def test_extract_is_pure_and_repeatable(self):
        recs = fx.BUILDERS["hub_between"]().records
        before = copy.deepcopy(recs)
        p = ai.prepare(recs)
        a, b = af.extract(p, None, RP), af.extract(p, None, RP)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(recs, before)

    def test_feature_objects_are_immutable(self):
        fs = af.extract(ai.prepare(fx.BUILDERS["hub_between"]().records), None, RP)
        with self.assertRaises(Exception):
            fs.visits[0].glance = True
        with self.assertRaises(Exception):
            fs.corpus.n = 0
        self.assertIsInstance(fs.visits, tuple)
        self.assertIsInstance(fs.visits[0].bag, tuple)


def strip(d):
    """Drop the seconds-valued fields from a FeatureSet dict."""
    if isinstance(d, dict):
        return {k: strip(v) for k, v in d.items() if k not in SECONDS_FIELDS}
    if isinstance(d, list):
        return [strip(x) for x in d]
    return d


class NoLeakageTests(unittest.TestCase):
    def test_only_permitted_fields_are_read(self):
        recs = fx.BUILDERS["hub_between"]().records
        base = af.extract(ai.prepare(recs), None, RP).to_dict()
        noisy = copy.deepcopy(recs)
        for r in noisy:
            r["obs_id"] = "SENTINELOBS"
            r["interaction"] = {"category": "typing", "seconds": {"typing": 1.0}, "ages": {"key": 1.0}}
            r["digest"]["text_hash"] = "zzzzzzzz"
            r["context"]["trigger"] = "SENTINELTRIG"
            r["context"]["pinned"] = True
        self.assertEqual(af.extract(ai.prepare(noisy), None, RP).to_dict(), base)

    def test_unknown_or_typed_fields_never_reach_extraction(self):
        recs = copy.deepcopy(fx.BUILDERS["hub_between"]().records)
        recs[0]["typed_text"] = "x"
        with self.assertRaises(ai.UnsupportedRecordError):
            ai.prepare(recs)
        recs = copy.deepcopy(fx.BUILDERS["hub_between"]().records)
        recs[0]["digest"]["typed"] = "x"
        with self.assertRaises(ai.UnsupportedRecordError):
            ai.prepare(recs)

    def test_raw_lines_and_url_queries_are_not_in_the_output(self):
        c = padded(25)
        c.visit(1, url="docs.example.com/d/1?session=ZZQUERYZZ#ZZFRAGZZ", kind="document",
                terms=["alpha", "bravo", "charlie", "delta"], lines=["ZZLINEZZ sentence about quarterly numbers"])
        fs = af.extract(ai.prepare(c.records), None, RP)
        text = json.dumps(fs.to_dict()).casefold()
        self.assertNotIn("zzquery", text)
        self.assertNotIn("zzfrag", text)
        self.assertNotIn("sentence about quarterly numbers", text)      # whole quoted lines are not carried

    def test_the_modules_are_offline_and_model_free(self):
        import ast
        import activity_evidence as ev
        allowed = {"math", "posixpath", "re", "json", "hashlib", "dataclasses", "exact_links", "moments",
                   "activity_contract", "activity_features", "types"}
        for mod in (af, ev):
            with open(mod.__file__, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module.split(".")[0])
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ("open", "exec", "eval", "print"), mod.__name__)
            self.assertLessEqual(imported, allowed, mod.__name__)


if __name__ == "__main__":
    unittest.main()

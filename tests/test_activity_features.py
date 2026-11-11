"""Visit features (Step 2, PR 2B): artifacts, namespaces, tokens, document frequency, quality, glance, internal shift.

Everything runs on Prepared input built with the PR 2A fixtures and harness; no inference is involved.
"""
import copy
import dataclasses
import json
import math
import unittest

import activity_contract as ac
import activity_features as af
import activity_fixtures as fx
import activity_harness as hn
import activity_input as ai

P = ac.ReplayParams()
FP = af.FeatureParams()
SENTINEL = "SENTINEL-LEAK-7431"


def prep(records, **kw):
    return ai.prepare(copy.deepcopy(records), **kw)


def feats(records, params=P, fparams=FP):
    """The visits' features (a tuple), for tests that look at one visit."""
    return af.extract_features(prep(records), params, fparams).visits


def one(item, topic="paper", dwell=90, n=6, **kw):
    """Records of one visit on `item`."""
    b = fx.Builder(1)
    b.visit(item, b.part(topic, dwell, n=n, **kw), {"x"})
    return b.records


def gh(path, title="t"):
    return fx.chrome("reading_page", f"github.com/{path}", title, f"https://github.com/{path}")


def arts(f, kind=None):
    return [(a.kind, a.key, a.cls) for a in f.artifacts if kind is None or a.kind == kind]


def ns(f, kind):
    return [(n.key, n.scope, n.integration) for n in f.namespaces if n.kind == kind]


class ParameterTests(unittest.TestCase):
    def test_time_valued_fields_are_all_declared(self):
        self.assertEqual(af.undeclared_time_fields(), [])
        self.assertEqual(set(af.FeatureParams.TIME), {"cannot_link_min_dwell_s", "session_gap_s"})

    def test_scaled_changes_only_the_time_fields(self):
        s = FP.scaled(3.0)
        for f in dataclasses.fields(FP):
            want = getattr(FP, f.name) * (3.0 if f.name in FP.TIME else 1.0)
            self.assertEqual(getattr(s, f.name), want, f.name)

    def test_defaults_are_the_adr_cutoffs(self):
        self.assertEqual((FP.hub_share, FP.rare_share, FP.rare_floor), (0.2, 0.05, 3))


class TokenClassTests(unittest.TestCase):
    RANK = {"single": 0, "rare": 1, "common": 2, "hub": 3}

    def test_every_count_has_exactly_one_class_and_hub_beats_rare(self):
        for n in range(1, 120):
            seq = [af.token_class(df, n) for df in range(1, n + 1)]
            self.assertTrue(all(c in self.RANK for c in seq))
            for df, c in enumerate(seq, 1):
                if df > n * 0.2 and df > 1:
                    self.assertEqual(c, "hub", (n, df))                            # hub takes precedence
                if df == 1:
                    self.assertEqual(c, "single", (n, df))
            ranks = [self.RANK[c] for c in seq]
            self.assertEqual(ranks, sorted(ranks), n)                              # single, rare, common, hub: contiguous, in order

    def test_the_overlap_the_adr_warns_about(self):
        self.assertEqual([af.token_class(df, 10) for df in (1, 2, 3, 4)], ["single", "rare", "hub", "hub"])   # df 3 is both <=3 and >2
        self.assertEqual(af.token_class(1, 3), "single")                           # a token seen once is never a hub, even for N < 5
        self.assertEqual(af.token_class(2, 3), "hub")
        self.assertEqual([af.token_class(df, 100) for df in (1, 2, 5, 6, 20, 21)],
                         ["single", "rare", "rare", "common", "common", "hub"])

    def test_cutoffs_are_exact_integer_arithmetic(self):
        self.assertEqual(af.token_class(3, 15), "rare")                            # 0.2 * 15 is 3.0000000000000004 in floats: df 3 is not > 3
        self.assertEqual(af.token_class(4, 15), "hub")

    def test_bad_counts_are_refused(self):
        for df, n in ((0, 5), (6, 5), (-1, 5), (1.5, 5), (1, 0)):
            with self.assertRaises(ValueError):
                af.token_class(df, n)

    def test_idf_values_and_monotonicity(self):
        self.assertEqual([round(af.idf(df, 10), 4) for df in (1, 2, 3, 5)], [0.8309, 0.6179, 0.4776, 0.2891])
        vals = [af.idf(df, 50) for df in range(1, 51)]
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in vals))
        self.assertEqual(af.idf(1, 0), 0.0)


class ArtifactTests(unittest.TestCase):
    def test_pull_request_issue_and_commit_urls(self):
        pr = feats(one(gh("Acme/Billing/pull/12/files")))[0]
        self.assertIn(("pr", "github.com/acme/billing#12", "H1"), arts(pr))
        self.assertEqual(next(a for a in pr.artifacts if a.kind == "pr").scope, "github.com/acme/billing")
        self.assertIn(("issue", "github.com/acme/billing#7", "H1"), arts(feats(one(gh("acme/billing/issues/7")))[0]))
        sha = "a" * 40
        c = feats(one(gh(f"acme/billing/commit/{sha[:12].upper()}")))[0]
        self.assertIn(("commit", f"github.com/acme/billing@{sha[:12]}", "L"), arts(c))
        self.assertIn(("commit", f"github.com/acme/billing@{sha}", "L"), arts(feats(one(gh(f"acme/billing/blob/{sha}/src/a.py")))[0]))

    def test_a_repository_name_is_case_insensitive_and_the_namespace_is_recorded(self):
        a = feats(one(gh("ACME/Billing/pull/3")))[0]
        b = feats(one(gh("acme/billing/pull/3")))[0]
        self.assertEqual(arts(a, "pr"), arts(b, "pr"))
        self.assertEqual(ns(a, "repo"), [("github.com/acme/billing", None, False)])

    def test_only_known_code_hosts_make_identities(self):
        other = feats(one(fx.chrome("reading_page", "example.com/acme/billing/pull/12", "t", "https://example.com/acme/billing/pull/12")))[0]
        self.assertEqual(arts(other, "pr"), [])
        self.assertEqual(ns(other, "repo"), [])

    def test_a_branch_needs_exactly_one_segment_after_tree(self):
        self.assertEqual(ns(feats(one(gh("acme/billing/tree/Feature-Retry")))[0], "branch"),
                         [("feature-retry", "repo:github.com/acme/billing", False)])
        for path in ("acme/billing/tree/feature/retry", "acme/billing/blob/feature/retry/src/a.py", "acme/billing/tree/feature/retry/src"):
            self.assertEqual(ns(feats(one(gh(path)))[0], "branch"), [], path)       # a slash cannot be told from a path
        self.assertEqual(ns(feats(one(gh("acme/billing/tree/main")))[0], "branch")[0][2], True)    # integration branch
        self.assertEqual(ns(feats(one(gh("acme/billing/tree/" + "b" * 40)))[0], "branch"), [])      # a sha is lineage, not a branch

    def test_google_documents_by_id_not_by_title(self):
        a = feats(one(fx.chrome("document", "d1", "Budget", "https://docs.google.com/document/d/AbCdEfGh123/edit")))[0]
        b = feats(one(fx.chrome("document", "d1", "Renamed", "https://docs.google.com/document/d/AbCdEfGh123/edit?x=1")))[0]
        self.assertEqual(arts(a, "document"), arts(b, "document"))
        self.assertEqual(arts(a, "document")[0][1], "docs.google.com/document/AbCdEfGh123")      # an id is case-sensitive

    def test_urls_are_normalised_and_have_no_query_or_fragment(self):
        a = feats(one(fx.chrome("web_page", "x", "t", "https://www.Example.com/a/b/?token=SECRET#frag")))[0]
        self.assertEqual(arts(a, "url")[0][1], "example.com/a/b")
        self.assertNotIn("SECRET", json.dumps(af.as_dict(af.extract_features(prep(one(fx.chrome("web_page", "x", "t", "https://www.Example.com/a/b/?token=SECRET#frag"))), P))))

    def test_search_queries_conversations_and_chats(self):
        q = feats(one(fx.Item("Web search", "com.google.Chrome", "web_search", "  Retry  BACKOFF ", "t", "https://google.com/search")))[0]
        self.assertEqual(arts(q, "search_query"), [("search_query", "retry backoff", "I")])
        c = feats(one(fx.Item("ChatGPT", "com.google.Chrome", "ai_conversation", "conv-123", "t", "https://chatgpt.com/c/conv-123")))[0]
        self.assertEqual(arts(c, "conversation"), [("conversation", "conv-123", "I")])
        s = feats(one(fx.slack("general")))[0]
        self.assertEqual(arts(s, "chat"), [("chat", "slack|general", "I")])

    def test_files_and_unscoped_references_come_from_identifiers_and_titles(self):
        f = feats(one(fx.code("a.py", "billing - #12"), extra_idents=["src/Attention_Layer.py", "retry_policy", "#34", "Notes.MD"]))[0]
        self.assertIn(("file", "a.py", "I"), arts(f))
        self.assertIn(("file", "attention_layer.py", "I"), arts(f))
        self.assertIn(("file", "notes.md", "I"), arts(f))
        self.assertNotIn("retry_policy", [k for _, k, _ in arts(f, "file")])        # snake_case is a word, not a file
        self.assertEqual(sorted(k for _, k, _ in arts(f, "ref")), ["ref#12", "ref#34"])
        self.assertFalse([t for t in f.tokens if t.text.startswith("#")])             # a reference is an artifact, not a word

    def test_editor_project_and_branch_come_from_the_window_title(self):
        item = fx.Item("Visual Studio Code", "com.microsoft.VSCode", "code_file", "retry.py", "retry.py - Billing (Feature-Retry)")
        f = feats(one(item))[0]
        self.assertEqual(ns(f, "project"), [("billing", None, False)])
        self.assertEqual(ns(f, "branch"), [("feature-retry", "project:billing", False)])
        main = feats(one(fx.Item("Visual Studio Code", "com.microsoft.VSCode", "code_file", "a.py", "a.py - billing (main)")))[0]
        self.assertTrue(ns(main, "branch")[0][2])
        plain = feats(one(fx.code("a.py", "billing")))[0]
        self.assertEqual(ns(plain, "branch"), [])                                     # absence is not a branch

    def test_every_visit_has_its_item_artifact_with_a_fixed_prior(self):
        t = feats(one(fx.terminal("zsh")))[0]
        item = next(a for a in t.artifacts if a.kind == "item")
        self.assertEqual((item.cls, item.specificity), ("I", 0.2))
        c = feats(one(fx.code("a.py")))[0]
        self.assertEqual(next(a for a in c.artifacts if a.kind == "item").specificity, 0.7)

    def test_artifacts_remember_which_moments_carried_them(self):
        b = fx.Builder(2)
        item = fx.code("a.py", "billing")
        b.visit(item, [b.part("paper", 40, extra_idents=["notes.md"]), b.part("paper", 40, extra_idents=[], idents=0)], {"x"})
        f = feats(b.records)[0]
        notes = next(a for a in f.artifacts if a.key == "notes.md")
        self.assertEqual(len(notes.moments), 1)
        self.assertEqual(len(next(a for a in f.artifacts if a.kind == "item").moments), 2)

    def test_lists_are_in_canonical_order(self):
        f = feats(one(fx.code("a.py", "billing"), extra_idents=["z.py", "b.py", "#9", "#2"]))[0]
        self.assertEqual(list(f.artifacts), sorted(f.artifacts, key=lambda a: (a.kind, a.key)))
        self.assertEqual(list(f.tokens), sorted(f.tokens, key=lambda t: t.text))


class TokenTests(unittest.TestCase):
    def test_sources_weights_and_the_largest_weight_wins(self):
        b = fx.Builder(3)
        b.visit(fx.code("a.py"), b.part("paper", 60, n=3), {"x"})
        r = b.records[0]
        r["digest"] = {"terms": ["attention", "retry"], "identifiers": ["Attention_Layer.py", "attention"], "lines": ["Attention ranks tokens quickly"],
                       "text_hash": "h"}
        f = feats([r])[0]
        tok = {t.text: t for t in f.tokens}
        self.assertEqual((tok["attention"].weight, tok["attention"].sources), (1.5, ("identifier", "line", "term")))   # term 1.0, identifier 1.5, line 0.5
        self.assertEqual((tok["attention_layer"].weight, tok["attention_layer"].sources), (0.75, ("stem",)))
        self.assertEqual(tok["attention_layer.py"].weight, 1.5)
        self.assertEqual(tok["retry"].weight, 1.0)
        self.assertEqual(tok["ranks"].weight, 0.5)

    def test_short_words_and_stop_words_are_dropped(self):
        b = fx.Builder(3)
        b.visit(fx.code("a.py"), b.part("paper", 60, n=3), {"x"})
        r = b.records[0]
        r["digest"] = {"terms": ["abc", "this", "that", "kelp"], "identifiers": [], "lines": ["with from have been"], "text_hash": None}
        self.assertEqual([t.text for t in feats([r])[0].tokens], ["kelp"])

    def test_generic_words_are_flagged_not_removed(self):
        b = fx.Builder(3)
        b.visit(fx.code("a.py"), b.part("paper", 60, n=3), {"x"})
        r = b.records[0]
        r["digest"] = {"terms": ["meeting", "kelp"], "identifiers": [], "lines": [], "text_hash": None}
        tok = {t.text: t.generic for t in feats([r])[0].tokens}
        self.assertEqual(tok, {"kelp": False, "meeting": True})

    def test_title_target_and_url_words_stay_apart_from_content(self):
        item = fx.chrome("reading_page", "docs.example.com/loomweaving", "Loomweaving guide", "https://docs.example.com/loomweaving")
        b = fx.Builder(3)
        b.visit(item, b.part(None, 60, n=0), {"x"})
        b.records[0]["digest"] = {"terms": [], "identifiers": [], "lines": [], "text_hash": None}
        f = feats(b.records)[0]
        self.assertEqual(f.tokens, ())
        self.assertIn("loomweaving", f.context_words)

    def test_missing_and_malformed_optional_fields_are_ignored_and_named(self):
        b = fx.Builder(3)
        b.visit(fx.code("a.py"), [b.part("paper", 40, n=3), b.part("paper", 40, n=3), b.part("paper", 40, n=3), b.part("paper", 40, n=3)], {"x"})
        r = b.records
        r[0]["digest"] = None
        r[1]["digest"] = {"terms": "oops", "identifiers": [1, None, "ok_name.py"], "lines": ["fine words here"]}
        r[2]["digest"] = {"terms": ["kelp"], "identifiers": None, "lines": 5}
        r[3]["provenance"], r[3]["n_frames"] = None, "many"
        for x in r:                                                                  # visit-constant fields are all missing together
            x["window"] = x["url"] = x["kind"] = None
        f = feats(r)[0]
        self.assertIn("kelp", [t.text for t in f.tokens])
        self.assertIn("digest.terms", f.issues)
        self.assertIn("digest.identifiers", f.issues)
        self.assertEqual(f.issues, tuple(sorted(set(f.issues))))
        self.assertEqual(af.as_dict(af.extract_features(prep(r), P)), af.as_dict(af.extract_features(prep(r), P)))

    def test_a_visit_with_only_item_data_still_has_features(self):
        b = fx.Builder(3)
        b.visit(fx.terminal("zsh"), b.part(None, 10, n=0), {"x"})
        r = b.records
        for key in ("window", "url", "target", "kind", "digest", "provenance"):
            r[0][key] = None
        f = af.extract_features(prep(r), P).visits[0]
        self.assertEqual(f.tokens, ())
        self.assertEqual([a.kind for a in f.artifacts], ["item"])


class DocumentFrequencyTests(unittest.TestCase):
    def build(self):
        b = fx.Builder(4)
        a, c = fx.code("a.py"), fx.code("c.py")
        v = [b.visit(a, [b.part("paper", 40, n=5), b.part("paper", 40, n=5)], {"x"}), b.visit(c, b.part("paper", 40, n=5), {"x"}),
             b.visit(fx.terminal("zsh"), b.part(None, 40, n=0), {"x"})]
        for r in b.records:
            if r["visit_id"] == v[2]:
                r["digest"] = {"terms": [], "identifiers": [], "lines": [], "text_hash": None}
        return b.records

    def test_df_counts_distinct_visits_and_n_counts_eligible_visits_only(self):
        fs = af.extract_features(prep(self.build()), P)
        self.assertEqual(len(fs.visits), 3)
        self.assertEqual(fs.stats.n_eligible, 2)                                   # the empty visit is not eligible
        self.assertTrue(all(1 <= d <= 2 for d in fs.stats.df.values()))
        multi = fs.visits[0]
        for t in multi.tokens:                                                      # two moments in one visit still count once
            self.assertEqual(fs.stats.df[t.text], sum(1 for v in fs.visits if t.text in {x.text for x in v.tokens}))

    def test_classes_and_idf_follow_the_functions(self):
        fs = af.extract_features(prep(self.build()), P)
        n = fs.stats.n_eligible
        for tok, d in fs.stats.df.items():
            self.assertEqual(fs.stats.classes[tok], af.token_class(d, n))
            self.assertEqual(fs.stats.idf[tok], round(af.idf(d, n), 9))
        self.assertTrue(fs.stats.small_window)                                      # 2 eligible visits: classes are unreliable

    def test_a_larger_window_separates_generic_from_distinctive_signals(self):
        f = fx.overlap_sweep(2)
        fs = af.extract_features(prep(f.records), P)
        classes = set(fs.stats.classes.values())
        self.assertTrue({"single", "rare"} <= classes or {"rare", "hub"} <= classes, classes)
        self.assertFalse(fs.stats.small_window)
        hubs = [t for t, c in fs.stats.classes.items() if c == "hub"]
        for t in hubs:
            self.assertGreater(fs.stats.df[t], 0.2 * fs.stats.n_eligible)

    def test_weighted_bags_leave_generic_tokens_out(self):
        fs = af.extract_features(prep(fx.continuous_multiapp().records), P)
        v = fs.visits[0]
        bag = af.weighted_bag(v, fs.stats)
        self.assertTrue(all(t not in af.GENERIC_TOKENS for t in bag))
        self.assertTrue(all(w >= 0 for w in bag.values()))


class QualityTests(unittest.TestCase):
    def q(self, prov, frames=3, tokens=True):
        b = fx.Builder(5)
        b.visit(fx.code("a.py"), b.part("paper", 40, n=5, frames=frames), {"x"})
        r = b.records
        r[0]["provenance"] = prov
        if not tokens:
            r[0]["digest"] = {"terms": [], "identifiers": [], "lines": [], "text_hash": None}
        v = feats(r)[0]
        return v.quality, dict(v.quality_parts)

    def test_provenance_factor(self):
        self.assertEqual(self.q({"ax_only": 4, "ocr_only": 0, "agreed": 2, "differed": 0})[0], 1.0)
        self.assertEqual(self.q({"ocr_only": 5})[0], 0.8)
        self.assertEqual(self.q({"ax_only": 1, "ocr_only": 1})[0], 0.9)
        self.assertEqual(self.q({"ocr": 3})[0], 0.8)                                 # keys it does not know say nothing
        for odd in (None, {}, {"ax_only": True, "ocr_only": "x"}, [1]):
            self.assertEqual(self.q(odd)[0], 0.8)

    def test_frame_and_content_factors_multiply(self):
        q, parts = self.q({"ocr_only": 5}, frames=1)
        self.assertEqual((q, parts["frames"]), (0.48, 0.6))
        q, parts = self.q({"ocr_only": 5}, tokens=False)
        self.assertEqual((q, parts["content"]), (0.4, 0.5))
        self.assertEqual(self.q({"ax_only": 5}, frames=1, tokens=False)[0], 0.3)


class GlanceTests(unittest.TestCase):
    def visit(self, item, dwell, frames, n=4):
        b = fx.Builder(6)
        b.visit(item, b.part("paper", dwell, n=n, frames=frames), {"x"})
        return feats(b.records)[0]

    def test_short_single_frame_unnamed_is_a_glance(self):
        v = self.visit(fx.terminal("zsh"), 10, 1)
        self.assertTrue(v.glance)
        self.assertIn("short_dwell_single_frame_unnamed", v.flag_reasons)

    def test_a_named_artifact_a_second_frame_or_a_longer_dwell_is_not(self):
        self.assertFalse(self.visit(fx.code("a.py"), 10, 1).glance)                 # a file is named
        self.assertFalse(self.visit(fx.terminal("zsh"), 10, 2).glance)
        self.assertFalse(self.visit(fx.terminal("zsh"), 40, 1).glance)

    def test_the_parameters_decide(self):
        b = fx.Builder(6)
        b.visit(fx.terminal("zsh"), b.part("paper", 20, n=4, frames=1), {"x"})
        r = b.records
        self.assertTrue(feats(r)[0].glance)
        self.assertFalse(feats(r, dataclasses.replace(P, glance_dwell_s=10.0))[0].glance)
        self.assertFalse(feats(r, P, dataclasses.replace(FP, glance_frames=1))[0].glance)

    def test_weak_evidence_and_substance(self):
        b = fx.Builder(6)
        b.visit(fx.terminal("zsh"), b.part(None, 60, n=0, frames=3), {"x"})
        weak = feats(b.records)[0]
        self.assertEqual(weak.tokens, ())
        self.assertTrue(weak.weak_evidence)
        self.assertIn("no_distinctive_content", weak.flag_reasons)
        self.assertFalse(weak.has_substance)
        good = self.visit(fx.code("a.py"), 60, 3)
        self.assertTrue(good.has_substance)
        low = self.visit(fx.code("a.py"), 60, 1)
        self.assertEqual((low.quality, low.weak_evidence), (0.48, True))
        self.assertIn("low_quality", low.flag_reasons)

    def test_ambiguous_fixture_marks_thin_visits(self):
        f = fx.ambiguous_moments()
        fs = af.extract_features(prep(f.records), P)
        flagged = {v.visit_id for v in fs.visits if not v.has_substance}
        self.assertEqual(flagged, set(f.expect_unassigned))                          # pages of only generic words are thin evidence
        self.assertTrue(all(v.tokens and all(t.generic for t in v.tokens) for v in fs.visits))
        self.assertFalse(any(v.glance for v in fs.visits))                           # long enough and named: weak, but not a glance


class InternalShiftTests(unittest.TestCase):
    def test_the_fixture_flags_the_topic_change_and_nothing_else(self):
        f = fx.internal_shift()
        fs = af.extract_features(prep(f.records), P)
        flagged = [v.visit_id for v in fs.visits if v.internal_shift["flag"]]
        self.assertEqual(flagged, f.flagged_shift)
        for vid in f.unflaggable_shift:                                             # the digest never changed: the layer cannot see it
            self.assertFalse(fs.visit(vid).internal_shift["flag"])
        self.assertEqual(len(fs.visits), len(prep(f.records).visits))                # a visit is never split

    def test_the_descriptor_names_the_moments_and_the_changed_words(self):
        f = fx.internal_shift()
        fs = af.extract_features(prep(f.records), P)
        d = fs.visit(f.flagged_shift[0]).internal_shift
        self.assertTrue(d["evaluated"] and d["flag"])
        self.assertLess(d["cos_min"], 0.30)
        self.assertEqual((d["from_moment"], d["to_moment"]), (f.flagged_shift[0] + ".0", f.flagged_shift[0] + ".1"))
        self.assertTrue(0 < len(d["new_tokens"]) <= 5 and 0 < len(d["dropped_tokens"]) <= 5)
        self.assertFalse(set(d["new_tokens"]) & set(d["dropped_tokens"]))

    def test_a_short_visit_is_described_but_not_flagged(self):
        f = fx.internal_shift()
        fs = af.extract_features(prep(f.records), dataclasses.replace(P, internal_shift_min_dwell_s=10_000.0))
        d = fs.visit(f.flagged_shift[0]).internal_shift
        self.assertEqual((d["evaluated"], d["flag"], d["reason"]), (True, False, "dwell_below_minimum"))

    def test_single_moment_and_unchanged_digest_visits(self):
        fs = af.extract_features(prep(fx.continuous_multiapp().records), P)
        self.assertTrue(all(not v.internal_shift["flag"] for v in fs.visits))
        self.assertTrue(all(v.internal_shift["reason"] == "single_moment" for v in fs.visits))
        f = fx.internal_shift()
        flips = af.extract_features(prep(f.records), P)
        for v in flips.visits:
            if len(v.moments) == 3:
                self.assertEqual(v.internal_shift["reason"], "no_comparable_moment")


class DeterminismTests(unittest.TestCase):
    def test_identical_input_gives_identical_output_in_any_input_order(self):
        for build in (fx.continuous_multiapp, fx.branches_alternating, fx.pr_numbers_differ, fx.internal_shift):
            recs = build().records
            base = af.fingerprint(af.extract_features(prep(recs), P))
            self.assertEqual(base, af.fingerprint(af.extract_features(prep(recs), P)))
            self.assertEqual(base, af.fingerprint(af.extract_features(prep(hn.shuffled(recs, 3)), P)))
            self.assertEqual(base, af.fingerprint(af.extract_features(prep(hn.with_duplicates(recs, 4)), P)))

    def test_results_are_immutable(self):
        fs = af.extract_features(prep(fx.continuous_multiapp().records), P)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            fs.visits[0].quality = 1.0
        with self.assertRaises(TypeError):
            fs.stats.df["new"] = 1
        with self.assertRaises(TypeError):
            fs.visits[0].internal_shift["flag"] = True
        self.assertIsInstance(fs.visits, tuple)
        self.assertIsInstance(fs.visits[0].artifacts, tuple)

    def test_the_input_is_never_modified(self):
        recs = fx.pr_numbers_differ().records
        before = json.dumps(recs, sort_keys=True)
        p = prep(recs)
        snapshot = json.dumps([v.moments for v in p.visits], sort_keys=True)
        af.extract_features(p, P)
        self.assertEqual(json.dumps(recs, sort_keys=True), before)
        self.assertEqual(json.dumps([v.moments for v in p.visits], sort_keys=True), snapshot)

    def test_application_names_are_not_features_of_identity(self):
        recs = fx.branches_alternating().records
        a = af.extract_features(prep(recs), P)
        b = af.extract_features(prep(hn.rename_apps(recs, 5)), P)
        def shape(fs):
            return [(v.visit_id, [(x.kind, x.cls) for x in v.artifacts if x.kind != "chat"], [(n.kind, n.key, n.scope) for n in v.namespaces],
                     [t.text for t in v.tokens], v.quality, v.glance) for v in fs.visits]
        self.assertEqual(shape(a), shape(b))


class TimeInvarianceTests(unittest.TestCase):
    NAMES = ("continuous_multiapp", "branches_alternating", "pr_numbers_differ", "internal_shift", "ambiguous_moments", "long_gaps")

    def run_at(self, recs, k=1.0, c=0.0):
        p = P.scaled(k)
        moved = hn.translate(hn.scale(recs, k), c) if (k != 1.0 or c) else recs
        prepared = ai.prepare(copy.deepcopy(moved), max_intra_visit_gap_s=p.max_intra_visit_gap_s,       # retention is a time limit too
                              retention_hours=ai.RETENTION_HOURS * k, check_capture_clock=(k == 1.0 and not c))
        return af.stable_dict(af.extract_features(prepared, p, FP.scaled(k)))

    def test_translation_changes_nothing(self):
        for name in self.NAMES:
            recs = fx.BUILDERS[name]().records
            base = self.run_at(recs)
            for c in (1234.5, -1000.0, 4 * 3600.0, -1e7):
                self.assertEqual(base, self.run_at(recs, c=c), (name, c))

    def test_joint_scaling_changes_nothing(self):
        for name in self.NAMES:
            recs = fx.BUILDERS[name]().records
            base = self.run_at(recs)
            for k in (1e-3, 1e-6, 0.25, 4.0, 1e3):
                self.assertEqual(base, self.run_at(recs, k=k), (name, k))

    def test_changing_timestamps_alone_may_change_flags(self):
        recs = fx.BUILDERS["internal_shift"]().records
        base = af.stable_dict(af.extract_features(prep(recs), P))
        shrunk = ai.prepare(hn.scale(recs, 1e-3), max_intra_visit_gap_s=P.max_intra_visit_gap_s, check_capture_clock=False)   # parameters NOT scaled
        self.assertNotEqual(base, af.stable_dict(af.extract_features(shrunk, P)))       # the shift no longer lasts long enough


class NoLeakageTests(unittest.TestCase):
    def test_only_permitted_fields_reach_the_output(self):
        recs = fx.continuous_multiapp().records
        for r in recs:
            r["interaction"]["category"] = SENTINEL
            r["context"]["trigger"] = SENTINEL
            r["obs_id"] = SENTINEL
            r["provenance"] = {"ocr_only": 2, SENTINEL: 3}
        out = json.dumps(af.as_dict(af.extract_features(prep(recs), P)))
        self.assertNotIn(SENTINEL, out)

    def test_interaction_ages_are_never_read(self):
        recs = fx.continuous_multiapp().records
        other = copy.deepcopy(recs)
        for r in other:
            r["interaction"]["ages"] = {"key": 0.1, "scroll": 0.2, "click": 0.3}
            r["interaction"]["seconds"] = {"typing": 999.0}
        self.assertEqual(af.fingerprint(af.extract_features(prep(recs), P)), af.fingerprint(af.extract_features(prep(other), P)))

    def test_typed_pasted_and_clipboard_fields_cannot_enter(self):
        for name in ("typed_text", "clipboard", "pasted"):
            bad = copy.deepcopy(fx.continuous_multiapp().records)
            bad[0][name] = "secret"
            with self.assertRaises(ai.ActivityInputError):
                ai.prepare(bad)

    def test_the_output_has_no_such_keys_and_no_urls_with_queries(self):
        out = json.dumps(af.as_dict(af.extract_features(prep(fx.branches_alternating().records), P)))
        for word in ("typed", "pasted", "clipboard", "keystroke"):
            self.assertNotIn(word, out)
        self.assertNotIn("?", "".join(a["key"] for v in json.loads(out)["visits"] for a in v["artifacts"] if a["kind"] == "url"))

    def test_gold_labels_are_not_inputs(self):
        f = fx.continuous_multiapp()
        self.assertNotIn("gold", json.dumps(af.as_dict(af.extract_features(prep(f.records), P))))
        self.assertTrue(all("label" not in k for r in f.records for k in r))


class FixtureIntegrationTests(unittest.TestCase):
    def test_every_fixture_extracts_with_valid_features(self):
        for f in fx.all_fixtures():
            fs = af.extract_features(prep(f.records), P)
            self.assertEqual([v.visit_id for v in fs.visits], [v.visit_id for v in prep(f.records).visits], f.name)
            for v in fs.visits:
                self.assertTrue(0.0 < v.quality <= 1.0, (f.name, v.visit_id))
                self.assertTrue(any(a.kind == "item" for a in v.artifacts))
                self.assertGreaterEqual(v.dwell_s, 0.0)
                self.assertTrue(math.isfinite(v.t_start) and v.t_end >= v.t_start)
            for tok, c in fs.stats.classes.items():
                self.assertEqual(c, af.token_class(fs.stats.df[tok], fs.stats.n_eligible))

    def test_shared_items_in_the_fixtures_are_visible_as_artifacts(self):
        for f in (fx.shared_doc_two_purposes(), fx.hub_between(), fx.same_tab_different_file()):
            fs = af.extract_features(prep(f.records), P)
            for item in f.shared_artifacts:
                holders = [v for v in fs.visits if any(a.kind == "item" and a.key == item for a in v.artifacts)]
                self.assertGreaterEqual(len(holders), 2, f.name)
                stats = fs.artifact_stats[("item", item)]
                self.assertEqual(stats.n_visits, len(holders))

    def test_artifact_stats_count_sessions_from_differences_only(self):
        f = fx.shared_doc_two_purposes(gap_s=5 * 3600.0)
        fs = af.extract_features(prep(f.records), P)
        doc = f.shared_artifacts[0]
        self.assertEqual(fs.artifact_stats[("item", doc)].n_sessions, 2)               # two sittings five hours apart
        self.assertEqual(fs.artifact_stats[("item", doc)].reuse, 0.2)
        short = af.extract_features(prep(f.records), P, dataclasses.replace(FP, session_gap_s=10 * 3600.0))
        self.assertEqual(short.artifact_stats[("item", doc)].n_sessions, 1)


if __name__ == "__main__":
    unittest.main()

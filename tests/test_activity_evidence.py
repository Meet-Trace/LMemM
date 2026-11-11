"""Evidence between visits (Step 2, PR 2B): shared artifacts, identities, lineage, namespaces, content, and cannot-links.

Evidence is facts and restrictions only. Nothing here asks whether two visits belong together.
"""
import copy
import dataclasses
import json
import unittest

import activity_contract as ac
import activity_evidence as ae
import activity_features as af
import activity_fixtures as fx
import activity_harness as hn
import activity_input as ai

P = ac.ReplayParams()
FP = af.FeatureParams()


def letters(i):
    return "filler" + "abcdefghij"[i // 10 % 10] + "klmnopqrst"[i % 10]


def scene(*specs, fillers=12, seed=1):
    """Visits as (item, dwell, terms, idents), then `fillers` visits with words of their own so document frequencies mean
    something. Returns (records, visit ids of the specs, fillers' ids)."""
    b = fx.Builder(seed)
    ids = []
    for item, dwell, terms, idents in specs:
        part = b.part(None, dwell, n=0, frames=3)
        part["terms"], part["idents"] = list(terms), list(idents)
        ids.append(b.visit(item, part, {"x"}))
    fill = []
    for i in range(fillers):
        part = b.part(None, 40, n=0, frames=3)
        part["terms"], part["idents"] = [letters(i), letters(i) + "x"], []
        fill.append(b.visit(fx.chrome("reading_page", f"f{i}.example.com", "f", f"https://f{i}.example.com/p"), part, {"y"}))
    return b.records, ids, fill


def analyse(records, params=P, fparams=FP):
    return ae.analyse(ai.prepare(copy.deepcopy(records)), params, fparams)


def gh(path):
    return fx.chrome("reading_page", f"github.com/{path}", "t", f"https://github.com/{path}")


def kinds(es, a, b):
    return sorted((e.type, e.category) for e in es.pair(a, b))


class ArtifactEvidenceTests(unittest.TestCase):
    def test_a_shared_identity_only_artifact_is_a_fact_with_provenance(self):
        recs, (a, b), _ = scene((fx.code("retry.py", "billing"), 60, ["kelpweave"], ["notes.md"]),
                                (fx.chrome("document", "d", "Spec", "https://docs.example.com/spec"), 60, ["kelpweave"], ["notes.md"]))
        fs, es = analyse(recs)
        got = [e for e in es.pair(a, b) if e.category == "file"]
        self.assertEqual(len(got), 1)
        e = got[0]
        self.assertEqual((e.type, e.cls, e.key, e.source, e.target, e.active), ("shared_artifact", "I", "notes.md", a, b, True))
        self.assertEqual((e.source_moments, e.target_moments), ((a + ".0",), (b + ".0",)))
        self.assertEqual(e.via, ("identifier",))
        self.assertEqual(e.detail["specificity"], 0.7)
        self.assertEqual(e.quality, min(fs.visit(a).quality, fs.visit(b).quality))

    def test_source_is_the_earlier_visit_and_nothing_pairs_with_itself(self):
        recs, ids, _ = scene(*[(fx.code("retry.py", "billing"), 60, ["kelpweave"], ["notes.md"]) for _ in range(4)])
        fs, es = analyse(recs)
        order = {v.visit_id: i for i, v in enumerate(fs.visits)}
        for e in es.items:
            self.assertLess(order[e.source], order[e.target])

    def test_duplicates_collapse_into_one_record_with_the_union_of_provenance(self):
        b = fx.Builder(2)
        parts = [b.part(None, 40, n=0), b.part(None, 40, n=0), b.part(None, 40, n=0)]
        for p_ in parts:
            p_["idents"] = ["notes.md"]
        a = b.visit(fx.code("a.py", "billing"), parts[:2], {"x"})
        c = b.visit(fx.code("c.py", "billing"), parts[2], {"x"})
        fs, es = analyse(b.records)
        notes = [e for e in es.pair(a, c) if e.key == "notes.md"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(notes[0].source_moments), 2)

    def test_exact_identity_for_the_same_pull_request(self):
        recs, (a, b, c), _ = scene((gh("acme/billing/pull/12"), 80, ["kelpweave"], []), (gh("Acme/Billing/pull/12/files"), 80, ["kelpweave"], []),
                                   (gh("acme/billing/pull/15"), 80, ["loomwarp"], []))
        fs, es = analyse(recs)
        ident = [e for e in es.pair(a, b) if e.type == "shared_identity"]
        self.assertEqual([(e.category, e.cls, e.detail["match"], e.detail["scope"]) for e in ident], [("pr", "H1", "exact", "github.com/acme/billing")])
        self.assertEqual([e for e in es.pair(a, c) if e.type == "shared_identity"], [])

    def test_unscoped_numbers_match_a_scoped_identity_and_record_how_well(self):
        recs, (page, editor, other, term), _ = scene(
            (gh("acme/billing/pull/12"), 80, ["kelpweave"], []),
            (fx.code("retry.py", "billing - #12"), 80, ["kelpweave"], ["#12"]),
            (fx.code("retry.py", "platform - #12"), 80, ["kelpweave"], ["#12"]),
            (fx.terminal("gh pr checkout 12"), 30, ["kelpweave"], ["#12"]))
        fs, es = analyse(recs)
        by = {e.target: e for e in es.items if e.source == page and e.type == "shared_identity"}
        self.assertEqual(by[editor].detail["match"], "unscoped_number")
        self.assertEqual(by[editor].detail["scope_match"], "project_equals_repo")        # the editor project is the repository name
        self.assertEqual(by[other].detail["scope_match"], "none")
        self.assertEqual(by[term].detail["scope_match"], "none")
        nums = [e for e in es.pair(editor, other) if e.type == "shared_identity"]
        self.assertEqual([(e.category, e.detail["match"]) for e in nums], [("ref", "number_only")])      # both sides only know "#12"

    def test_scoped_identities_of_different_repositories_do_not_match(self):
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 80, ["kelpweave"], []), (gh("acme/platform/pull/12"), 80, ["kelpweave"], []))
        fs, es = analyse(recs)
        self.assertEqual([e for e in es.pair(a, b) if e.type == "shared_identity"], [])

    def test_commit_lineage_is_a_relationship_and_not_an_identity(self):
        sha = "c" * 40
        recs, (a, b, c), _ = scene((gh(f"acme/billing/commit/{sha[:9]}"), 60, ["kelpweave"], []), (gh(f"acme/billing/blob/{sha}/src/a.py"), 60, ["loomwarp"], []),
                                   (gh(f"acme/billing/commit/{'d' * 9}"), 60, ["kelpweave"], []))
        fs, es = analyse(recs)
        lin = [e for e in es.items if e.type == "shared_lineage"]
        self.assertEqual(lin, [])                                                         # a 9-character and a 40-character sha are different keys
        recs2, (x, y), _ = scene((gh(f"acme/billing/commit/{sha}"), 60, ["kelpweave"], []), (gh(f"acme/billing/blob/{sha}/src/a.py"), 60, ["loomwarp"], []))
        fs2, es2 = analyse(recs2)
        lin = [e for e in es2.pair(x, y) if e.type == "shared_lineage"]
        self.assertEqual([(e.category, e.cls) for e in lin], [("commit", "L")])
        self.assertFalse([e for e in es2.pair(x, y) if e.type == "shared_identity"])

    def test_the_same_document_id_under_two_titles_is_one_shared_artifact(self):
        recs, (a, b), _ = scene((fx.chrome("document", "d1", "Budget", "https://docs.google.com/document/d/AbCdEfGh123/edit"), 60, ["kelpweave"], []),
                                (fx.chrome("document", "d2", "Renamed", "https://docs.google.com/document/d/AbCdEfGh123/edit"), 60, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertIn(("shared_artifact", "document"), kinds(es, a, b))


class NamespaceEvidenceTests(unittest.TestCase):
    def test_soft_context_is_class_n_and_carries_its_scope(self):
        recs, (a, b), _ = scene((gh("acme/billing/tree/feature-retry"), 60, ["kelpweave"], []), (gh("acme/billing/tree/feature-retry"), 60, ["loomwarp"], []))
        fs, es = analyse(recs)
        ns = {(e.category, e.key): e for e in es.pair(a, b) if e.type == "shared_namespace"}
        self.assertEqual(set(k for k, _ in ns), {"domain", "section", "repo", "branch"})
        self.assertTrue(all(e.cls == "N" for e in ns.values()))
        self.assertEqual(ns[("branch", "feature-retry")].detail["scope"], "repo:github.com/acme/billing")
        self.assertFalse(ns[("branch", "feature-retry")].detail["integration"])

    def test_the_same_branch_name_in_different_repositories_is_not_shared(self):
        recs, (a, b), _ = scene((gh("acme/billing/tree/dev-wip"), 60, ["kelpweave"], []), (gh("acme/platform/tree/dev-wip"), 60, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertFalse([e for e in es.pair(a, b) if e.category == "branch"])

    def test_integration_branches_are_marked(self):
        recs, (a, b), _ = scene((gh("acme/billing/tree/main"), 60, ["kelpweave"], []), (gh("acme/billing/tree/main"), 60, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertTrue(next(e for e in es.pair(a, b) if e.category == "branch").detail["integration"])

    def test_editor_project_is_shared_context_across_files(self):
        recs, (a, b), _ = scene((fx.code("a.py", "billing"), 60, ["kelpweave"], []), (fx.code("b.py", "Billing"), 60, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertIn(("shared_namespace", "project"), kinds(es, a, b))


class ContentEvidenceTests(unittest.TestCase):
    def test_only_pairs_sharing_a_rare_distinctive_word_get_content_evidence(self):
        recs, (a, b, c), _ = scene((fx.chrome("reading_page", "a.x.com", "t", "https://a.x.com/1"), 60, ["zebraloom", "kelpweave"], []),
                                   (fx.chrome("reading_page", "b.x.com", "t", "https://b.x.com/1"), 60, ["zebraloom", "orchardwing"], []),
                                   (fx.chrome("reading_page", "c.x.com", "t", "https://c.x.com/1"), 60, ["meeting", "orchardwing"], []))
        fs, es = analyse(recs)
        self.assertEqual(fs.stats.classes["zebraloom"], "rare")
        got = [e for e in es.items if e.type == "shared_tokens"]
        self.assertEqual([(e.source, e.target) for e in got], [(a, b), (b, c)][:1] + [(b, c)])
        ab = next(e for e in got if (e.source, e.target) == (a, b))
        self.assertEqual((ab.detail["n_shared_rare"], ab.detail["shared_rare"]), (1, ["zebraloom"]))
        # the cosine of the idf-weighted bags, recomputed here from the definition
        n = fs.stats.n_eligible
        idf = lambda t: af.idf(fs.stats.df[t], n)
        bag_a = {"zebraloom": idf("zebraloom"), "kelpweave": idf("kelpweave")}
        bag_b = {"zebraloom": idf("zebraloom"), "orchardwing": idf("orchardwing")}
        cos = bag_a["zebraloom"] * bag_b["zebraloom"] / ((sum(v * v for v in bag_a.values()) ** 0.5) * (sum(v * v for v in bag_b.values()) ** 0.5))
        self.assertAlmostEqual(ab.detail["cosine"], cos, places=8)

    def test_generic_and_hub_words_are_never_distinctive_evidence(self):
        specs = [(fx.chrome("reading_page", f"p{i}.x.com", "t", f"https://p{i}.x.com/1"), 60, ["meeting", "hubwording"], []) for i in range(6)]
        recs, ids, _ = scene(*specs)
        fs, es = analyse(recs)
        self.assertEqual(fs.stats.classes["hubwording"], "hub")
        self.assertEqual(es.of_type("shared_tokens"), ())

    def test_common_words_are_counted_but_never_start_a_pair(self):
        specs = [(fx.chrome("reading_page", "a.x.com", "t", "https://a.x.com/1"), 60, ["zebraloom", "commonwordx"], []),
                 (fx.chrome("reading_page", "b.x.com", "t", "https://b.x.com/1"), 60, ["zebraloom", "commonwordx"], [])]
        specs += [(fx.chrome("reading_page", f"c{i}.x.com", "t", f"https://c{i}.x.com/1"), 60, ["commonwordx"], []) for i in range(3)]
        recs, (a, b, *_), _ = scene(*specs, fillers=55)
        fs, es = analyse(recs)
        self.assertEqual(fs.stats.classes["commonwordx"], "common")
        e = next(x for x in es.of_type("shared_tokens") if (x.source, x.target) == (a, b))
        self.assertEqual((e.detail["n_shared_rare"], e.detail["n_shared_common"]), (1, 1))
        self.assertEqual({(x.source, x.target) for x in es.of_type("shared_tokens")}, {(a, b)})

    def test_a_small_window_has_no_rare_words(self):
        recs, ids, _ = scene((fx.code("a.py", "billing"), 60, ["zebraloom"], []), (fx.code("b.py", "billing"), 60, ["zebraloom"], []), fillers=0)
        fs, es = analyse(recs)
        self.assertTrue(fs.stats.small_window)
        self.assertEqual(es.of_type("shared_tokens"), ())


class CannotLinkTests(unittest.TestCase):
    def pr_scene(self, dwell_a=90, dwell_b=90, terms_a=("kelpweave",), terms_b=("loomwarp",), idents_a=("#12",), idents_b=("#15",), paths=("pull/12", "pull/15")):
        recs, (a, b), _ = scene((gh(f"acme/billing/{paths[0]}"), dwell_a, list(terms_a), list(idents_a)),
                                (gh(f"acme/billing/{paths[1]}"), dwell_b, list(terms_b), list(idents_b)))
        return analyse(recs), a, b

    def test_two_different_pull_requests_of_one_repository(self):
        (fs, es), a, b = self.pr_scene()
        cl = [e for e in es.pair(a, b) if e.type == "cannot_link"]
        self.assertEqual([(e.category, e.cls, e.active) for e in cl], [("x_pr", "CL", True)])
        self.assertEqual((cl[0].detail["repo"], cl[0].detail["numbers"], cl[0].detail["override"]), ("github.com/acme/billing", [12, 15], None))
        self.assertEqual(es.cannot_link_pairs(), frozenset([frozenset((a, b))]))

    def test_the_dwell_condition_uses_the_declared_time_parameter(self):
        (fs, es), a, b = self.pr_scene(dwell_a=30)
        self.assertEqual(es.of_type("cannot_link"), ())
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 30, ["kelpweave"], ["#12"]), (gh("acme/billing/pull/15"), 90, ["loomwarp"], ["#15"]))
        fs, es = analyse(recs, P, dataclasses.replace(FP, cannot_link_min_dwell_s=20.0))
        self.assertTrue(es.cannot_link_pairs())

    def test_a_shared_identity_overrides_the_restriction_and_the_record_says_so(self):
        (fs, es), a, b = self.pr_scene(idents_b=("#15", "#12"))
        cl = next(e for e in es.pair(a, b) if e.type == "cannot_link")
        self.assertEqual((cl.active, cl.detail["override"]), (False, "shared_identity"))
        self.assertEqual(es.cannot_link_pairs(), frozenset())

    def test_two_shared_rare_words_override_and_one_does_not(self):
        (fs, es), a, b = self.pr_scene(terms_a=("alphawing", "betawing"), terms_b=("alphawing", "betawing"))
        self.assertEqual(next(e for e in es.pair(a, b) if e.type == "cannot_link").detail["override"], "shared_rare_tokens")
        (fs, es), a, b = self.pr_scene(terms_a=("alphawing", "kelpweave"), terms_b=("alphawing", "loomwarp"))
        self.assertTrue(next(e for e in es.pair(a, b) if e.type == "cannot_link").active)

    def test_a_visit_that_names_two_numbers_is_not_restricted(self):
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 90, ["kelpweave"], []), (gh("acme/billing/pull/15"), 90, ["loomwarp"], []))
        recs[0]["url"] = "github.com/acme/billing/pull/12"
        extra = copy.deepcopy(recs[0])
        # the visit mentions a second scoped identity in the same repository: ambiguous, so no restriction
        fs = af.extract_features(ai.prepare(copy.deepcopy(recs)), P)
        self.assertEqual(len([x for x in fs.visit(a).artifacts if x.kind == "pr"]), 1)
        (fs2, es2), x, y = self.pr_scene()
        self.assertEqual(len(es2.cannot_link_pairs()), 1)

    def test_different_repositories_never_restrict_each_other(self):
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 90, ["kelpweave"], []), (gh("acme/platform/pull/15"), 90, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertEqual(es.of_type("cannot_link"), ())

    def branches(self, one, two, dwell=20):
        recs, (a, b), _ = scene((gh(f"acme/billing/tree/{one}"), dwell, ["kelpweave"], []), (gh(f"acme/billing/tree/{two}"), dwell, ["loomwarp"], []))
        return analyse(recs), a, b

    def test_two_different_non_integration_branches(self):
        (fs, es), a, b = self.branches("feature-retry", "fix-pagination")
        cl = [e for e in es.pair(a, b) if e.type == "cannot_link"]
        self.assertEqual([(e.category, e.active, e.detail["scope_match"]) for e in cl], [("x_branch", True, "same_scope")])
        self.assertEqual(cl[0].detail["branches"], ["feature-retry", "fix-pagination"])

    def test_same_branch_integration_branch_and_missing_branch_never_restrict(self):
        for one, two in (("dev-wip", "dev-wip"), ("main", "fix-pagination"), ("master", "develop"), ("trunk", "feature-x")):
            (fs, es), a, b = self.branches(one, two)
            self.assertEqual(es.of_type("cannot_link"), (), (one, two))
        recs, (a, b), _ = scene((gh("acme/billing/tree/feature-x"), 90, ["kelpweave"], []), (gh("acme/billing"), 90, ["loomwarp"], []))
        fs, es = analyse(recs)
        self.assertEqual(es.of_type("cannot_link"), ())                                  # absence is not a different branch

    def test_a_branch_in_an_editor_title_is_comparable_with_a_repository_page_by_name_only(self):
        title = lambda branch, project="billing": fx.Item("Visual Studio Code", "com.microsoft.VSCode", "code_file", "a.py", f"a.py - {project} ({branch})")
        recs, (page, editor, other), _ = scene((gh("acme/billing/tree/feature-retry"), 20, ["kelpweave"], []), (title("fix-pagination"), 20, ["loomwarp"], []),
                                                (title("fix-pagination", "platform"), 20, ["orchardwing"], []))
        fs, es = analyse(recs)
        got = {e.target: e.detail["scope_match"] for e in es.items if e.source == page and e.type == "cannot_link"}
        self.assertEqual(got, {editor: "project_equals_repo_name"})                       # the 'platform' project is not the 'billing' repository

    def test_a_branch_with_a_slash_in_a_path_is_not_a_branch(self):
        f = fx.blob_slash_branch()
        fs, es = ae.analyse(ai.prepare(f.records), P)
        self.assertEqual(es.of_type("cannot_link"), ())
        self.assertFalse([n for v in fs.visits for n in v.namespaces if n.kind == "branch"])

    def test_conflicts_pair_positive_evidence_with_an_active_restriction(self):
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 90, ["kelpweave"], ["notes.md"]), (gh("acme/billing/pull/15"), 90, ["loomwarp"], ["notes.md"]))
        fs, es = analyse(recs)
        self.assertEqual(es.conflicts(), (((a, b), ("shared_artifact", "shared_tokens"), ("x_pr",)),))      # the stem "notes" is a shared rare word too
        recs, (a, b), _ = scene((gh("acme/billing/pull/12"), 90, ["kelpweave"], ["notes.md", "#15"]), (gh("acme/billing/pull/15"), 90, ["loomwarp"], ["notes.md"]))
        fs, es = analyse(recs)
        self.assertEqual(es.conflicts(), ())                                              # overridden: not active, so not a conflict

    def test_fixture_expectations_hold_for_every_fixture(self):
        for f in fx.all_fixtures():
            fs, es = ae.analyse(ai.prepare(f.records), P)
            active = es.cannot_link_pairs()
            self.assertTrue({frozenset(p) for p in f.cannot_link} <= active, f.name)
            self.assertFalse({frozenset(p) for p in f.must_merge} & active, f.name)


class BoundsAndDeterminismTests(unittest.TestCase):
    def test_a_key_shared_by_many_visits_is_limited_to_a_successor_window(self):
        item = fx.code("a.py", "billing")
        b = fx.Builder(3)
        for _ in range(6):
            b.visit(item, b.part(None, 40, n=0), {"x"})
            b.visit(fx.chrome("reading_page", f"sep{len(b.visits)}.x.com", "t", "https://x.com/s"), b.part(None, 20, n=0), {"y"})
        recs = b.records
        fs, es = analyse(recs, P, dataclasses.replace(FP, max_group=3))
        items = [e for e in es.items if e.category == "item"]
        self.assertEqual(len(items), 9)                                                   # 0-1 0-2 1-2 1-3 2-3 2-4 3-4 3-5 4-5
        self.assertTrue(any(k.startswith("item:") for k in es.truncated))
        fs, es = analyse(recs, P, dataclasses.replace(FP, max_group=40))
        self.assertEqual(len([e for e in es.items if e.category == "item"]), 15)
        self.assertFalse([k for k in es.truncated if k.startswith("item:")])

    def test_repeated_runs_and_any_input_order_give_the_same_evidence(self):
        for build in (fx.branches_alternating, fx.pr_numbers_differ, fx.continuous_multiapp, fx.shared_doc_two_purposes):
            recs = build().records
            base = json.dumps(ae.as_dict(analyse(recs)[1]), sort_keys=True)
            self.assertEqual(base, json.dumps(ae.as_dict(analyse(recs)[1]), sort_keys=True))
            self.assertEqual(base, json.dumps(ae.as_dict(analyse(hn.shuffled(recs, 2))[1]), sort_keys=True))
            self.assertEqual(base, json.dumps(ae.as_dict(analyse(hn.with_duplicates(recs, 2))[1]), sort_keys=True))

    def test_translation_and_joint_scaling_change_no_evidence(self):
        for name in ("branches_alternating", "pr_numbers_differ", "continuous_multiapp", "hub_between"):
            recs = fx.BUILDERS[name]().records
            base = ae.as_dict(analyse(recs)[1])
            for k, c in ((1.0, 4321.0), (1.0, -1e7), (1e-3, 0.0), (1e-6, 0.0), (1e-9, 0.0), (4.0, 0.0), (0.25, -77.0)):
                p = P.scaled(k)
                moved = hn.translate(hn.scale(recs, k), c)
                prepared = ai.prepare(copy.deepcopy(moved), max_intra_visit_gap_s=p.max_intra_visit_gap_s, retention_hours=ai.RETENTION_HOURS * k,
                                      check_capture_clock=False)
                self.assertEqual(base, ae.as_dict(ae.analyse(prepared, p, FP.scaled(k))[1]), (name, k, c))

    def test_evidence_is_immutable_and_decides_nothing(self):
        fs, es = analyse(fx.pr_numbers_differ().records)
        e = es.items[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            e.active = False
        with self.assertRaises(TypeError):
            e.detail["x"] = 1
        self.assertEqual({f.name for f in dataclasses.fields(ae.Evidence)},
                         {"type", "source", "target", "category", "key", "cls", "active", "detail", "source_moments", "target_moments", "via", "quality"})
        self.assertIsInstance(es.items, tuple)

    def test_gaps_in_optional_fields_do_not_stop_it(self):
        recs = copy.deepcopy(fx.branches_alternating().records)
        for r in recs[::3]:
            r["digest"], r["provenance"], r["url"], r["window"] = None, None, None, None
        fs, es = analyse(recs)
        self.assertEqual(len(fs.visits), len(es.visit_ids))

    def test_pairs_are_unique_per_key(self):
        fs, es = analyse(fx.branches_alternating().records)
        keys = [(e.type, e.source, e.target, e.category, e.key) for e in es.items]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys, sorted(keys, key=lambda k: (es.visit_ids.index(k[1]), es.visit_ids.index(k[2]), k[0], k[3], k[4])))


if __name__ == "__main__":
    unittest.main()

"""The synthetic fixtures: valid Moment input, deterministic, consistent gold, and the structure each scenario promises."""
import json
import os
import re
import stat
import tempfile
import unittest

import activity_eval as ev
import activity_fixtures as fx
import activity_input as ai
import moments


def j(x):
    return json.dumps(x, sort_keys=True)


class EveryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all = fx.all_fixtures()

    def test_all_scenarios_present(self):
        names = {f.name for f in self.all}
        for need in ("continuous_multiapp", "two_in_one_app", "interruptions_returns", "long_gaps", "shared_entities",
                     "ambiguous_moments", "shared_doc_two_purposes", "hub_between", "same_doc_short_gap",
                     "same_tab_different_file", "branches_alternating", "same_branch_two_tasks", "same_pr_three_pages",
                     "pr_numbers_differ", "blob_slash_branch", "internal_shift", "overlap_sweep_2", "continuous_multiapp_noise40"):
            self.assertIn(need, names)

    def test_deterministic_for_a_seed_and_never_reads_the_clock(self):
        for name, make in fx.BUILDERS.items():
            self.assertEqual(j(make().records), j(make().records), name)
        self.assertNotEqual(j(fx.continuous_multiapp(1).records), j(fx.continuous_multiapp(2).records))
        self.assertTrue(all(r["t_start"] >= fx.T0 for f in self.all for r in f.records))

    def test_records_are_valid_moments_in_the_persisted_shape(self):
        for f in self.all:
            with self.subTest(f.name):
                p = ai.prepare(f.records)
                self.assertEqual(p.report["duplicates_dropped"], 0)
                for r in f.records:
                    self.assertEqual(set(r) - ai.RECORD_KEYS, set())
                    self.assertEqual(set(r["digest"]) - ai.DIGEST_KEYS, set())
                    self.assertEqual(r["schema"], moments.SCHEMA)
                    for t in r["digest"]["terms"]:
                        self.assertRegex(t, r"^[A-Za-z]{4,20}$")
                    self.assertEqual(len(r["digest"]["lines"]) <= 3, True)

    def test_gold_refers_to_real_moments_and_unsure_is_labelled(self):
        for f in self.all:
            with self.subTest(f.name):
                ids = {r["id"] for r in f.records}
                self.assertLessEqual(set(f.gold), ids)
                self.assertLessEqual(f.unsure, set(f.gold))
                if not f.name.startswith("overlap_sweep"):
                    self.assertEqual(set(f.gold), ids)

    def test_expectations_refer_to_real_visits_and_agree_with_gold(self):
        for f in self.all:
            with self.subTest(f.name):
                vids = set(f.visit_ids())
                vg = {v: set().union(*[f.gold[m] for m in ms if m in f.gold]) if any(m in f.gold for m in ms) else set()
                      for v, ms in f.moments_of().items()}
                for group in (f.must_merge, f.must_not_merge, f.cannot_link):
                    for a, b in group:
                        self.assertIn(a, vids)
                        self.assertIn(b, vids)
                self.assertLessEqual(set(f.expect_unassigned) | set(f.flagged_shift) | set(f.unflaggable_shift), vids)
                for a, b in f.must_merge:
                    self.assertTrue(vg[a] & vg[b], (a, b))
                for a, b in f.must_not_merge:
                    self.assertFalse(vg[a] & vg[b], (a, b))
                for a, b in f.cannot_link:
                    self.assertFalse(vg[a] & vg[b], (a, b))

    def test_gold_sheet_round_trips_through_the_existing_reader(self):
        for f in self.all:
            if not f.gold:
                continue
            with self.subTest(f.name), tempfile.TemporaryDirectory() as d:
                f.write_gold_sheet(d)
                gold, unsure, problems = ev.load_sheet(d)
                self.assertEqual(problems, [])
                self.assertEqual(gold, f.gold)
                self.assertEqual(unsure, f.unsure)

    def test_files_are_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "m")
            paths = fx.two_in_one_app().write_jsonl(sub)
            self.assertEqual(stat.S_IMODE(os.stat(sub).st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in paths))

    def test_no_typed_text_fields_anywhere(self):
        for f in self.all:
            text = j(f.records)
            for bad in ("typed", "pasted", "clipboard", "keystroke"):
                self.assertNotIn(f'"{bad}', text, f.name)


class NoiseTests(unittest.TestCase):
    def test_noise_changes_words_only(self):
        v = fx.noisy_variants()
        clean = v[0.0].records
        for lv in (0.1, 0.2, 0.4):
            noisy = v[lv].records
            self.assertEqual([(r["id"], r["t_start"], r["t_end"], r["item_id"], r["visit_id"]) for r in clean],
                             [(r["id"], r["t_start"], r["t_end"], r["item_id"], r["visit_id"]) for r in noisy])
        self.assertEqual(j(clean), j(fx.continuous_multiapp(1, 0.0).records))
        differing = lambda a, b: sum(x["digest"]["terms"] != y["digest"]["terms"] for x, y in zip(a, b))
        self.assertEqual(differing(clean, clean), 0)
        self.assertGreater(differing(clean, v[0.4].records), differing(clean, v[0.1].records) - 1)
        self.assertGreater(differing(clean, v[0.4].records), 0)

    def test_corrupt_keeps_letters_and_length(self):
        import random
        rng = random.Random(1)
        for w in ("attention", "softmax", "perplexity", "warmup"):
            for _ in range(20):
                c = fx.corrupt(w, rng)
                self.assertRegex(c, r"^[A-Za-z]{4,20}$")


class ScenarioStructureTests(unittest.TestCase):
    def test_continuous_crosses_four_apps_with_one_label(self):
        f = fx.continuous_multiapp()
        self.assertEqual(len({r["app"] for r in f.records}), 4)
        self.assertEqual({tuple(sorted(v)) for v in f.gold.values()}, {("paper-impl",)})

    def test_two_in_one_app_shares_the_app_not_the_work(self):
        f = fx.two_in_one_app()
        self.assertEqual({r["app"] for r in f.records}, {"Google Chrome"})
        self.assertEqual({l for v in f.gold.values() for l in v}, {"invoices", "trip"})

    def test_interruptions_are_short_single_frame_noise(self):
        f = fx.interruptions_returns()
        by = {r["visit_id"]: r for r in f.records}
        self.assertEqual(f.returns, 3)
        for v in f.expect_unassigned:
            r = by[v]
            self.assertLess(r["t_end"] - r["t_start"], 30)
            self.assertEqual(r["n_frames"], 1)
            self.assertEqual(f.gold[r["id"]], set())

    def test_long_gap_is_a_parameter(self):
        for g in (300.0, 3600.0):
            f = fx.long_gaps(gap_s=g)
            first, back = f.must_merge[0][0], f.must_merge[-1][1]
            starts = {r["visit_id"]: r["t_start"] for r in f.records}
            ends = {r["visit_id"]: r["t_end"] for r in f.records}
            second = [v for v in f.visit_ids() if starts[v] > ends[f.must_merge[0][1]]][0]
            self.assertAlmostEqual(starts[second] - ends[f.must_merge[0][1]], g + 2.0, places=3)

    def test_shared_artifact_fixtures_really_share_an_item(self):
        f = fx.shared_doc_two_purposes()
        doc = f.shared_artifacts[0]
        labels = {tuple(sorted(f.gold[r["id"]])) for r in f.records if r["item_id"] == doc}
        self.assertEqual(labels, {("budget",), ("hiring",)})
        f = fx.same_tab_different_file()
        self.assertEqual(len({r["item_id"] for r in f.records}), 1)
        f = fx.hub_between()
        inbox = f.shared_artifacts[0]
        self.assertEqual(sum(r["item_id"] == inbox for r in f.records), 6)
        self.assertTrue(all(f.gold[r["id"]] == set() for r in f.records if r["item_id"] == inbox))

    def test_branch_and_pr_fixtures_expose_what_the_rules_can_see(self):
        f = fx.branches_alternating()
        urls = " ".join(r["url"] or "" for r in f.records)
        self.assertIn("tree/feature-retry", urls)
        self.assertIn("tree/fix-pagination", urls)
        self.assertTrue(f.cannot_link)
        f = fx.same_branch_two_tasks()
        self.assertEqual({r["url"] for r in f.records if r["url"]}, {"github.com/acme/billing/tree/dev-wip"})
        self.assertEqual(f.cannot_link, [])
        f = fx.pr_numbers_differ()
        self.assertIn("pull/12", " ".join(r["url"] for r in f.records))
        self.assertIn("pull/15", " ".join(r["url"] for r in f.records))
        self.assertEqual(f.cannot_link, [])                       # contrast only (ADR 0004), never a hard cannot-link
        self.assertTrue(f.must_not_merge)
        f = fx.blob_slash_branch()
        self.assertNotIn("/tree/", " ".join(r["url"] for r in f.records))
        self.assertEqual(f.cannot_link, [])
        f = fx.same_pr_three_pages()
        self.assertTrue(all("#12" in r["digest"]["identifiers"] for r in f.records))

    def test_overlap_sweep_shares_exactly_the_requested_rare_words(self):
        for k in (0, 2, 4):
            f = fx.overlap_sweep(k)
            by = {}
            for r in f.records:
                by[r["visit_id"]] = set(r["digest"]["terms"])
            a, b = f.visit_pair
            rare = {"gantry", "ferrite", "quorum", "lattice", "tessera", "umbrage", "vellum", "zephyr"}
            self.assertEqual(len(by[a] & by[b] & rare), k)
            self.assertEqual(f.gold, {})

    def test_internal_shift_fixture_has_the_three_cases(self):
        f = fx.internal_shift()
        p = ai.prepare(f.records)
        shifted, flips, blind = p.visits
        d = lambda m: (tuple(m["digest"]["terms"]), tuple(m["digest"]["identifiers"]))
        self.assertNotEqual(d(shifted.moments[0]), d(shifted.moments[1]))
        self.assertEqual(len({d(m) for m in flips.moments}), 1)
        self.assertEqual(len({m["interaction"]["category"] for m in flips.moments}), 2)
        self.assertEqual(len({d(m) for m in blind.moments}), 1)
        self.assertNotEqual(f.gold[blind.moments[0]["id"]], f.gold[blind.moments[1]["id"]])
        self.assertEqual((f.flagged_shift, f.unflaggable_shift), ([shifted.visit_id], [blind.visit_id]))


if __name__ == "__main__":
    unittest.main()

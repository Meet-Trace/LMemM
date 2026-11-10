"""The invariant harness: stand-ins that obey the contract pass it, and each deliberately broken one is caught.

No inference exists yet. The stand-ins restate structural baselines (sittings, items) in the result contract, which is
enough to prove every check can both pass and fail, so the same checks can later run unchanged against the real thing.
"""
import unittest

import activity_contract as ac
import activity_fixtures as fx
import activity_harness as hn
import activity_stubs as st

P = ac.ReplayParams()


def failing(res):
    return {k for k, v in res.items() if v}


class GoodStubsPass(unittest.TestCase):
    def test_sittings_item_and_abstaining_obey_every_invariant_on_every_fixture(self):
        fixtures = fx.all_fixtures()
        for infer in (st.sittings_infer, st.item_infer, st.abstaining_infer):
            for f in fixtures:
                with self.subTest(infer.__name__, fixture=f.name):
                    res = hn.run_all(infer, f.records, P, forget_visits=f.visit_ids()[:2])
                    self.assertEqual(failing(res), set(), res)


class EveryBadStubIsCaught(unittest.TestCase):
    def test_hardcoded_duration_breaks_joint_scaling(self):
        recs = fx.long_gaps(gap_s=800.0).records
        self.assertEqual(hn.check_joint_scaling(st.sittings_infer, recs, P), [])
        self.assertTrue(hn.check_joint_scaling(st.hardcoded_gap_infer, recs, P))

    def test_time_of_day_breaks_translation(self):
        base = hn.translate(fx.continuous_multiapp().records, 3.9 * 3600 - 2)       # starts just before noon UTC
        self.assertEqual(hn.check_translation(st.sittings_infer, base, P), [])
        self.assertTrue(hn.check_translation(st.time_of_day_infer, base, P))

    def test_record_order_breaks_order_invariance(self):
        recs = fx.two_in_one_app().records
        self.assertEqual(hn.check_order_and_duplicates(st.sittings_infer, recs, P), [])
        self.assertTrue(hn.check_order_and_duplicates(st.order_dependent_infer, recs, P))

    def test_counter_ids_break_the_id_rules(self):
        recs = fx.two_in_one_app().records
        self.assertTrue(hn.check_ids(st.counter_id_infer, recs, P))
        self.assertTrue(hn.check_contract(st.counter_id_infer, recs, P))

    def test_status_leak_breaks_status_independence(self):
        recs = fx.long_gaps(gap_s=3600.0).records
        self.assertEqual(hn.check_status_independence(st.sittings_infer, recs, P), [])
        self.assertTrue(hn.check_status_independence(st.status_leak_infer, recs, P))

    def test_leaking_between_runs_breaks_forget(self):
        f = fx.two_in_one_app()
        st._MEMORY.clear()
        st.leaky_infer(f.records, P)                                   # an earlier run saw everything
        gone = f.visit_ids()[:2]
        self.assertEqual(hn.check_forget(st.sittings_infer, f.records, P, gone), [])
        self.assertTrue(hn.check_forget(st.leaky_infer, f.records, P, gone))

    def test_typed_field_in_output_is_caught(self):
        recs = fx.two_in_one_app().records
        self.assertEqual(hn.check_no_typed_text(st.sittings_infer, recs, P), [])
        self.assertTrue(hn.check_no_typed_text(st.typed_infer, recs, P))

    def test_typed_field_in_input_is_caught(self):
        recs = fx.two_in_one_app().records
        recs[0] = dict(recs[0], typed_text="secret")
        out = hn.check_no_typed_text(st.sittings_infer, recs, P)
        self.assertTrue(out)

    def test_totality_and_tentative_prediction_are_caught(self):
        recs = fx.two_in_one_app().records
        self.assertTrue(hn.check_contract(st.partial_infer, recs, P))
        bad = hn.check_contract(st.tentative_leak_infer, recs, P)
        self.assertTrue(any("tentative" in b for b in bad))

    def test_hidden_state_breaks_determinism(self):
        recs = fx.two_in_one_app().records
        self.assertTrue(hn.check_determinism(st.hidden_state_infer, recs, P))
        self.assertEqual(hn.check_determinism(st.sittings_infer, recs, P), [])

    def test_app_names_matter_to_a_stub_that_uses_them(self):
        def uses_app(records, params):
            import activity_input as ai
            p = ai.prepare(records)
            by = {}
            for v in p.visits:
                by.setdefault(v.moments[0]["app"] == "Google Chrome", []).append(v.visit_id)
            return st._build(p, list(by.values()))
        recs = fx.continuous_multiapp().records
        self.assertTrue(hn.check_app_rename(uses_app, recs, P))
        self.assertEqual(hn.check_app_rename(st.sittings_infer, recs, P), [])


class FixtureExpectations(unittest.TestCase):
    def test_cannot_link_and_pair_checks_can_fail_and_pass(self):
        f = fx.branches_alternating()
        # sittings put everything in one activity: it breaks every cannot-link and every must-not-merge
        self.assertEqual(len(hn.check_cannot_links(st.sittings_infer, f.records, P, f.cannot_link)), len(f.cannot_link))
        self.assertTrue(hn.check_must_pairs(st.sittings_infer, f.records, P, f.must_merge, f.must_not_merge))
        # one activity per visit satisfies them
        self.assertEqual(hn.check_cannot_links(st.item_infer, f.records, P, f.cannot_link), [])

    def test_directional_helper_for_a_time_parameter(self):
        make = lambda g: fx.long_gaps(gap_s=g).records
        series = hn.outcome_series(st.sittings_infer, make, P, [300.0, 1200.0, 3600.0], lambda r: len(r["activities"]))
        self.assertEqual(series, [1, 2, 2])
        self.assertEqual(hn.check_directional(series, [1, 2, 2]), [])
        self.assertEqual(len(hn.check_directional(series, [1, 1, 2])), 1)

    def test_time_params_check_is_green_on_the_real_class(self):
        self.assertEqual(hn.check_time_params_declared(), [])


class TransformTests(unittest.TestCase):
    def test_transforms_do_not_touch_their_input_and_keep_records_valid(self):
        import activity_input as ai
        recs = fx.continuous_multiapp().records
        snap = [dict(r) for r in recs]
        for out in (hn.translate(recs, 100.0), hn.scale(recs, 4), hn.rename_apps(recs, 1), hn.shuffled(recs, 1),
                    hn.with_duplicates(recs, 1)):
            ai.prepare(out)
        self.assertEqual([dict(r) for r in recs], snap)

    def test_scale_multiplies_durations_and_translate_does_not(self):
        r = fx.long_gaps().records
        later = [x for x in r if x["context"]["same_item_before"]][0]
        s, t = hn.scale([later], 2)[0], hn.translate([later], 50.0)[0]
        self.assertEqual(s["context"]["same_item_before"]["gap_s"], later["context"]["same_item_before"]["gap_s"] * 2)
        self.assertEqual(t["context"]["same_item_before"]["gap_s"], later["context"]["same_item_before"]["gap_s"])
        self.assertEqual(t["context"]["same_item_before"]["ended_at"], later["context"]["same_item_before"]["ended_at"] + 50.0)

    def test_remove_visits_scrubs_pointers_like_a_real_forget(self):
        f = fx.long_gaps()
        first = f.visit_ids()[0]
        later = [r for r in f.records if (r["context"].get("same_item_before") or {}).get("visit_id") == first]
        self.assertTrue(later)
        kept, gone = hn.remove_visits(f.records, [first])
        self.assertTrue(all((r["context"].get("same_item_before") or {}).get("visit_id") != first for r in kept))
        self.assertTrue(all(r["context"].get("prev_moment_id") not in gone for r in kept))


if __name__ == "__main__":
    unittest.main()

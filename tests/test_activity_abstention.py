"""Abstention-aware evaluation, on hand-made results (no inference anywhere)."""
import unittest

import activity_abstention as ab
import activity_contract as ac
import activity_fixtures as fx
import activity_input as ai
from activity_stubs import _build


def result(prepared, groups, states=None, flags=None):
    return _build(prepared, groups, flags, states)


class VisitGoldTests(unittest.TestCase):
    def build(self, parts_labels):
        b = fx.Builder(1)
        parts = [b.part("budget", 60, labels=lab) for lab in parts_labels]
        b.visit(fx.code("a.py"), parts)
        f = b.done("x")
        p = ai.prepare(f.records)
        return p.visits[0], f.gold

    def test_also_labels_do_not_break_purity(self):
        v, gold = self.build([{"A", "B"}, {"A"}])
        g = ab.visit_gold(v, gold)
        self.assertEqual((g["kind"], g["labels"]), ("pure", {"A"}))

    def test_different_labels_are_mixed_and_get_no_label(self):
        v, gold = self.build([{"A"}, {"B"}])
        g = ab.visit_gold(v, gold)
        self.assertEqual((g["kind"], g["labels"]), ("mixed", set()))

    def test_noise_beside_an_activity_is_mixed_all_noise_is_pure(self):
        v, gold = self.build([{"A"}, set()])
        self.assertEqual(ab.visit_gold(v, gold)["kind"], "mixed")
        v, gold = self.build([set(), set()])
        g = ab.visit_gold(v, gold)
        self.assertEqual((g["kind"], g["labels"]), ("pure", set()))

    def test_unsure_moments_do_not_count_and_a_visit_with_none_is_unlabelled(self):
        f = fx.ambiguous_moments()
        p = ai.prepare(f.records)
        self.assertTrue(all(ab.visit_gold(v, f.gold, f.unsure)["kind"] == "unlabelled" for v in p.visits))


class TwoActivityTests(unittest.TestCase):
    def setUp(self):
        self.f = fx.two_in_one_app()
        self.p = ai.prepare(self.f.records)
        self.ids = [v.visit_id for v in self.p.visits]
        self.a, self.b = self.ids[0::2], self.ids[1::2]            # invoices, trip (alternating)

    def test_perfect_result(self):
        r = ab.report(self.p, self.f.gold, self.f.unsure, result(self.p, [self.a, self.b]))
        self.assertEqual(r["precision_assigned"], 1.0)
        self.assertEqual(r["recall_eligible"], 1.0)
        self.assertEqual(r["coverage"]["all"], 1.0)
        self.assertEqual(r["states"]["rates"][ac.ASSIGNED], 1.0)
        self.assertEqual(r["tentative_rate"], 0.0)
        self.assertEqual(r["visits"], {"total": 12, "pure": 12, "mixed_gold": 0, "unlabelled": 0})

    def test_abstention_is_visible_and_tentative_is_not_a_success(self):
        x, y = self.a[:3], self.b[:2]
        states = {self.b[2]: (ac.TENTATIVE, "artifact_only"), self.a[3]: (ac.AMBIGUOUS, "margin_too_small")}
        states.update({v: (ac.UNASSIGNED, "no_candidate") for v in self.a[4:] + self.b[3:]})
        res = result(self.p, [x, y], states)
        assert ac.validate_result(res, {v.visit_id: v.t_start for v in self.p.visits}) == []
        r = ab.report(self.p, self.f.gold, self.f.unsure, res)
        c = r["states"]["counts"]
        self.assertEqual((c["assigned"], c["tentative"], c["ambiguous"], c["unassigned"]), (5, 1, 1, 5))
        self.assertAlmostEqual(r["states"]["rates"]["unassigned"], 5 / 12)
        self.assertAlmostEqual(r["tentative_rate"], 1 / 12)
        self.assertEqual(r["states"]["reasons"]["unassigned:no_candidate"], 5)
        self.assertEqual(r["precision_assigned"], 1.0)                     # 4 predicted pairs, all true
        self.assertAlmostEqual(r["recall_eligible"], 4 / 30)               # 30 true pairs, abstentions are misses
        self.assertEqual(r["coverage"]["all"], 5 / 12)
        self.assertEqual(r["coverage"]["eligible_visits"], 12)

    def test_singleton_seeds_are_reported_apart(self):
        res = result(self.p, [self.a[:3], self.b[:2], [self.b[2]]],
                     {v: (ac.UNASSIGNED, "no_candidate") for v in self.a[3:] + self.b[3:]})
        cov = ab.report(self.p, self.f.gold, self.f.unsure, res)["coverage"]
        self.assertEqual(cov["all"], 6 / 12)
        self.assertEqual(cov["excluding_singletons"], 5 / 12)
        self.assertEqual(cov["singleton_activities"], 1)

    def test_nothing_assigned_has_undefined_precision_not_one(self):
        res = result(self.p, [], {v: (ac.UNASSIGNED, "no_candidate") for v in self.ids})
        r = ab.report(self.p, self.f.gold, self.f.unsure, res)
        self.assertIsNone(r["precision_assigned"])
        self.assertEqual(r["recall_eligible"], 0.0)
        self.assertEqual(r["coverage"]["all"], 0.0)

    def test_a_result_that_breaks_the_contract_is_refused(self):
        res = result(self.p, [self.a])
        with self.assertRaises(ValueError):
            ab.report(self.p, self.f.gold, self.f.unsure, res)

    def test_wrong_join_lowers_precision(self):
        res = result(self.p, [self.a[:2] + [self.b[0]], self.b[1:], self.a[2:]])
        r = ab.report(self.p, self.f.gold, self.f.unsure, res)
        self.assertLess(r["precision_assigned"], 1.0)

    def test_equal_coverage_comparison_restricts_baselines_to_assigned_visits(self):
        keep = self.a[:2] + self.b[:2]
        res = result(self.p, [self.a[:2], self.b[:2]], {v: (ac.UNASSIGNED, "no_candidate") for v in self.ids if v not in keep})
        whole = {m["id"]: "all" for m in self.p.records}
        out = ab.at_equal_coverage(self.p, self.f.gold, self.f.unsure, res, {"B5 whole": whole})
        self.assertEqual(out["visits"], 4)
        self.assertEqual(out["method"]["precision"], 1.0)
        self.assertAlmostEqual(out["B5 whole"]["precision"], 2 / 6)
        self.assertEqual(out["B5 whole"]["recall"], 1.0)


class MixedGoldAndShiftTests(unittest.TestCase):
    def setUp(self):
        self.f = fx.internal_shift()
        self.p = ai.prepare(self.f.records)
        self.shifted, self.flips, self.blind = [v.visit_id for v in self.p.visits]

    def flag(self, vid, idx):
        v = self.p.visit(vid)
        return {"from_moment_id": v.moments[0]["id"], "to_moment_id": v.moments[idx]["id"]}

    def test_mixed_visits_are_counted_apart_and_left_out_of_primary_scores(self):
        res = result(self.p, [[self.shifted], [self.flips], [self.blind]])
        r = ab.report(self.p, self.f.gold, self.f.unsure, res)
        self.assertEqual(r["visits"], {"total": 3, "pure": 1, "mixed_gold": 2, "unlabelled": 0})
        self.assertAlmostEqual(r["mixed_gold"]["share_of_labelled"], 2 / 3)
        self.assertEqual(r["mixed_gold"]["treatment"]["assigned"], 2)
        self.assertIsNone(r["precision_assigned"])                         # one pure visit: no pairs, nothing to score
        self.assertEqual(len(r["mixed_gold"]["assigned"]), 2)
        covers = r["mixed_gold"]["assigned"][0]["covers"]
        self.assertEqual(covers, {"budget": False, "hiring": False})        # joined no pure visit of either label

    def test_internal_shift_against_moment_gold(self):
        res = result(self.p, [[self.shifted], [self.flips], [self.blind]], flags={self.shifted: self.flag(self.shifted, 1)})
        s = ab.report(self.p, self.f.gold, self.f.unsure, res)["internal_shift"]
        self.assertEqual((s["tp"], s["fp"], s["fn"], s["tn"]), (1, 0, 1, 1))
        self.assertEqual(s["precision"], 1.0)
        self.assertEqual(s["detection_rate"], 0.5)
        self.assertEqual(s["missed"], [self.blind])                       # the digest never changed: a known blind spot
        self.assertEqual(s["boundary_hit_rate"], 1.0)
        self.assertAlmostEqual(s["flag_rate"], 1 / 3)

    def test_false_alarm_and_wrong_boundary(self):
        res = result(self.p, [[self.shifted], [self.flips], [self.blind]],
                     flags={self.flips: self.flag(self.flips, 2), self.shifted: self.flag(self.shifted, 0)})
        s = ab.report(self.p, self.f.gold, self.f.unsure, res)["internal_shift"]
        self.assertEqual(s["false_alarms"], [self.flips])
        self.assertEqual(s["fp"], 1)
        self.assertEqual(s["boundary_hit_rate"], 1.0)                      # moment 0 is within one of the change at 1

    def test_ceiling_shows_the_cost_of_visit_level_assignment(self):
        c = ab.ceiling(self.p, self.f.gold, self.f.unsure)
        self.assertAlmostEqual(c["precision"], 3 / 5)
        self.assertEqual(c["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()

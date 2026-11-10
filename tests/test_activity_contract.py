"""The result contract: activity ids, the parameter registry, result validation. No inference."""
import unittest

import activity_contract as ac
import activity_fixtures as fx
import activity_input as ai


class IdTests(unittest.TestCase):
    def test_id_is_a_hash_of_the_earliest_member_only(self):
        starts = {"v1-1": 10.0, "v2-2": 5.0, "v3-3": 7.0}
        a = ac.activity_id(ac.earliest_member(starts, ["v1-1", "v2-2", "v3-3"]))
        self.assertEqual(a, ac.activity_id("v2-2"))
        self.assertTrue(a.startswith("act-") and len(a) == 16)
        self.assertEqual(ac.activity_id("v2-2"), ac.activity_id("v2-2"))
        self.assertNotEqual(ac.activity_id("v2-2"), ac.activity_id("v3-3"))

    def test_ties_break_on_visit_id(self):
        self.assertEqual(ac.earliest_member({"b": 1.0, "a": 1.0}, ["b", "a"]), "a")


class ParamTests(unittest.TestCase):
    def test_every_seconds_field_is_declared(self):
        self.assertEqual(ac.undeclared_time_params(), [])
        self.assertEqual(ac.declared_but_missing(), [])

    def test_undeclared_field_is_caught(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Bad(ac.ReplayParams):
            sneaky_gap_s: float = 5.0
        self.assertEqual(ac.undeclared_time_params(Bad), ["sneaky_gap_s"])

    def test_scaled_multiplies_every_time_param_and_nothing_else(self):
        p = ac.ReplayParams()
        q = p.scaled(4)
        for n in ac.ASSIGNMENT_TIME + ac.REPORTING_TIME:
            self.assertEqual(getattr(q, n), getattr(p, n) * 4)

    def test_reporting_group_only(self):
        p = ac.ReplayParams()
        self.assertEqual(p.with_reporting(status_active_s=1.0).status_active_s, 1.0)
        with self.assertRaises(ValueError):
            p.with_reporting(t_resume_s=1.0)


def row(v, state=ac.ASSIGNED, act=None, reason=None):
    return {"visit_id": v, "state": state, "activity_id": act, "reason": reason, "candidates": []}


class ValidateTests(unittest.TestCase):
    starts = {"a": 1.0, "b": 2.0, "c": 3.0}

    def good(self):
        act = ac.activity_id("a")
        return {"visits": [row("a", act=act), row("b", act=act), row("c", ac.UNASSIGNED, None, "no_substance")],
                "activities": [{"id": act, "members": ["a", "b"]}]}

    def test_sound_result_passes(self):
        self.assertEqual(ac.validate_result(self.good(), self.starts), [])

    def test_totality_one_state_per_visit(self):
        r = self.good()
        r["visits"] = r["visits"][:-1]
        self.assertTrue(ac.validate_result(r, self.starts))
        r = self.good()
        r["visits"].append(row("a", act=ac.activity_id("a")))
        self.assertTrue(ac.validate_result(r, self.starts))

    def test_only_assigned_carries_an_activity(self):
        r = self.good()
        r["visits"][2].update(state=ac.TENTATIVE, activity_id=ac.activity_id("a"), reason="artifact_only")
        self.assertTrue(any("must not carry" in p for p in ac.validate_result(r, self.starts)))

    def test_reason_codes_and_assigned_reason(self):
        r = self.good()
        r["visits"][2]["reason"] = None
        self.assertTrue(ac.validate_result(r, self.starts))
        r = self.good()
        r["visits"][0]["reason"] = "margin_too_small"
        self.assertTrue(ac.validate_result(r, self.starts))

    def test_activity_id_must_hash_the_earliest_member(self):
        r = self.good()
        r["activities"][0]["id"] = r["visits"][0]["activity_id"] = r["visits"][1]["activity_id"] = ac.activity_id("b")
        self.assertTrue(any("earliest" in p for p in ac.validate_result(r, self.starts)))

    def test_predictions_only_for_assigned_and_partition(self):
        r = self.good()
        moments = {"a": ["a.0"], "b": ["b.0", "b.1"], "c": ["c.0"]}
        pred = ac.predictions(r, moments)
        self.assertEqual(set(pred), {"a.0", "b.0", "b.1"})
        self.assertEqual(ac.partition(r), {frozenset({"a", "b"})})


if __name__ == "__main__":
    unittest.main()

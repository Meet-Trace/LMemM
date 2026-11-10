"""calibrate_judge reads a taught judge's shift off held-out lessons, and project_reasoner_ai.run applies it."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import calibrate_judge as cj  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner_ai as ai  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "project_grouping"


class ShiftTest(unittest.TestCase):
    def test_the_shift_keeps_the_allowed_share_of_false_yeses(self):
        scores = [(0, -3.0), (0, -2.0), (0, -1.0), (0, 0.5), (1, 2.0), (1, -0.5)]
        self.assertAlmostEqual(cj.false_yes_rate(scores), 0.25)
        shift = cj.shift_for(scores, 0.25)
        self.assertAlmostEqual(shift, 0.25)                      # the line halfway between the "no" scores 0.5 and -1.0
        self.assertAlmostEqual(cj.false_yes_rate(scores, shift), 0.25)
        self.assertAlmostEqual(cj.recall(scores, shift), 0.5)
        self.assertAlmostEqual(cj.recall(scores), 0.5)
        self.assertAlmostEqual(cj.shift_for(scores, 0.0), -0.5)    # no false yes at all: the line is the top "no"
        self.assertAlmostEqual(cj.false_yes_rate(scores, cj.shift_for(scores, 0.0)), 0.0)

    def test_a_stricter_judge_gets_a_positive_shift_and_a_looser_one_a_negative_shift(self):
        reference = [("pair", 0, -2.0), ("pair", 0, -1.0), ("pair", 0, 1.0), ("pair", 0, -3.0),
                     ("pair", 1, 3.0), ("pair", 1, 1.0), ("pair", 1, -1.0), ("pair", 1, 2.0)]
        strict = [(k, y, s - 2.0) for k, y, s in reference]
        loose = [(k, y, s + 2.0) for k, y, s in reference]
        self.assertAlmostEqual(cj.calibrate(strict, reference)["pair"]["shift"], 2.0)
        self.assertAlmostEqual(cj.calibrate(loose, reference)["pair"]["shift"], -2.0)
        same = cj.calibrate(reference, reference)["pair"]
        self.assertAlmostEqual(same["shift"], 0.0)
        self.assertEqual(same["recall_after"], same["reference_recall"])
        self.assertEqual(same["false_yes_after"], same["false_yes_allowed"])

    def test_kinds_without_both_answers_are_left_out_and_the_work_question_is_never_read(self):
        scores = [("pair", 0, -1.0), ("pair", 1, 1.0), ("project", 1, 2.0), ("general", 0, 3.0)]
        self.assertEqual(set(cj.calibrate(scores, None, 0.0)), {"pair"})


class BiasTest(unittest.TestCase):
    def run_day(self, bias, answers):
        items = pge.load_day(FIXTURES / "versions_day.json")
        timeline = json.loads((FIXTURES / "versions_day.json").read_text())["timeline"]
        seen = []

        def judge(instruct, query, doc):
            seen.append(instruct)
            return answers[instruct]
        _, _, log = ai.run(items, timeline, judge, None, "raw", bias=bias)
        return log, seen

    ANSWERS = {ai.INSTRUCT_GENERAL: -2.0, ai.INSTRUCT_PAIR: -0.5, ai.INSTRUCT_PROJECT: -0.5, ai.INSTRUCT_WORK: 1.0}

    def tasks(self, log):
        return [t for e in log if e["step"] == "relate" for t in e["tasks"]]

    def test_a_pair_bias_turns_near_misses_into_merges_and_leaves_the_other_questions_alone(self):
        plain, _ = self.run_day(None, self.ANSWERS)
        shifted, _ = self.run_day({"pair": 1.0}, self.ANSWERS)
        self.assertGreater(len(self.tasks(plain)), len(self.tasks(shifted)))
        general = lambda log: [e["score"] for e in log if e["step"] == "general"]      # noqa: E731
        self.assertEqual(general(plain), general(shifted))

    def test_a_project_bias_turns_a_no_to_a_project_into_a_join(self):
        plain, _ = self.run_day(None, self.ANSWERS)
        shifted, _ = self.run_day({"project": 1.0}, self.ANSWERS)
        joins = lambda log: sum(1 for e in log if e.get("decision", "").startswith("joined"))   # noqa: E731
        self.assertEqual(joins(plain), 0)
        self.assertGreater(joins(shifted), 0)

    def test_no_bias_changes_nothing(self):
        a, _ = self.run_day(None, self.ANSWERS)
        b, _ = self.run_day({"pair": 0.0, "project": 0.0}, self.ANSWERS)
        self.assertEqual(a, b)


class SubsetAndMeanTest(unittest.TestCase):
    def test_scores_are_split_by_story_in_the_order_of_the_lessons(self):
        rows = [{"kind": "pair", "label": 1, "story": "a"}, {"kind": "general", "label": 1, "story": "a"},
                {"kind": "pair", "label": 0, "story": "b"}, {"kind": "project", "label": 1, "story": "a"}]
        scores = [("pair", 1, 2.0), ("pair", 0, -1.0), ("project", 1, 0.5)]
        self.assertEqual(cj.by_story(rows, scores, {"a"}), [("pair", 1, 2.0), ("project", 1, 0.5)])
        self.assertEqual(cj.by_other_story(rows, scores, {"a"}), [("pair", 0, -1.0)])

    def test_the_hard_stories_are_named(self):
        ids = cj.hard_story_ids()
        self.assertIn("rb-meal-planner", ids)
        self.assertEqual(len(ids), 22)

    def test_the_mean_judge_averages_asks_both_and_adds_up_its_cost(self):
        class One:
            calls, seconds = 2, 0.5

            def __init__(self, value):
                self.value, self.asked = value, 0

            def __call__(self, instruct, query, doc):
                self.asked += 1
                return self.value
        a, b = One(1.0), One(4.0)
        judge = ai.MeanJudge([a, b])
        self.assertAlmostEqual(judge("i", "q", "d"), 2.5)
        self.assertEqual((a.asked, b.asked, judge.calls, judge.seconds), (1, 1, 4, 1.0))
        _, _, log = ai.run(pge.load_day(FIXTURES / "sample_day.json"), [], judge, None, "raw")
        self.assertTrue(log)                                   # it runs wherever a judge does
        self.assertGreater(a.asked, 1)


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "experiments", "moment_goals"))

import closeness as C
import grouping as G

S = 1_000_000_000


def unit(*v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class GroupingTests(unittest.TestCase):
    def test_close_vectors_group_and_far_ones_do_not(self):
        vectors = np.stack([unit(1, 0, 0), unit(0.98, 0.1, 0), unit(0, 1, 0), unit(0.05, 0.99, 0)])
        self.assertEqual(G.group(vectors, 0.8), [[0, 1], [2, 3]])
        self.assertEqual(G.group(vectors, 0.999), [[0], [1], [2], [3]])

    def test_chain_of_neighbours_is_not_merged_into_one_group(self):
        vectors = np.stack([unit(1, 0), unit(1, 0.45), unit(1, 0.9), unit(1, 1.4)])
        groups = G.group(vectors, 0.9)
        self.assertGreater(len(groups), 1)


class ScoringTests(unittest.TestCase):
    def test_pair_scoring(self):
        ids = ["a", "b", "c", "d"]
        truth = {frozenset(("a", "b")), frozenset(("c", "d"))}
        scope = set(ids)
        s = C.score(C.pairs_of([[0, 1], [2], [3]], ids), truth, scope)
        self.assertEqual((s["found"], s["hit"]), (1, 1))
        self.assertEqual(s["precision"], 1.0)
        self.assertEqual(s["recall"], 0.5)
        wrong = C.score(C.pairs_of([[0, 2]], ids), truth, scope)
        self.assertEqual(wrong["precision"], 0.0)
        self.assertEqual(C.score(set(), truth, scope)["precision"], None)

    def test_pairs_outside_labelled_scope_are_ignored(self):
        ids = ["a", "b", "c"]
        s = C.score(C.pairs_of([[0, 1, 2]], ids), {frozenset(("a", "b"))}, {"a", "b"})
        self.assertEqual(s["found"], 1)

    def test_time_baseline_groups_nearby_moments(self):
        moments = [{"start_ns": 0, "end_ns": 10 * S}, {"start_ns": 100 * S, "end_ns": 110 * S}, {"start_ns": 5000 * S, "end_ns": 5010 * S}]
        self.assertEqual(C.time_groups(moments, 600), [[0, 1], [2]])
        self.assertEqual(C.time_groups(moments, 10), [[0], [1], [2]])


class EndToEndTests(unittest.TestCase):
    def test_closeness_run_writes_groups_and_reports(self):
        words = {"Editing the tracker": unit(1, 0, 0), "Updating the outreach sheet": unit(0.97, 0.2, 0),
                 "Reading a paper": unit(0, 1, 0), "Skimming research article": unit(0.1, 0.98, 0)}
        moments = [{"id": f"m{i}", "start_ns": i * 1000 * S, "end_ns": i * 1000 * S + 10 * S} for i in range(4)]
        goals = [{"moment_id": f"m{i}", "goal": g} for i, g in enumerate(words)]
        with tempfile.TemporaryDirectory() as d:
            json.dump({"moments": moments, "models": {"vlm": {"goals": goals, "groups": []}}, "baseline_packet_groups": []}, open(os.path.join(d, "moment_goals.json"), "w"))
            json.dump({"m0": "sheet", "m1": "sheet", "m2": "paper", "m3": "paper"}, open(os.path.join(d, "labels.json"), "w"))
            original = C.embed
            C.embed = lambda host, model, texts, threads=0: np.stack([words[t] for t in texts])
            try:
                self.assertEqual(C.main(["--goals", d, "--model", "vlm", "--threshold", "0.8"]), 0)
            finally:
                C.embed = original
            saved = json.load(open(os.path.join(d, "moment_goals.json")))
        self.assertEqual(saved["models"]["vlm"]["groups"], [[0, 1], [2, 3]])
        self.assertEqual(saved["models"]["vlm"]["group_threshold"], 0.8)
        reasons = saved["models"]["vlm"]["group_reasons"]
        self.assertEqual(sorted(reasons), ["1", "2"])
        self.assertIn("average similarity", reasons["1"][0])
        self.assertIn("Closest pair", reasons["1"][1])
        self.assertTrue(any(line.startswith("Time (supporting evidence only)") for line in reasons["1"]))
        self.assertTrue(any(line.startswith("Nearest Moment left out") for line in reasons["1"]))

    def test_moments_without_a_description_stay_unassigned(self):
        moments = [{"id": f"m{i}", "start_ns": i * S, "end_ns": i * S + S} for i in range(3)]
        goals = [{"moment_id": "m0", "goal": "a"}, {"moment_id": "m1", "goal": ""}, {"moment_id": "m2", "goal": "a"}]
        with tempfile.TemporaryDirectory() as d:
            json.dump({"moments": moments, "models": {"vlm": {"goals": goals, "groups": []}}, "baseline_packet_groups": []}, open(os.path.join(d, "moment_goals.json"), "w"))
            original = C.embed
            C.embed = lambda host, model, texts, threads=0: np.stack([unit(1, 0)] * len(texts))
            try:
                C.main(["--goals", d, "--model", "vlm"])
            finally:
                C.embed = original
            saved = json.load(open(os.path.join(d, "moment_goals.json")))
        self.assertEqual(saved["models"]["vlm"]["groups"], [[0, 2]])


if __name__ == "__main__":
    unittest.main()

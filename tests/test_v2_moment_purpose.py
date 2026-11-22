import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "experiments", "moment_goals"))

import numpy as np

import purpose


class ParseTests(unittest.TestCase):
    def test_purpose_two_lines(self):
        r = purpose.parse_purpose("PURPOSE: Fix a failing test in the trace pipeline\nWHY HERE: the terminal runs the tests")
        self.assertTrue(r["clear"])
        self.assertEqual(r["why"], "the terminal runs the tests")

    def test_unclear_purpose(self):
        self.assertFalse(purpose.parse_purpose("PURPOSE: unclear\nWHY HERE: n/a")["clear"])

    def test_empty_purpose(self):
        self.assertFalse(purpose.parse_purpose("")["clear"])

    def test_verdict_same(self):
        r = purpose.parse_verdict("VERDICT: SAME\nREASON: both fix the same bug")
        self.assertEqual(r["verdict"], "SAME")
        self.assertEqual(r["reason"], "both fix the same bug")

    def test_verdict_lowercase_different(self):
        self.assertEqual(purpose.parse_verdict("verdict: different\nreason: x")["verdict"], "DIFFERENT")

    def test_verdict_unknown_keeps_text(self):
        r = purpose.parse_verdict("I cannot tell")
        self.assertEqual(r["verdict"], "unknown")


class GroupTests(unittest.TestCase):
    def test_pair_group(self):
        self.assertEqual(purpose.build_groups(3, {(0, 1)}), [[0, 1], [2]])

    def test_chain_without_support_does_not_merge(self):
        self.assertEqual(purpose.build_groups(3, {(0, 1), (1, 2)}), [[0, 1], [2]])

    def test_full_triangle_merges(self):
        self.assertEqual(purpose.build_groups(3, {(0, 1), (1, 2), (0, 2)}), [[0, 1, 2]])

    def test_no_pairs(self):
        self.assertEqual(purpose.build_groups(2, set()), [[0], [1]])


class CandidateTests(unittest.TestCase):
    def test_floor_and_neighbours(self):
        v = np.array([[1, 0], [0.99, 0.14], [0, 1]], dtype=np.float32)
        v = v / np.linalg.norm(v, axis=1, keepdims=True)
        pairs, _ = purpose.candidate_pairs(v, 0.5, 2)
        self.assertEqual(pairs, [(0, 1)])


if __name__ == "__main__":
    unittest.main()

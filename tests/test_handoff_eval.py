"""Deterministic quality evaluation of the rule generator on the versioned synthetic set.

This checks the critical facts only (constraints kept, nothing speculative promoted to a decision,
completed work not repeated, unrelated material left out). It does not replace human review of
real handoffs, which the release checklist requires.
"""

import json
import os
import subprocess
import sys
import unittest

from handoff.generator import RuleGenerator
from handoff.model import Conversation, Message

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handoff_fixtures")
SET = json.load(open(os.path.join(HERE, "conversations-v1.json"), encoding="utf-8"))

# Release thresholds (see docs/handoff.md). A drop below any of these blocks release.
MIN_CONSTRAINT_RECALL = 1.0
MAX_UNSUPPORTED_DECISIONS = 0
MIN_FACT_RECALL = 0.95
MAX_SIZE_RATIO = 0.8                      # package length / transcript length on long conversations


def run(fix):
    msgs = [Message(m["role"], m["text"]) for m in fix["messages"]]
    conv = Conversation("fixture", "Assistant", fix["id"], "complete", msgs)
    return RuleGenerator().generate(conv, "Cursor", fix["expect"].get("max_chars", 24000))


def has(items, needle):
    return any(needle.lower() in (i.text if hasattr(i, "text") else i).lower() for i in items)


class EvalSetTests(unittest.TestCase):
    def test_set_is_current_and_large_enough(self):
        self.assertEqual(SET["version"], 1)
        self.assertGreaterEqual(len(SET["conversations"]), 50)
        cats = {c["category"] for c in SET["conversations"]}
        self.assertGreaterEqual(len(cats), 9)
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_eval_set.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_thresholds(self):
        c_total = c_hit = f_total = f_hit = unsupported = 0
        misses, ratios = [], []
        for fix in SET["conversations"]:
            e, out = fix["expect"], run(fix)
            for needle in e.get("constraints", []):
                c_total += 1
                if has(out.context, needle) and needle.lower() in out.text.lower():
                    c_hit += 1
                else:
                    misses.append((fix["id"], "constraint", needle))
            for key, pool in (("task", [out.task]), ("decisions", out.decisions), ("rejected", out.rejected),
                              ("completed", out.completed), ("outstanding", out.outstanding), ("artifacts", out.artifacts)):
                for needle in e.get(key, []):
                    f_total += 1
                    if has(pool, needle):
                        f_hit += 1
                    else:
                        misses.append((fix["id"], key, needle))
            for needle in e.get("forbid_in_decisions", []):
                if has(out.decisions, needle):
                    unsupported += 1
                    misses.append((fix["id"], "unsupported decision", needle))
            for needle in e.get("forbid_in_completed", []):
                if has(out.completed, needle):
                    unsupported += 1
                    misses.append((fix["id"], "repeats unfinished work as done", needle))
            for needle in e.get("forbid_in_text", []):
                if needle.lower() in out.text.lower():
                    unsupported += 1
                    misses.append((fix["id"], "unrelated material", needle))
            for needle, n in e.get("cite", {}).items():
                line = next((i for i in out.context + out.decisions if needle.lower() in i.text.lower()), None)
                if line is None or n not in line.refs:
                    f_total += 1
                    misses.append((fix["id"], f"provenance msg {n}", needle))
                else:
                    f_total += 1
                    f_hit += 1
            if len(fix["messages"]) > 20:
                ratios.append(len(out.text) / sum(len(m["text"]) for m in fix["messages"]))
        report = f"\nconstraints {c_hit}/{c_total}, facts {f_hit}/{f_total}, unsupported {unsupported}\n" + \
                 "\n".join(map(str, misses))
        self.assertGreaterEqual(c_hit / c_total, MIN_CONSTRAINT_RECALL, report)
        self.assertLessEqual(unsupported, MAX_UNSUPPORTED_DECISIONS, report)
        self.assertGreaterEqual(f_hit / f_total, MIN_FACT_RECALL, report)
        self.assertTrue(ratios)
        self.assertLess(max(ratios), MAX_SIZE_RATIO, ratios)


if __name__ == "__main__":
    unittest.main()

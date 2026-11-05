#!/usr/bin/env python3
"""How well does a small model answer "were these two things used for the same work?"

    python3 scripts/judge_pairs.py --model gemma3:1b
    python3 scripts/judge_pairs.py --model gemma3:1b --day tests/fixtures/project_grouping/held_out_day.json

Takes every pair of things on a labelled day that belong to the same project, and as many pairs that do
not (picked at random with a fixed seed), asks the model each pair in both orders (src/pair_judge.py),
and compares with the labels. The code-only assign method is scored on the same pairs for comparison.
"""
import argparse
import itertools
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pair_judge  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402

DEFAULT_DAY = ROOT / "tests" / "fixtures" / "project_grouping" / "sample_day.json"


def pct(n, d):
    return "  n/a" if not d else f"{100 * n / d:3.0f}%"


def pick_pairs(items, max_pairs, seed=7):
    """(same pairs, different pairs): every same-project pair, and as many different ones at random."""
    gold = pge.gold(items, "project")
    same, diff = [], []
    for a, b in itertools.combinations(sorted(items), 2):
        (same if gold[a] & gold[b] else diff).append((a, b))
    random.Random(seed).shuffle(same)
    random.Random(seed).shuffle(diff)
    half = max_pairs // 2
    return same[:half], diff[:max(half, max_pairs - len(same[:half]))]


def main(argv=None, model=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=Path, default=DEFAULT_DAY)
    p.add_argument("--model", default="gemma3:1b")
    p.add_argument("--url", default="http://127.0.0.1:11434", help="Ollama on this Mac")
    p.add_argument("--max-pairs", type=int, default=60)
    p.add_argument("--show", type=int, default=8, help="print this many wrong answers")
    args = p.parse_args(argv)
    items = pge.load_day(args.day)
    timeline = json.loads(args.day.read_text()).get("timeline") or []
    model = model or reasoner.OllamaModel(args.model, args.url)
    same, diff = pick_pairs(items, args.max_pairs)
    code, _, _ = assign.run(items, timeline, graph.CapitalisedNames())
    rows, started = [], time.monotonic()
    for truth, pairs in (("yes", same), ("no", diff)):
        for a, b in pairs:
            how = pair_judge.usage(a, b, timeline)
            r = pair_judge.judge(model, items[a], items[b], how)
            together = bool(set(code.get(a, [])) & set(code.get(b, [])))
            rows.append({"a": a, "b": b, "truth": truth, "how": how, **r, "code": "yes" if together else "no"})
    took = time.monotonic() - started

    def table(key):
        yes = [r for r in rows if r[key] == "yes"]
        no = [r for r in rows if r[key] == "no"]
        right = sum(1 for r in yes + no if r[key] == r["truth"])
        found = sum(1 for r in yes if r["truth"] == "yes")
        return (pct(right, len(yes) + len(no)), pct(found, len(yes)), pct(found, len(same)),
                pct(len(rows) - len(yes) - len(no), len(rows)))
    print(f"{args.day.name}: {len(same)} same-project pairs, {len(diff)} different, model {args.model}, "
          f"{2 * len(rows)} calls in {took:.0f} s ({took / max(1, 2 * len(rows)):.1f} s a call)\n")
    print(f"{'':22} {'right when sure':>15} {'yes is right':>13} {'same found':>11} {'unsure':>7}")
    for label, key in (("model, both orders", "verdict"), ("code (assign method)", "code")):
        print(f"{label:22} " + " ".join(f"{v:>{w}}" for v, w in zip(table(key), (15, 13, 11, 7))))
    flips = sum(1 for r in rows if r["answers"][0] != r["answers"][1])
    firsts = [r["answers"][0] for r in rows]
    print(f"\norder flips: {flips} of {len(rows)} pairs changed answer when the cards were swapped")
    print("first-order answers: " + ", ".join(f"{k} {firsts.count(k)}" for k in ("yes", "no", "unsure")))
    wrong = [r for r in rows if r["verdict"] in ("yes", "no") and r["verdict"] != r["truth"]]
    for r in wrong[:args.show]:
        print(f"  said {r['verdict']}, truth {r['truth']}: {items[r['a']].get('title')}  +  {items[r['b']].get('title')}")
    return rows


if __name__ == "__main__":
    main()

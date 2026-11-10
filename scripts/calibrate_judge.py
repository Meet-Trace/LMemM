#!/usr/bin/env python3
"""Read the shift a taught judge needs off held-out lessons, so the exam days are not used to set it. Research only.

    python scripts/calibrate_judge.py --lessons lessons_v5/valid.jsonl \\
        --reference-adapter judge_v3 --adapter judge_v5 --out calibration_v5.json

A judge taught on many look-alikes (judge_v5: three in four of the "no" answers about a story are its twin) says
"no" more often than the judge the pipeline's zero was set for, so its right answers fall below zero too. The
pipeline decides "yes" above a margin of 0, and a wrong join is the worst mistake, so the shift is chosen to
keep the REFERENCE judge's wrong-join rate and nothing else: for each kind of lesson (pair, project), the
share of held-out "no" lessons the reference says yes to at 0 is its false-yes rate; the new judge gets the
smallest shift that leaves it no more false yeses than that. What it then gains in recall is measured, not
assumed. Both judges read the same held-out lessons, which neither was taught on (lessons from make_judge_lessons.py,
valid.jsonl). The result is the `bias` of project_reasoner_ai.run ({"pair": x, "project": y}): add it to the
judge's answer.

With --reference-adapter left out, only the new judge is scored and the shift is read against a false-yes rate
given with --false-yes.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KINDS = ("pair", "project")


def false_yes_rate(scores, shift=0.0):
    """Share of the "no" lessons answered yes ((score + shift) > 0). scores: [(label, score)]."""
    no = [s for y, s in scores if y == 0]
    return sum(1 for s in no if s + shift > 0) / len(no) if no else 0.0


def recall(scores, shift=0.0):
    yes = [s for y, s in scores if y == 1]
    return sum(1 for s in yes if s + shift > 0) / len(yes) if yes else 0.0


def shift_for(scores, rate):
    """A shift that leaves at most `rate` of the "no" lessons answered yes, as large as that allows. The shift is
    added to the answer, so it is positive when the judge is too strict. The line sits halfway between the lowest
    "no" lesson allowed above it and the highest one that is not, so it does not rest on one lesson's exact score."""
    no = sorted((s for y, s in scores if y == 0), reverse=True)
    if not no:
        return 0.0
    allowed = int(rate * len(no) + 1e-9)               # this many "no" lessons may stay above the line
    if allowed == 0:
        line = no[0]
    elif allowed < len(no):
        line = (no[allowed] + no[allowed - 1]) / 2
    else:
        line = no[-1] - 1.0
    return -line


def calibrate(new, reference=None, false_yes=None):
    """{"pair": {...}, "project": {...}} from [(kind, label, score)] of the new judge (and the reference's)."""
    out = {}
    for kind in KINDS:
        mine = [(y, s) for k, y, s in new if k == kind]
        if not mine or not any(y == 0 for y, _ in mine) or not any(y == 1 for y, _ in mine):
            continue
        ref = [(y, s) for k, y, s in (reference or []) if k == kind]
        rate = false_yes_rate(ref) if ref else (false_yes if false_yes is not None else 0.0)
        shift = shift_for(mine, rate)
        out[kind] = {"shift": round(shift, 3), "false_yes_allowed": round(rate, 4),
                     "false_yes_at_zero": round(false_yes_rate(mine), 4),
                     "false_yes_after": round(false_yes_rate(mine, shift), 4),
                     "recall_at_zero": round(recall(mine), 4), "recall_after": round(recall(mine, shift), 4),
                     "reference_recall": round(recall(ref), 4) if ref else None,
                     "lessons": {"yes": sum(1 for y, _ in mine if y == 1), "no": sum(1 for y, _ in mine if y == 0)}}
    return out


def score_lessons(scorer, rows):
    """[(kind, label, log-odds yes vs no)] for the lessons of the kinds in KINDS (in the order of those lessons)."""
    import tiny_scorer as ts
    out = []
    for r in rows:
        if r.get("kind") in KINDS:
            out.append((r["kind"], r["label"], ts.yes_no(scorer.options_for(r["instruct"], r["query"], r["doc"]))))
    return out


def hard_story_ids(path=ROOT / "research" / "judge_lessons" / "stories_hard.json"):
    return {s["id"] for s in json.loads(Path(path).read_text())}


def by_story(rows, scores, ids):
    """The scores of the lessons whose story is in `ids` (rows are the lessons, scores their scores in KINDS order)."""
    kept = [r for r in rows if r.get("kind") in KINDS]
    return [x for r, x in zip(kept, scores) if r.get("story") in ids]


def by_other_story(rows, scores, ids):
    kept = [r for r in rows if r.get("kind") in KINDS]
    return [x for r, x in zip(kept, scores) if r.get("story") not in ids]


def load_scores(repo, adapter, rows):
    import tiny_scorer as ts
    return score_lessons(ts.MlxRerankScorer(repo, adapter), rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lessons", type=Path, required=True, help="valid.jsonl from make_judge_lessons.py")
    p.add_argument("--adapter", required=True, help="the judge to calibrate (a train_judge.py adapter folder)")
    p.add_argument("--reference-adapter", default=None, help="the judge whose wrong-join rate is kept")
    p.add_argument("--false-yes", type=float, default=None, help="false-yes rate to keep when there is no reference")
    p.add_argument("--model", default="mlx-community/Qwen3-Reranker-0.6B-4bit")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    rows = [json.loads(x) for x in args.lessons.read_text().splitlines() if x.strip()]
    reference = load_scores(args.model, args.reference_adapter, rows) if args.reference_adapter else None
    new = load_scores(args.model, args.adapter, rows)
    result = calibrate(new, reference, args.false_yes)
    bias = {k: v["shift"] for k, v in result.items()}
    # the same numbers for the lessons of the round-5 stories and for the older ones, each against the reference
    # on the same lessons: did the new stories teach anything on lessons held out from them?
    hard = hard_story_ids()
    subsets = {}
    if reference:
        for name, pick in (("round 5 stories", by_story), ("older stories", by_other_story)):
            subsets[name] = calibrate(pick(rows, new, hard), pick(rows, reference, hard))
    raw = [{"kind": r["kind"], "label": r["label"], "story": r.get("story"), "new": n[2],
            "reference": (reference[i][2] if reference else None)}
           for i, (r, n) in enumerate(zip([r for r in rows if r.get("kind") in KINDS], new))]
    args.out.write_text(json.dumps({"bias": bias, "by_kind": result, "by_stories": subsets, "adapter": args.adapter,
                                    "reference": args.reference_adapter, "lessons": str(args.lessons)}, indent=1))
    args.out.with_suffix(".scores.json").write_text(json.dumps(raw))
    print(json.dumps({"bias": bias, "by_kind": result, "by_stories": subsets}, indent=1))
    return result


if __name__ == "__main__":
    main()

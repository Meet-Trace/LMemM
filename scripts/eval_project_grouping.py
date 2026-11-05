#!/usr/bin/env python3
"""Score a grouping of things into projects against a labelled day. Reads files only.

    python3 scripts/eval_project_grouping.py                       # baselines on the invented day
    python3 scripts/eval_project_grouping.py --day my_day.json     # a day you labelled
    python3 scripts/eval_project_grouping.py --predicted out.json  # also score {id: [project, ...]}

See src/project_grouping_eval.py for what the numbers mean.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import project_grouping_eval as pge  # noqa: E402

DEFAULT_DAY = ROOT / "tests" / "fixtures" / "project_grouping" / "sample_day.json"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=Path, default=DEFAULT_DAY, help="labelled day (default: the invented sample)")
    p.add_argument("--predicted", type=Path, help="a grouping to score: {id: [project path, ...]}")
    p.add_argument("--mistakes", type=int, default=5, help="mistakes to list per grouping (0 for none)")
    args = p.parse_args(argv)
    try:
        items = pge.load_day(args.day)
        groupings = {name: fn(items) for name, fn in pge.BASELINES.items()}
        if args.predicted:
            raw = json.loads(args.predicted.read_text())
            if not isinstance(raw, dict) or not all(isinstance(v, list) for v in raw.values()):
                raise ValueError("--predicted must be a JSON object of id: [project, ...].")
            groupings[args.predicted.stem] = raw
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    print(pge.report(items, groupings, mistakes=args.mistakes))


if __name__ == "__main__":
    main()

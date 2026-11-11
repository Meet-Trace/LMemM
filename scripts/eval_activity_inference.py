#!/usr/bin/env python3
"""Evaluate the activity inference against labelled datasets. Reads files only; nothing is sent anywhere.

    python3 scripts/eval_activity_inference.py DATASET_DIR [DATASET_DIR ...] [--json out.json] [--scenario NAME]

A dataset directory holds moments.jsonl, units.csv (the annotation sheet filled in by you) and optionally dataset.json
({"name": ..., "scenario": ...}). See src/activity_evaluation.py. Output is counts, rates and opaque ids only.
Exit codes: 0 ok, 2 usage / dataset problem, 3 the input failed an integrity check (activity_input).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import activity_evaluation as ae  # noqa: E402
import activity_input as ai  # noqa: E402


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("datasets", nargs="+", type=Path)
    a.add_argument("--json", type=Path, help="write the full summary here (owner-only file)")
    a.add_argument("--scenario", help="only datasets with this scenario tag")
    args = a.parse_args(argv)
    try:
        sets = [ae.load_dataset(p) for p in args.datasets]
    except ae.DatasetError as e:
        print(f"dataset problem: {e}", file=sys.stderr)
        return 2
    if args.scenario:
        sets = [d for d in sets if d.scenario == args.scenario]
    if not sets:
        print("no dataset selected", file=sys.stderr)
        return 2
    try:
        summary = ae.evaluate_many(sets)
    except ai.ActivityInputError as e:
        print(f"input rejected: {e.problems[:3]}", file=sys.stderr)
        return 3
    print(ae.render(summary))
    if args.json:
        fd = os.open(args.json, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ae.dump_json(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

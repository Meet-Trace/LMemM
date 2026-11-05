#!/usr/bin/env python3
"""Export your own days from memory.json and sessions/ into day files to label and score. Read-only.

    python3 scripts/export_real_days.py --list                          # which days exist
    python3 scripts/export_real_days.py --from 2026-11-02 --to 2026-11-08

Writes data/labelled-days/<day>.json (git-ignored). Re-exporting keeps labels you already gave.
These files hold your real screen text: keep them on this Mac.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import real_days  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--memory-dir", default=None, help="default: LMemM's memory folder")
    p.add_argument("--out", default=None, help="default: <data>/labelled-days")
    p.add_argument("--from", dest="first", help="first day, YYYY-MM-DD")
    p.add_argument("--to", dest="last", help="last day, YYYY-MM-DD")
    p.add_argument("--list", action="store_true", help="only list the days that have sessions")
    args = p.parse_args(argv)
    memory_dir = args.memory_dir or config.paths().memory_dir
    out = args.out or os.path.join(config.paths().data_dir, "labelled-days")
    try:
        if args.list:
            rows = real_days.summary(memory_dir)
            print(f"{'day':12} {'things':>7} {'visits':>7} {'minutes':>8}")
            for day, things, visits, minutes in rows:
                print(f"{day:12} {things:>7} {visits:>7} {minutes:>8}")
            print(f"\n{len(rows)} days")
            return
        written = real_days.export(memory_dir, out, args.first, args.last)
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    for path in written:
        print(path)
    print(f"\n{len(written)} day files in {out}. Label them, then score with scripts/reason_projects.py --day <file>.")


if __name__ == "__main__":
    main()

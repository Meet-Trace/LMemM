#!/usr/bin/env python3
"""Tools to label and score activities. Reads files only; nothing is sent anywhere.

    # 1. a sheet to label (from moments recorded with config.MOMENT_LOG=True, or a stored session):
    python3 scripts/eval_activities.py sheet --moments-dir ~/path/to/memory/moments --out ~/Desktop/sheet1
    python3 scripts/eval_activities.py sheet --session ~/path/to/memory/sessions/<id>.json --memory ~/path/to/memory/memory.json --out ~/Desktop/sheet2
    #    fill the `activity` column in units.csv, and one row per activity in activities.csv

    # 2. score the structural baselines against what you labelled (and any system's output):
    python3 scripts/eval_activities.py score --sheet ~/Desktop/sheet1 [--units units.json] [--predicted out.json]

    # 3. the baselines on the invented labelled days (no labelling needed):
    python3 scripts/eval_activities.py days [--day tests/fixtures/project_grouping/pivot_day.json]

`--predicted` is {unit_id: cluster}. See src/activity_eval.py for what each number means.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import activity_eval as ev  # noqa: E402
import moment_store  # noqa: E402

DAYS = ROOT / "tests" / "fixtures" / "project_grouping"


def show(units, gold, extra=None, unsure=(), out=print):
    cov = ev.coverage(units, gold, unsure)
    out(f"  units {cov['units']}, labelled {cov['labelled']} (noise {cov['noise']}), unlabelled {cov['unlabelled']}, "
        f"unsure {cov['unsure']}, activities {cov['activities']}")
    systems = dict(ev.baselines(units))
    systems.update(extra or {})
    for name, pred in systems.items():
        out("  " + ev.report(name, ev.evaluate(units, gold, pred, unsure)))
    for note in ev.limits(cov):
        out("  note: " + note)


def cmd_days(a):
    paths = [a.day] if a.day else sorted(DAYS.glob("*_day.json"))
    for p in paths:
        day = json.loads(Path(p).read_text())
        units, gold = ev.units_from_day(day)
        print(f"{Path(p).name}")
        show(units, gold)
    return 0


def cmd_sheet(a):
    if a.moments_dir:
        recs = moment_store.MomentStore(str(a.moments_dir)).read(since=a.since)
        units = ev.units_from_moments(recs)
    elif a.session:
        doc = json.loads(Path(a.session).read_text())
        items = {}
        if a.memory:
            mem = json.loads(Path(a.memory).read_text())
            items = mem.get("items", mem)
        units = ev.units_from_session(doc, items)
    else:
        print("give --moments-dir or --session", file=sys.stderr)
        return 2
    if not units:
        print("nothing to label", file=sys.stderr)
        return 1
    up, ap = ev.write_sheet(units, str(a.out))
    fd = os.open(Path(a.out) / "units.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(units, fh)
    print(f"{len(units)} units -> {up}\nfill `activity` there (blank = skip, '-' = noise), and list each activity in {ap}")
    return 0


def cmd_score(a):
    gold, unsure, problems = ev.load_sheet(str(a.sheet))
    for p in problems:
        print("problem:", p)
    units_path = Path(a.units) if a.units else Path(a.sheet) / "units.json"
    units = json.loads(units_path.read_text())
    extra = {}
    if a.predicted:
        extra["predicted"] = {k: v for k, v in json.loads(Path(a.predicted).read_text()).items()}
    show(units, gold, extra, unsure)
    return 1 if problems else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("days"); d.add_argument("--day", type=Path); d.set_defaults(fn=cmd_days)
    s = sub.add_parser("sheet")
    s.add_argument("--moments-dir", type=Path); s.add_argument("--since", type=float)
    s.add_argument("--session", type=Path); s.add_argument("--memory", type=Path)
    s.add_argument("--out", type=Path, required=True); s.set_defaults(fn=cmd_sheet)
    c = sub.add_parser("score")
    c.add_argument("--sheet", type=Path, required=True); c.add_argument("--units", type=Path)
    c.add_argument("--predicted", type=Path); c.set_defaults(fn=cmd_score)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

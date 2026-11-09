#!/usr/bin/env python3
"""Measure project grouping on generated streams with the hard cases built in (src/synthetic_days.py).

    python3 scripts/stress_projects.py --projects 100 --seed 101 --model caps
    python3 scripts/stress_projects.py --projects 1000 --seed 101 --model none --method graph

Seeds 1-99 are for tuning; report on seeds 100 and up. Generated data is not real data: it only shows
how a method handles the problems the generator was written to contain.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import project_grouping_eval as pge  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import project_reasoner_judge as judge  # noqa: E402
import synthetic_days  # noqa: E402

METHODS = {"graph": lambda items, timeline, model: graph.run(items, timeline, model),
           "assign": lambda items, timeline, model: assign.run(items, timeline, model),
           "judge": lambda items, timeline, model: judge.run(items, timeline, model)}


def pct(x):
    return "  n/a" if x is None else f"{x * 100:3.0f}%"


def main(argv=None, model=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--projects", type=int, default=100)
    p.add_argument("--seed", type=int, default=101)
    p.add_argument("--model", default="caps", help="none, caps, or an Ollama model name")
    p.add_argument("--method", choices=sorted(METHODS), default="assign")
    p.add_argument("--save", type=Path, help="also write the generated stream here")
    args = p.parse_args(argv)
    doc = synthetic_days.generate(args.projects, args.seed)
    if args.save:
        args.save.write_text(json.dumps(doc, indent=1))
    items, timeline = doc["items"], doc["timeline"]
    if model is None:
        model = (None if args.model == "none" else graph.CapitalisedNames() if args.model == "caps"
                 else reasoner.OllamaModel(args.model))
    started = time.monotonic()
    assigned, projects, log = METHODS[args.method](items, timeline, model)
    took = time.monotonic() - started
    days = len({v["from"][:10] for v in timeline})
    print(f"{args.projects} projects, seed {args.seed}: {len(items)} places, {len(timeline)} visits over {days} days; "
          f"method {args.method}, model {args.model}, {took:.1f}s, {len(projects)} projects made")
    print(f"\n{'level':12} {'pair P':>7} {'pair R':>7} {'B3 P':>6} {'B3 R':>6} {'B3 F1':>6} {'filed':>6} {'wrong mix':>10}")
    for level in pge.LEVELS:
        s = pge.score(pge.gold(items, level), assigned, level)
        b = pge.bcubed(pge.gold(items, level), assigned, level)
        print(f"{level:12} {pct(s['precision']):>7} {pct(s['recall']):>7} {pct(b['precision']):>6} {pct(b['recall']):>6}"
              f" {pct(b['f1']):>6} {pct(b['filed']):>6} {pct(b['wrong_mix']):>10}")
    print("\nHard cases: project pairs merged / pairs of that kind")
    for kind, (bad, total) in synthetic_days.hard_case_merges(doc, assigned).items():
        print(f"  {kind:16} {bad:>4} / {total:<5} {pct(bad / total if total else None)}")
    return assigned, doc


if __name__ == "__main__":
    main()

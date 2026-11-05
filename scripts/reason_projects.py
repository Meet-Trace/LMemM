#!/usr/bin/env python3
"""Let a local model group a labelled day into projects, sitting by sitting, and score the result.

    ollama pull qwen2.5:7b
    python3 scripts/reason_projects.py --model qwen2.5:7b
    python3 scripts/reason_projects.py --model qwen2.5:7b --day my_day.json --out run.json

Needs an Ollama server on this Mac (127.0.0.1). Reads the day file only; never touches LMemM's
memory or projects. See src/project_reasoner.py for how it decides.
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
import project_reasoner_steps as steps  # noqa: E402
import project_reasoner_tiny as tiny  # noqa: E402

DEFAULT_DAY = ROOT / "tests" / "fixtures" / "project_grouping" / "sample_day.json"


def main(argv=None, model=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=Path, default=DEFAULT_DAY)
    p.add_argument("--model", default="qwen2.5:7b", help="an Ollama model name")
    p.add_argument("--url", default="http://127.0.0.1:11434", help="Ollama on this Mac")
    p.add_argument("--mode", choices=("steps", "one-shot", "tiny"), default="steps",
                   help="steps: many small questions (default); one-shot: one big question per sitting; "
                        "tiny: fill-in-the-blank and multiple choice, for models under 1B")
    p.add_argument("--votes", type=int, default=1, help="tiny mode: ask each question this many times, keep the majority")
    p.add_argument("--out", type=Path, help="write predictions, projects and the decision log here")
    p.add_argument("--mistakes", type=int, default=5)
    args = p.parse_args(argv)
    try:
        items = pge.load_day(args.day)
        timeline = json.loads(args.day.read_text()).get("timeline")
        if not timeline:
            raise ValueError("This day has no \"timeline\", so there are no sittings to reason over.")
        model = model or reasoner.OllamaModel(args.model, args.url)
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    started = time.monotonic()
    if args.mode == "tiny":
        assigned, projects, log = tiny.run(items, timeline, model, votes=args.votes)
    else:
        assigned, projects, log = (steps if args.mode == "steps" else reasoner).run(items, timeline, model)
    took = time.monotonic() - started
    for entry in log:
        if "skipped" in entry:
            print(f"SKIPPED {entry.get('step') or entry.get('sitting')}: {entry['skipped']}")
        elif "place" in entry or entry.get("step") == "calls":
            bits = [entry.get("place") or "", entry.get("topic") and f"topic={entry['topic']}",
                    entry.get("part") and f"part={entry['part']}", entry.get("decision")]
            print("  ".join(b for b in bits if b))
        elif "picked" in entry:
            print(f"{entry['step']} {entry['name']!r} -> {entry['picked']!r} (from {entry['options']})")
        elif "decision" in entry:
            conf = f" ({entry['confidence']:.2f})" if entry.get("confidence") is not None else ""
            print(f"{entry['decision']}: {entry.get('project') or '-'}{conf} <- {', '.join(entry['places'])}")
            print(f"    doing: {entry['doing']}")
            if entry.get("why"):
                print(f"    why:   {entry['why']}")
    print(f"\nProjects it made: {', '.join(projects) or 'none'}   ({took:.0f}s)\n")
    name = f"model: {args.model} ({args.mode}{', votes ' + str(args.votes) if args.mode == 'tiny' and args.votes > 1 else ''})"
    print(pge.report(items, {**{n: f(items) for n, f in pge.BASELINES.items()}, name: assigned},
                     mistakes=args.mistakes))
    if args.out:
        args.out.write_text(json.dumps({"model": args.model, "mode": args.mode, "seconds": round(took, 1), "predicted": assigned,
                                        "projects": projects, "log": log}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

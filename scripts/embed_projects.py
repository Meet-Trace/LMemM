#!/usr/bin/env python3
"""Group with meaning vectors (all-minilm on the plain card) plus switches and shared names, and tune it.

    python3 scripts/embed_projects.py                       # tune: labelled days + generated seeds 1-3
    python3 scripts/embed_projects.py --report 0.35,4,1     # report one setting on seeds 101-103
    python3 scripts/embed_projects.py --embed hash          # no model: words hashed into a vector

A setting is floor,task,project (project_reasoner_assign.EMBED): cosine below floor says nothing; task is
the link evidence for a back-to-back pair at full similarity; project is the score for a task at full
similarity to a project's average. The first row ("off") is the same method without vectors.
Vectors are cached in .cache/place_vectors.json, so only new cards are embedded. Generated and labelled
data are invented; nothing here reads your memory.
"""
import argparse
import itertools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import place_vectors as pv  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import synthetic_days as sd  # noqa: E402

DAYS = [ROOT / "tests" / "fixtures" / "project_grouping" / n for n in ("sample_day.json", "held_out_day.json")]
GRID = {"floor": (0.1, 0.25, 0.4), "task": (2.0, 4.0, 8.0), "project": (0.5, 1.5)}


def streams(seeds, projects, days=True):
    out = []
    if days:
        for d in DAYS:
            doc = json.loads(d.read_text())
            out.append((d.stem, {"items": pge.load_day(d), "timeline": doc.get("timeline") or []}))
    for s in seeds:
        out.append((f"seed {s}", sd.generate(projects, s)))
    return out


def score(doc, vectors, setting):
    items, tl = doc["items"], doc["timeline"]
    assigned, _, _ = assign.run(items, tl, graph.CapitalisedNames(), vectors=vectors, embed=setting)
    b = pge.bcubed(pge.gold(items, "project"), assigned, "project")
    hard = sum(x for x, _ in sd.hard_case_merges(doc, assigned).values()) if "world" in doc else 0
    return b, hard


def main(argv=None, embed=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--embed", default="ollama", choices=("ollama", "hash"))
    p.add_argument("--embed-model", default="all-minilm:22m")
    p.add_argument("--embed-url", default="http://127.0.0.1:11455")
    p.add_argument("--screen", action="store_true", help="also embed the screen text")
    p.add_argument("--projects", type=int, default=100)
    p.add_argument("--seeds", default=None, help="default 1,2,3 when tuning, 101,102,103 with --report")
    p.add_argument("--report", default=None, help="floor,task,project to report on held-out seeds")
    p.add_argument("--cache", type=Path, default=ROOT / ".cache" / "place_vectors.json")
    args = p.parse_args(argv)
    embed = embed or (pv.WordHashEmbedder() if args.embed == "hash" else pv.ollama(args.embed_model, args.embed_url))
    seeds = [int(s) for s in (args.seeds or ("101,102,103" if args.report else "1,2,3")).split(",")]
    runs = streams(seeds, args.projects)
    started = time.monotonic()
    vecs = {name: pv.vectors(doc["items"], embed, cache=args.cache, screen=args.screen) for name, doc in runs}
    print(f"{sum(len(v) for v in vecs.values())} cards embedded or read from cache in {time.monotonic() - started:.0f} s"
          f" ({'with' if args.screen else 'without'} screen text)\n")
    if args.report:
        f, t, pj = (float(x) for x in args.report.split(","))
        settings = [None, {"floor": f, "task": t, "project": pj}]
    else:
        settings = [None] + [dict(zip(GRID, combo)) for combo in itertools.product(*GRID.values())]
    names = [n for n, _ in runs]
    print(f"{'setting':16} " + " ".join(f"{n[:14]:>22}" for n in names) + f" {'mean F1':>8}")
    print(f"{'':16} " + " ".join(f"{'P/R/F1 wrong hard':>22}" for _ in names))
    for st in settings:
        cells, f1s = [], []
        for name, doc in runs:
            b, hard = score(doc, vecs[name] if st else None, st)
            f1s.append(b["f1"])
            cells.append(f"{b['precision']*100:3.0f}/{b['recall']*100:3.0f}/{b['f1']*100:3.0f} {b['wrong_mix']*100:3.0f}% {hard:>3}")
        label = "off" if st is None else f"{st['floor']},{st['task']:g},{st['project']:g}"
        print(f"{label:16} " + " ".join(f"{c:>22}" for c in cells) + f" {100 * sum(f1s) / len(f1s):>7.0f}%")


if __name__ == "__main__":
    main()

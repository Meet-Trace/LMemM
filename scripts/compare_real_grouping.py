#!/usr/bin/env python3
"""Your real days, grouped with and without meaning vectors, side by side. Read-only, stays on this Mac.

    python3 scripts/compare_real_grouping.py --days 14 --setting 0.4,4,0.5

Prints each project the method makes (name, how many things, their titles) for "off" and for the
setting, then the things whose project changed. Titles and counts only, never the text. Vectors are
cached in .cache/real_vectors.json in this folder (git ignores it); delete it when done.
"""
import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import group_names  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import place_vectors as pv  # noqa: E402
import project_finder as finder  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import real_days  # noqa: E402


def groups(items, timeline, vectors, setting):
    _, _, log = assign.run(items, timeline, graph.CapitalisedNames(), vectors=vectors, embed=setting)
    found = next((e["projects"] for e in log if e["step"] == "projects"), {})
    return {p: g for p, g in found.items()}


def show(title, found, items, out):
    out(f"\n== {title}: {len(found)} projects")
    for p, g in sorted(found.items(), key=lambda x: -len(x[1]["places"])):
        out(f"  {g['label'][:30]:30} {len(g['places']):>3} things, {g['tasks']} sittings")
        for i in g["places"][:8]:
            out(f"      {(items[i].get('app') or '?')[:14]:14} {(items[i].get('title') or i)[:60]}")
        if len(g["places"]) > 8:
            out(f"      ... and {len(g['places']) - 8} more")


def main(argv=None, out=print, embed=None, name_model=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--memory", default=None, help="default: <data>/memory")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--setting", default="0.4,4,0.5", help="floor,task,project")
    p.add_argument("--screen", action="store_true")
    p.add_argument("--embed-model", default="all-minilm:22m")
    p.add_argument("--embed-url", default="http://127.0.0.1:11455")
    p.add_argument("--cache", type=Path, default=ROOT / ".cache" / "real_vectors.json")
    p.add_argument("--name-model", default=None, help="e.g. gemma3:1b: also suggest a name for each group")
    p.add_argument("--url", default="http://127.0.0.1:11434", help="Ollama with the naming model")
    p.add_argument("--no-vectors", action="store_true", help="only the groups without vectors (and names)")
    args = p.parse_args(argv)
    memory = args.memory or config.paths().memory_dir
    items, visits = real_days.read_items(memory), real_days.read_visits(memory)
    things, timeline = finder.recent(items, visits, datetime.now(), args.days)
    out(f"{len(things)} things and {len(timeline)} visits in the last {args.days} days")
    off = groups(things, timeline, None, None)
    if args.name_model or name_model:
        ask = name_model or reasoner.OllamaModel(args.name_model, args.url)
        keys = {i: finder._keys(things[i]) for i in things}
        out(f"\n== names for the {len(off)} groups (code / finder / {args.name_model or 'model'})")
        for g in sorted(off.values(), key=lambda g: -len(g["places"])):
            ids = [i for i in g["places"] if i in things]
            r = group_names.suggest(ask, things, ids, fallback=finder.name_for(things, ids, keys) or g["label"])
            out(f"  {len(ids):>3} things  {g['label'][:16]:16} {str(finder.name_for(things, ids, keys))[:20]:20} "
                f"-> {r['name'][:40]} ({r['by']})")
    if args.no_vectors:
        show("off (no vectors)", off, things, out)
        return
    embed = embed or pv.ollama(args.embed_model, args.embed_url)
    vecs = pv.vectors(things, embed, cache=args.cache, screen=args.screen)
    f, t, pj = (float(x) for x in args.setting.split(","))
    on = groups(things, timeline, vecs, {"floor": f, "task": t, "project": pj})
    show("off (no vectors)", off, things, out)
    show(f"with vectors {args.setting}", on, things, out)

    def where(found):
        at = defaultdict(lambda: "unfiled")
        for g in found.values():
            for i in g["places"]:
                at[i] = g["label"]
        return at
    a, b = where(off), where(on)
    moved = [i for i in things if a[i] != b[i]]
    out(f"\n== {len(moved)} things changed project")
    for i in moved[:40]:
        out(f"  {(things[i].get('title') or i)[:50]:50} {a[i][:20]:>20} -> {b[i][:20]}")


if __name__ == "__main__":
    main()

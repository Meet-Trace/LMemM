#!/usr/bin/env python3
"""Are your remembered things one document / tab / thread / chat each? Read-only.

    python3 scripts/thing_granularity.py            # uses <data>/memory
    python3 scripts/thing_granularity.py --memory /path/to/memory

Per app: how many things, how many have no real name (a window number, a hash, the app's own name), and
the things that look like several pieces of work in one (used on many days, with many different headings
in their text). Prints titles and counts only, never the text itself.
"""
import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import real_days  # noqa: E402

GENERIC = re.compile(r"^(?:$|untitled|new tab|[\w ]+ [0-9a-f]{16,}$|(?:terminal|app_window|folder|code_file)-[0-9a-f]+$)", re.I)


def unnamed(item):
    title = (item.get("title") or "").strip()
    return bool(GENERIC.match(title)) or title.casefold() == (item.get("app") or "").casefold()


def days_used(visits):
    return len({v["from"][:10] for v in visits})


def report(memory_dir, out=print):
    items = real_days.read_items(memory_dir)
    visits = defaultdict(list)
    for v in real_days.read_visits(memory_dir):
        visits[v["item"]].append(v)
    by_app = defaultdict(list)
    for iid, item in items.items():
        by_app[(item.get("app") or "?", item.get("kind") or "?")].append(iid)
    out(f"{len(items)} things in {memory_dir}\n")
    out(f"{'app':24} {'kind':16} {'things':>6} {'unnamed':>8} {'many days':>9}")
    worst = []
    for (app, kind), ids in sorted(by_app.items(), key=lambda x: -len(x[1])):
        bad = [i for i in ids if unnamed(items[i])]
        long = [i for i in ids if days_used(visits[i]) >= 3]
        out(f"{app[:24]:24} {kind[:16]:16} {len(ids):>6} {len(bad):>8} {len(long):>9}")
        worst += [(days_used(visits[i]), app, items[i].get("title") or i) for i in long]
    total = len(items)
    named = sum(1 for i in items.values() if not unnamed(i))
    out(f"\n{named} of {total} things have a real name ({100 * named // max(1, total)}%).")
    if worst:
        out("Used on 3 or more days (may hold several pieces of work):")
        for d, app, title in sorted(worst, reverse=True)[:15]:
            out(f"  {d:>2} days  {app} · {title[:60]}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--memory", default=None, help="default: <data>/memory")
    args = p.parse_args(argv)
    report(args.memory or config.paths().memory_dir)


if __name__ == "__main__":
    main()

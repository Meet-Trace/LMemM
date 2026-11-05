"""LMemM - turn your own memory.json and session timelines into day files you can label and score.

Read-only: it never writes to memory.json or sessions/. The day files it writes hold your real screen
text, so they go under the data directory (git-ignored) and must never be committed or uploaded.

A day file has the same shape as tests/fixtures/project_grouping/sample_day.json, plus a
"label_status" per thing: "todo" until you label it in the labelling page, then "done". A day with
any "todo" left cannot be scored, so half-labelled days never produce misleading numbers.

Known limit: memory.json keeps one record per place with its latest text, not per-day text, so a place
used on several days shows the same text on each of them.
"""

import json
import os
from datetime import datetime

ABOUT = ("Real day exported from memory.json and sessions/. Contains your screen text: keep it on this Mac, "
         "never commit or upload it. label = project paths ('Project/Sub-project'); [] = belongs to nothing.")


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def read_items(memory_dir):
    """Every item in memory.json by id, read without touching anything."""
    doc = read_json(os.path.join(memory_dir, "memory.json"))
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("memory.json has no full item list ('items').")
    return {i["id"]: i for i in items if isinstance(i, dict) and i.get("id")}


def read_visits(memory_dir):
    """Every on-screen visit from every session file: [{item, from, to}], oldest first."""
    folder = os.path.join(memory_dir, "sessions")
    visits = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".json"):
            continue
        try:
            doc = read_json(os.path.join(folder, name))
        except (OSError, ValueError):
            continue
        for v in doc.get("timeline") or []:
            if isinstance(v, dict) and v.get("item") and v.get("from") and v.get("to"):
                visits.append({"item": v["item"], "from": v["from"], "to": v["to"]})
    return sorted(visits, key=lambda v: v["from"])


def by_day(visits):
    """{YYYY-MM-DD: [visits]} by the date each visit started."""
    out = {}
    for v in visits:
        out.setdefault(v["from"][:10], []).append(v)
    return out


def day_doc(day, visits, items, previous=None):
    """One day file. Labels already given in `previous` (an earlier export of the same day) are kept."""
    old = (previous or {}).get("items") or {}
    things = {}
    for iid in dict.fromkeys(v["item"] for v in visits):
        if iid not in items:
            continue
        item = {k: v for k, v in items[iid].items() if k not in {"screenshot", "project"}}
        before = old.get(iid) or {}
        item["label"] = before.get("label") if isinstance(before.get("label"), list) else []
        item["label_status"] = before.get("label_status") or "todo"
        things[iid] = item
    timeline = [v for v in visits if v["item"] in things]
    return {"version": 1, "day": day, "about": ABOUT, "exported": datetime.now().isoformat(timespec="seconds"),
            "items": things, "timeline": timeline}


def summary(memory_dir):
    """[(day, things, visits, minutes on screen)] for every day with sessions."""
    rows = []
    for day, visits in sorted(by_day(read_visits(memory_dir)).items()):
        minutes = sum(max(0.0, (datetime.fromisoformat(v["to"]) - datetime.fromisoformat(v["from"])).total_seconds())
                      for v in visits) / 60
        rows.append((day, len({v["item"] for v in visits}), len(visits), round(minutes)))
    return rows


def export(memory_dir, out_dir, first=None, last=None):
    """Write one day file per day in [first, last] into out_dir. Returns the paths written."""
    items = read_items(memory_dir)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for day, visits in sorted(by_day(read_visits(memory_dir)).items()):
        if (first and day < first) or (last and day > last):
            continue
        path = os.path.join(out_dir, f"{day}.json")
        previous = None
        if os.path.exists(path):
            try:
                previous = read_json(path)
            except (OSError, ValueError):
                raise ValueError(f"{path} exists but is not readable JSON; not overwriting your labels.")
        doc = day_doc(day, visits, items, previous)
        if not doc["items"]:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        written.append(path)
    return written

"""LMemM - the vision-model Moments of Trace V2, read as Work History events. Read only, no UI, no model, no network.

Trace V2 (trace_v2/, experiments/moment_goals/) keeps its Moments in a SQLite database and writes what a vision model
said about each stable Moment to JSON files beside it. This reads both and hands history_model plain events:

    description   what happened on screen ("User scrolls through terminal output...")  -> the line under the title
    purpose       why you were doing it ("Explore Git repository structure...")        -> the title
    activity      Moments the pair check judged to be the same goal share an index; history shows them once

    V1  Only Moments that still exist count. The JSON outlives a forget, so a Moment missing from the database, or
        merged into another, is not shown, and neither is its text. Nothing here writes to either store.
    V2  The database is opened read only. A missing, locked or older database is "no V2 data" (None), never an error.
    V3  Only described Moments appear. A Moment becomes stable after fifteen minutes, so the latest quarter hour has
        no entry yet. Nothing about it is made up.
    V4  The app is the one most of the Moment's observations came from (its display name, not the bundle id).
"""

import json
import os
import sqlite3
from datetime import datetime

MODEL = "lfm3-vl"                       # the vision model whose descriptions and purposes are read (a prefix match)
_CACHE = {}


def goals_dir():
    return os.path.expanduser(os.environ.get("LMEMM_V2_GOALS") or "~/trace_prompts")


def data_dir():
    return os.path.expanduser(os.environ.get("TRACE_V2_DATA_DIR") or "~/Library/Application Support/Trace/V2")


def _json(path):
    try:
        stat = os.stat(path)
        key = (stat.st_mtime_ns, stat.st_size)
        hit = _CACHE.get(path)
        if hit and hit[0] == key:
            return hit[1]
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    _CACHE[path] = (key, doc)
    return doc


def _model(models, purpose):
    for name, info in (models or {}).items():
        if name.startswith(MODEL) and name.endswith("(purpose)") == purpose and isinstance(info, dict):
            return info
    return {}


def _goal_by_moment(info):
    return {g.get("moment_id"): " ".join(str(g.get("goal") or "").split()) for g in info.get("goals") or () if isinstance(g, dict)}


def _alive(conn, ids):
    """{moment id: (app display name, state)} for the Moments that still exist and are not merged into another."""
    out = {}
    for mid in ids:
        row = conn.execute("SELECT state FROM moment_revisions WHERE moment_id=? ORDER BY revision DESC LIMIT 1", (mid,)).fetchone()
        if not row or row[0] in ("merged", "insufficient"):
            continue
        app = conn.execute("SELECT o.app_name FROM moment_member_index i JOIN observations o USING (observation_id) "
                           "WHERE i.moment_id=? GROUP BY o.app_name ORDER BY count(*) DESC LIMIT 1", (mid,)).fetchone()
        out[mid] = (app[0] if app and app[0] else "", row[0])
    return out


def read(goals=None, data=None):
    """Events for history_model, oldest first, or None when there is no V2 data to show (V2)."""
    goals, data = goals or goals_dir(), data or data_dir()
    path, db = os.path.join(goals, "moment_goals.json"), os.path.join(data, "observations.sqlite3")
    key = (path, db, _stamp(path), _stamp(db), _stamp(db + "-wal"))
    hit = _CACHE.get("read")
    if hit and hit[0] == key:
        return hit[1]                                           # the window asks on every refresh; the files change rarely
    out = _read(path, db)
    _CACHE["read"] = (key, out)
    return out


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read(path, db):
    doc = _json(path)
    if not isinstance(doc, dict) or not os.path.exists(db):
        return None
    what = _goal_by_moment(_model(doc.get("models"), False))
    why = _goal_by_moment(_model(doc.get("models"), True))
    moments = [m for m in doc.get("moments") or () if isinstance(m, dict) and m.get("id") in what]
    if not moments:
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            alive = _alive(conn, [m["id"] for m in moments])
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    index = {m["id"]: i for i, m in enumerate(doc["moments"])}
    activity = {}
    for n, group in enumerate(g for g in _model(doc.get("models"), True).get("groups") or () if isinstance(g, list) and len(g) > 1):
        for i in group:
            activity[i] = n
    out = []
    for m in moments:
        mid = m["id"]
        if mid not in alive:
            continue
        try:
            start, end = int(m["start_ns"]) / 1e9, int(m["end_ns"]) / 1e9
        except (KeyError, TypeError, ValueError):
            continue
        purpose = why.get(mid) or ""
        if not (purpose or what[mid]):
            continue                                              # the model wrote nothing usable: no entry, nothing made up
        out.append({"v2": {"id": mid, "title": purpose or what[mid], "line": what[mid] if purpose else "",
                           "app": alive[mid][0], "activity": activity.get(index.get(mid))},
                    "item": mid, "start": datetime.fromtimestamp(start), "end": datetime.fromtimestamp(end),
                    "seconds": max(0, int(end - start))})
    out.sort(key=lambda e: e["start"])
    return out or None

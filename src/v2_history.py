"""LMemM - the vision-model Moments of Trace V2, read as Work History events. Read only, no UI, no model, no network.

Trace V2 (trace_v2/) keeps evidence in observations.sqlite3 and everything derived from it in derived.sqlite3:
Moments, the vision description of each stable Moment, its purpose and the Activity it belongs to. This reads both
(derived attached read only) and hands history_model plain events:

    description   what happened on screen ("User scrolls through terminal output...")  -> the line under the title
    purpose       why you were doing it ("Explore Git repository structure...")        -> the title
    activity      Moments that belong to one Activity (two or more) share an index; history shows them once

    V1  Only Moments that still exist count. A forgotten Moment is deleted from the derived file with its description
        and purpose; a Moment merged into another is not shown. Nothing here writes to either file.
    V2  Both files are opened read only. A missing, locked or older database is "no V2 data" (None), never an error.
    V3  Every current Moment appears, stable or still growing. Until the vision model has described it, its title is the
        window title it was seen in most (or the app) and there is no line under it; nothing about it is made up.
    V4  The app is the one most of the Moment's observations came from (its display name, not the bundle id).
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

_CACHE = {}
_APPS = {}
_LOCK = threading.Lock()

_MOMENTS = """
SELECT r.moment_id, r.start_ns, r.end_ns,
       (SELECT d.description FROM moment_descriptions d WHERE d.moment_id = r.moment_id AND d.status = 'ok'
         AND d.description IS NOT NULL AND d.description != '' ORDER BY d.recorded_at DESC LIMIT 1),
       (SELECT i.purpose FROM activity_interpretations i WHERE i.moment_id = r.moment_id AND i.status = 'clear'
         ORDER BY i.recorded_at DESC LIMIT 1),
       r.state
FROM moment_revisions r
WHERE r.state IN ('stable', 'provisional') AND r.revision = (SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id = r.moment_id)
ORDER BY r.start_ns, r.moment_id"""

_ACTIVITIES = """
SELECT m.moment_id, m.activity_id FROM activity_member_index m
JOIN activity_revisions r ON r.activity_id = m.activity_id
WHERE r.revision = (SELECT max(x.revision) FROM activity_revisions x WHERE x.activity_id = r.activity_id)
  AND r.state != 'merged' AND r.moment_count > 1"""


def data_dir():
    return os.path.expanduser(os.environ.get("TRACE_V2_DATA_DIR") or "~/Library/Application Support/Trace/V2")


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def read(data=None, wait=False):
    """Events for history_model, oldest first, or None when there is no V2 data to show (V2).

    The window asks on every refresh and the read can take seconds on a big store, so it never waits for one: a changed
    store starts a read on a background thread and the last finished answer (None the first time) is returned until
    it is done. `wait=True` runs the read inline (tests)."""
    data = data or data_dir()
    db, derived = os.path.join(data, "observations.sqlite3"), os.path.join(data, "derived.sqlite3")
    key = (db, derived, _stamp(derived), _stamp(derived + "-wal"))     # not the evidence file: capture writes it every few seconds
    with _LOCK:
        hit = _CACHE.get("read")
        if hit and hit[0] == key:
            return hit[1]
        if wait:
            pass
        elif _CACHE.get("busy"):
            return hit[1] if hit and hit[2] == (db, derived) else None
        else:
            _CACHE["busy"] = True
            threading.Thread(target=_refresh, args=(key, db, derived), name="v2-history", daemon=True).start()
            return hit[1] if hit and hit[2] == (db, derived) else None
    _refresh(key, db, derived)
    return _CACHE["read"][1]


def _refresh(key, db, derived):
    try:
        out = _read(db, derived)
    except Exception:                                           # noqa: BLE001 - history must never break the window
        out = None
    with _LOCK:
        _CACHE["read"] = (key, out, (db, derived))
        _CACHE["busy"] = False
        _CACHE["version"] = _CACHE.get("version", 0) + 1


def paused(data=None):
    """Why V2's descriptions wait right now ("battery at 17%"), from its runner_stats.json, or None."""
    try:
        with open(os.path.join(data or data_dir(), "runner_stats.json")) as fh:
            stats = json.load(fh)
        return None if stats.get("final") else (stats.get("descriptions") or {}).get("paused")
    except (OSError, ValueError, AttributeError):
        return None


def version():
    """Counts finished background reads, so a window that got None the first time can tell when to ask again."""
    return _CACHE.get("version", 0)


def _read(db, derived):
    if not (os.path.exists(db) and os.path.exists(derived)):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.3)
        try:
            conn.execute("ATTACH DATABASE ? AS derived", (f"file:{derived}?mode=ro",))
            rows = conn.execute(_MOMENTS).fetchall()
            members = dict(conn.execute(_ACTIVITIES).fetchall())
            apps = _APPS.setdefault(db, {})
            for gone in set(apps) - {r[0] for r in rows}:          # a forgotten or merged Moment's name is not kept
                del apps[gone]
            for mid, _s, _e, _what, _why, state in rows:
                if mid in apps and apps[mid][2] == "stable":
                    continue                                       # a stable Moment's members do not change
                top = conn.execute("SELECT o.app_name, o.window_title FROM moment_member_index i JOIN observations o "
                                   "USING (observation_id) WHERE i.moment_id=? GROUP BY o.app_name, o.window_title "
                                   "ORDER BY count(*) DESC LIMIT 1", (mid,)).fetchone()
                apps[mid] = ((top[0] or "") if top else "", (top[1] or "") if top else "", state)
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    number = {}
    out = []
    for mid, start_ns, end_ns, what, why, _state in rows:
        what, why = " ".join((what or "").split()), " ".join((why or "").split())
        app, window = apps[mid][0], " ".join(apps[mid][1].split())
        if not (what or why or window or app):
            continue                                              # nothing to call it by: no entry, nothing made up
        start, end = start_ns / 1e9, end_ns / 1e9
        group = members.get(mid)
        out.append({"v2": {"id": mid, "title": why or what or window or app, "line": what if why else "", "app": app,
                           "activity": None if group is None else number.setdefault(group, len(number))},
                    "item": mid, "start": datetime.fromtimestamp(start), "end": datetime.fromtimestamp(end),
                    "seconds": max(0, int(end - start))})
    return out or None

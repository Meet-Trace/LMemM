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
    V3  Only described Moments appear. A Moment is stable after a quiet gap or a long run, so the latest minutes have
        no entry yet. Nothing about it is made up.
    V4  The app is the one most of the Moment's observations came from (its display name, not the bundle id).
"""

import os
import sqlite3
from datetime import datetime

_CACHE = {}

_MOMENTS = """
SELECT r.moment_id, r.start_ns, r.end_ns,
       (SELECT d.description FROM moment_descriptions d WHERE d.moment_id = r.moment_id AND d.status = 'ok'
         AND d.description IS NOT NULL AND d.description != '' ORDER BY d.recorded_at DESC LIMIT 1),
       (SELECT i.purpose FROM activity_interpretations i WHERE i.moment_id = r.moment_id AND i.status = 'clear'
         ORDER BY i.recorded_at DESC LIMIT 1)
FROM moment_revisions r
WHERE r.state = 'stable' AND r.revision = (SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id = r.moment_id)
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


def read(data=None):
    """Events for history_model, oldest first, or None when there is no V2 data to show (V2)."""
    data = data or data_dir()
    db, derived = os.path.join(data, "observations.sqlite3"), os.path.join(data, "derived.sqlite3")
    key = (db, derived, _stamp(db), _stamp(db + "-wal"), _stamp(derived), _stamp(derived + "-wal"))
    hit = _CACHE.get("read")
    if hit and hit[0] == key:
        return hit[1]                                           # the window asks on every refresh; the files change rarely
    out = _read(db, derived)
    _CACHE["read"] = (key, out)
    return out


def _read(db, derived):
    if not (os.path.exists(db) and os.path.exists(derived)):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            conn.execute("ATTACH DATABASE ? AS derived", (f"file:{derived}?mode=ro",))
            rows = conn.execute(_MOMENTS).fetchall()
            members = dict(conn.execute(_ACTIVITIES).fetchall())
            apps = {}
            for mid, *_ in rows:
                app = conn.execute("SELECT o.app_name FROM moment_member_index i JOIN observations o USING (observation_id) "
                                   "WHERE i.moment_id=? GROUP BY o.app_name ORDER BY count(*) DESC LIMIT 1", (mid,)).fetchone()
                apps[mid] = app[0] if app and app[0] else ""
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    number = {}
    out = []
    for mid, start_ns, end_ns, what, why in rows:
        what, why = " ".join((what or "").split()), " ".join((why or "").split())
        if not (what or why):
            continue                                              # nothing usable was written: no entry, nothing made up
        start, end = start_ns / 1e9, end_ns / 1e9
        group = members.get(mid)
        out.append({"v2": {"id": mid, "title": why or what, "line": what if why else "", "app": apps[mid],
                           "activity": None if group is None else number.setdefault(group, len(number))},
                    "item": mid, "start": datetime.fromtimestamp(start), "end": datetime.fromtimestamp(end),
                    "seconds": max(0, int(end - start))})
    return out or None

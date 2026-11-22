"""LMemM - Work History, as data. Plain functions, no UI, all tested.

The page answers: "what did I work on, and when?". It is a read only view over what LMemM already keeps: the
session files (long lived, one per run, a timeline of stretches that point at things) joined to the live things.
Nothing here writes or caches memory of its own. Recent time comes from Moments (H11); it never reads the event trail.
window.py draws it and forwards presses to page_model.press(); it decides nothing.

    H1  Today is the default. Yesterday, This week (the last 7 days) and Earlier (older days that have anything)
        are one press away, and so is any single day (previous, next, or a jump to a day that has history).
    H2  A day comes from the session id (YYYYMMDD-HHMMSS). Events only store a clock time, so a clock that goes
        backwards means midnight passed and the next date applies.
    H3  Only the session files that can hold the day are read: those that start on it, and the one before (it may
        run past midnight). A file is parsed again only when it changes. A file that cannot be read is skipped.
    H4  Two stretches in a row on the same thing are one entry (first start to last end, seconds added). Different
        things are never merged: when nothing says they belong together, the individual things show.
    H5  An entry says what the live thing says now: its title, app, project and the line it already stores. An
        event's own `doing` and `app` are never shown, because the session file outlives a Forget and still
        holds them. A forgotten thing has no entry. Nothing about it is made up.
    H6  A duration shows only from 60 seconds up. A description shows only when the thing stores one.
    H7  A thing from an app on the Never remember list is hidden, in every mode and in search.
    H8  Timeline lists days newest first, each day earliest first, so a day reads top to bottom. Projects and Apps
        regroup the same entries (sections by most recent entry) and keep that order inside each section.
    H9  Earlier pages itself seven days at a time, never reading further than it shows. Search pages by fifty.
    H10 Search looks at thing titles, project names and old names, notes (open and done) and kept excerpt text,
        case and accents ignored, across all of history. No model, no network, no index of its own.
    H11 When Moments are on (the tracker passes a list, even an empty one) they are the only record of the time, and
        only the last 48 hours (config.MOMENT_RETENTION_HOURS) show: one entry per visit, never one per tab switch.
        A visit's moments are one stretch, and a visit with under MIN_VISIT_S of dwell is a pass-through and is
        dropped. Session files are not read at all, and Earlier is not offered (it would always be empty). Only when
        Moments are off (None) does the page fall back to the session files, as before.
"""

import os
import re
from datetime import date, datetime, timedelta

import config
import favicons
import page_model as pm
import projects
import store

SCOPES = (("today", "Today"), ("yesterday", "Yesterday"), ("week", "This week"), ("earlier", "Earlier"))
MODES = (("timeline", "Timeline"), ("projects", "Projects"), ("apps", "Apps"))
JUMP_DAYS = 60
WEEK_DAYS = 7
MIN_SECONDS = 60
MIN_VISIT_S = 30                    # a visit shorter than this is a pass-through (a tab clicked past), not work
EMPTY = {"title": "Nothing remembered yet", "line": "Your work will appear here as you go."}
NO_DAY = {"title": "Nothing remembered for this day", "line": "Pick another day, or look at This week."}
NONE_FOUND = {"title": "Nothing found", "line": "Try part of a name, a project, a note or some text you read."}

_SESSION = re.compile(r"^(\d{8})-(\d{6})\.json$")
_CACHE = {}
_CACHE_MAX = 256


# ---------------------------------------------------------------- days

def day_of(iso):
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def label(day, today):
    gap = (today - day).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Yesterday"
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}" + ("" if day.year == today.year else f" {day.year}")


def selected(hist, today):
    """The single day the page is on (or the newest day of a range)."""
    if hist["scope"] == "yesterday":
        return today - timedelta(days=1)
    if hist["scope"] == "day":
        return day_of(hist["day"]) or today
    return today


def _names(sessions_dir):
    """[(start datetime, file name)] sorted oldest first, from the file names alone."""
    out = []
    if not sessions_dir:
        return out
    try:
        entries = os.listdir(sessions_dir)
    except (OSError, TypeError):
        return out
    for name in entries:
        m = _SESSION.match(name)
        if m:
            try:
                out.append((datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"), name))
            except ValueError:
                pass
    out.sort()
    return out


def days(sessions_dir, live=None, moments=()):
    """The days that have a session or a moment starting on them, newest first, without opening a session file."""
    found = {start.date() for start, _name in _names(sessions_dir)} | {e["start"].date() for e in moments}
    started = _start_of(live[0]) if live else None
    if started:
        found.add(started.date())
    return sorted(found, reverse=True)


def _start_of(session_id):
    try:
        return datetime.strptime(str(session_id), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


# ---------------------------------------------------------------- sessions

def _clock(text):
    try:
        return datetime.strptime(text, "%H:%M:%S").time()
    except (TypeError, ValueError):
        return None


def _events(start, timeline):
    """The stretches of one session with real dates. Only the fields the page needs are kept: the event's own `app`
    and `doing` are dropped here (H5)."""
    out, last = [], start
    for e in timeline if isinstance(timeline, list) else ():
        if not isinstance(e, dict) or not isinstance(e.get("item"), str):
            continue
        a, b = _clock(e.get("from")), _clock(e.get("to"))
        if a is None:
            continue
        begin = datetime.combine(last.date(), a)
        if begin < last:
            begin += timedelta(days=1)
        end = datetime.combine(begin.date(), b if b is not None else a)
        if end < begin:
            end += timedelta(days=1)
        try:
            seconds = max(0, int(e.get("seconds") or 0))
        except (TypeError, ValueError):
            seconds = 0
        out.append({"item": e["item"], "start": begin, "end": end, "seconds": seconds})
        last = end
    return out


def moment_events(records):
    """Moment records (dicts, as MomentStore.read gives them) -> one stretch per visit, oldest first (H11). A visit
    with under MIN_VISIT_S of dwell is dropped. Records that cannot be read are skipped."""
    visits = {}
    for r in records or ():
        try:
            item, vid = r["item_id"], r["visit_id"]
            a, b = float(r["t_start"]), float(r["t_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(item, str) or not isinstance(vid, str):
            continue
        v = visits.setdefault(vid, {"item": item, "a": a, "b": b, "dwell": 0.0})
        v["a"], v["b"] = min(v["a"], a), max(v["b"], b)
        v["dwell"] += max(0.0, b - a)
    out = [{"item": v["item"], "start": datetime.fromtimestamp(v["a"]), "end": datetime.fromtimestamp(v["b"]),
            "seconds": int(v["dwell"])} for v in visits.values() if v["dwell"] >= MIN_VISIT_S]
    out.sort(key=lambda e: e["start"])
    return out


def _load(path):
    """One session file's stretches, parsed again only when the file changed (H3). Never raises."""
    import json
    try:
        stat = os.stat(path)
        key = (stat.st_mtime_ns, stat.st_size)
        hit = _CACHE.get(path)
        if hit and hit[0] == key:
            return hit[1]
        with open(path) as fh:
            doc = json.load(fh)
        start = _start_of(os.path.basename(path)[:-5])
        parsed = _events(start, doc.get("timeline")) if start and isinstance(doc, dict) else []
    except (OSError, ValueError, AttributeError):
        return []
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[path] = (key, parsed)
    return parsed


def day_events(sessions_dir, day, live=None, names=None, moments=()):
    """The stretches that began on `day`, oldest first. `live` is (session id, events) of the running session: it is
    not on disk until saved, so it is read from memory and its file, if any, is ignored. `moments` (moment_events) replace
    every session stretch that starts at or after the first of them (H11)."""
    names = _names(sessions_dir) if names is None else names
    live_id = live[0] if live else None
    before = [n for s, n in names if s.date() < day and n[:-5] != live_id]
    names = [n for s, n in names if s.date() == day and n[:-5] != live_id]
    files = before[-1:] + names
    out = []
    for name in files:
        out += [e for e in _load(os.path.join(sessions_dir, name)) if e["start"].date() == day]
    started = _start_of(live_id) if live else None
    if started:
        out += [e for e in _events(started, [dict(x) for x in live[1]]) if e["start"].date() == day]
    if moments:
        out = [e for e in out if e["start"] < moments[0]["start"]]
        out += [e for e in moments if e["start"].date() == day]
    out.sort(key=lambda e: e["start"])
    return out


# ---------------------------------------------------------------- rows

def hidden_apps(skip):
    """The apps the person never wants remembered, folded, by name and by id."""
    out = set()
    for entry in skip or ():
        for key in ("id", "name") if isinstance(entry, dict) else ():
            if isinstance(entry.get(key), str):
                out.add(pm.fold(entry[key]))
        if isinstance(entry, str):
            out.add(pm.fold(entry))
    return out


def _visible(item, hide):
    return isinstance(item, dict) and pm.fold(item.get("app") or "") not in hide


def _describe(item):
    written = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    line = " ".join(str(written.get("summary") or item.get("doing") or "").split())
    return line if line and pm.fold(line) != pm.fold(pm.title_of(item)) else None


def _row(reg, item, key):
    main = projects._main(item)
    live = bool(main) and pm._live(reg, main)
    return {"key": key, "id": item["id"], "title": pm.title_of(item), "app": item.get("app", ""),
            "kind": item.get("kind", ""), "url": None if isinstance(item.get("content"), str) else favicons.page_url(item), "pid": main if live else None,
            "project": " › ".join(projects.path_names(reg, main)) if live else "", "hue": pm.hue(main) if live else None,
            "doing": _describe(item)}


def merged(events):
    """Adjacent stretches on one thing become one (H4)."""
    out = []
    for e in events:
        if out and out[-1]["item"] == e["item"]:
            out[-1] = {**out[-1], "end": max(out[-1]["end"], e["end"]), "seconds": out[-1]["seconds"] + e["seconds"]}
        else:
            out.append(dict(e))
    return out


def entries(reg, items, events, hide=frozenset()):
    """Rows for one day's stretches, earliest first."""
    rows = []
    for e in merged(events):
        item = items.get(e["item"])
        if not _visible(item, hide):
            continue
        row = _row(reg, item, f"{e['item']}@{e['start']:%Y%m%d%H%M%S}")
        span = f"{e['start']:%H:%M}" + (f"–{e['end']:%H:%M}" if f"{e['end']:%H:%M}" != f"{e['start']:%H:%M}" else "")
        row.update(day=e["start"].date().isoformat(), at=e["start"].isoformat(timespec="seconds"), span=span,
                   seconds=e["seconds"], duration=spent(e["seconds"]) if e["seconds"] >= MIN_SECONDS else None)
        rows.append(row)
    return rows


def spent(seconds):
    return f"{seconds // 60}m" if seconds < 3600 else store.duration(seconds)


def _summary(rows):
    total = sum(r["seconds"] for r in rows)
    things = len({r["id"] for r in rows})
    apps = len({r["app"] for r in rows if r["app"]})
    bits = ([spent(total)] if total >= MIN_SECONDS else []) + [pm.plural(things, "thing")]
    return " · ".join(bits + ([pm.plural(apps, "app")] if apps else []))


# ---------------------------------------------------------------- the page

def _range(hist, today, sessions_dir, live, names, reg, items, hide, moments):
    """[(day, rows)] for the scope, newest day first, and whether Earlier has more to show (H1, H9)."""
    scope = hist["scope"]
    if scope == "week":
        want = [today - timedelta(days=k) for k in range(WEEK_DAYS)]
        return [(d, entries(reg, items, day_events(sessions_dir, d, live, names, moments), hide)) for d in want], False
    if scope == "earlier":
        edge = today - timedelta(days=WEEK_DAYS - 1)
        older = [d for d in days(sessions_dir, live, moments) if d < edge]
        got, more = [], False
        for d in older:
            rows = entries(reg, items, day_events(sessions_dir, d, live, names, moments), hide)
            if not rows:
                continue
            if len(got) >= hist["n"]:
                more = True
                break
            got.append((d, rows))
        return got, more
    d = selected(hist, today)
    return [(d, entries(reg, items, day_events(sessions_dir, d, live, names, moments), hide))], False


def _stamped(rows, today):
    for r in rows:
        r["day_label"] = label(day_of(r["day"]), today)
    return rows


def _groups(rows, mode, today):
    """Timeline: a section per day. Projects and Apps: a section per project or app. Newest day first, earliest first
    inside a day, in every mode (H8)."""
    ordered = sorted(rows, key=lambda r: (r["day"], r["at"]))
    ordered.sort(key=lambda r: r["day"], reverse=True)
    if mode == "timeline":
        out = {}
        for r in ordered:
            out.setdefault(r["day"], []).append(r)
        return [{"title": label(day_of(d), today), "rows": _stamped(rs, today)} for d, rs in out.items()]
    out = {}
    for r in ordered:
        key = (r["pid"] or "") if mode == "projects" else r["app"]
        out.setdefault(key, []).append(r)
    sections = []
    for key, rs in out.items():
        if mode == "projects":
            title = rs[0]["project"] or "No project"
            hue = rs[0]["hue"]
        else:
            title, hue = key or "No app", None
        sections.append({"title": title, "hue": hue, "rows": _stamped(rs, today),
                         "recent": max(r["at"] for r in rs), "none": not key})
    sections.sort(key=lambda s: s["recent"], reverse=True)
    sections.sort(key=lambda s: s["none"])
    return [{k: v for k, v in s.items() if k not in ("recent", "none")} for s in sections]


def _jump(sessions_dir, live, today, moments=()):
    return [{"day": d.isoformat(), "title": label(d, today)} for d in days(sessions_dir, live, moments)[:JUMP_DAYS]]


def view(reg, items, state, now, sessions_dir=None, live=None, skip=(), moments=None):
    """The history page for state["hist"]. A failure to read never raises (a file that cannot be read is skipped)."""
    hist = state["hist"]
    today = now.date()
    hide = hidden_apps(skip)
    feed = moments is not None
    if feed:
        cutoff = now - timedelta(hours=config.MOMENT_RETENTION_HOURS)
        moments = [e for e in moment_events(moments) if e["end"] >= cutoff]
        sessions_dir, live = None, None
    else:
        moments = []
    names = _names(sessions_dir) if sessions_dir else []
    page = {"kind": "history", "title": "Work History", "back": True, "mode": hist["mode"], "scope": hist["scope"],
            "q": hist["q"], "scopes": [{"id": k, "title": t, "on": k == hist["scope"]} for k, t in SCOPES if not (feed and k == "earlier")],
            "modes": [{"id": k, "title": t, "on": k == hist["mode"]} for k, t in MODES],
            "day": selected(hist, today).isoformat(), "jump": _jump(sessions_dir, live, today, moments) if sessions_dir or feed else [],
            "can_next": hist["scope"] in ("yesterday", "day"), "can_prev": True}
    if hist["scope"] in ("week", "earlier"):
        page["can_next"] = page["can_prev"] = False
    page["heading"] = {"week": "This week", "earlier": "Earlier"}.get(hist["scope"]) or label(selected(hist, today), today)
    if hist["q"]:
        return {**page, **_search(reg, items, hist, hide, now)}
    if not items or not (names or moments or live and live[1]):
        return {**page, "groups": [], "more": False, "summary": "", "empty": EMPTY}
    ranged, more = _range(hist, today, sessions_dir, live, names, reg, items, hide, moments)
    rows = [r for _d, rs in ranged for r in rs]
    return {**page, "groups": _groups(rows, hist["mode"], today), "more": more,
            "summary": _summary(rows), "empty": None if rows else (NO_DAY if hist["scope"] in ("today", "yesterday", "day") else EMPTY)}


# ---------------------------------------------------------------- search

def _excerpts(item):
    content = item.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, dict):
        found = False
        for e in content.get("excerpts") or ():
            if isinstance(e, dict) and isinstance(e.get("text"), str):
                found = True
                yield e["text"]
        if not found and isinstance(content.get("text"), str):
            yield content["text"]


def _project_text(reg, item):
    out = []
    for p in projects._members_of(item):
        if pm._live(reg, p):
            row = projects.get(reg, p)
            out += projects.path_names(reg, p) + list(row.get("aliases") or [])
    return out


def matches(reg, item, want):
    """Why a thing matches `want` (already folded): its title, a project name, a note, or kept text. None if it does not."""
    if want in pm.fold(pm.title_of(item)):
        return "title"
    if any(want in pm.fold(t) for t in _project_text(reg, item)):
        return "project"
    for n in item.get("notes") or ():
        if isinstance(n, dict) and isinstance(n.get("text"), str) and want in pm.fold(n["text"]):
            return "note"
    if any(want in pm.fold(t) for t in _excerpts(item)):
        return "text"
    return None


def _search(reg, items, hist, hide, now):
    want = pm.fold(hist["q"])
    found = []
    for item in pm._by_recent(i for i in items.values() if _visible(i, hide)):
        if matches(reg, item, want):
            found.append(item)
    rows = []
    for item in found[:hist["lim"]]:
        row = _row(reg, item, "s" + item["id"])
        row.update(when=pm.stamp(item.get("last_seen"), now) or pm.ago(item.get("last_seen"), now), span="", duration=None)
        rows.append(row)
    return {"groups": [{"title": pm.plural(len(found), "result"), "rows": rows}] if rows else [],
            "more": max(0, len(found) - hist["lim"]), "summary": "", "empty": None if rows else NONE_FOUND, "search": True}

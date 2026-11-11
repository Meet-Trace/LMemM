"""LMemM - a clean context export: what an AI should be handed, nothing operational.

`memory.json` and the session timelines are LMemM's own working data: every visit,
trigger, timestamp and activity second, kept because the tracker needs them to decide
what's the same thing and when to remind you. Most of that is noise to anything reading
for context. This module distills it down to what you were actually doing and why:

    {"project": "Q3 plan", "things": [
        {"app": "Google Docs", "what": "Q3 plan", "doing": "Working on \"Q3 plan\"",
         "when": "6 Oct, 23:49–23:51 (7 visits)",
         "notes": [{"text": "add a pricing table", "status": "open"}],
         "content": ["Pricing section goes here, three tiers ..."]}
    ]}

Dropped entirely: screenshots, item/ref ids, triggers, per-visit timeline entries,
activity-category seconds, resurfacing records. A thing with neither a note nor any
kept content is dropped too - there's nothing to tell an AI about it.
"""

import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import config
import labels as labels_mod
from memory_content import CHROME_LINE   # menu bars and other UI chrome that slips past the content filter
import notes as notes_mod
import store


def _clean(text):
    """Drop UI-chrome lines from one excerpt; return None if nothing real is left."""
    lines = [l.strip() for l in text.split("\n")]
    kept = [l for l in lines if l and not CHROME_LINE.match(l)]
    return " ".join(kept) if kept else None


def _content(item):
    """Deduplicated, chrome-stripped excerpt text, oldest first, newest kept content last."""
    out, seen = [], set()
    for excerpt in item.get("content", {}).get("excerpts", []):
        cleaned = _clean(excerpt["text"])
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _when(item):
    first, last = item.get("first_seen"), item.get("last_seen")
    try:
        f, l = datetime.fromisoformat(first), datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return None
    same_day = f.date() == l.date()
    span = f.strftime("%-d %b, %H:%M") + "–" + (l.strftime("%H:%M") if same_day else l.strftime("%-d %b, %H:%M"))
    visits = item.get("visits", 1)
    return span + (f" ({visits} visits)" if visits > 1 else "")


def distill(item, label=None):
    """One thing, stripped to what an AI would want to know about it. None if there's
    nothing worth telling - no note and no kept content. `label` is its labels.py entry, if any."""
    note_rows = [{"text": n["text"], "status": "done" if notes_mod.is_done(item, n) else "open"}
                 for n in item.get("notes", [])]
    body = _content(item)
    if not note_rows and not body:
        return None
    row = {"app": item["app"], "what": item.get("title") or item["doing"], "doing": item["doing"]}
    when = _when(item)
    if when:
        row["when"] = when
    if note_rows:
        row["notes"] = note_rows
    if body:
        row["content"] = body
    latest = {k: v for k, v in (item.get("state") or {}).items() if v and v != row["what"]}
    if latest:
        row["details"] = latest
    if label and label.get("summary"):
        row["summary"] = label["summary"]                     # written by a model: marked as such
        row["summary_by"] = label.get("source", "model")
        if label.get("entities"):
            row["entities"] = label["entities"]
        if label.get("open_question"):
            row["open_question"] = label["open_question"]
    return row


def by_project(items):
    """Every distillable thing, grouped by project, most recently touched project first."""
    groups = OrderedDict()
    labelled = labels_mod.load_labels()
    for item in sorted(items.values(), key=lambda i: i["last_seen"], reverse=True):
        row = distill(item, labelled.get(item["id"]))
        if row is None:
            continue
        groups.setdefault(notes_mod.project_of(item), []).append(row)
    return [{"project": name, "things": rows} for name, rows in groups.items()]


def _items_in_session(doc):
    return {e["item"] for e in doc.get("timeline", [])}


def for_session(items, session_doc):
    """Context for one session: only the things that session actually touched."""
    touched = _items_in_session(session_doc)
    return by_project({iid: item for iid, item in items.items() if iid in touched})


def since(items, days):
    cutoff = datetime.now().timestamp() - days * 86400
    recent = {iid: item for iid, item in items.items()
             if _parses(item.get("last_seen")) and _parses(item["last_seen"]).timestamp() >= cutoff}
    return by_project(recent)


def _parses(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def build(items, session=None, days=None):
    """The export document. `session` picks one session's things; `days` picks a window
    across all sessions; neither picks everything distillable."""
    if session is not None:
        projects = for_session(items, session["doc"])
        scope = {"session": session["doc"]["session"]}
    elif days is not None:
        projects = since(items, days)
        scope = {"days": days}
    else:
        projects = by_project(items)
        scope = {"days": None}
    things = sum(len(p["things"]) for p in projects)
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), **scope,
            "projects": projects, "things": things, "notes_open": sum(
                1 for p in projects for t in p["things"] for n in t.get("notes", []) if n["status"] == "open")}


def latest_session_doc():
    sessions = sorted(Path(config.paths().sessions_dir).glob("*.json"))
    return json.loads(sessions[-1].read_text()) if sessions else None


def find_session_doc(session_id):
    path = Path(config.paths().sessions_dir) / (session_id + ".json")
    return json.loads(path.read_text()) if path.exists() else None


def export(session_id=None, days=None):
    """What `lmemm.py context` builds and writes: the document, and where it was saved.
    No session and no days: the latest session. `session_id`: that session. `days`: the
    window across every session, ignoring session boundaries."""
    items = store.load_items()
    if days is None:
        found = find_session_doc(session_id) if session_id else latest_session_doc()
        if found is None:
            raise ValueError(f"no session {session_id}" if session_id else "no session to build context for yet")
        doc = build(items, session={"doc": found})
    else:
        doc = build(items, days=days)
    name = doc.get("session") or f"last-{days}-days"
    path = Path(config.paths().memory_dir) / "context" / f"{name}.json"
    store.write_json(str(path), doc)
    return doc, path

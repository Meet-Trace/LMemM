"""LMemM - your notes (⌃⌥N) on the things you work on.

A note is written for whatever was in front when you pressed the hotkey. The
screenshot taken at that moment is resolved in the background, so a note may wait
("pending") until OCR says which memory item that screen is; it is never guessed
onto the previous item.

Every note is a pending edit until marked done; it resurfaces when you come back
to the thing it's on, and `lmemm.py notes` shows all open ones by project.
"""

import hashlib
from datetime import datetime

import favicons


def record(items, frame_item, session_notes, target, text, at):
    """Store a note. `target` is ("frame", capture ts) or ("item", item id).
    Returns the item it landed on, or None if it is still waiting for its frame."""
    kind, key = target
    item_id = frame_item.get(key) if kind == "frame" else key
    item = items.get(item_id)
    note = {"at": at, "text": text}
    if item:
        note["while"] = item["doing"]
        item.setdefault("notes", []).append(note)
        item["last_seen"] = max(item["last_seen"], at)
    status = ("attached" if item else
              "pending" if kind == "frame" and key not in frame_item else "unresolved")
    session_notes.append({"item": item["id"] if item else None, **note,
                          "frame": key if kind == "frame" else None, "status": status})
    return item


def attach_pending(items, frame_item, session_notes):
    """Attach notes whose frame has now been resolved to an item."""
    for note in session_notes:
        if note.get("status") != "pending":
            continue
        item = items.get(frame_item.get(note.get("frame")))
        if item is not None:
            item.setdefault("notes", []).append({"at": note["at"], "text": note["text"], "while": item["doing"]})
            item["last_seen"] = max(item["last_seen"], note["at"])
            note.update(item=item["id"], status="attached")


def fail_pending(session_notes, frame_ts):
    """The frame a note was waiting for could not be resolved."""
    for note in session_notes:
        if note.get("frame") == frame_ts and note.get("status") == "pending":
            note["status"] = "unresolved"


# ---------------------------------------------------------------- notes as pending edits
#
# Every note is a pending edit until you mark it done. Done-state lives in
# item["notes_done"] = {note id: when}, never inside the note itself: the provenance
# store matches notes by value, so a note must not change after it is written.

GROUPS = {"email_draft": "Email", "email": "Email", "mailbox": "Email", "email_search": "Email",
          "chat": "Chats", "chat_list": "Chats", "ai_conversation": "AI chats"}


def note_id(note):
    """Stable id derived from the note, so notes written before ids existed get one too."""
    return "n-" + hashlib.sha1(f'{note["at"]}|{note["text"]}'.encode()).hexdigest()[:6]


def is_done(item, note):
    return note_id(note) in item.get("notes_done", {})


def open_notes(item):
    return [n for n in item.get("notes", []) if not is_done(item, n)]


def project_of(item):
    """Where an item belongs in the project view. A project you (or an accepted
    suggestion) put it in wins; otherwise it falls back to a guess from the app."""
    if item.get("project"):
        return item["project"]
    state = item.get("state") or {}
    if item.get("kind") == "code_file" and state.get("project"):
        return state["project"]
    if item.get("kind") == "document":
        return item.get("title") or state.get("document") or item["app"]
    return GROUPS.get(item.get("kind"), item["app"])


def set_done(items, ids, done=True, at=None):
    """Mark notes (by id) done or open again. Returns the ids that were found."""
    at = at or datetime.now().isoformat(timespec="seconds")
    wanted, found = set(ids), []
    for item in items.values():
        for n in item.get("notes", []):
            nid = note_id(n)
            if nid not in wanted:
                continue
            found.append(nid)
            marks = item.setdefault("notes_done", {})
            if done:
                marks.setdefault(nid, at)
            else:
                marks.pop(nid, None)
            if not marks:
                item.pop("notes_done")
    return found


def due_for_resurfacing(item, now_iso, cooldown):
    """Open notes to remind you of when you come back to this item, or []. A note you
    added since the last reminder always shows next time; otherwise at most once per
    `cooldown` seconds."""
    pending = open_notes(item)
    last = item.get("resurfaced_at")
    if not pending:
        return []
    if last and _seconds_between(last, now_iso) < cooldown and not any(n["at"] > last for n in pending):
        return []
    return pending


def _seconds_between(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def pending_view(items, include_done=False):
    """Open notes grouped project -> item, most recent first. Also written to pending.json."""
    projects = {}
    for item in items.values():
        shown = item.get("notes", []) if include_done else open_notes(item)
        if not shown:
            continue
        entry = {"id": item["id"], "app": item["app"], "what": item.get("title") or item["doing"],
                 "last_seen": item["last_seen"][:16].replace("T", " "),
                 "notes": [{"id": note_id(n), "at": n["at"][:16].replace("T", " "), "text": n["text"],
                            **({"done": item["notes_done"][note_id(n)][:16].replace("T", " ")}
                               if is_done(item, n) else {})}
                           for n in sorted(shown, key=lambda n: n["at"], reverse=True)]}
        projects.setdefault(project_of(item), []).append(entry)
    ordered = []
    for name, entries in projects.items():
        entries.sort(key=lambda e: e["notes"][0]["at"], reverse=True)
        ordered.append({"project": name,
                        "open": sum(1 for e in entries for n in e["notes"] if "done" not in n),
                        "items": entries})
    ordered.sort(key=lambda p: p["items"][0]["notes"][0]["at"], reverse=True)
    return {"updated": datetime.now().isoformat(timespec="seconds").replace("T", " ")[:16],
            "open": sum(p["open"] for p in ordered), "projects": ordered}


def card(items, item_id):
    """What the on-screen widget shows for the thing in front: the edits left on its
    project (this thing's first), and the project's plan: every note, open then done,
    plus its history (notes written / ticked off, newest first)."""
    item = items.get(item_id)
    if item is None:
        return None
    project = project_of(item)
    members = [i for i in items.values() if project_of(i) == project]
    left, plan, history = [], [], []
    for i in sorted(members, key=lambda i: (i["id"] != item_id, i["last_seen"]), reverse=False):
        what = i.get("title") or i["doing"]
        for n in i.get("notes", []):
            nid = note_id(n)
            done_at = i.get("notes_done", {}).get(nid)
            row = {"id": nid, "text": n["text"], "at": n["at"], "on": what,
                   "here": i["id"] == item_id, "done": done_at}
            plan.append(row)
            if not done_at:
                left.append(row)
            history.append({"at": n["at"], "event": "added", "text": n["text"], "on": what})
            if done_at:
                history.append({"at": done_at, "event": "done", "text": n["text"], "on": what})
    left.sort(key=lambda r: (not r["here"], r["at"]))
    plan.sort(key=lambda r: (bool(r["done"]), r["at"]))
    history.sort(key=lambda h: h["at"], reverse=True)
    return {"item": item_id, "filed": item.get("project"),
            "title": item.get("title") or item["doing"], "app": item["app"], "project": project,
            "left": left, "plan": plan, "history": history,
            "things": len(members), "visits": sum(i.get("visits", 0) for i in members),
            "seconds": sum(i.get("seconds", 0) for i in members)}


def note_target(data, label):
    """The note card's "Note for" line and the name the pill confirms with. A thing you put
    in a project reads "Pricing › Q3 plan"; one that is not in a project is just its name.
    With nothing known yet it falls back to the app and tab (label)."""
    title = ((data or {}).get("title") or "").strip()
    if not title:
        return {"crumb": label, "where": label}
    filed = (data.get("filed") or "").strip()
    return {"crumb": f"{filed} › {title}" if filed else title, "where": title}


def done_view(data, now_iso):
    """The Done list for the thing in front: its finished notes, newest first, grouped by day.
    Returns {"title", "groups": [(label, rows)], "count"}; a row is {"id", "text", "when"}."""
    from datetime import timedelta
    today = datetime.fromisoformat(now_iso).date()
    rows = sorted((n for n in (data or {}).get("plan", []) if n["done"] and n["here"]),
                  key=lambda n: n["done"], reverse=True)
    groups = {}
    for n in rows:
        at = datetime.fromisoformat(n["done"])
        clock = at.strftime("%I:%M %p").lstrip("0")
        if at.date() == today:
            label, when = "Today", f"Today, {clock}"
        elif at.date() == today - timedelta(days=1):
            label, when = "Yesterday", f"Yesterday, {clock}"
        else:
            label, when = "Earlier", f"{at.strftime('%b')} {at.day}, {clock}"
        groups.setdefault(label, []).append({"id": n["id"], "text": n["text"], "when": when})
    order = [k for k in ("Today", "Yesterday", "Earlier") if k in groups]
    return {"title": (data or {}).get("title", ""), "groups": [(k, groups[k]) for k in order], "count": len(rows)}


def pill_summary(data):
    """(open notes on the thing in front, text of the first one) for the pill itself.
    Notes on other things in the project don't light the pill: it is about *here*."""
    here = [n for n in (data or {}).get("left", []) if n["here"]]
    return len(here), (here[0]["text"] if here else "")


def blank_card(title, app):
    """The card for a place with no notes (and nothing remembered yet) so the pill can
    say "No notes here" instead of staying blank."""
    return {"title": title, "app": app, "project": app, "left": [], "plan": [], "history": [],
            "things": 0, "visits": 0, "seconds": 0}



def card_view(data, mode="here", show_done=False, fading=()):
    """What the pill's card draws, as plain data (widget.py only turns it into views).

    mode "here": the open notes on this thing, plus how many more wait elsewhere in the project.
    mode "project": every open note on the project grouped by thing, with the done ones
    behind a "Done · N" row. `fading` holds note ids ticked a moment ago: they stay on
    screen struck through for a beat instead of vanishing under the cursor."""
    if data is None:
        return {"empty": True}
    fading = set(fading)
    shown = [n for n in data["plan"] if not n["done"] or n["id"] in fading]
    row = lambda n: {"id": n["id"], "text": n["text"], "done": bool(n["done"])}
    if mode == "project":
        groups = {}
        for n in sorted(shown, key=lambda n: not n["here"]):
            groups.setdefault(n["on"], []).append(row(n))
        done = [row(n) for n in data["plan"] if n["done"] and n["id"] not in fading]
        return {"mode": "project", "title": data["project"], "caption": f"{len(data['left'])} open",
                "back": data["title"], "groups": list(groups.items()),
                "done": done if show_done else [], "done_count": len(done)}
    return {"mode": "here", "title": data["title"], "caption": data["app"],
            "rows": [row(n) for n in shown if n["here"]],
            "more": sum(1 for n in data["left"] if not n["here"]), "project": data["project"],
            "done_count": sum(1 for n in data["plan"] if n["done"]),
            "done_here": sum(1 for n in data["plan"] if n["done"] and n["here"]),
            "item": data.get("item"), "filed": data.get("filed")}


# ---------------------------------------------------------------- projects you file things into
#
# item["project"] = "Pricing" is a project the user chose (by hand, or by accepting a
# suggestion). Items without it keep the guessed grouping in project_of(). A project exists
# as long as something is in it, so there is no separate list to keep in step.
# item["declined_projects"] = ["Pricing"] remembers a suggestion the user turned down for
# that thing, so the same group is not offered twice.

def set_project(items, ids, name):
    """Put these items in project `name`. Returns the ids that were found."""
    name = " ".join((name or "").split())
    if not name:
        return []
    found = []
    for iid in ids:
        item = items.get(iid)
        if item is not None:
            item["project"] = name
            found.append(iid)
    return found


def clear_project(items, ids):
    """Take these items out of the project you put them in."""
    found = []
    for iid in ids:
        item = items.get(iid)
        if item is not None and item.pop("project", None) is not None:
            found.append(iid)
    return found


def decline_project(items, ids, name):
    """Remember that `name` was refused for these items."""
    for iid in ids:
        item = items.get(iid)
        if item is not None and name not in item.setdefault("declined_projects", []):
            item["declined_projects"].append(name)


def was_declined(items, ids, name):
    return any(name in (items.get(i) or {}).get("declined_projects", []) for i in ids)


def project_names(items):
    """Projects you have made, most recently worked on first: [{"name", "things", "last_seen"}]."""
    found = {}
    for item in items.values():
        name = item.get("project")
        if not name:
            continue
        entry = found.setdefault(name, {"name": name, "things": 0, "last_seen": ""})
        entry["things"] += 1
        entry["last_seen"] = max(entry["last_seen"], item["last_seen"])
    return sorted(found.values(), key=lambda p: p["last_seen"], reverse=True)


def open_now(items, item_id, now_iso, minutes=30, limit=2):
    """Other things you had in front recently (not already in this one's project), newest first.
    The picker offers to file them together with this one."""
    here = items.get(item_id)
    if here is None:
        return []
    near = []
    for item in items.values():
        if item["id"] == item_id or (here.get("project") and item.get("project") == here["project"]):
            continue
        if _seconds_between(item["last_seen"], now_iso) <= minutes * 60:
            near.append(item)
    near.sort(key=lambda i: i["last_seen"], reverse=True)
    return [{"id": i["id"], "title": i.get("title") or i["doing"]} for i in near[:limit]]


def picker_view(items, item_id, query="", now_iso=None):
    """What the "add to a project" picker draws, as plain data.

    rows: your projects filtered by `query` (the one this thing is in is marked `here`).
    new:  the name a "New project" row would create: the typed text, or "" while nothing
          is typed (the row is always there so creating a project is never hidden).
    also: other things open lately, offered as a tick to file them too."""
    item = items.get(item_id)
    if item is None:
        return None
    query = " ".join((query or "").split())
    q = query.lower()
    rows = [{"name": p["name"], "meta": f"{p['things']} thing{'s' * (p['things'] != 1)}",
             "here": p["name"] == item.get("project")}
            for p in project_names(items) if not q or q in p["name"].lower()]
    exact = any(r["name"].lower() == q for r in rows)
    return {"title": f"Add {item.get('title') or item['doing']} to a project",
            "total": len(project_names(items)), "kind": item.get("kind", ""),
            "current": item.get("project"), "rows": rows, "new": "" if (not query or exact) else query,
            "also": open_now(items, item_id, now_iso or datetime.now().isoformat(timespec="seconds"))}


def suggestion_view(suggestion, items, answered=0, first_times=3):
    """What the suggestion card draws. `suggestion` is {"name", "ids", "reason"}.
    Until you have answered `first_times` suggestions the pill says "Group these?" in words and
    the card adds a line on what a project is; after that the pill is just the mark."""
    if not suggestion:
        return None
    things = [{"id": i, "title": items[i].get("title") or items[i]["doing"], "app": items[i]["app"],
               "kind": items[i].get("kind", ""), "url": favicons.page_url(items[i])}
              for i in suggestion["ids"] if i in items]
    if len(things) < 2:
        return None
    return {"name": suggestion["name"], "things": things, "reason": suggestion.get("reason", ""),
            "peek": f"{suggestion['name']} · {len(things)} things",
            "first_time": answered < first_times}

"""LMemM - the project page, as data. Plain functions, no UI, all tested.

The page answers one question: "where was I?". Everything here is read only: it looks at the
project tree (projects.py) and the remembered things and returns what to show. window.py
draws it and forwards presses to `press()`; it decides nothing.

    P1  The sidebar is the tree. Any depth, each level pages itself ("Show 12 more"), a
        project opens and closes on its own, and an archived project (or anything inside one)
        is not there. Counts roll up by each thing's main project (R13), so nothing is counted twice.
    P2  A project page opens with "Pick up where you left off": the things in it (and inside it)
        with open notes, newest first, at most three. With none it says All caught up and names
        the last thing you worked on. It never shows an empty gap.
    P3  Under that, the sub-projects, then the things, grouped Today / This week / Earlier,
        newest first. "Only here" or "With sub-projects" is one switch; the filters are the apps
        that are actually in this project, never a fixed list.
    P4  A thing shows where it lives only when that is not the page you are on, and the other
        projects it is also in, softly ("also in ...").
    P5  Needs you is every thing with an open note, anywhere, newest first. Home shows the top
        few and the count; "See all" is the full list.
    P6  Search looks at project names (and the old names left by a merge), thing titles and open
        notes, case and accents ignored, across every project. Nothing matches: it says so.
    P7  Every list is cut at a sensible number with a "Show N more" row; nothing is silently
        dropped, and 10,000 projects stay as quick as 10.
    P8  Press only changes what you are looking at (`press`). Nothing here writes to memory.
    P10 A note you just ticked stays on its thing, crossed out, until its hold is over (rules.FinishFlow,
        R10), then moves to Finished. Tapping a finished note opens it again. The window reads and
        writes the same notes as the pill, so a tick in either is a tick in both.
    P9  Any thing, and any note of it, opens its own page: the whole title, where it lives (and
        the other projects it is in), the open notes in full, the finished ones, when you last
        saw it and how long you spent, and what LMemM read from it. Back returns to the page you
        came from, scrolled to the top of it.
"""

import functools
import unicodedata
from datetime import datetime

import favicons
import notes
import projects
import settings_model
import store

ARCHIVE_ID = "archived"            # the tree's folder of archived projects (not a real project id: those are p1, p2...)
SIDE_LIMIT = 8                        # rows per level in the sidebar before "Show more"
PICK_UP = 3
NOTES_SHOWN = 3
SUBS_SHOWN = 6
THINGS_SHOWN = 20
NEEDS_HOME = 3
NEEDS_SIDE = 3
SEARCH_LIMITS = {"projects": 6, "notes": 8, "things": 25}
CRUMBS_MAX = 5


# ---------------------------------------------------------------- words

@functools.lru_cache(maxsize=1 << 17)                  # searching 60,000 titles again on every key would be slow
def fold(text):
    """For matching: lower case, no accents."""
    text = text or ""
    if text.isascii():
        return text.casefold()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def parse(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def ago(iso, now):
    """"Just now", "12 min ago", "3 h ago", "Yesterday", "Oct 3"."""
    when = parse(iso)
    if not when:
        return ""
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if when.date() == now.date():
        return f"{int(seconds // 3600)} h ago"
    days = (now.date() - when.date()).days
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return when.strftime("%b ") + str(when.day) + ("" if when.year == now.year else f", {when.year}")


def group_of(iso, now):
    when = parse(iso)
    if not when:
        return "Earlier"
    days = (now.date() - when.date()).days
    return "Today" if days <= 0 else "This week" if days < 7 else "Earlier"


def title_of(item):
    return item.get("title") or item.get("doing") or item.get("app") or "Untitled"


HUES = 8                              # the window has eight calm colours (never red: red is for a permission that is off)


def hue(pid):
    """A project's colour, 0 to 7. Stable: the same project always has the same one."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(str(pid))) % HUES


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else many or one + 's'}"


# ---------------------------------------------------------------- what you are looking at

def new_state():
    return {"view": "home", "pid": None, "q": "", "open": [], "deep": True, "app": None,
            "shown": THINGS_SHOWN, "subs": SUBS_SHOWN, "lim": {}, "needs_all": False, "tid": None, "back": None, "ex_open": [], "sec": "you"}


def press(reg, state, action, arg=None, items=None):
    """A new state after a press. Unknown projects (deleted since) fall back to home."""
    s = {**state, "open": list(state["open"]), "lim": dict(state["lim"]), "ex_open": list(state.get("ex_open", []))}
    if action == "home":
        s.update(view="home", pid=None, q="", needs_all=False)
    elif action == "go":
        if not projects.exists(reg, arg):
            return press(reg, state, "home")
        s.update(view="project", pid=arg, q="", app=None, shown=THINGS_SHOWN, subs=SUBS_SHOWN, needs_all=False)
        for up in projects.ancestors(reg, arg):                    # the sidebar shows where you are
            if up not in s["open"]:
                s["open"].append(up)
    elif action == "thing":
        if items is not None and arg not in items:
            return state
        if state["view"] != "thing":
            s["back"] = {"view": state["view"], "pid": state["pid"], "q": state["q"]}
        s.update(view="thing", tid=arg, ex_open=[])
    elif action == "back":
        before = state.get("back") or {"view": "home", "pid": None, "q": ""}
        if before["view"] == "project" and not projects.exists(reg, before["pid"]):
            before = {"view": "home", "pid": None, "q": ""}
        s.update(view=before["view"], pid=before["pid"], q=before["q"], tid=None, back=None)
    elif action == "expand":
        if arg in s["ex_open"]:
            s["ex_open"].remove(arg)
        else:
            s["ex_open"].append(arg)
    elif action == "toggle":
        if arg in s["open"]:
            s["open"].remove(arg)
        else:
            s["open"].append(arg)
    elif action == "deep":
        s.update(deep=bool(arg), shown=THINGS_SHOWN)
    elif action == "app":
        s.update(app=arg, shown=THINGS_SHOWN)
    elif action == "more":
        s["shown"] += THINGS_SHOWN
    elif action == "more_subs":
        s["subs"] += SUBS_SHOWN
    elif action == "more_side":
        key = arg or "top"
        s["lim"][key] = s["lim"].get(key, SIDE_LIMIT) + SIDE_LIMIT
    elif action == "needs":
        s.update(view="needs", pid=None, q="")
    elif action == "settings":
        if state["view"] != "settings":
            s["back"] = {"view": state["view"], "pid": state["pid"], "q": state["q"]}
        s.update(view="settings", pid=None, q="", sec=arg if arg in settings_model.SECTIONS else state.get("sec", "you"))
    elif action == "archived":
        s.update(view="archived", pid=None, q="")
        if ARCHIVE_ID not in s["open"]:
            s["open"].append(ARCHIVE_ID)
    elif action == "search":
        q = (arg or "").strip()
        if q:
            s.update(view="search", q=q)
        else:
            return press(reg, state, "clear")
    elif action == "clear":
        back = "project" if s["pid"] and projects.exists(reg, s["pid"]) else "home"
        s.update(view=back, q="")
    return s


# ---------------------------------------------------------------- rows

def _live(reg, pid):
    return projects.exists(reg, pid) and not projects.hidden(reg, pid)


def _thing(reg, item, now, here=None, with_notes=False):
    """One thing as a row. `here` is the project page it is shown on (P4)."""
    main = projects._main(item)
    others = [projects.get(reg, p)["name"] for p in projects._members_of(item)
              if p != main and _live(reg, p)]
    bits = [item.get("app", "")]
    if main and main != here and _live(reg, main):
        bits.append(" › ".join(projects.path_names(reg, main)))
    if others:
        bits.append("also in " + ", ".join(others))
    open_notes = notes.open_notes(item)
    row = {"id": item["id"], "pid": main if main and _live(reg, main) else None, "title": title_of(item), "sub": " · ".join(b for b in bits if b),
           "ago": ago(item.get("last_seen"), now), "open": len(open_notes), "app": item.get("app", ""),
           "kind": item.get("kind", ""), "url": favicons.page_url(item)}
    if with_notes:
        row["notes"] = [n["text"] for n in open_notes[:NOTES_SHOWN]]
        row["more_notes"] = max(0, len(open_notes) - NOTES_SHOWN)
    return row


def _subs(reg, pid):
    """How many sub-projects a project has directly (archived ones do not count)."""
    return sum(1 for k in projects.children(reg, pid) if not reg["projects"][k]["archived"])


def _by_recent(things):
    return sorted(things, key=lambda i: i.get("last_seen", ""), reverse=True)


def archived_roots(reg):
    """The archived projects you archived yourself: not the ones that are only hidden because
    something above them is archived. Bringing one back brings everything inside it back."""
    return [p for p, row in reg["projects"].items()
            if row["archived"] and not any(reg["projects"][a]["archived"] for a in projects.ancestors(reg, p))]


def needs_you(items):
    return _by_recent(i for i in items.values() if notes.open_notes(i))


def _count_line(c, subs=0):
    parts = [plural(c["things"], "thing")]
    if subs:
        parts.append(plural(subs, "sub-project"))
    if c["open"]:
        parts.append(f"{c['open']} open")
    return " · ".join(parts)


# ---------------------------------------------------------------- the sidebar

def side(reg, items, state, counts, now):
    needs = needs_you(items)
    return {"needs": {"count": len(needs), "rows": [_thing(reg, i, now) for i in needs[:NEEDS_SIDE]]},
            "tree": tree_rows(reg, state, counts),
            "unplaced": len(projects.unassigned(items)), "archived": len(archived_roots(reg))}


def tree_rows(reg, state, counts):
    """The visible rows of the tree, top to bottom (P1)."""
    out = []
    openset = set(state["open"])

    def walk(parent, level):
        kids = [k for k in projects.children(reg, parent) if not reg["projects"][k]["archived"]]
        limit = state["lim"].get(parent or "top", SIDE_LIMIT)
        for pid in kids[:limit]:
            has = any(not reg["projects"][k]["archived"] for k in projects.children(reg, pid))
            row = {"id": pid, "name": reg["projects"][pid]["name"], "hue": hue(pid), "level": level, "expandable": has,
                   "expanded": has and pid in openset, "selected": state["view"] == "project" and state["pid"] == pid,
                   "count": counts[pid]["things"], "open": counts[pid]["open"]}
            out.append(row)
            if row["expanded"]:
                walk(pid, level + 1)
        if len(kids) > limit:
            left = len(kids) - limit
            out.append({"id": None, "more": parent or "top", "level": level,
                        "name": f"Show {min(left, SIDE_LIMIT)} more of {left}"})

    walk(None, 0)
    roots = archived_roots(reg)
    if roots:                                                  # archived projects live in a folder at the end of the tree
        expanded = ARCHIVE_ID in openset
        out.append({"id": ARCHIVE_ID, "name": "Archived", "level": 0, "expandable": True, "expanded": expanded,
                    "selected": state["view"] == "archived", "count": len(roots), "open": 0, "folder": True})
        if expanded:
            for pid in roots[:state["lim"].get(ARCHIVE_ID, SIDE_LIMIT)]:
                out.append({"id": pid, "name": reg["projects"][pid]["name"], "hue": hue(pid), "level": 1,
                            "expandable": False, "expanded": False, "archived": True,
                            "selected": state["view"] == "project" and state["pid"] == pid,
                            "count": counts[pid]["things"], "open": counts[pid]["open"]})
            if len(roots) > state["lim"].get(ARCHIVE_ID, SIDE_LIMIT):
                left = len(roots) - state["lim"].get(ARCHIVE_ID, SIDE_LIMIT)
                out.append({"id": None, "more": ARCHIVE_ID, "level": 1, "name": f"Show {min(left, SIDE_LIMIT)} more of {left}"})
    return out


# ---------------------------------------------------------------- the main area

def crumbs(reg, pid):
    """All projects › ... › this one. A long path keeps its first and last two (P1)."""
    chain = [{"id": p, "name": reg["projects"][p]["name"]} for p in projects.path(reg, pid)]
    if len(chain) > CRUMBS_MAX:
        hidden = chain[1:-2]
        chain = chain[:1] + [{"id": None, "name": "…", "title": " › ".join(c["name"] for c in hidden)}] + chain[-2:]
    return chain


def main(reg, items, state, counts, now, fading=(), sugg=None, prefs=None):
    view = state["view"]
    if view == "settings" and prefs:
        return prefs
    if view == "thing" and state.get("tid") in items:
        return thing_page(reg, items[state["tid"]], now, state.get("ex_open", ()), fading)
    if view == "search":
        return search(reg, items, state["q"], now)
    if view == "archived":
        rows = [{"id": p, "name": reg["projects"][p]["name"], "hue": hue(p),
                 "line": _count_line(counts[p], _subs(reg, p)),
                 "where": " › ".join(projects.path_names(reg, p)[:-1])} for p in archived_roots(reg)]
        return {"kind": "archived", "title": "Archived", "back": True,
                "meta": "Hidden from the tree, Pick up and search. Nothing is deleted.", "rows": rows,
                "empty": None if rows else {"title": "Nothing archived", "line": "Projects you archive wait here until you bring them back."}}
    if view == "needs":
        rows = [_thing(reg, i, now, with_notes=True) for i in needs_you(items)]
        return {"kind": "needs", "title": "Needs you", "meta": plural(len(rows), "thing") + " with open notes",
                "things": rows, "back": True}
    if view == "project" and projects.exists(reg, state["pid"]):
        return project_page(reg, items, state, counts, now, sugg)
    return home(reg, items, counts, now, sugg)


def home(reg, items, counts, now, sugg=None):
    top = [p for p in projects.children(reg) if not reg["projects"][p]["archived"]]
    needs = needs_you(items)
    cards = [{"id": p, "name": reg["projects"][p]["name"], "hue": hue(p), "line": _count_line(counts[p], _subs(reg, p))}
             for p in top]
    if not top and not items:
        empty = {"title": "Nothing here yet", "line": "LMemM is learning what you work on. Things appear here by themselves."}
    elif not top:
        empty = {"title": "No projects yet", "line": "Your things are saved. Projects you make will appear here."}
    else:
        empty = None
    return {"kind": "home", "title": "All projects", "meta": plural(len(top), "project"),
            "needs": {"count": len(needs), "rows": [_thing(reg, i, now, with_notes=True) for i in needs[:NEEDS_HOME]]},
            "cards": cards, "unplaced": len(projects.unassigned(items)), "empty": empty,
            "suggest": _suggest_home(sugg)}


def _suggest_home(sugg):
    """The suggestions on the first page: projects to make, and which projects have things waiting (S1)."""
    if not sugg or not sugg["count"]:
        return None
    return {"count": sugg["count"], "projects": sugg["projects"],
            "items": [{"id": e["id"], "pid": e["pid"], "name": e["name"], "hue": e["hue"], "count": len(e["things"]),
                       "line": e["reason"]} for e in sugg["items"]]}


def _suggest_for(sugg, pid):
    for e in (sugg or {}).get("items", []):
        if e["pid"] == pid:
            return e
    return None


def project_page(reg, items, state, counts, now, sugg=None):
    pid = state["pid"]
    c = counts[pid]
    kids = [k for k in projects.children(reg, pid) if not reg["projects"][k]["archived"]]
    deep = state["deep"] and bool(kids)
    everything = projects.members(reg, items, pid, deep=True)
    here = projects.members(reg, items, pid, deep=deep)
    pick = [i for i in everything if notes.open_notes(i)][:PICK_UP]
    page = {"kind": "project", "pid": pid, "hue": hue(pid), "archived": projects.hidden(reg, pid), "title": reg["projects"][pid]["name"], "crumbs": crumbs(reg, pid),
            "meta": " · ".join(([plural(len(kids), "sub-project")] if kids else []) + [_count_line(c)]),
            "suggested": _suggest_for(sugg, pid)}
    if not everything and not kids:
        page["empty"] = {"title": "Nothing here yet",
                         "line": "Things you work on in this project show up by themselves."}
        return page
    page["pick_up"] = [_thing(reg, i, now, here=pid, with_notes=True) for i in pick]
    if not pick:
        last = everything[0] if everything else None
        page["caught_up"] = {"title": "All caught up", "line": "No open notes in this project." + (
            f" Last worked on: {title_of(last)}, {ago(last.get('last_seen'), now).lower()}." if last else "")}
    page["subs"] = [{"id": k, "name": reg["projects"][k]["name"], "hue": hue(k), "line": _count_line(counts[k], _subs(reg, k))}
                    for k in kids[:state["subs"]]]
    page["subs_more"] = max(0, len(kids) - state["subs"])
    apps = {}
    for i in here:
        apps[i.get("app", "")] = apps.get(i.get("app", ""), 0) + 1
    page["filters"] = [{"app": a, "count": n} for a, n in sorted(apps.items(), key=lambda kv: (-kv[1], kv[0])) if a][:6]
    chosen = [i for i in here if state["app"] is None or i.get("app") == state["app"]]
    page["deep"] = {"on": deep, "show": bool(kids)}
    page["total"] = len(chosen)
    groups = {"Today": [], "This week": [], "Earlier": []}
    for i in chosen[:state["shown"]]:
        groups[group_of(i.get("last_seen"), now)].append(_thing(reg, i, now, here=pid))
    page["groups"] = [{"title": t, "things": r} for t, r in groups.items() if r]
    page["things_more"] = max(0, len(chosen) - state["shown"])
    return page


# ---------------------------------------------------------------- one thing

LATEST_SHOWN = 6


VISIBLE_LINES = 6
EXCERPTS_SHOWN = 3
DECISIONS_SHOWN = 3


def excerpts_of(content, now, expanded=()):
    """What LMemM read from a thing, newest first, as short readable passages (P9). Older memory
    kept one plain string; now it is {"excerpts": [{"id", "text", "source", "last_seen",
    "decision_quotes"}, ...]}, newest last (memory_content.py). Anything odd becomes no passage."""
    if isinstance(content, str):
        raw = [{"id": "text", "text": content}]
    elif isinstance(content, dict):
        raw = [e for e in content.get("excerpts") or [] if isinstance(e, dict)]
        if not raw and isinstance(content.get("text"), str):
            raw = [{"id": "text", "text": content["text"]}]
    else:
        raw = []
    out = []
    for e in reversed(raw):
        text = e.get("text")
        if not isinstance(text, str):
            continue
        lines = []
        for line in text.splitlines():
            line = " ".join(line.split())
            if line and line not in lines:
                lines.append(line)
        if not lines:
            continue
        eid = str(e.get("id") or len(out))
        open_ = eid in expanded
        source = e.get("source") if isinstance(e.get("source"), dict) else {}
        quotes = [q for q in e.get("decision_quotes") or [] if isinstance(q, str)][:DECISIONS_SHOWN]
        out.append({"id": eid, "when": ago(e.get("last_seen") or e.get("observed_at"), now),
                    "source": source.get("window") or source.get("app") or "",
                    "lines": lines if open_ else lines[:VISIBLE_LINES],
                    "more": 0 if open_ else max(0, len(lines) - VISIBLE_LINES), "open": open_,
                    "decisions": quotes})
        if len(out) == EXCERPTS_SHOWN:
            break
    return out


def thing_page(reg, item, now, expanded=(), fading=()):
    """Everything LMemM knows about one thing, read only (P9)."""
    main = projects._main(item)
    places = [{"id": p, "name": projects.get(reg, p)["name"], "hue": hue(p), "main": p == main,
               "path": " › ".join(projects.path_names(reg, p))}
              for p in projects._members_of(item) if _live(reg, p)]
    mine = [n for n in item.get("notes", []) if isinstance(n, dict) and isinstance(n.get("text"), str)]
    fading = set(fading)
    shown = [n for n in mine if not notes.is_done(item, n) or notes.note_id(n) in fading]
    done = [n for n in mine if notes.is_done(item, n) and notes.note_id(n) not in fading]
    state = {k: v for k, v in (item.get("state") or {}).items() if v and str(v) != title_of(item)}
    stats = [("Last seen", ago(item.get("last_seen"), now)), ("First seen", ago(item.get("first_seen"), now)),
             ("Time spent", store.duration(item.get("seconds"))), ("Visits", f"{int(item.get('visits') or 0):,}")]
    return {"kind": "thing", "title": title_of(item), "app": item.get("app", ""), "back": True,
            "thing_kind": item.get("kind", ""), "url": favicons.page_url(item),
            "meta": " · ".join(b for b in (item.get("app", ""), "last seen " + ago(item.get("last_seen"), now).lower()) if b),
            "places": places,
            "item": item["id"],
            "open": [{"id": notes.note_id(n), "text": n["text"], "when": ago(n.get("at"), now),
                      "done": notes.is_done(item, n)} for n in shown],
            "open_count": sum(1 for n in shown if not notes.is_done(item, n)),
            "done": [{"id": notes.note_id(n), "text": n["text"], "when": ago(n.get("at"), now)} for n in done],
            "stats": [(k, v) for k, v in stats if v],
            "latest": [(str(k).replace("_", " ").capitalize(), str(v)[:200]) for k, v in list(state.items())[:LATEST_SHOWN]],
            "excerpts": excerpts_of(item.get("content"), now, expanded)}


# ---------------------------------------------------------------- search

def search(reg, items, q, now):
    """Projects, things and open notes that match, across every project (P6)."""
    want = fold(q)
    found_projects = [p for p, row in reg["projects"].items()
                      if _live(reg, p) and (want in fold(row["name"]) or any(want in fold(a) for a in row["aliases"]))]
    things, hits = [], []
    for item in _by_recent(items.values()):
        if want in fold(title_of(item)):
            things.append(item)
        for note in notes.open_notes(item):
            if want in fold(note["text"]):
                hits.append((item, note))
    lim = SEARCH_LIMITS
    return {"kind": "search", "title": f"Results for “{q}”", "back": True,
            "meta": f"{plural(len(found_projects), 'project')} · {plural(len(things), 'thing')} · {plural(len(hits), 'note')}",
            "projects": [{"id": p, "name": reg["projects"][p]["name"], "hue": hue(p), "path": " › ".join(projects.path_names(reg, p))}
                         for p in found_projects[:lim["projects"]]],
            "notes": [{"text": n["text"], "thing": title_of(i), "id": i["id"]} for i, n in hits[:lim["notes"]]],
            "things": [_thing(reg, i, now) for i in things[:lim["things"]]],
            "things_more": max(0, len(things) - lim["things"]),
            "none": not (found_projects or things or hits)}


# ---------------------------------------------------------------- the whole page

def view(reg, items, state, now=None, fading=(), sugg=None, prefs=None):
    """fading: ids of notes ticked a moment ago, still on screen crossed out (P10). sugg: suggestions.view()."""
    now = now or datetime.now()
    with projects.indexed(reg, items):                                     # 10,000 projects must not mean 10,000 scans
        counts = projects.counts(reg, items)
        return {"side": side(reg, items, state, counts, now), "main": main(reg, items, state, counts, now, fading, sugg, prefs)}


def signature(v):
    return repr(v)

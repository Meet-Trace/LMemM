"""LMemM - project finder: notice projects in what you already did, and only ever suggest them.

The tracker calls `propose` now and then while you are idle. It runs the assign method
(project_reasoner_assign.py) over the things you used recently and turns what it finds into the
proposals that suggestions.py already holds and the window and pill already show:

    a new project     "These 4 things look like one project: Kestrel"   Create / Dismiss
    an existing one   "These 2 things may belong in Q3 plan"            Add / Not here

    H1  Nothing is filed without your answer; this file only offers (suggestions.S1).
    H2  Only what recurs: a new project needs NEW_MIN_TASKS separate sittings and NEW_MIN_THINGS things,
        so one busy afternoon is not a project.
    H3  Things already in a project steer the rest: when most of a group you used together is already
        filed in one of your projects, the rest is offered for that project, not as a new one.
    H4  Never a flood: at most MAX_WAITING proposals wait at once; the strongest are offered first.
    H5  Refusals stick: suggestions.py never offers a declined name or a "Not here" thing again (R14).
    H7  Unanswered is not refused: a suggestion left SHELVE_AFTER_DAYS is set aside to make room, and
        offered again once it has rested SHELF_REST_DAYS and there is room. Meanwhile the same group is
        not offered as if it were new.
    H6  Every offer and every answer goes to a local log (grouping_log.jsonl beside memory.json), so your
        answers become labels to score the method against. It never leaves this Mac.

No model by default: the capitalised-word rule scores as well as gemma3:1b on the test streams and makes
no noise. `model` can be any callable the assign method accepts.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

import config
import project_reasoner_assign as assign
import project_reasoner_graph as graph
import group_names
import projects
import understand
import suggestions

LOOKBACK_DAYS = 14
NEW_MIN_TASKS = 2          # sittings a group must show up in before it is offered as a project
NEW_MIN_THINGS = 3
JOIN_SHARE = 0.5           # filed share of a group that steers the rest into that project
MAX_WAITING = 3
SHELVE_AFTER_DAYS = 3      # unanswered this long: set aside to make room (H7)
SHELF_REST_DAYS = 7        # set aside this long: offered again when there is room
LOG_NAME = "grouping_log.jsonl"
OWN_APPS = {"LMemM", "python3", "Python", "UserNotificationCenter"}   # LMemM's own windows are not your work
SKIP_FIELDS = {"screenshot", "project", "project_id", "also_in", "not_in", "declined_projects", "label", "label_status"}


def recent(items, visits, now, days=LOOKBACK_DAYS):
    """The things and visits of the last `days` days, with your own filing taken out so the method
    cannot copy it (it only steers the result afterwards, H3)."""
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    timeline = [v for v in visits if v["from"] >= since and v["item"] in items]
    timeline = [v for v in timeline if items[v["item"]].get("app") not in OWN_APPS]
    used = dict.fromkeys(v["item"] for v in timeline)
    out = {i: {k: v for k, v in items[i].items() if k not in SKIP_FIELDS} for i in used}
    for thing in out.values():                    # older things saved before capture named every window
        if not thing.get("title"):
            thing["title"] = understand.place_name(thing)
    return out, timeline


def _filed(reg, item):
    return [p for p in projects._members_of(item) if projects.exists(reg, p) and not projects.hidden(reg, p)]


def _keys(item):
    """The names a place carries (the same names the method uses), lower-cased."""
    names = graph.model_names(graph.Model(graph.CapitalisedNames(), []), item, [])
    return {k for k, v in assign.entities(item, names).items()
            if v >= assign.PRIOR["channel"] and not k.replace(".", "").isdigit()}


def _spelling(key, items, ids):
    """How you write it ("180DC", "LMemM"), from the things themselves."""
    pattern = re.compile(r"(?<![\w])" + re.escape(key) + r"(?![\w])", re.IGNORECASE)
    seen = Counter(m.group(0) for i in ids for m in pattern.finditer(json.dumps(items[i], ensure_ascii=False)))
    best = [w for w, _ in seen.most_common() if w != w.lower()] or [w for w, _ in seen.most_common()]
    return best[0] if best else key.title()


def name_for(items, ids, keys_of):
    """A short name: the name most of the group shares and little else does; a second one when it is
    nearly as strong ("180DC Northwind")."""
    inside = Counter(k for i in ids for k in keys_of[i])
    everywhere = Counter(k for ks in keys_of.values() for k in ks)
    score = {k: n * n / everywhere[k] for k, n in inside.items() if n >= 2}
    top = sorted(score, key=lambda k: (-score[k], k))[:2]
    if not top:
        return None
    if len(top) == 2 and (score[top[1]] < 0.9 * score[top[0]] or top[0] in top[1] or top[1] in top[0]):
        top = top[:1]
    return " ".join(_spelling(k, items, ids) for k in top)


def plan(reg, items, groups, keys_of=None, namer=None, seen=None):
    """What to offer for each group the method found: [(strength, kind, payload)], strongest first.
    kind "items": (pid, ids, reason); kind "project": (name, ids, reason)."""
    out = []
    for g in groups.values():
        ids = [i for i in g["places"] if i in items]
        filed = Counter(p for i in ids for p in _filed(reg, items[i]))
        loose = [i for i in ids if not _filed(reg, items[i])]
        if filed:
            pid, in_it = filed.most_common(1)[0]
            filed_things = len(ids) - len(loose)
            if loose and in_it >= 2 and in_it / filed_things >= JOIN_SHARE:
                why = f"Used alongside {in_it} things already in it"
                out.append((1000 + g["tasks"] + in_it, "items", (pid, loose, why)))   # your projects first
            continue
        if g["tasks"] >= NEW_MIN_TASKS and len(ids) >= NEW_MIN_THINGS:
            name = name_for(items, ids, keys_of) if keys_of else None
            name = name or g["label"]
            if namer is not None:                 # a tiny model names what code grouped (group_names.py)
                name = group_names.suggest(namer, seen or items, ids, fallback=name)["name"] or name
            why = f"Used together in {g['tasks']} separate sittings"
            out.append((g["tasks"] * 2 + len(ids), "project", (name, ids, why)))
    # two groups the method kept apart but that carry the same name are offered as one project: you can
    # still split it, and two cards called "LMemM" would only confuse
    merged, at = [], {}
    for strength, kind, (target, ids, why) in sorted(out, key=lambda x: -x[0]):
        key = (kind, projects.key(target) if kind == "project" else target)
        if key in at:
            s0, k0, (t0, ids0, why0) = merged[at[key]]
            tasks = int(why0.split()[3]) + int(why.split()[3]) if kind == "project" else None
            merged[at[key]] = (s0 + strength, k0, (t0, list(dict.fromkeys(ids0 + ids)),
                                                   f"Used together in {tasks} separate sittings" if tasks else why0))
        else:
            at[key] = len(merged)
            merged.append((strength, kind, (target, ids, why)))
    return sorted(merged, key=lambda x: -x[0])


def find(reg, items, visits, now=None, model=None, days=LOOKBACK_DAYS, namer=None):
    """Run the method over the last LOOKBACK_DAYS days and plan what to offer (see `plan`). Reads only;
    the tracker calls it outside its lock, on a copy of its items."""
    now = now or datetime.now()
    things, timeline = recent(items, visits, now, days)
    if len(things) < NEW_MIN_THINGS:
        return []
    _, _, log = assign.run(things, timeline, model if model is not None else graph.CapitalisedNames())
    groups = next((e["projects"] for e in log if e["step"] == "projects"), {})
    return plan(reg, items, groups, {i: _keys(things[i]) for i in things}, namer, things)


def _when(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def shelve(sug, now):
    """Set aside what has waited SHELVE_AFTER_DAYS without an answer, so new suggestions have room (H7).
    Returns how many were set aside. Nothing is declined: a set-aside suggestion comes back later."""
    shelf, moved = sug.setdefault("shelved", []), 0
    for kind in ("projects", "items"):
        for e in list(sug[kind]):
            offered = _when(e.setdefault("offered", now.isoformat(timespec="seconds")))
            if offered and now - offered >= timedelta(days=SHELVE_AFTER_DAYS):
                sug[kind].remove(e)
                shelf.append({"kind": kind, "entry": e, "shelved": now.isoformat(timespec="seconds")})
                record("shelve", sid=e["id"], target=e.get("name") or e.get("pid"), ids=list(e["ids"]))
                moved += 1
    return moved


def _same(kind, entry, target, ids):
    if (kind == "items") != ("pid" in entry):
        return False
    first = entry.get("pid") if kind == "items" else projects.key(entry["name"])
    return first == (target if kind == "items" else projects.key(target)) or set(entry["ids"]) == set(ids)


def _put(sug, reg, items, kind, target, ids, why, now):
    entry = (suggestions.offer_items(sug, reg, items, target, ids, why) if kind == "items"
             else suggestions.offer_project(sug, items, target, ids, why))
    if entry is not None:
        entry.setdefault("offered", now.isoformat(timespec="seconds"))
    return entry


def offer(sug, reg, items, planned, now=None):
    """Make room (H7), bring back what has rested long enough, then add the strongest planned proposals
    while there is room (H4). Returns the entries added or brought back."""
    now = now or datetime.now()
    shelve(sug, now)
    suggestions.tidy(sug, reg, items)
    room = MAX_WAITING - len(sug["projects"]) - len(sug["items"])
    added, waiting = [], {id(e) for e in sug["projects"] + sug["items"]}
    shelf = sug.setdefault("shelved", [])
    for s in sorted(shelf, key=lambda s: s["shelved"]):
        rested = _when(s["shelved"])
        if len(added) >= room or (rested and now - rested < timedelta(days=SHELF_REST_DAYS)):
            continue
        e = s["entry"]
        kind = "items" if s["kind"] == "items" else "project"
        target = e["pid"] if kind == "items" else e["name"]
        if kind == "items" and not projects.exists(reg, target):
            shelf.remove(s)
            continue
        entry = _put(sug, reg, items, kind, target, e["ids"], e["reason"], now)
        shelf.remove(s)                        # back on the list, or no longer makes sense (S3)
        if entry is not None and id(entry) not in waiting:
            entry["offered"] = now.isoformat(timespec="seconds")
            waiting.add(id(entry))
            added.append(entry)
            record("unshelve", kind=kind, sid=entry["id"], target=target, ids=list(entry["ids"]))
    for _, kind, (target, ids, why) in planned:
        if len(added) >= room:
            break
        if any(_same(kind, s["entry"], target, ids) for s in shelf):
            continue                           # set aside already: it comes back on its own (H7)
        entry = _put(sug, reg, items, kind, target, ids, why, now)
        if entry is not None and id(entry) not in waiting:
            waiting.add(id(entry))
            added.append(entry)
            record("offer", kind=kind, sid=entry["id"], target=target, ids=list(entry["ids"]), reason=why)
    return added


def propose(reg, items, visits, sug, now=None, model=None, days=LOOKBACK_DAYS):
    """find + offer in one call (for tests and the CLI)."""
    return offer(sug, reg, items, find(reg, items, visits, now, model, days), now)


def log_path():
    return os.path.join(config.paths().memory_dir, LOG_NAME)


def record(event, **data):
    """Append one line to the local decision log (H6). Never raises: a full disk must not stop capture."""
    try:
        os.makedirs(config.paths().memory_dir, exist_ok=True)
        with open(log_path(), "a") as fh:
            fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "event": event, **data},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_log():
    try:
        with open(log_path()) as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []

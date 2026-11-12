"""LMemM - Activity annotation: review a recorded day as a few dozen cards, not hundreds of rows. Plain functions, no UI.

Human ground truth for the Activity evaluation (docs/adr/0002-moments-and-evaluation.md). It READS recorded moments and
the deterministic feature/evidence layers; it never runs inference, never writes into the moment folder or the sheet, and
never changes the engine. Everything the human decides goes into a separate annotation folder:

    proposal.json   snapshot of the proposed cards (so a later code change cannot silently re-cut your review)
    events.jsonl    append-only log: label / merge / split / undo. The state is always a replay of this file.
    activities.json (export) human-defined Activities: which visits belong to each, plus the optional Project field
    provenance.json (export) dataset fingerprint, proposal parameters, event count, hashes
    dataset/        (export) a copy of the moments + a units.csv that `eval_activities.py replay` reads unchanged

Words.
    stretch   consecutive visits of ONE screen item, close in time, whose text still resembles the previous visit.
    card      what the human reviews: a stretch, a few stretches linked by strong evidence, or a bucket of glances.
    Nothing is assumed to be one Activity: a card is only a unit of review. Two cards are one Activity only when the human
    merges them or gives them the same name. Activity (what the engine is scored on) and Project (never scored) are
    separate fields.

Rules that protect against merging separate tasks (the point of the design).
    A1  A gap longer than `link_gap_s` between visits of one item always starts a new stretch.
    A2  A clear change of text inside one item starts a new stretch (cosine below `shift_cos`).
    A3  Sharing an app, a window or a screen item never groups two stretches. Same-item proximity is only a SUGGESTION.
    A4  Only strong evidence groups stretches (>=2 rare shared identifiers, or the same specific page/file), and only
        when they are at most `group_gap_s` apart and the whole card spans at most `group_span_s` (no long chains).
    A5  Annotations are per visit: a card can be split at any visit, and cards can be merged across any time distance.
"""

import collections
import copy
import csv
import hashlib
import json
import os
import shutil
import time

import activity_contract as ac
import activity_evidence as ev
import activity_features as af
import activity_input as ai

DEFAULTS = {
    "link_gap_s": 120.0,      # A1
    "shift_cos": 0.03,        # A2
    "glance_s": 10.0,         # a stretch with less total dwell is a glance
    "bucket_s": 600.0,        # glances are reviewed in bulk, one card per this window
    "group_gap_s": 900.0,     # A4: 15 minutes
    "group_span_s": 1800.0,   # A4: a card never spans more than this through evidence links
    "near_gap_s": 900.0,      # a same-item suggestion is "near" within this gap
    "divider_cos": 0.15,      # the page marks a text dip between two visits below this
}
NOISE = "-"
STATUSES = ("open", "activity", "unrelated", "uncertain")


# ---------------------------------------------------------------- proposal

def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def propose(records, opts=None):
    """Moments -> proposal dict (JSON-able). Deterministic: same moments and options, same proposal."""
    o = dict(DEFAULTS, **(opts or {}))
    rp = ac.ReplayParams()
    prepared = ai.prepare(records, max_intra_visit_gap_s=rp.max_intra_visit_gap_s)
    fs, es = ev.analyse(prepared, None, rp)
    F = {f.visit_id: f for f in fs.visits}
    V = {v.visit_id: v for v in prepared.visits}
    order = [v.visit_id for v in prepared.visits]
    item = {v: V[v].moments[0].get("item_id") for v in order}

    visits, cos_prev = {}, {}
    stretches, open_ = [], {}
    for v in order:
        it = item[v]
        s = open_.get(it)
        if s is not None:
            last = s["visits"][-1]
            gap = V[v].t_start - V[last].t_end
            cos = af.content_similarity(F[last], F[v], fs.corpus, fs.params)["cos_idf"]
            enough = len(F[last].bag) >= fs.params.min_bag_tokens and len(F[v].bag) >= fs.params.min_bag_tokens
            if gap <= o["link_gap_s"] and not (enough and cos < o["shift_cos"]):
                s["visits"].append(v)
                cos_prev[v] = round(cos, 3) if enough else None
                continue
        s = {"id": "S%03d" % (len(stretches) + 1), "item": it, "visits": [v]}
        stretches.append(s)
        open_[it] = s
    for s in stretches:
        s["t0"], s["t1"] = V[s["visits"][0]].t_start, V[s["visits"][-1]].t_end
        s["dwell"] = sum(F[v].dwell_s for v in s["visits"])
    of = {v: s for s in stretches for v in s["visits"]}

    for v in order:
        m0 = V[v].moments[0]
        terms, idents, lines = [], [], []
        for m in V[v].moments:
            d = m.get("digest") or {}
            terms += [t for t in d.get("terms") or [] if t not in terms]
            idents += [t for t in d.get("identifiers") or [] if t not in idents]
            lines += [x for x in d.get("lines") or [] if x not in lines]
        visits[v] = {"id": v, "t0": V[v].t_start, "t1": V[v].t_end, "dwell": round(F[v].dwell_s, 1),
                     "app": m0.get("app") or "", "item": item[v], "title": _clip(m0.get("window") or "", 120),
                     "where": _clip(m0.get("url") or "", 120), "terms": terms[:8], "identifiers": idents[:5],
                     "summary": _clip(" | ".join(lines), 200), "moments": [m["id"] for m in V[v].moments],
                     "stretch": of[v]["id"], "cos_prev": cos_prev.get(v)}

    glance = [s for s in stretches if s["dwell"] < o["glance_s"]]
    main = [s for s in stretches if s["dwell"] >= o["glance_s"]]
    sid = {s["id"]: s for s in stretches}
    mid = {s["id"] for s in main}

    # evidence links between main stretches (A4 candidates) and same-item proximity (suggestions only, A3)
    pair_ev = collections.defaultdict(list)
    for e in es.items:
        if e.polarity == "support":
            pair_ev[(e.source, e.target)].append(e)
    strong = {}                                    # (sid, sid) -> reason
    for (x, y), evs in pair_ev.items():
        sx, sy = of[x], of[y]
        if sx is sy or sx["id"] not in mid or sy["id"] not in mid:
            continue
        why = None
        for e in evs:
            if e.type == "shared_identifiers" and sum(1 for i in e.strength.get("items", []) if i.get("class") == "rare") >= 2:
                why = "shared identifiers"
            elif e.type == "same_artifact" and e.strength.get("band") == "high" and not e.strength.get("hub_pattern"):
                why = why or "same page or file"
        if why:
            key = tuple(sorted((sx["id"], sy["id"])))
            strong.setdefault(key, why)

    def gap_of(a, b):
        a, b = sid[a], sid[b]
        return max(0.0, max(a["t0"], b["t0"]) - min(a["t1"], b["t1"]))

    parent = {s["id"]: s["id"] for s in main}
    span = {s["id"]: (s["t0"], s["t1"]) for s in main}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    held_back = {}                                 # strong links not applied because of the gap or span caps
    for (a, b), why in sorted(strong.items(), key=lambda kv: (gap_of(*kv[0]), kv[0])):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        if gap_of(a, b) > o["group_gap_s"]:
            held_back[(a, b)] = why
            continue
        t0, t1 = min(span[ra][0], span[rb][0]), max(span[ra][1], span[rb][1])
        if t1 - t0 > o["group_span_s"]:
            held_back[(a, b)] = why
            continue
        parent[ra] = rb
        span[rb] = (t0, t1)

    comps = collections.defaultdict(list)
    for s in main:
        comps[find(s["id"])].append(s)
    blocks = []
    for comp in comps.values():
        comp.sort(key=lambda s: s["t0"])
        vs = [v for s in comp for v in s["visits"]]
        dw = sum(s["dwell"] for s in comp)
        blocks.append({"kind": "group", "stretches": [s["id"] for s in comp], "visits": vs, "t0": comp[0]["t0"],
                       "t1": max(s["t1"] for s in comp), "dwell": round(dw, 1),
                       "priority": 0 if len(comp) > 1 else (1 if dw >= 30 else 2)})
    buckets = collections.defaultdict(list)
    for s in glance:
        buckets[int(s["t0"] // o["bucket_s"])].append(s)
    for k in sorted(buckets):
        ss = sorted(buckets[k], key=lambda s: s["t0"])
        blocks.append({"kind": "glance", "stretches": [s["id"] for s in ss], "visits": [v for s in ss for v in s["visits"]],
                       "t0": ss[0]["t0"], "t1": max(s["t1"] for s in ss), "dwell": round(sum(s["dwell"] for s in ss), 1),
                       "priority": 3})
    blocks.sort(key=lambda b: (b["t0"], b["stretches"][0]))
    for i, b in enumerate(blocks, 1):
        b["id"] = "B%03d" % i
        b["visits"].sort(key=order.index)
    bof = {s: b["id"] for b in blocks for s in b["stretches"]}

    # suggestions between cards: hints only, never grouping
    rank = {"shared identifiers": 0, "same page or file": 0, "same window, near": 1, "same window, far": 2}
    sug = collections.defaultdict(dict)            # block id -> {other id: (reason, gap)}

    def add(a, b, why, gap):
        ba, bb = bof[a], bof[b]
        if ba == bb:
            return
        for x, y in ((ba, bb), (bb, ba)):
            old = sug[x].get(y)
            if old is None or rank[why] < rank[old[0]]:
                sug[x][y] = (why, round(gap))

    for (a, b), why in held_back.items():
        add(a, b, why, gap_of(a, b))
    by_item = collections.defaultdict(list)
    for s in sorted(main, key=lambda s: s["t0"]):
        by_item[s["item"]].append(s)
    for ss in by_item.values():
        for p, q in zip(ss, ss[1:]):
            g = gap_of(p["id"], q["id"])
            add(p["id"], q["id"], "same window, near" if g <= o["near_gap_s"] else "same window, far", g)
    for b in blocks:
        mine = sorted(sug.get(b["id"], {}).items(), key=lambda kv: (rank[kv[1][0]], kv[1][1], kv[0]))[:4]
        b["suggest"] = [{"block": k, "why": w, "gap_s": g} for k, (w, g) in mine]
        b["apps"] = [a for a, _ in collections.Counter(visits[v]["app"] for v in b["visits"]).most_common(3)]

    return {"schema": 1, "options": o, "fingerprint": fingerprint(records), "visit_order": order,
            "n_visits": len(order), "n_stretches": len(stretches), "blocks": blocks, "visits": visits}


def fingerprint(records):
    body = sorted((r["id"], r["t_start"], r["t_end"]) for r in records)
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def read_records(dataset_dir):
    recs, _ = ai.read_folder(os.path.join(dataset_dir, "moments"))
    return recs


# ---------------------------------------------------------------- state = replay of the event log

class AnnotationError(ValueError):
    pass


def initial_state(proposal):
    blocks = {}
    for b in proposal["blocks"]:
        blocks[b["id"]] = {"id": b["id"], "visits": list(b["visits"]), "status": "open", "activity": "", "project": "",
                           "origin": "proposed", "from": []}
    return {"blocks": blocks, "next": len(proposal["blocks"]) + 1}


def _label_of(blocks):
    """The label a merged/split card inherits: the shared label if all agree, otherwise open."""
    keys = {(b["status"], b["activity"], b["project"]) for b in blocks}
    if len(keys) == 1:
        s, a, p = next(iter(keys))
        return s, a, p
    return "open", "", ""


def apply_event(state, ev_, proposal):
    """Apply one event to a state (mutates, validates first). Raises AnnotationError and leaves the state alone."""
    st = copy.deepcopy(state)
    blocks, op = st["blocks"], ev_.get("op")
    order = {v: i for i, v in enumerate(proposal["visit_order"])}

    def get(bid):
        if bid not in blocks:
            raise AnnotationError(f"no such card {bid}")
        return blocks[bid]

    def new_block(vs, parts, label):
        bid = "B%03d" % st["next"]
        st["next"] += 1
        s, a, p = label
        blocks[bid] = {"id": bid, "visits": sorted(vs, key=order.get), "status": s, "activity": a, "project": p,
                       "origin": op, "from": parts}
        return bid

    if op == "label":
        status = ev_.get("status")
        if status not in STATUSES:
            raise AnnotationError(f"bad status {status!r}")
        act = " ".join(str(ev_.get("activity") or "").split())
        if status == "activity" and (not act or act.lower() in (NOISE, "noise", "none", "n/a")):
            raise AnnotationError("an Activity needs a name (use Unrelated for noise)")
        ids = list(ev_.get("blocks") or [])
        if not ids:
            raise AnnotationError("no cards selected")
        for bid in ids:
            b = get(bid)
            b["status"] = status
            b["activity"] = act if status == "activity" else ""
            if "project" in ev_ and status == "activity":
                b["project"] = " ".join(str(ev_.get("project") or "").split())
            if status != "activity":
                b["project"] = ""
    elif op == "merge":
        ids = list(dict.fromkeys(ev_.get("blocks") or []))
        if len(ids) < 2:
            raise AnnotationError("merge needs at least two cards")
        parts = [get(i) for i in ids]
        vs = [v for b in parts for v in b["visits"]]
        label = _label_of(parts)
        for i in ids:
            del blocks[i]
        new_block(vs, ids, label)
    elif op == "split":
        b = get(ev_.get("block"))
        move = list(dict.fromkeys(ev_.get("visits") or []))
        if not move or not set(move) <= set(b["visits"]) or len(move) == len(b["visits"]):
            raise AnnotationError("split needs some, but not all, of the card's visits")
        keep = [v for v in b["visits"] if v not in set(move)]
        label = (b["status"], b["activity"], b["project"])
        del blocks[b["id"]]
        new_block(keep, [b["id"]], label)
        new_block(move, [b["id"]], label)
    else:
        raise AnnotationError(f"unknown operation {op!r}")
    return st


def replay(proposal, events):
    """Events (oldest first) -> state. An `undo` event cancels the latest still-applied event."""
    applied = []
    for e in events:
        if e.get("op") == "undo":
            if applied:
                applied.pop()
        else:
            applied.append(e)
    st = initial_state(proposal)
    for e in applied:
        st = apply_event(st, e, proposal)
    return st


def events_path(folder):
    return os.path.join(folder, "events.jsonl")


def read_events(folder):
    p = events_path(folder)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_event(folder, proposal, event):
    """Validate against the current state, then append (never rewrite). Returns the new state."""
    events = read_events(folder)
    e = {k: v for k, v in event.items() if k != "ts"}
    if e.get("op") == "undo":
        live = 0
        for x in events:
            live += -1 if x.get("op") == "undo" else 1
        if live <= 0:
            raise AnnotationError("nothing to undo")
    else:
        apply_event(replay(proposal, events), e, proposal)
    e["ts"] = round(time.time(), 3)
    fd = os.open(events_path(folder), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(e, sort_keys=True) + "\n")
    return replay(proposal, events + [e])


def progress(state):
    c = collections.Counter(b["status"] for b in state["blocks"].values())
    nv = collections.Counter()
    for b in state["blocks"].values():
        nv[b["status"]] += len(b["visits"])
    return {"cards": dict(c), "visits": dict(nv)}


def activity_names(state):
    return sorted({b["activity"] for b in state["blocks"].values() if b["status"] == "activity"})


# ---------------------------------------------------------------- export

def human_activities(state, proposal):
    """{name: {"visits": [...], "cards": [...], "project": str}} in time order. One name = one Activity, however many
    cards (and however far apart in time) carry it. Conflicting Project values for one Activity are all listed."""
    order = {v: i for i, v in enumerate(proposal["visit_order"])}
    acts = {}
    for b in sorted(state["blocks"].values(), key=lambda b: order[b["visits"][0]]):
        if b["status"] != "activity":
            continue
        a = acts.setdefault(b["activity"], {"visits": [], "cards": [], "projects": []})
        a["visits"] += b["visits"]
        a["cards"].append({"id": b["id"], "origin": b["origin"], "from": b["from"], "visits": b["visits"]})
        if b["project"] and b["project"] not in a["projects"]:
            a["projects"].append(b["project"])
    for a in acts.values():
        a["visits"].sort(key=order.get)
    return acts


def export(folder, dataset_dir, out_dir=None):
    """Write activities.json, provenance.json and dataset/ (moments copy + units.csv). Original files are only read."""
    proposal = load_proposal(folder)
    events = read_events(folder)
    state = replay(proposal, events)
    recs = read_records(dataset_dir)
    if fingerprint(recs) != proposal["fingerprint"]:
        raise AnnotationError("the dataset changed since the proposal was made; make a new annotation folder")
    out = out_dir or folder
    os.makedirs(out, mode=0o700, exist_ok=True)
    visit_label = {}
    for b in state["blocks"].values():
        for v in b["visits"]:
            visit_label[v] = (b["status"], b["activity"])
    acts = human_activities(state, proposal)
    doc = {"schema": 1, "note": "Activity names are the ground truth. `projects` is separate context and is never scored.",
           "activities": {k: {"visits": a["visits"], "projects": a["projects"], "cards": a["cards"]} for k, a in sorted(acts.items())},
           "unrelated_visits": sorted((v for v, (s, _) in visit_label.items() if s == "unrelated"), key=proposal["visit_order"].index),
           "uncertain_visits": sorted((v for v, (s, _) in visit_label.items() if s == "uncertain"), key=proposal["visit_order"].index),
           "unreviewed_visits": sorted((v for v, (s, _) in visit_label.items() if s == "open"), key=proposal["visit_order"].index)}
    _write_json(os.path.join(out, "activities.json"), doc)
    ds = os.path.join(out, "dataset")
    mdir = os.path.join(ds, "moments")
    os.makedirs(mdir, mode=0o700, exist_ok=True)
    src = os.path.join(dataset_dir, "moments")
    for fn in sorted(os.listdir(src)):
        if fn.endswith(".jsonl"):
            shutil.copyfile(os.path.join(src, fn), os.path.join(mdir, fn))
    gold_rows, gold_n = [], collections.Counter()
    by_moment = {}
    for v, info in proposal["visits"].items():
        for m in info["moments"]:
            by_moment[m] = v
    for r in sorted(recs, key=lambda r: (r.get("t_start", 0), r.get("seq", 0))):
        s, a = visit_label.get(by_moment.get(r["id"]), ("open", ""))
        act = a if s == "activity" else (NOISE if s == "unrelated" else "")
        gold_n[s] += 1
        gold_rows.append({"unit_id": r["id"], "visit_id": r.get("visit_id") or "", "moment_id": r["id"],
                          "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t_start"])), "dur_s": round(r["t_end"] - r["t_start"]),
                          "app": r.get("app") or "", "where": r.get("url") or r.get("window") or "", "item_id": r.get("item_id") or "",
                          "what": "", "activity": act, "also": "", "unsure": "", "note": "uncertain" if s == "uncertain" else ""})
    fd = os.open(os.path.join(ds, "units.csv"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["unit_id", "visit_id", "moment_id", "start", "dur_s", "app", "where", "item_id", "what",
                                           "activity", "also", "unsure", "note"])
        w.writeheader()
        w.writerows(gold_rows)
    _write_json(os.path.join(ds, "dataset.json"), {"name": os.path.basename(os.path.normpath(folder)) + "-human"})
    prov = {"schema": 1, "source_dataset": os.path.abspath(dataset_dir), "dataset_fingerprint": proposal["fingerprint"],
            "proposal_options": proposal["options"], "proposal_sha": _sha(proposal),
            "events": len(events), "events_sha": _sha(events), "state_sha": _sha(state["blocks"]),
            "progress": progress(state), "moments_by_status": dict(gold_n),
            "uses_inference_output": False,
            "how_exported": "visit decisions are copied to every moment of the visit; unrelated -> '-', uncertain/open -> blank (left out of scores)"}
    _write_json(os.path.join(out, "provenance.json"), prov)
    return {"activities": len(acts), "dataset": ds, "progress": prov["progress"], "moments_by_status": dict(gold_n)}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _write_json(path, doc):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")


def proposal_path(folder):
    return os.path.join(folder, "proposal.json")


def load_proposal(folder):
    with open(proposal_path(folder), encoding="utf-8") as fh:
        return json.load(fh)


def init_folder(folder, dataset_dir, opts=None):
    """Create the annotation folder with a proposal snapshot, or reuse the existing one if it matches the dataset."""
    recs = read_records(dataset_dir)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.path.exists(proposal_path(folder)):
        p = load_proposal(folder)
        if p["fingerprint"] != fingerprint(recs):
            raise AnnotationError("this annotation folder was made for a different dataset")
        return p, False
    p = propose(recs, opts)
    _write_json(proposal_path(folder), p)
    return p, True


def _headline(vs):
    """The most recognisable label for a set of visits: the longest-dwelling title that is not just the app's name."""
    best = {}
    for v in vs:
        t = (v["title"] or v["where"] or "").strip()
        if t and t.lower() != (v["app"] or "").lower():
            best[t] = best.get(t, 0) + v["dwell"]
    return max(best, key=lambda t: (best[t], t)) if best else ""


def _near(proposal, vid, step):
    i = proposal["visit_order"].index(vid) + step
    if 0 <= i < len(proposal["visit_order"]):
        return proposal["visits"][proposal["visit_order"][i]]
    return None


def view(proposal, state):
    """What the page shows: cards with their visits resolved, a readable headline, and what was just before and after."""
    cards = []
    pb = {b["id"]: b for b in proposal["blocks"]}
    for b in state["blocks"].values():
        vs = [proposal["visits"][v] for v in b["visits"]]
        t0, t1 = min(v["t0"] for v in vs), max(v["t1"] for v in vs)
        words = []
        for v in vs:
            for w in v["terms"]:
                if w not in words:
                    words.append(w)
        summary = next((v["summary"] for v in sorted(vs, key=lambda v: -v["dwell"]) if v["summary"]), "")
        head = _headline(vs)
        inside = set(b["visits"])
        before = after = None
        for v in vs:                              # nearest visits of the day that are not in this card
            for step in (-1, 1):
                j, x = step, _near(proposal, v["id"], step)
                while x is not None and x["id"] in inside:
                    j += step
                    x = _near(proposal, v["id"], j)
                if x is None:
                    continue
                if step < 0 and x["t1"] <= t0 and (before is None or x["t1"] > before["t1"]):
                    before = x
                if step > 0 and x["t0"] >= t1 and (after is None or x["t0"] < after["t0"]):
                    after = x
        ctx = lambda x, d: None if x is None else {"app": x["app"], "title": "" if (x["title"] or "").lower() == (x["app"] or "").lower() else (x["title"] or x["where"]), "t": x["t0"], "gap_s": round(abs(d))}
        card = {"id": b["id"], "status": b["status"], "activity": b["activity"], "project": b["project"], "origin": b["origin"],
                "t0": t0, "t1": t1, "dwell": round(sum(v["dwell"] for v in vs)),
                "apps": [a for a, _ in collections.Counter(v["app"] for v in vs).most_common(3)], "visits": vs,
                "headline": head, "words": words[:12], "summary": summary,
                "readable": bool(head or words or summary),
                "before": ctx(before, t0 - before["t1"]) if before else None, "after": ctx(after, after["t0"] - t1) if after else None}
        if b["id"] in pb and pb[b["id"]]["visits"] == b["visits"]:
            card["kind"], card["priority"], card["suggest"] = pb[b["id"]]["kind"], pb[b["id"]]["priority"], pb[b["id"]]["suggest"]
        else:
            card["kind"] = "edited"
            card["priority"] = 0
            card["suggest"] = []
        cards.append(card)
    for c in cards:                               # suggestions that point at cards that no longer exist are dropped
        c["suggest"] = [s for s in c["suggest"] if s["block"] in state["blocks"]]
    cards.sort(key=lambda c: c["t0"])
    return {"cards": cards, "names": activity_names(state), "progress": progress(state), "n_visits": proposal["n_visits"],
            "n_stretches": proposal["n_stretches"], "divider_cos": proposal["options"]["divider_cos"],
            "link_gap_s": proposal["options"]["link_gap_s"]}

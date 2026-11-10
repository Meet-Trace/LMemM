"""LMemM - how good is a grouping of moments into activities? Plain functions, no UI, no models.

Step 2, PR 1 (docs/adr/0002-moments-and-evaluation.md). This is the tooling that comes BEFORE any inference:
you label real sessions, and every later system (and the baselines below) is scored against your labels.

    unit    one row you label: a visit (legacy sessions / fixtures) or a moment. A unit dict has
            unit_id, t_start, t_end, dur_s, app, item_id, visit_id, text.
    gold    {unit_id: set of activity labels}. A unit with an empty set is NOISE (belongs to nothing);
            a unit missing from gold, or None, is UNLABELLED and left out of every score (and counted).
    pred    {unit_id: cluster id}. Any hashable. A system that has no opinion about a unit leaves it out
            (it is then its own singleton).

Two units are TOGETHER when their gold label sets share a label. Noise is together with nothing.

Levels. The primary annotation unit is the MOMENT (level "moment"): a label may span many moments and many
visits, and a moment may carry two labels (`also`). Visit-level units (level "visit": stored sessions and the
invented fixture days) are a legacy, coarser view; they are scored the same way but are never mixed with, or
compared to, moment-level results.

Primary scores leave out UNSURE units (their count is reported). `inclusive` repeats the headline numbers with
them in. Dwell-weighted numbers are supplementary.
"""

import csv
import io
import itertools
import json
import os
from datetime import datetime

SHORT_S = 120.0            # an intervening visit this short is "a short visit" for the sandwich test
SITTING_GAP = 300.0        # same gap project_reasoner uses for a sitting

SHEET_UNITS = ["unit_id", "visit_id", "moment_id", "start", "dur_s", "app", "where", "item_id", "what", "activity", "also", "unsure", "note"]
SHEET_ACTIVITIES = ["label", "objective", "outcome", "started", "ended", "notes"]
INSTRUCTIONS = """HOW TO LABEL (units.csv)

Unit. Each row is one MOMENT: a stretch of one visit where nothing material changed (level: {level}).
One activity usually spans many rows, across apps and across separate visits. visit_id groups the rows of a
visit; moment_id is the row's own id (unit_id).{legacy}

activity   A short name you choose, e.g. paper, invoices, trip. Use the same name every time for the same piece
           of work, also when you come back to it later or after other work in between.
           '-' means noise: it belongs to no activity (idle browsing, a notification, a look at the clock).
           Leave blank to skip a row: it is left out of every score and counted as unlabelled.
also       Only for concurrent work: a row that truly serves TWO activities (reading a paper while writing the
           invoice that cites it). Put the second name here; several can be separated with ';'. Do not use it
           to hedge: if you are not sure, use `unsure`.
unsure     Put Y when you could not tell what you were doing in this row, or which activity it belongs to.
           Unsure rows are excluded from the main scores and reported as a count (inclusive scores are shown
           separately).
note       Anything you want to remember. Never scored.

activities.csv is optional context for later evaluation: one row per activity with its objective, outcome and
start/end if you want. An objective is NOT needed to score grouping.
"""
NOISE_WORDS = {"-", "noise", "none", "n/a"}


# ---------------------------------------------------------------- units

def _ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v)).timestamp()


def units_from_day(day):
    """Units (one per timeline event) and gold from a labelled day (tests/fixtures/project_grouping/*.json).
    Gold label = the item's "Project/Workstream" paths; an item with none is noise."""
    items = day["items"]
    units, gold = [], {}
    for i, ev in enumerate(day["timeline"]):
        it = items.get(ev["item"], {})
        uid = f"e{i:03d}"
        a, b = _ts(ev["from"]), _ts(ev["to"])
        units.append({"unit_id": uid, "t_start": a, "t_end": b, "dur_s": max(0.0, b - a), "app": it.get("app"),
                      "item_id": ev["item"], "visit_id": uid, "moment_id": None, "level": "visit",
                      "text": it.get("title") or it.get("doing") or ev["item"]})
        gold[uid] = set(it.get("label") or [])
    return units, gold


def units_from_moments(records):
    """Units from stored moments (moment_store.MomentStore.read). One unit per moment."""
    units = []
    for r in sorted(records, key=lambda r: (r.get("t_start", 0), r.get("seq", 0))):
        d = r.get("digest") or {}
        units.append({"unit_id": r["id"], "t_start": r["t_start"], "t_end": r["t_end"],
                      "dur_s": max(0.0, r["t_end"] - r["t_start"]), "app": r.get("app"),
                      "item_id": r.get("item_id"), "visit_id": r.get("visit_id"), "moment_id": r["id"],
                      "level": "moment", "where": r.get("url") or r.get("window") or "",
                      "text": " | ".join(d.get("lines") or []) or ", ".join((d.get("terms") or [])[:6])})
    return units


def units_from_session(doc, items=None):
    """Units from a stored session file (memory/sessions/*.json): one per timeline event."""
    items = items or {}
    units = []
    for i, ev in enumerate(doc.get("timeline") or doc.get("events") or []):
        it = items.get(ev.get("item"), {})
        try:
            a, b = _ts(ev["from"]), _ts(ev["to"])
        except (KeyError, ValueError):
            continue
        uid = f"s{i:03d}"
        units.append({"unit_id": uid, "t_start": a, "t_end": b, "dur_s": max(0.0, b - a),
                      "app": ev.get("app") or it.get("app"), "item_id": ev.get("item"), "visit_id": uid,
                      "moment_id": None, "level": "visit", "text": it.get("title") or ev.get("doing") or ""})
    return units


# ---------------------------------------------------------------- annotation sheet

def _hms(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def write_sheet(units, directory):
    """Write units.csv and activities.csv for you to fill in. Files are owner-only. Returns the two paths."""
    levels = {u.get("level", "moment") for u in units}
    if len(levels) > 1:
        raise ValueError("a sheet holds one level of unit; moment-level and visit-level are never mixed")
    level = levels.pop() if levels else "moment"
    os.makedirs(directory, mode=0o700, exist_ok=True)
    legacy = "" if level == "moment" else ("\nLEGACY visit-level sheet: each row is a whole visit from a stored session, a coarser unit than a "
                                          "moment. Its scores are not comparable with moment-level ones.")
    ip = os.path.join(directory, "HOW_TO_LABEL.txt")
    fd = os.open(ip, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(INSTRUCTIONS.format(level=level, legacy=legacy))
    up, ap = os.path.join(directory, "units.csv"), os.path.join(directory, "activities.csv")
    for path, header, rows in (
            (up, SHEET_UNITS, [{"unit_id": u["unit_id"], "visit_id": u.get("visit_id") or "", "moment_id": u.get("moment_id") or "",
                                "start": _hms(u["t_start"]), "dur_s": round(u["dur_s"]),
                                "app": u.get("app") or "", "where": u.get("where") or "",
                                "item_id": u.get("item_id") or "", "what": (u.get("text") or "")[:200],
                                "activity": "", "also": "", "unsure": "", "note": ""} for u in units]),
            (ap, SHEET_ACTIVITIES, [])):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
    return up, ap


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_sheet(directory):
    """Read what you filled in. -> (gold, unsure, problems). An objective is never required (activities.csv is
    context for later evaluation, not an input to grouping scores). Blank `activity` = unlabelled; "-"/"noise" = noise;
    `also` adds a second label to a unit that belongs to two. Problems are listed, never guessed around."""
    rows = _read_csv(os.path.join(directory, "units.csv"))
    gold, unsure, problems, seen = {}, set(), [], set()
    for r in rows:
        uid = (r.get("unit_id") or "").strip()
        if uid in seen:
            problems.append(f"duplicate unit_id {uid}")
        seen.add(uid)
        a = (r.get("activity") or "").strip()
        if not a:
            continue                                   # unlabelled
        labels = set()
        for raw in [a] + [x for x in (r.get("also") or "").split(";")]:
            raw = raw.strip()
            if raw and raw.lower() not in NOISE_WORDS:
                labels.add(raw)
        if a.lower() in NOISE_WORDS and labels:
            problems.append(f"{uid}: noise with extra labels")
        gold[uid] = labels
        if (r.get("unsure") or "").strip().lower() in ("y", "yes", "1", "x"):
            unsure.add(uid)
    return gold, unsure, problems


# ---------------------------------------------------------------- relations

def together(gold, a, b):
    """True/False when both are labelled, else None."""
    ga, gb = gold.get(a), gold.get(b)
    if ga is None or gb is None:
        return None
    return bool(ga & gb)


def _cluster(pred, uid):
    return pred.get(uid, ("__single__", uid))


def _f1(p, r):
    return None if p is None or r is None else (0.0 if p + r == 0 else 2 * p * r / (p + r))


def _ratio(a, b):
    return None if not b else a / b


def pairwise(units, gold, pred, weighted=False, cross_app=False):
    """Pair precision / recall / F1 over labelled units. E3: no predicted pairs -> precision None."""
    lab = [u for u in units if gold.get(u["unit_id"]) is not None]
    tp = fp = fn = 0.0
    for a, b in itertools.combinations(lab, 2):
        if cross_app and (a.get("app") == b.get("app")):
            continue
        w = (a["dur_s"] * b["dur_s"]) if weighted else 1.0
        t = bool(gold[a["unit_id"]] & gold[b["unit_id"]])
        p = _cluster(pred, a["unit_id"]) == _cluster(pred, b["unit_id"])
        if p and t:
            tp += w
        elif p:
            fp += w
        elif t:
            fn += w
    prec, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"precision": prec, "recall": rec, "f1": _f1(prec, rec), "pairs_true": tp + fn, "pairs_pred": tp + fp}


def bcubed(units, gold, pred):
    """B-cubed precision/recall, multi-label reading: a unit's gold class is every unit it is TOGETHER with
    (shares at least one label with) plus itself, so classes may overlap. Per unit: precision = share of its
    predicted cluster that is in its class; recall = share of its class that is in its predicted cluster.
    A partition cannot place a two-label unit with both of its activities' units AND keep those apart, so
    with overlapping labels the best possible score is below 1.0 (see tests)."""
    lab = [u["unit_id"] for u in units if gold.get(u["unit_id"]) is not None]
    if not lab:
        return {"precision": None, "recall": None, "f1": None}
    ps, rs = [], []
    for i in lab:
        same = [j for j in lab if _cluster(pred, j) == _cluster(pred, i)]
        tog = [j for j in lab if j == i or gold[i] & gold[j]]
        ok = [j for j in same if j == i or gold[i] & gold[j]]
        ps.append(len(ok) / len(same))
        rs.append(len(ok) / len(tog))
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)
    return {"precision": p, "recall": r, "f1": _f1(p, r)}


def fragmentation(units, gold, pred):
    """Mean number of predicted clusters each gold activity is spread over (1.0 is perfect)."""
    per = {}
    for u in units:
        for l in gold.get(u["unit_id"]) or ():
            per.setdefault(l, set()).add(_cluster(pred, u["unit_id"]))
    return (sum(len(v) for v in per.values()) / len(per)) if per else None


def impure_clusters(units, gold, pred):
    """Share of predicted clusters (2+ labelled units) that contain ANY pair that is not together. A coarse
    cluster-level view; `wrong_pair_share` is the pair-level one."""
    groups = {}
    for u in units:
        if gold.get(u["unit_id"]) is not None:
            groups.setdefault(_cluster(pred, u["unit_id"]), []).append(u["unit_id"])
    multi = [g for g in groups.values() if len(g) > 1]
    if not multi:
        return None
    bad = sum(1 for g in multi if any(not gold[a] & gold[b] for a, b in itertools.combinations(g, 2)))
    return bad / len(multi)


def wrong_pair_share(units, gold, pred):
    """Share of the pairs the grouping put together that are wrong (1 - pairwise precision). None if no pairs."""
    p = pairwise(units, gold, pred)["precision"]
    return None if p is None else 1.0 - p


def switches(units, gold, pred):
    """At every change of app/item between consecutive labelled units: 'same activity?' answered right?"""
    us = sorted(units, key=lambda u: u["t_start"])
    right = total = 0
    for a, b in zip(us, us[1:]):
        if (a.get("app"), a.get("item_id")) == (b.get("app"), b.get("item_id")):
            continue
        t = together(gold, a["unit_id"], b["unit_id"])
        if t is None:
            continue
        total += 1
        right += (_cluster(pred, a["unit_id"]) == _cluster(pred, b["unit_id"])) == t
    return {"accuracy": _ratio(right, total), "n": total}


def returns(units, gold, pred):
    """Return events and whether the grouping kept them with the earlier work.

    Over the labelled, non-noise units in time order, a RUN of label L is a maximal stretch of consecutive units
    that carry L (a unit that carries L and other labels continues L's run; concurrent activities do not break
    each other). A RETURN is a run of L that comes after some other unit(s) that did not carry L, i.e. a run
    after the first. A unit that returns to several labels at once is ONE event, and it is correct if it is
    grouped with at least one unit of an earlier run of any label it returns to. Staying on L is never an event."""
    us = [u for u in sorted(units, key=lambda u: u["t_start"]) if gold.get(u["unit_id"])]
    events = {}                                          # unit index -> earlier units of the labels it returns to
    for l in {l for u in us for l in gold[u["unit_id"]]}:
        seen_run, prev_has = [], False
        for i, u in enumerate(us):
            has = l in gold[u["unit_id"]]
            if has and not prev_has and seen_run:
                events.setdefault(i, []).extend(seen_run)
            if has:
                seen_run.append(i)
            prev_has = has
    right = sum(1 for i, earlier in events.items()
                if any(_cluster(pred, us[j]["unit_id"]) == _cluster(pred, us[i]["unit_id"]) for j in earlier))
    return {"accuracy": _ratio(right, len(events)), "n": len(events)}


def sandwiches(units, gold, pred, short_s=SHORT_S):
    """A, X, A where X is a short visit to something else. Reports BOTH failure directions so neither
    'never absorb' nor 'always absorb' can look good:
        absorbed_wrong  of the X that are NOT with A in gold, the share predicted with A
        split_wrong     of the X that ARE with A in gold, the share predicted apart from A"""
    us = sorted(units, key=lambda u: u["t_start"])
    n_diff = n_same = abs_wrong = split_wrong = 0
    for i in range(len(us) - 2):
        a, x, c = us[i], us[i + 1], us[i + 2]
        if a.get("item_id") != c.get("item_id") or x.get("item_id") == a.get("item_id") or x["dur_s"] > short_s:
            continue
        t = together(gold, a["unit_id"], x["unit_id"])
        if t is None or together(gold, a["unit_id"], c["unit_id"]) is not True:
            continue
        p = _cluster(pred, a["unit_id"]) == _cluster(pred, x["unit_id"])
        if t:
            n_same += 1
            split_wrong += not p
        else:
            n_diff += 1
            abs_wrong += p
    return {"n": n_diff + n_same, "absorbed_wrong": _ratio(abs_wrong, n_diff), "split_wrong": _ratio(split_wrong, n_same),
            "n_not_with_a": n_diff, "n_with_a": n_same}


def coverage(units, gold, unsure=()):
    """Counts. `labelled` excludes unsure units (they are left out of the primary scores)."""
    unsure = set(unsure)
    labelled = [u for u in units if gold.get(u["unit_id"]) is not None and u["unit_id"] not in unsure]
    noise = [u for u in labelled if not gold[u["unit_id"]]]
    return {"units": len(units), "labelled": len(labelled), "noise": len(noise),
            "unlabelled": len([u for u in units if gold.get(u["unit_id"]) is None]),
            "unsure": len([u for u in units if gold.get(u["unit_id"]) is not None and u["unit_id"] in unsure]),
            "activities": len({l for u in labelled for l in gold[u["unit_id"]]})}


def ece(confidences, correct, bins=10):
    """Expected calibration error for later PRs: |mean confidence - accuracy| averaged over bins."""
    if not confidences:
        return None
    total, err = len(confidences), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confidences) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if idx:
            err += len(idx) / total * abs(sum(confidences[i] for i in idx) / len(idx) - sum(correct[i] for i in idx) / len(idx))
    return err


def _metrics(units, gold, pred, short_s):
    return {"pairwise": pairwise(units, gold, pred), "cross_app": pairwise(units, gold, pred, cross_app=True),
            "bcubed": bcubed(units, gold, pred), "fragmentation": fragmentation(units, gold, pred),
            "impure_clusters": impure_clusters(units, gold, pred), "wrong_pair_share": wrong_pair_share(units, gold, pred),
            "switches": switches(units, gold, pred), "returns": returns(units, gold, pred),
            "sandwich": sandwiches(units, gold, pred, short_s)}


def evaluate(units, gold, pred, unsure=(), short_s=SHORT_S):
    """Every metric for one grouping. The top level is PRIMARY: unsure units are left out and counted in
    `coverage`. `inclusive` holds the same headline numbers with unsure units kept in; `supplementary` holds the
    dwell-weighted pairwise numbers."""
    unsure = set(unsure)
    primary = {k: v for k, v in gold.items() if k not in unsure}
    out = _metrics(units, primary, pred, short_s)
    out["coverage"] = coverage(units, gold, unsure)
    out["inclusive"] = _metrics(units, gold, pred, short_s) if unsure else None
    out["supplementary"] = {"pairwise_dwell": pairwise(units, primary, pred, weighted=True)}
    return out


# ---------------------------------------------------------------- baselines (structure only, no models)

def sittings(units, gap=SITTING_GAP):
    """Units split wherever more than `gap` seconds pass with nothing."""
    out, n, end = {}, 0, None
    for u in sorted(units, key=lambda u: u["t_start"]):
        if end is not None and u["t_start"] - end > gap:
            n += 1
        out[u["unit_id"]] = f"sit{n}"
        end = max(end or 0, u["t_end"])
    return out


def absorb_short(units, short_s=SHORT_S):
    """REFERENCE ONLY: A, short X, A -> X joins A. Included to measure what an adjacency rule costs, never as a design."""
    us = sorted(units, key=lambda u: u["t_start"])
    pred = {u["unit_id"]: u["item_id"] for u in us}
    for i in range(len(us) - 2):
        a, x, c = us[i], us[i + 1], us[i + 2]
        if a["item_id"] == c["item_id"] and x["item_id"] != a["item_id"] and x["dur_s"] <= short_s:
            pred[x["unit_id"]] = a["item_id"]
    return pred


def baselines(units):
    """{name: {unit_id: cluster}} for the structural baselines."""
    return {
        "B1 each visit": {u["unit_id"]: u["visit_id"] or u["unit_id"] for u in units},
        "B2 each item": {u["unit_id"]: u["item_id"] for u in units},
        "B3 sittings (300 s)": sittings(units),
        "B4 each app": {u["unit_id"]: u.get("app") for u in units},
        "B5 whole session": {u["unit_id"]: "all" for u in units},
        "B7 absorb short (reference only)": absorb_short(units),
    }


# ---------------------------------------------------------------- report

def _pct(v):
    return "  -  " if v is None else f"{100 * v:4.0f}%"


def report(name, res, supplementary=False):
    """One printed line of headline numbers (primary: unsure units left out)."""
    pw, ca, bc = res["pairwise"], res["cross_app"], res["bcubed"]
    sw, rt, sa = res["switches"], res["returns"], res["sandwich"]
    frag = res["fragmentation"]
    line = (f"{name:34s} P {_pct(pw['precision'])} R {_pct(pw['recall'])} F1 {_pct(pw['f1'])} | "
            f"x-app R {_pct(ca['recall'])} | b-cubed F1 {_pct(bc['f1'])} | frag {'-' if frag is None else f'{frag:.1f}'} | "
            f"wrong pairs {_pct(res['wrong_pair_share'])} impure clusters {_pct(res['impure_clusters'])} | "
            f"switch {_pct(sw['accuracy'])} | return {_pct(rt['accuracy'])} (n {rt['n']}) | "
            f"sandwich absorbed {_pct(sa['absorbed_wrong'])} split {_pct(sa['split_wrong'])}")
    if supplementary:
        line += f" || dwell-weighted F1 {_pct(res['supplementary']['pairwise_dwell']['f1'])}"
        if res.get("inclusive"):
            line += f" | with unsure F1 {_pct(res['inclusive']['pairwise']['f1'])}"
    return line


def limits(cov):
    """Plain statements about how far the numbers can be trusted."""
    out = []
    if cov["labelled"] < 30:
        out.append(f"only {cov['labelled']} labelled units: indicative, not statistically meaningful")
    if cov["activities"] < 3:
        out.append("fewer than 3 activities: grouping metrics are easy to game")
    if cov["unlabelled"]:
        out.append(f"{cov['unlabelled']} unlabelled units left out of every score")
    if cov["unsure"]:
        out.append(f"{cov['unsure']} unsure units left out of the primary scores (inclusive numbers shown with --supplementary)")
    return out


def dump_json(obj):
    return json.dumps(obj, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o))

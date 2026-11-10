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
"""

import csv
import io
import itertools
import json
import os
from datetime import datetime

SHORT_S = 120.0            # an intervening visit this short is "a short visit" for the sandwich test
SITTING_GAP = 300.0        # same gap project_reasoner uses for a sitting

SHEET_UNITS = ["unit_id", "start", "dur_s", "app", "where", "item_id", "what", "activity", "also", "unsure", "note"]
SHEET_ACTIVITIES = ["label", "objective", "outcome", "started", "ended", "notes"]
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
                      "item_id": ev["item"], "visit_id": uid,
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
                      "item_id": r.get("item_id"), "visit_id": r.get("visit_id"),
                      "where": r.get("url") or r.get("window") or "",
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
                      "text": it.get("title") or ev.get("doing") or ""})
    return units


# ---------------------------------------------------------------- annotation sheet

def _hms(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def write_sheet(units, directory):
    """Write units.csv and activities.csv for you to fill in. Files are owner-only. Returns the two paths."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    up, ap = os.path.join(directory, "units.csv"), os.path.join(directory, "activities.csv")
    for path, header, rows in (
            (up, SHEET_UNITS, [{"unit_id": u["unit_id"], "start": _hms(u["t_start"]), "dur_s": round(u["dur_s"]),
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
    """Read what you filled in. -> (gold, unsure, problems). Blank `activity` = unlabelled; "-"/"noise" = noise;
    `also` adds a second label to a unit that belongs to two. Problems are listed, never guessed around."""
    rows = _read_csv(os.path.join(directory, "units.csv"))
    acts = {r["label"].strip(): r for r in _read_csv(os.path.join(directory, "activities.csv")) if (r.get("label") or "").strip()}
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
    used = {l for s in gold.values() for l in s}
    for l in sorted(used):
        if l not in acts:
            problems.append(f"activity '{l}' is used but has no row in activities.csv")
        elif not (acts[l].get("objective") or "").strip():
            problems.append(f"activity '{l}' has no objective")
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
    """B-cubed precision/recall with the TOGETHER relation (a unit is together with itself)."""
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


def wrong_merge(units, gold, pred):
    """Share of predicted clusters (2+ labelled units) that hold two units that are NOT together."""
    groups = {}
    for u in units:
        if gold.get(u["unit_id"]) is not None:
            groups.setdefault(_cluster(pred, u["unit_id"]), []).append(u["unit_id"])
    multi = [g for g in groups.values() if len(g) > 1]
    if not multi:
        return None
    bad = sum(1 for g in multi if any(not gold[a] & gold[b] for a, b in itertools.combinations(g, 2)))
    return bad / len(multi)


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
    """When a gold activity resumes after other work: is the first unit of the later run in the same
    cluster as the last unit of the earlier run?"""
    us = [u for u in sorted(units, key=lambda u: u["t_start"]) if gold.get(u["unit_id"])]
    by = {}
    for i, u in enumerate(us):
        for l in gold[u["unit_id"]]:
            by.setdefault(l, []).append(i)
    right = total = 0
    for l, idx in by.items():
        for a, b in zip(idx, idx[1:]):
            if b - a > 1:                                # something else happened in between
                total += 1
                right += _cluster(pred, us[a]["unit_id"]) == _cluster(pred, us[b]["unit_id"])
    return {"accuracy": _ratio(right, total), "n": total}


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
    labelled = [u for u in units if gold.get(u["unit_id"]) is not None]
    noise = [u for u in labelled if not gold[u["unit_id"]]]
    return {"units": len(units), "labelled": len(labelled), "noise": len(noise),
            "unlabelled": len(units) - len(labelled), "unsure": len([u for u in units if u["unit_id"] in unsure]),
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


def evaluate(units, gold, pred, unsure=(), short_s=SHORT_S):
    """Every metric for one grouping."""
    return {"coverage": coverage(units, gold, unsure),
            "pairwise": pairwise(units, gold, pred), "pairwise_dwell": pairwise(units, gold, pred, weighted=True),
            "cross_app": pairwise(units, gold, pred, cross_app=True), "bcubed": bcubed(units, gold, pred),
            "fragmentation": fragmentation(units, gold, pred), "wrong_merge": wrong_merge(units, gold, pred),
            "switches": switches(units, gold, pred), "returns": returns(units, gold, pred),
            "sandwich": sandwiches(units, gold, pred, short_s)}


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


def report(name, res):
    """One printed line of headline numbers."""
    pw, ca, bc = res["pairwise"], res["cross_app"], res["bcubed"]
    sw, rt, sa = res["switches"], res["returns"], res["sandwich"]
    frag = res["fragmentation"]
    return (f"{name:34s} P {_pct(pw['precision'])} R {_pct(pw['recall'])} F1 {_pct(pw['f1'])} | "
            f"x-app R {_pct(ca['recall'])} | b-cubed F1 {_pct(bc['f1'])} | frag {'-' if frag is None else f'{frag:.1f}'} | "
            f"wrong-merge {_pct(res['wrong_merge'])} | switch {_pct(sw['accuracy'])} | return {_pct(rt['accuracy'])} | "
            f"sandwich absorbed {_pct(sa['absorbed_wrong'])} split {_pct(sa['split_wrong'])}")


def limits(cov):
    """Plain statements about how far the numbers can be trusted."""
    out = []
    if cov["labelled"] < 30:
        out.append(f"only {cov['labelled']} labelled units: indicative, not statistically meaningful")
    if cov["activities"] < 3:
        out.append("fewer than 3 activities: grouping metrics are easy to game")
    if cov["unlabelled"]:
        out.append(f"{cov['unlabelled']} unlabelled units left out of every score")
    return out


def dump_json(obj):
    return json.dumps(obj, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o))

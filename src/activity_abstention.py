"""LMemM - evaluation that shows abstention: how much a replay assigned, how right that was, and what it left out.

Step 2, PR 2A (docs/adr/0003-activity-inference-design.md, 13.7 and 14.2). Works on a Result (activity_contract) and
moment-level gold (activity_eval: {moment_id: set of labels}, empty set = noise, missing = unlabelled), so it can
be tested on hand-made results before any inference exists. The existing activity_eval metrics are reused, not changed.

Visit gold, without a majority vote anywhere:
    A visit is PURE when its labelled, non-unsure moments all carry the same label set family, meaning the sets share at
    least one label (an `also` label never breaks purity), or all of them are noise. It is MIXED-GOLD when they do not
    (including a noise moment next to an activity moment). It is UNLABELLED when none of its moments is labelled.
    A pure visit's gold is the labels common to all its labelled moments. Mixed-gold visits get no label of any kind
    and are left out of the primary visit-level scores; they are counted and described separately.
"""

import activity_contract as ac
import activity_eval as ev


def _labelled(visit, gold, unsure):
    return [m for m in visit.moments if gold.get(m["id"]) is not None and m["id"] not in unsure]


def visit_gold(visit, gold, unsure=()):
    """{"kind": pure|mixed|unlabelled, "labels": set, "n": labelled moments} for one visit."""
    ms = _labelled(visit, gold, set(unsure))
    if not ms:
        return {"kind": "unlabelled", "labels": set(), "n": 0}
    sets = [set(gold[m["id"]]) for m in ms]
    common = set.intersection(*sets)
    if all(not s for s in sets) or common:
        return {"kind": "pure", "labels": common, "n": len(ms)}
    return {"kind": "mixed", "labels": set(), "n": len(ms)}


def _units(visits):
    return [{"unit_id": v.visit_id, "t_start": v.t_start, "t_end": v.t_end, "dur_s": v.dwell_s,
             "app": v.moments[0].get("app"), "item_id": v.item_id, "visit_id": v.visit_id, "level": "visit"} for v in visits]


def _rate(n, d):
    return None if not d else n / d


def state_rates(visits, result):
    """Assigned / tentative / ambiguous / unassigned rates over all visits (counts and dwell), plus reasons."""
    rows = {r["visit_id"]: r for r in result["visits"]}
    total = len(visits)
    dwell_total = sum(v.dwell_s for v in visits)
    out = {"visits": total, "counts": {s: 0 for s in ac.STATES}, "rates": {}, "dwell_rates": {}, "reasons": {}}
    dwell = {s: 0.0 for s in ac.STATES}
    for v in visits:
        r = rows[v.visit_id]
        out["counts"][r["state"]] += 1
        dwell[r["state"]] += v.dwell_s
        if r["state"] != ac.ASSIGNED:
            key = f"{r['state']}:{r['reason']}"
            out["reasons"][key] = out["reasons"].get(key, 0) + 1
    for s in ac.STATES:
        out["rates"][s] = _rate(out["counts"][s], total)
        out["dwell_rates"][s] = _rate(dwell[s], dwell_total)
    return out


def coverage(visits, result, eligible_ids=()):
    """Assignment coverage, with and without singleton activities (seeds that no second visit ever joined)."""
    assigned = ac.visit_predictions(result)
    size = {}
    for a in assigned.values():
        size[a] = size.get(a, 0) + 1
    linked = {v for v, a in assigned.items() if size[a] >= 2}
    eligible = set(eligible_ids)
    return {"all": _rate(len(assigned), len(visits)),
            "excluding_singletons": _rate(len(linked), len(visits)),
            "singleton_activities": sum(1 for n in size.values() if n == 1),
            "eligible_visits": len(eligible),
            "eligible_assigned": _rate(len(eligible & set(assigned)), len(eligible)),
            "eligible_linked": _rate(len(eligible & linked), len(eligible))}


def eligible_ids(pure):
    """Pure labelled (non-noise) visits that share a label with at least one other pure visit: a correct join exists."""
    out = set()
    for a, ga in pure.items():
        if ga and any(b != a and ga & gb for b, gb in pure.items()):
            out.add(a)
    return out


def mixed_report(visits, result, gold, unsure, pure_gold):
    """Count, shares and treatment of mixed-gold visits, plus what the assigned ones joined (descriptive only)."""
    rows = {r["visit_id"]: r for r in result["visits"]}
    info = {v.visit_id: visit_gold(v, gold, unsure) for v in visits}
    mixed = [v for v in visits if info[v.visit_id]["kind"] == "mixed"]
    labelled = [v for v in visits if info[v.visit_id]["kind"] != "unlabelled"]
    out = {"count": len(mixed), "share_of_labelled": _rate(len(mixed), len(labelled)),
           "dwell_share": _rate(sum(v.dwell_s for v in mixed), sum(v.dwell_s for v in labelled)),
           "treatment": {s: 0 for s in ac.STATES}, "assigned": []}
    by_act = {}
    for v, a in ac.visit_predictions(result).items():
        if v in pure_gold:
            by_act.setdefault(a, []).append(v)
    for v in mixed:
        r = rows[v.visit_id]
        out["treatment"][r["state"]] += 1
        if r["state"] == ac.ASSIGNED:
            labels = sorted(set().union(*[gold[m["id"]] for m in _labelled(v, gold, set(unsure))]))
            members = by_act.get(r["activity_id"], [])
            covers = {l: any(l in pure_gold[m] for m in members) for l in labels}
            out["assigned"].append({"visit_id": v.visit_id, "activity_id": r["activity_id"], "covers": covers})
    return out


def _changes(visit, gold, unsure):
    """Indices (into visit.moments) of the first moment after each change of gold between labelled neighbours."""
    ms = [(i, set(gold[m["id"]])) for i, m in enumerate(visit.moments)
          if gold.get(m["id"]) is not None and m["id"] not in unsure]
    out = []
    for (i, a), (j, b) in zip(ms, ms[1:]):
        if (a and b and not (a & b)) or (bool(a) != bool(b)):
            out.append(j)
    return out


def shift_report(visits, result, gold, unsure):
    """internal_shift against moment-level gold. Positives are mixed-gold visits among evaluable ones (>= 2 labelled)."""
    rows = {r["visit_id"]: r for r in result["visits"]}
    unsure = set(unsure)
    tp = fp = fn = tn = unverifiable = 0
    missed, false_alarms, hits, tp_ids = [], [], 0, []
    for v in visits:
        flag = rows[v.visit_id].get("internal_shift")
        info = visit_gold(v, gold, unsure)
        evaluable = info["n"] >= 2
        if not evaluable:
            unverifiable += 1 if flag else 0
            continue
        positive = info["kind"] == "mixed"
        if flag and positive:
            tp += 1
            tp_ids.append(v.visit_id)
            idx = {m["id"]: i for i, m in enumerate(v.moments)}
            to = idx.get(flag.get("to_moment_id"))
            if to is not None and any(abs(c - to) <= 1 for c in _changes(v, gold, unsure)):
                hits += 1
        elif flag:
            fp += 1
            false_alarms.append(v.visit_id)
        elif positive:
            fn += 1
            missed.append(v.visit_id)
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "unverifiable_flags": unverifiable,
            "precision": _rate(tp, tp + fp), "detection_rate": _rate(tp, tp + fn),
            "missed": missed, "false_alarms": false_alarms, "boundary_hit_rate": _rate(hits, tp),
            "flag_rate": _rate(sum(1 for r in rows.values() if r.get("internal_shift")), len(rows)),
            "flag_rate_assigned": _rate(sum(1 for r in rows.values() if r.get("internal_shift") and r["state"] == ac.ASSIGNED),
                                        sum(1 for r in rows.values() if r["state"] == ac.ASSIGNED))}


def ceiling(prepared, gold, unsure=()):
    """Moment-level pairwise scores of an ORACLE visit-level method: each visit's moments all take the visit's most
    frequent gold label by dwell (ties: smallest label name). It shows what the visit-level choice itself costs."""
    unsure = set(unsure)
    pred = {}
    for v in prepared.visits:
        weight = {}
        for m in _labelled(v, gold, unsure):
            for l in gold[m["id"]]:
                weight[l] = weight.get(l, 0.0) + max(0.0, m["t_end"] - m["t_start"])
        if weight:
            label = sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            for m in v.moments:
                pred[m["id"]] = label
    units = ev.units_from_moments(prepared.records)
    return ev.evaluate(units, gold, pred, unsure)["pairwise"]


def report(prepared, gold, unsure, result):
    """Everything 13.7 and 14.2 ask for, for one result."""
    unsure = set(unsure)
    visits = prepared.visits
    problems = ac.validate_result(result, {v.visit_id: v.t_start for v in visits})
    if problems:
        raise ValueError("result breaks the contract: " + "; ".join(problems[:3]))
    info = {v.visit_id: visit_gold(v, gold, unsure) for v in visits}
    pure = {k: g["labels"] for k, g in info.items() if g["kind"] == "pure"}
    pure_visits = [v for v in visits if v.visit_id in pure]
    pred = ac.visit_predictions(result)
    scores = ev.evaluate(_units(pure_visits), pure, pred)
    elig = eligible_ids(pure)
    return {
        "visits": {"total": len(visits), "pure": len(pure), "mixed_gold": sum(1 for g in info.values() if g["kind"] == "mixed"),
                   "unlabelled": sum(1 for g in info.values() if g["kind"] == "unlabelled")},
        "states": state_rates(visits, result),
        "coverage": coverage(visits, result, elig),
        "precision_assigned": scores["pairwise"]["precision"],
        "recall_eligible": scores["pairwise"]["recall"],
        "pairwise": scores["pairwise"], "wrong_pair_share": scores["wrong_pair_share"],
        "impure_clusters": scores["impure_clusters"],
        "tentative_rate": state_rates(visits, result)["rates"][ac.TENTATIVE],
        "mixed_gold": mixed_report(visits, result, gold, unsure, pure),
        "internal_shift": shift_report(visits, result, gold, unsure),
        "ceiling": ceiling(prepared, gold, unsure),
    }


def at_equal_coverage(prepared, gold, unsure, result, baselines):
    """Method and baselines scored on the same visits only: pure visits the method assigned. `baselines` maps a
    name to {moment_id: cluster}; a visit takes its first moment's cluster. Abstaining is never rewarded."""
    unsure = set(unsure)
    pred = ac.visit_predictions(result)
    pure = {v.visit_id: visit_gold(v, gold, unsure) for v in prepared.visits}
    keep = [v for v in prepared.visits if v.visit_id in pred and pure[v.visit_id]["kind"] == "pure"]
    g = {v.visit_id: pure[v.visit_id]["labels"] for v in keep}
    units = _units(keep)
    out = {"visits": len(keep), "method": ev.pairwise(units, g, pred)}
    for name, clusters in baselines.items():
        vp = {v.visit_id: clusters[v.moments[0]["id"]] for v in keep if v.moments[0]["id"] in clusters}
        out[name] = ev.pairwise(units, g, vp)
    return out

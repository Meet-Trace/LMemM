"""LMemM - how reliably does the activity inference find coherent Activities in LABELLED real data? (Step 2, PR 2D)

Offline and read-only. Pure Python: no model, no judge, no network, no change to the inference. Labelled datasets go in, a
reproducible JSON result and a short text summary come out.

A DATASET is a folder:
    moments/*.jsonl   Moment records exactly as MomentStore writes them (record with config.MOMENT_LOG, copy the day files)
    units.csv         the labelled sheet (scripts/eval_activities.py sheet ...; fill `activity`, `also`, `unsure`)
    dataset.json      optional {"name": "...", "scenarios": {"<tag>": ["<visit_id>", ...]}} to slice results by your own tags
Nothing else is read. No dataset ships with the repository: synthetic fixtures test this code, they are never a result.

Conventions (reused from activity_abstention, not redefined):
    gold is moment-level; a visit is PURE when its labelled, non-unsure moments share a label (or are all noise), MIXED-GOLD
    when they do not, UNLABELLED when none is labelled. Mixed-gold visits get no label and are counted apart, never scored.
    An `unsure` moment is left out; a visit with only unsure moments is unlabelled. Primary scores use pure visits only.

What is measured (pairs are over pure visits; noise is together with nothing):
    false merge     two visits assigned to ONE activity that the gold keeps apart (a precision error)
    missed merge    two visits the gold puts together that the engine did not join, split by cause:
                    `abstained` (either side was not assigned) or `split` (assigned to two different activities)
    visit outcome   joined_correct / joined_partial / joined_wrong (assigned beside judged visits), alone_ok / alone_missed
                    (assigned but alone), abstained_ok / abstained_missed (not assigned); `missed` means a correct join
                    existed (the visit has a gold partner), `ok` means none did
    scenarios       derived from the gold and the visits (cross_app, single_app, interleaved, returns, short_visits, noise,
                    internal_shift_flagged) plus your dataset.json tags. A scenario counts every pair that TOUCHES one of its
                    visits (at least one endpoint inside), so an error between a scenario visit and an outside visit is counted.
    per activity    visits, assigned share, recall of its own pairs, number of engine activities it is spread over

Outputs hold visit ids, activity ids and the label names you chose. They never hold screen content (no terms, titles, URLs or
lines). The result is deterministic: the same dataset and engine give byte-identical JSON; there is no timestamp.
"""

import dataclasses
import hashlib
import itertools
import json
import os

import activity_abstention as ab
import activity_contract as ac
import activity_eval as ev
import activity_input as ai

MAX_PAIRS = 50
SCENARIOS = ("cross_app", "single_app", "interleaved", "returns", "short_visits", "noise", "internal_shift_flagged")


# ---------------------------------------------------------------- datasets

@dataclasses.dataclass(frozen=True)
class Dataset:
    name: str
    records: list
    gold: dict
    unsure: frozenset
    scenarios: dict                      # tag -> frozenset of visit ids (from dataset.json)
    problems: tuple


def load_dataset(directory):
    """Folder -> Dataset. Problems (a sheet row naming no moment, bad sheet rows) are listed, never guessed around."""
    recs, _ = ai.read_folder(os.path.join(directory, "moments"))
    gold, unsure, problems = ev.load_sheet(directory)
    ids = {r["id"] for r in recs}
    problems = list(problems) + [f"sheet names unknown moment {u}" for u in sorted(set(gold) - ids)]
    name, scenarios = os.path.basename(os.path.normpath(directory)), {}
    meta = os.path.join(directory, "dataset.json")
    if os.path.exists(meta):
        with open(meta, encoding="utf-8") as fh:
            doc = json.load(fh)
        name = str(doc.get("name") or name)
        scenarios = {str(k): frozenset(v) for k, v in (doc.get("scenarios") or {}).items()}
    gold = {k: v for k, v in gold.items() if k in ids}
    return Dataset(name, recs, gold, frozenset(unsure & set(gold)), scenarios, tuple(problems))


def fingerprint(records, gold, unsure):
    """A short hash of what was evaluated (ids, times, labels): two runs with the same fingerprint saw the same data."""
    body = {"moments": sorted((r["id"], r["t_start"], r["t_end"]) for r in records),
            "gold": sorted((k, sorted(v)) for k, v in gold.items()), "unsure": sorted(unsure)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the pair analysis

def _rate(n, d):
    return None if not d else n / d


def _f1(p, r):
    return None if p is None or r is None else (0.0 if p + r == 0 else 2 * p * r / (p + r))


def _tally(pure, pred, touching=None):
    """Pair tallies over pure visits. `touching`: only pairs with at least one endpoint in this set."""
    t = {"tp": 0, "fp": 0, "fn": 0, "fn_abstained": 0, "fn_split": 0, "fp_pairs": [], "fn_pairs": []}
    ids = sorted(pure)
    for a, b in itertools.combinations(ids, 2):
        if touching is not None and a not in touching and b not in touching:
            continue
        gold_together = bool(pure[a] & pure[b])
        joined = a in pred and b in pred and pred[a] == pred[b]
        if joined and gold_together:
            t["tp"] += 1
        elif joined:
            t["fp"] += 1
            t["fp_pairs"].append((a, b))
        elif gold_together:
            t["fn"] += 1
            cause = "fn_abstained" if (a not in pred or b not in pred) else "fn_split"
            t[cause] += 1
            t["fn_pairs"].append((a, b, "abstained" if cause == "fn_abstained" else "split"))
    return t


def _scores(t):
    p, r = _rate(t["tp"], t["tp"] + t["fp"]), _rate(t["tp"], t["tp"] + t["fn"])
    return {"precision": p, "recall": r, "f1": _f1(p, r), "true_pairs": t["tp"] + t["fn"], "predicted_pairs": t["tp"] + t["fp"],
            "false_merges": t["fp"], "missed_merges": t["fn"], "missed_abstained": t["fn_abstained"], "missed_split": t["fn_split"]}


def _outcomes(visits, pure, result):
    """Visit-level outcome counts over pure visits (see the module docstring)."""
    rows = {r["visit_id"]: r for r in result["visits"]}
    pred = ac.visit_predictions(result)
    by_act = {}
    for v, a in pred.items():
        if v in pure:
            by_act.setdefault(a, []).append(v)
    eligible = ab.eligible_ids(pure)
    out = {k: 0 for k in ("joined_correct", "joined_partial", "joined_wrong", "alone_ok", "alone_missed",
                          "abstained_ok", "abstained_missed")}
    for v in sorted(pure):
        if v in pred:
            others = [m for m in by_act[pred[v]] if m != v]
            if not others:
                out["alone_missed" if v in eligible else "alone_ok"] += 1
                continue
            together = sum(1 for m in others if pure[v] & pure[m])
            out["joined_correct" if together == len(others) else "joined_wrong" if together == 0 else "joined_partial"] += 1
        else:
            out["abstained_missed" if v in eligible else "abstained_ok"] += 1
    out["pure_visits"] = len(pure)
    out["eligible_visits"] = len(eligible)
    out["unjudged_in_activities"] = sum(1 for v, a in pred.items() if v not in pure)
    return out


def _outcome_rates(o):
    n = o["pure_visits"]
    good = o["joined_correct"] + o["alone_ok"] + o["abstained_ok"]
    return {"joined_wrongly_or_partly": _rate(o["joined_wrong"] + o["joined_partial"], n),
            "missed_opportunity": _rate(o["alone_missed"] + o["abstained_missed"], n),
            "handled_correctly": _rate(good, n)}


# ---------------------------------------------------------------- scenarios and activities

def _visit_facts(visits, pure):
    out = {}
    for v in visits:
        if v.visit_id in pure:
            out[v.visit_id] = {"app": v.moments[0].get("app"), "t_start": v.t_start, "t_end": v.t_end, "dwell_s": v.dwell_s}
    return out


def derived_scenarios(visits, pure, result, params):
    """{tag: set of visit ids}. Observable only: gold labels, apps, times, dwell, the engine's own internal_shift flag."""
    facts = _visit_facts(visits, pure)
    flag = {r["visit_id"] for r in result["visits"] if r.get("internal_shift")}
    labels = sorted({l for g in pure.values() for l in g})
    members = {l: sorted((v for v in facts if l in pure[v]), key=lambda v: (facts[v]["t_start"], v)) for l in labels}
    sets = {k: set() for k in SCENARIOS}
    for l, vs in members.items():
        if len(vs) < 2:
            continue
        apps = {facts[v]["app"] for v in vs}
        sets["cross_app" if len(apps) > 1 else "single_app"] |= set(vs)
        lo, hi = facts[vs[0]]["t_start"], facts[vs[-1]]["t_start"]
        if any(not (pure[o] & {l}) and lo < facts[o]["t_start"] < hi for o in facts):
            sets["interleaved"] |= set(vs)
        if any(facts[b]["t_start"] - facts[a]["t_end"] > params.t_resume_s for a, b in zip(vs, vs[1:])):
            sets["returns"] |= set(vs)
    sets["short_visits"] = {v for v, f in facts.items() if f["dwell_s"] < params.seed_dwell_s}
    sets["noise"] = {v for v, g in pure.items() if not g}
    sets["internal_shift_flagged"] = {v for v in facts if v in flag}
    return sets


def _scenario_report(name, members, pure, pred, rows, cap):
    members = set(members) & set(pure)
    if not members:
        return {"visits": 0}
    t = _tally(pure, pred, touching=members)
    states = {s: 0 for s in ac.STATES}
    for v in members:
        states[rows[v]["state"]] += 1
    return dict(_scores(t), visits=len(members), states=states,
                abstention_rate=_rate(len(members) - states[ac.ASSIGNED], len(members)),
                false_merge_pairs=[list(p) for p in t["fp_pairs"][:cap]], missed_merge_pairs=[list(p) for p in t["fn_pairs"][:cap]])


def _per_activity(visits, pure, pred):
    facts = {v.visit_id for v in visits}
    labels = sorted({l for g in pure.values() for l in g})
    out = {}
    for l in labels:
        vs = sorted(v for v in pure if l in pure[v] and v in facts)
        joined = true_pairs = 0
        for a, b in itertools.combinations(vs, 2):
            true_pairs += 1
            joined += 1 if (a in pred and b in pred and pred[a] == pred[b]) else 0
        spread = {pred[v] for v in vs if v in pred}
        out[l] = {"visits": len(vs), "assigned": sum(1 for v in vs if v in pred), "true_pairs": true_pairs, "joined_pairs": joined,
                  "recall": _rate(joined, true_pairs), "engine_activities": len(spread)}
    return out


# ---------------------------------------------------------------- evaluate

def evaluate(prepared, gold, unsure, result, params, scenarios=None, max_pairs=MAX_PAIRS):
    """Score one result (any engine's, or a hand-made one) against moment-level gold. Pure and deterministic."""
    unsure = set(unsure)
    problems = ac.validate_result(result, {v.visit_id: v.t_start for v in prepared.visits})
    if problems:
        raise ValueError("result breaks the contract: " + "; ".join(problems[:3]))
    visits = prepared.visits
    info = {v.visit_id: ab.visit_gold(v, gold, unsure) for v in visits}
    pure = {k: g["labels"] for k, g in info.items() if g["kind"] == "pure"}
    pred = ac.visit_predictions(result)
    rows = {r["visit_id"]: r for r in result["visits"]}
    tally = _tally(pure, pred)
    outcomes = _outcomes(visits, pure, result)
    sets = derived_scenarios(visits, pure, result, params)
    sets.update({f"manifest:{k}": v for k, v in (scenarios or {}).items()})
    absent = ab.report(prepared, gold, unsure, result)
    units = ev.units_from_moments(prepared.records)
    return {
        "headline": {"precision_of_assigned": _scores(tally)["precision"], "recall_of_eligible": _scores(tally)["recall"],
                     "false_merge_pairs": tally["fp"], "missed_merge_pairs": tally["fn"],
                     "assigned_rate": absent["states"]["rates"][ac.ASSIGNED],
                     "abstention_rate": 1 - absent["states"]["rates"][ac.ASSIGNED] if visits else None,
                     "ambiguous_rate": absent["states"]["rates"][ac.AMBIGUOUS],
                     **_outcome_rates(outcomes)},
        "visits": absent["visits"],
        "pairs": dict(_scores(tally), false_merge_pairs_listed=[list(p) for p in tally["fp_pairs"][:max_pairs]],
                      missed_merge_pairs_listed=[list(p) for p in tally["fn_pairs"][:max_pairs]]),
        "outcomes": outcomes,
        "states": absent["states"],
        "coverage": absent["coverage"],
        "mixed_gold": absent["mixed_gold"],
        "unsure": {"moments": len(unsure), "visits_with_unsure_moments": sum(1 for v in visits if any(m["id"] in unsure for m in v.moments))},
        "internal_shift": absent["internal_shift"],
        "scenarios": {k: _scenario_report(k, sets[k], pure, pred, rows, max_pairs) for k in sorted(sets)},
        "activities": _per_activity(visits, pure, pred),
        "baselines_at_equal_coverage": ab.at_equal_coverage(prepared, gold, unsure, result, ev.baselines(units)),
        "ceiling": absent["ceiling"],
        "tallies": {k: tally[k] for k in ("tp", "fp", "fn", "fn_abstained", "fn_split")} | {"outcomes": outcomes},
    }


def run_dataset(dataset, infer, params=None, max_pairs=MAX_PAIRS, scoring=None):
    """Replay a dataset through `infer(prepared, params)` exactly as activity_contract.replay does (strict validation),
    then evaluate. Raises activity_input.ActivityInputError for records that break the integrity rules."""
    params = params or ac.ReplayParams()
    prepared = ai.prepare(dataset.records, max_intra_visit_gap_s=params.max_intra_visit_gap_s)
    result = infer(prepared, params)
    out = evaluate(prepared, dataset.gold, dataset.unsure, result, params, dataset.scenarios, max_pairs)
    out["dataset"] = {"name": dataset.name, "fingerprint": fingerprint(dataset.records, dataset.gold, dataset.unsure),
                      "moments": len(dataset.records), "labelled_moments": len(dataset.gold), "problems": list(dataset.problems)}
    out["engine"] = engine_description(infer, params, scoring)
    out["limits"] = limits(out)
    return out


def combine(results):
    """Micro-average over datasets: pair and outcome counts are summed, then the rates are recomputed."""
    tp = sum(r["tallies"]["tp"] for r in results)
    fp = sum(r["tallies"]["fp"] for r in results)
    fn = sum(r["tallies"]["fn"] for r in results)
    outcomes = {}
    for r in results:
        for k, v in r["tallies"]["outcomes"].items():
            outcomes[k] = outcomes.get(k, 0) + v
    p, rc = _rate(tp, tp + fp), _rate(tp, tp + fn)
    return {"datasets": [r["dataset"]["name"] for r in results], "precision_of_assigned": p, "recall_of_eligible": rc, "f1": _f1(p, rc),
            "false_merge_pairs": fp, "missed_merge_pairs": fn, "outcomes": outcomes, **_outcome_rates(outcomes)} if results else {}


def limits(res):
    """Plain statements about how far the numbers can be trusted."""
    out = []
    v = res["visits"]
    if v["pure"] < 30:
        out.append(f"only {v['pure']} pure labelled visits: indicative, not statistically meaningful")
    if res["pairs"]["true_pairs"] == 0:
        out.append("no pair of visits shares a label: recall is undefined")
    if v["mixed_gold"]:
        out.append(f"{v['mixed_gold']} mixed-gold visits left out of the primary scores")
    if v["unlabelled"]:
        out.append(f"{v['unlabelled']} unlabelled visits left out of every score")
    if res["unsure"]["moments"]:
        out.append(f"{res['unsure']['moments']} unsure moments left out")
    if res["dataset"]["problems"] if "dataset" in res else False:
        out.append("the dataset has problems: " + "; ".join(res["dataset"]["problems"][:3]))
    return out


def engine_description(infer, params, scoring=None):
    """What produced the result, so a run can be repeated: the infer function, the replay parameters and the scoring config."""
    d = {"infer": f"{getattr(infer, '__module__', '?')}.{getattr(infer, '__name__', '?')}", "replay_params": dataclasses.asdict(params)}
    if scoring is not None:
        d["scoring"] = dataclasses.asdict(scoring)
    return d


# ---------------------------------------------------------------- output

def _string_values(obj):
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _string_values(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _string_values(v)]
    return [obj] if isinstance(obj, str) else []


def dumps(obj):
    return json.dumps(obj, indent=2, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))


def write_result(obj, out_dir, protected=()):
    """evaluation.json into out_dir (owner-only). Refuses a folder inside any protected (data) folder."""
    target = os.path.realpath(out_dir)
    for p in protected:
        base = os.path.realpath(p)
        if target == base or target.startswith(base + os.sep):
            raise ValueError(f"refusing to write inside {p}: results never go into the data folder")
    os.makedirs(target, mode=0o700, exist_ok=True)
    path = os.path.join(target, "evaluation.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(dumps(obj) + "\n")
    return path


def _pct(v):
    return "  -  " if v is None else f"{100 * v:4.0f}%"


def summary_lines(res):
    """A few lines that answer: how reliably are coherent activities found? (one dataset's result)"""
    h, o, v = res["headline"], res["outcomes"], res["visits"]
    lines = [f"{res['dataset']['name']}  [{res['dataset']['fingerprint']}]  visits {v['total']} (pure {v['pure']}, "
             f"mixed-gold {v['mixed_gold']}, unlabelled {v['unlabelled']})",
             f"  when it joins, it is right: precision {_pct(h['precision_of_assigned'])}   "
             f"what it should join, it joins: recall {_pct(h['recall_of_eligible'])}",
             f"  false merges {h['false_merge_pairs']} pairs   missed merges {h['missed_merge_pairs']} pairs "
             f"(abstained {res['pairs']['missed_abstained']}, split {res['pairs']['missed_split']})",
             f"  assigned {_pct(h['assigned_rate'])}   abstained {_pct(h['abstention_rate'])} (ambiguous {_pct(h['ambiguous_rate'])})",
             f"  visits: joined right {o['joined_correct']}, partly {o['joined_partial']}, wrong {o['joined_wrong']}; "
             f"alone ok {o['alone_ok']} / missed {o['alone_missed']}; abstained ok {o['abstained_ok']} / missed {o['abstained_missed']}"]
    for tag, s in res["scenarios"].items():
        if s.get("visits"):
            lines.append(f"  scenario {tag:24s} visits {s['visits']:4d}  P {_pct(s['precision'])} R {_pct(s['recall'])} "
                         f"abstained {_pct(s['abstention_rate'])}")
    lines += [f"  note: {n}" for n in res["limits"]]
    return lines

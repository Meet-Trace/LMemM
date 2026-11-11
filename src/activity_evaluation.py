"""LMemM - evaluate the deterministic activity inference against labelled datasets (Step 2, PR 2D).

    dataset (moments + your labels)  ->  prepare  ->  infer  ->  compare with the labels  ->  metrics

The question this answers: how reliably does the inference identify coherent activities from real behaviour? It changes
nothing about inference (no weight, threshold or rule is touched), uses no model or judge, and reads files only.

A DATASET is a directory:
    moments.jsonl   Moment records, one JSON object per line (exactly the persisted Moment shape; e.g. a copy of
                    config.MOMENT_LOG output). Real data stays on the owner's disk: nothing here copies it anywhere.
    units.csv       The annotation sheet of activity_eval (`activity`, `also`, `unsure` per moment). Labelling rules,
                    noise ('-'), unlabelled (blank) and unsure are exactly those of PR 1.
    dataset.json    Optional: {"name": ..., "scenario": ...}. The scenario is a free tag (e.g. "single-app-focus",
                    "context-switching") used to group results. Defaults: the directory name and "unspecified".
New datasets are added by dropping in another directory; nothing in this module is dataset specific.

Conventions (activity_abstention, 13.7 / 14.2), reused unchanged:
    - The scored unit is the VISIT. A visit is PURE when its labelled, non-unsure moments share a label (or are all
      noise), MIXED-GOLD when they do not, UNLABELLED when it has none. Mixed-gold and unlabelled visits are left out of
      the primary pairwise scores and reported separately; unsure moments are excluded from gold and counted.
    - Abstaining is never rewarded: an unassigned, tentative or ambiguous visit forms no predicted pair, so a true pair
      that includes it is a MISSED MERGE (reported by the reason it was missed), and precision is reported on the pairs
      the engine actually made.
    - internal_shift is scored against moment-level gold, as in PR 2A.

Metrics per dataset (all counts are exact; ratios are None when their denominator is 0):
    pairwise precision / recall / F1 over pure visits; pair counts tp / fp / fn
    false merges    predicted-together pairs with no shared gold label (visit ids, capped), by kind
    missed merges   true pairs not predicted together: `split` (both assigned, different activities) or `abstained`
                    (at least one visit not assigned), with the abstention state and reason of the abstaining visit(s)
    visit outcomes  assigned clean / assigned with a false merge / assigned alone (a seed nobody joined) /
                    tentative / ambiguous / unassigned, with reasons; abstention and ambiguity rates
    mixed-gold, unsure and unlabelled counts, internal_shift, and the structural baselines (sittings, items) scored on
    the same assigned visits (`at_equal_coverage`) for context
Results are grouped by scenario and pooled by summing pair counts (never by averaging ratios).

Privacy. Output contains counts, rates, opaque visit ids and reason codes only: never window titles, URLs, terms,
identifiers or lines. The summary carries a fingerprint of each dataset (hash of its canonical records and labels) so a
rerun on the same data is comparable; it contains no timestamps or run-dependent values, so output is reproducible.

Not supported by the existing data model (flagged, not worked around): there is no per-activity ground truth beyond the
label names, so activity-level quality is read from the pair counts and the impure/fragmented cluster counts;
retention is disabled here on purpose (a labelled multi-day dataset is evaluated whole), whereas live replay prunes to 48 h.
"""

import hashlib
import itertools
import json
import os
from dataclasses import dataclass

import activity_abstention as ab
import activity_contract as ac
import activity_eval as ev
import activity_inference as inf
import activity_input as ai

LIST_LIMIT = 50                                    # ids listed per error kind (counts are always exact)
SCHEMA = 1


class DatasetError(ValueError):
    """A dataset that cannot be evaluated (missing file, labels that name no moment, unreadable records)."""


@dataclass(frozen=True)
class Dataset:
    name: str
    scenario: str
    records: tuple
    gold: dict            # moment_id -> set of labels (empty set = noise); missing = unlabelled
    unsure: frozenset
    problems: tuple       # label-sheet problems, reported and never guessed around

    def fingerprint(self):
        blob = json.dumps({"records": sorted(json.dumps(r, sort_keys=True) for r in self.records),
                           "gold": {k: sorted(v) for k, v in sorted(self.gold.items())}, "unsure": sorted(self.unsure)},
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- loading

def load_dataset(path):
    """Directory -> Dataset. Raises DatasetError for anything missing or inconsistent."""
    path = str(path)
    mp, up, dp = (os.path.join(path, n) for n in ("moments.jsonl", "units.csv", "dataset.json"))
    for p in (mp, up):
        if not os.path.isfile(p):
            raise DatasetError(f"{os.path.basename(path.rstrip('/'))}: missing {os.path.basename(p)}")
    records = []
    with open(mp, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    raise DatasetError(f"{os.path.basename(mp)} line {n}: not JSON") from None
    meta = {}
    if os.path.isfile(dp):
        with open(dp, encoding="utf-8") as fh:
            meta = json.load(fh)
    gold, unsure, problems = ev.load_sheet(path)
    ids = {r.get("id") for r in records}
    unknown = sorted(u for u in gold if u not in ids)
    if unknown:
        raise DatasetError(f"{len(unknown)} labelled unit ids match no moment (first: {unknown[0]})")
    return Dataset(name=str(meta.get("name") or os.path.basename(path.rstrip("/"))),
                   scenario=str(meta.get("scenario") or "unspecified"), records=tuple(records), gold=gold,
                   unsure=frozenset(unsure), problems=tuple(problems))


# ---------------------------------------------------------------- pair errors

def _pure(prepared, gold, unsure):
    info = {v.visit_id: ab.visit_gold(v, gold, unsure) for v in prepared.visits}
    return info, {k: g["labels"] for k, g in info.items() if g["kind"] == "pure"}


def pair_counts(pure, assignment):
    """tp / fp / fn over pure visits. `assignment`: visit_id -> activity id for ASSIGNED visits only. Two visits are
    TRUE-together when their gold sets share a label (noise with nothing); predicted-together when both are assigned to
    one activity. Same definition as activity_eval.pairwise at visit level."""
    tp = fp = fn = 0
    for a, b in itertools.combinations(sorted(pure), 2):
        truth = bool(pure[a] & pure[b])
        pred = a in assignment and b in assignment and assignment[a] == assignment[b]
        if pred and truth:
            tp += 1
        elif pred:
            fp += 1
        elif truth:
            fn += 1
    return tp, fp, fn


def pair_errors(prepared, gold, unsure, result, limit=LIST_LIMIT):
    """Where the grouping is wrong, as opaque visit ids. False merges are predicted pairs without a shared label; missed
    merges are true pairs not predicted together, split by why: both visits assigned (a `split`) or an abstention."""
    _, pure = _pure(prepared, gold, unsure)
    rows = {r["visit_id"]: r for r in result["visits"]}
    assignment = ac.visit_predictions(result)
    false_merges, splits, abstained = [], [], []
    by_reason, noise_merges = {}, 0
    for a, b in itertools.combinations(sorted(pure), 2):
        truth = bool(pure[a] & pure[b])
        pred = a in assignment and b in assignment and assignment[a] == assignment[b]
        if pred and not truth:
            false_merges.append((a, b))
            noise_merges += 0 if (pure[a] and pure[b]) else 1
        elif truth and not pred:
            if a in assignment and b in assignment:
                splits.append((a, b))
            else:
                why = sorted({f"{rows[v]['state']}:{rows[v]['reason']}" for v in (a, b) if v not in assignment})
                by_reason["+".join(why)] = by_reason.get("+".join(why), 0) + 1
                abstained.append((a, b))
    touched = {v for p in false_merges for v in p}
    return {"false_merges": {"count": len(false_merges), "involving_noise": noise_merges, "pairs": false_merges[:limit]},
            "missed_merges": {"count": len(splits) + len(abstained), "split": {"count": len(splits), "pairs": splits[:limit]},
                              "abstained": {"count": len(abstained), "by_reason": dict(sorted(by_reason.items())),
                                            "pairs": abstained[:limit]}},
            "visits_in_false_merges": len(touched)}


def visit_outcomes(prepared, gold, unsure, result):
    """What happened to each PURE visit: assigned clean / with a false merge / alone, or an abstention state."""
    _, pure = _pure(prepared, gold, unsure)
    rows = {r["visit_id"]: r for r in result["visits"]}
    assignment = ac.visit_predictions(result)
    size = {}
    for a in assignment.values():
        size[a] = size.get(a, 0) + 1
    bad = set()
    for a, b in itertools.combinations(sorted(pure), 2):
        if a in assignment and b in assignment and assignment[a] == assignment[b] and not (pure[a] & pure[b]):
            bad |= {a, b}
    out = {"pure_visits": len(pure), "assigned_clean": 0, "assigned_with_false_merge": 0, "assigned_alone": 0,
           "tentative": 0, "ambiguous": 0, "unassigned": 0, "abstention_reasons": {}}
    for v in sorted(pure):
        r = rows[v]
        if r["state"] == ac.ASSIGNED:
            key = "assigned_with_false_merge" if v in bad else "assigned_alone" if size[assignment[v]] == 1 else "assigned_clean"
            out[key] += 1
        else:
            out[r["state"]] += 1
            k = f"{r['state']}:{r['reason']}"
            out["abstention_reasons"][k] = out["abstention_reasons"].get(k, 0) + 1
    out["abstention_reasons"] = dict(sorted(out["abstention_reasons"].items()))
    n = len(pure)
    out["abstention_rate"] = None if not n else (out["tentative"] + out["ambiguous"] + out["unassigned"]) / n
    out["ambiguous_rate"] = None if not n else out["ambiguous"] / n
    return out


# ---------------------------------------------------------------- one dataset

def _ratio(a, b):
    return None if not b else a / b


def _scores(tp, fp, fn):
    p, r = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r,
            "f1": None if p is None or r is None else (0.0 if p + r == 0 else 2 * p * r / (p + r))}


def evaluate_dataset(ds, params=None, infer=None, scoring=None):
    """Replay one dataset through the inference and score it. `infer(prepared, params)` defaults to the engine's own;
    `scoring` is passed through untouched (this module never builds one)."""
    params = params or ac.ReplayParams()
    prepared = ai.prepare(list(ds.records), retention_hours=None, max_intra_visit_gap_s=params.max_intra_visit_gap_s)
    if infer is not None:
        result = infer(prepared, params)
    elif scoring is not None:
        result = inf.infer(prepared, params, scoring)
    else:
        result = inf.infer(prepared, params)
    rep = ab.report(prepared, ds.gold, ds.unsure, result)
    _, pure = _pure(prepared, ds.gold, ds.unsure)
    tp, fp, fn = pair_counts(pure, ac.visit_predictions(result))
    unsure_visits = sum(1 for v in prepared.visits if any(m["id"] in ds.unsure for m in v.moments))
    units = ev.units_from_moments(prepared.records)
    baselines = ab.at_equal_coverage(prepared, ds.gold, ds.unsure, result, ev.baselines(units))
    notes = []
    if tp + fn == 0:
        notes.append("no true pair among pure visits: recall is undefined (label more repeat work)")
    if not pure:
        notes.append("no pure labelled visit: nothing can be scored")
    return {
        "name": ds.name, "scenario": ds.scenario, "fingerprint": ds.fingerprint(), "label_problems": list(ds.problems),
        "visits": rep["visits"], "unsure_visits": unsure_visits,
        "pairwise": _scores(tp, fp, fn),
        "errors": pair_errors(prepared, ds.gold, ds.unsure, result),
        "outcomes": visit_outcomes(prepared, ds.gold, ds.unsure, result),
        "states": {"counts": rep["states"]["counts"], "rates": rep["states"]["rates"], "reasons": rep["states"]["reasons"]},
        "coverage": rep["coverage"], "mixed_gold": {k: v for k, v in rep["mixed_gold"].items() if k != "assigned"},
        "internal_shift": {k: v for k, v in rep["internal_shift"].items() if k not in ("missed", "false_alarms")},
        "wrong_pair_share": rep["wrong_pair_share"], "impure_clusters": rep["impure_clusters"],
        "at_equal_coverage": baselines, "notes": notes,
    }


# ---------------------------------------------------------------- many datasets

def pooled(results):
    """Pool pair counts of several dataset results (sum of counts, never an average of ratios)."""
    tp = sum(r["pairwise"]["tp"] for r in results)
    fp = sum(r["pairwise"]["fp"] for r in results)
    fn = sum(r["pairwise"]["fn"] for r in results)
    o = [r["outcomes"] for r in results]
    keys = ("pure_visits", "assigned_clean", "assigned_with_false_merge", "assigned_alone", "tentative", "ambiguous", "unassigned")
    tot = {k: sum(x[k] for x in o) for k in keys}
    n = tot["pure_visits"]
    return {"datasets": len(results), "pairwise": _scores(tp, fp, fn), "outcomes": tot,
            "false_merges": sum(r["errors"]["false_merges"]["count"] for r in results),
            "missed_merges": sum(r["errors"]["missed_merges"]["count"] for r in results),
            "abstention_rate": None if not n else (tot["tentative"] + tot["ambiguous"] + tot["unassigned"]) / n,
            "ambiguous_rate": None if not n else tot["ambiguous"] / n}


def evaluate_many(datasets, params=None, infer=None, scoring=None):
    """List of Dataset -> {schema, datasets, by_scenario, overall}. Order of input does not matter (sorted by name)."""
    results = sorted((evaluate_dataset(d, params, infer, scoring) for d in datasets), key=lambda r: (r["scenario"], r["name"]))
    scenarios = sorted({r["scenario"] for r in results})
    return {"schema": SCHEMA, "datasets": results,
            "by_scenario": {s: pooled([r for r in results if r["scenario"] == s]) for s in scenarios},
            "overall": pooled(results),
            "inference": {"params": _dataclass_dict(params or ac.ReplayParams()),
                          "scoring": _dataclass_dict(scoring or inf.SCORING)}}


def _dataclass_dict(obj):
    return {k: getattr(obj, k) for k in obj.__dataclass_fields__}


def dump_json(summary):
    return json.dumps(summary, sort_keys=True, indent=1, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))


# ---------------------------------------------------------------- text

def _pct(v):
    return "n/a" if v is None else f"{100 * v:.1f}%"


def render(summary):
    """A short plain-text summary: counts and rates only."""
    lines = []

    def block(title, p, o, fm, mm):
        pw = p
        lines.append(f"{title}: precision {_pct(pw['precision'])}, recall {_pct(pw['recall'])}, F1 {_pct(pw['f1'])}  "
                     f"(pairs tp {pw['tp']}, fp {pw['fp']}, fn {pw['fn']}; false merges {fm}, missed merges {mm})")
        lines.append(f"    pure visits {o['pure_visits']}: assigned clean {o['assigned_clean']}, with false merge "
                     f"{o['assigned_with_false_merge']}, alone {o['assigned_alone']}, tentative {o['tentative']}, "
                     f"ambiguous {o['ambiguous']}, unassigned {o['unassigned']}")
    ov = summary["overall"]
    block(f"OVERALL ({ov['datasets']} dataset(s))", ov["pairwise"], ov["outcomes"], ov["false_merges"], ov["missed_merges"])
    for s, v in summary["by_scenario"].items():
        block(f"scenario {s}", v["pairwise"], v["outcomes"], v["false_merges"], v["missed_merges"])
    for r in summary["datasets"]:
        block(f"  {r['name']} [{r['scenario']}]", r["pairwise"], r["outcomes"], r["errors"]["false_merges"]["count"],
              r["errors"]["missed_merges"]["count"])
        v = r["visits"]
        lines.append(f"    mixed-gold {v['mixed_gold']}, unlabelled {v['unlabelled']}, visits with unsure moments {r['unsure_visits']}")
        for n in r["notes"] + r["label_problems"]:
            lines.append(f"    note: {n}")
    return "\n".join(lines)

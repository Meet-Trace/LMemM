"""Invariant harness for activity inference (Step 2, PR 2A). TEST-ONLY, and independent of any inference.

An inference is any callable `infer(prepared, params) -> result` obeying the contract in src/activity_contract.py. It
receives INPUT THAT HAS ALREADY BEEN VALIDATED (activity_input.Prepared): validation happens once, at load. The checks
below take such a callable and return a list of violations ([] means the invariant holds), so the same harness runs
against stand-ins today and against the real inference in PR 2C, unchanged. Negative tests (test_activity_harness)
prove each check can fail.

Re-timed input. Translation and scaling change timestamps but, by design, never visit ids (an id is an immutable label
written at capture time, and activity ids are hashes of it). Original records are therefore validated with every rule,
including the capture-clock part of V4; re-timed records are validated with every rule EXCEPT that part
(check_capture_clock=False), because their timestamps no longer match the capture clock on purpose. All other
integrity rules, including the relative part of V4, still apply to them.

Checks: determinism, record/file order and duplicate invariance, translation, joint scaling, status independence,
forget consistency, no typed text / no fields outside the Moment schema, totality and ids, cannot-links, app-name
invariance, abstention not predicted, and a directional helper for time parameters.
"""

import copy
import json
import random
import re

import activity_contract as ac
import activity_input as ai
import moments

def prepared_for(records, params, capture_clock=True):
    """Validate records (time-valued limits come from params) and return Prepared."""
    return ai.prepare(records, max_intra_visit_gap_s=params.max_intra_visit_gap_s, check_capture_clock=capture_clock)


def run(infer, records, params, capture_clock=True):
    """Original records go through the public, strict `replay`. Deliberately re-timed copies (capture_clock=False) are
    prepared here, with only the absolute part of V4 off, and handed to `infer(prepared, params)` directly."""
    if capture_clock:
        return ac.replay(infer, records, params)
    return infer(prepared_for(records, params, capture_clock=False), params)


# ---------------------------------------------------------------- record transforms (all pure: inputs untouched)


def _shift_times(rec, f, g=None):
    """Apply f to every timestamp and g (default f) to every duration in one record."""
    g = g or f
    r = copy.deepcopy(rec)
    r["t_start"], r["t_end"] = f(r["t_start"]), f(r["t_end"])
    ctx = r.get("context") or {}
    if ctx.get("gap_s") is not None:
        ctx["gap_s"] = g(ctx["gap_s"])
    sib = ctx.get("same_item_before")
    if isinstance(sib, dict):
        sib["ended_at"] = f(sib["ended_at"])
        sib["gap_s"] = g(sib["gap_s"])
    if "dwell_s" in r:
        r["dwell_s"] = g(r["dwell_s"])
    inter = r.get("interaction") or {}
    inter["seconds"] = {k: g(v) for k, v in (inter.get("seconds") or {}).items()}
    if inter.get("ages"):
        inter["ages"] = {k: (None if v is None else g(v)) for k, v in inter["ages"].items()}
    return r


def translate(records, c):
    """Add c seconds (positive or negative) to every timestamp. Durations, ids and counts are unchanged."""
    return [_shift_times(r, lambda t: t + c, lambda d: d) for r in records]


def scale(records, k):
    """Multiply every timestamp and duration by k (any k > 0). Visit ids are left as they are."""
    return [_shift_times(r, lambda t: t * k, lambda d: d * k) for r in records]


def shuffled(records, seed):
    out = list(records)
    random.Random(seed).shuffle(out)
    return out


def with_duplicates(records, seed, share=0.3):
    rng = random.Random(seed)
    out = list(records) + [copy.deepcopy(r) for r in records if rng.random() < share]
    rng.shuffle(out)
    return out


def rename_apps(records, seed):
    """Give every app and bundle id a new random name, consistently. The item id is kept; ref follows the app."""
    rng = random.Random(seed)
    apps, bundles = {}, {}
    out = []
    for r in records:
        r = copy.deepcopy(r)
        a, b = r.get("app"), r.get("bundle_id")
        apps.setdefault(a, f"App{rng.randrange(10 ** 6)}")
        bundles.setdefault(b, f"org.example.b{rng.randrange(10 ** 6)}")
        r["app"], r["bundle_id"] = apps[a], bundles[b]
        r["ref"] = f"{r['app']}|{r.get('kind')}|{r.get('target')}"
        out.append(r)
    return out


def remove_visits(records, visit_ids):
    """What a partial forget leaves: the visits gone, and pointers to them scrubbed (as MomentStore.forget does)."""
    gone_v = set(visit_ids)
    gone_ids = {r["id"] for r in records if r["visit_id"] in gone_v}
    kept = [copy.deepcopy(r) for r in records if r["visit_id"] not in gone_v]
    for r in kept:
        moments.scrub(r.setdefault("context", {}), gone_ids, gone_v)
    return kept, gone_ids


# ---------------------------------------------------------------- comparing results

def _close(a, b, tol=1e-9):
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    return a == b


def canonical_result(result):
    """The result with visits and activities in a fixed order, for comparing runs."""
    out = copy.deepcopy(result)
    out["visits"] = sorted(out.get("visits") or [], key=lambda r: r["visit_id"])
    out["activities"] = sorted(out.get("activities") or [], key=lambda a: a["id"])
    for a in out["activities"]:
        a["members"] = sorted(a.get("members") or [])
    return out


def same_outcome(a, b):
    return _close(canonical_result(a), canonical_result(b))


def _starts(records):
    first = {}
    for r in sorted(records, key=lambda r: (r["t_start"], r["seq"])):
        first.setdefault(r["visit_id"], r["t_start"])
    return first


# ---------------------------------------------------------------- the checks (each returns a list of violations)

def check_contract(infer, records, params):
    """Totality, states, reasons, activity ids, predictions only for assigned visits."""
    res = run(infer, records, params)
    out = ac.validate_result(res, _starts(records))
    moments_of = {}
    for r in records:
        moments_of.setdefault(r["visit_id"], []).append(r["id"])
    pred = ac.predictions(res, moments_of)
    for row in res["visits"]:
        if row["state"] != ac.ASSIGNED and any(m in pred for m in moments_of[row["visit_id"]]):
            out.append(f"{row['visit_id']}: {row['state']} visit appears in predictions")
    return out


def check_determinism(infer, records, params):
    a, b = run(infer, records, params), run(infer, copy.deepcopy(records), params)
    return [] if same_outcome(a, b) else ["two runs on identical input differ"]


def check_order_and_duplicates(infer, records, params, seeds=(1, 2)):
    base = run(infer, records, params)
    out = []
    for s in seeds:
        if not same_outcome(base, run(infer, shuffled(records, s), params)):
            out.append(f"record order changes the outcome (seed {s})")
        if not same_outcome(base, run(infer, with_duplicates(records, s), params)):
            out.append(f"identical duplicate records change the outcome (seed {s})")
    return out


def check_translation(infer, records, params, offsets=(1234.5, 4 * 3600.0 + 1.5, -86400.0 * 400)):
    """Offsets of either sign, applied to already-validated data (see the module docstring)."""
    base = run(infer, records, params)
    return [f"translating all timestamps by {c} changes the outcome" for c in offsets
            if not same_outcome(base, run(infer, translate(records, c), params, capture_clock=False))]


def check_joint_scaling(infer, records, params, factors=(0.5, 2, 8)):
    """Timestamps and every time parameter scaled together change nothing."""
    base = run(infer, records, params)
    return [f"scaling timestamps and time parameters by {k} changes the outcome" for k in factors
            if not same_outcome(base, run(infer, scale(records, k), params.scaled(k), capture_clock=False))]


def check_status_independence(infer, records, params):
    """Reporting horizons may change reporting, never an assignment, a state, a reason or an id."""
    base = run(infer, records, params)
    other = run(infer, records, params.with_reporting(status_active_s=params.status_active_s * 7, status_paused_s=params.status_paused_s * 0.01))

    def core(res):
        return {"visits": [{k: r.get(k) for k in ("visit_id", "state", "activity_id", "reason")} for r in canonical_result(res)["visits"]],
                "activities": [{"id": a["id"], "members": a["members"]} for a in canonical_result(res)["activities"]]}
    return [] if core(base) == core(other) else ["reporting parameters changed an assignment"]


def check_app_rename(infer, records, params, seed=5):
    base = run(infer, records, params)
    return [] if same_outcome(base, run(infer, rename_apps(records, seed), params)) else ["renaming apps changes the outcome"]


def _tokens(rec):
    d = rec.get("digest") or {}
    return {t.lower() for t in (d.get("terms") or []) + (d.get("identifiers") or [])}


def check_forget(infer, records, params, visit_ids):
    """After removing visits (and scrubbing pointers), nothing about them appears in the output."""
    kept, gone_ids = remove_visits(records, visit_ids)
    out_text = json.dumps(run(infer, kept, params), sort_keys=True)
    out = []
    for g in sorted(gone_ids) + sorted(visit_ids):
        if g in out_text:
            out.append(f"removed id {g} appears in the output")
    kept_tokens = set()
    for r in kept:
        kept_tokens |= _tokens(r)
        kept_tokens |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", " ".join(str(r.get(k) or "") for k in ("window", "target", "url")))}
    only_gone = set()
    for r in records:
        if r["id"] in gone_ids:
            only_gone |= _tokens(r)
    for t in sorted(only_gone - kept_tokens):
        if re.search(r"\b" + re.escape(t) + r"\b", out_text, re.I):
            out.append(f"token {t!r} that only removed moments contained appears in the output")
    return out


def check_no_typed_text(infer, records, params):
    """Inputs have no field outside the Moment schema, and the output names none either."""
    out = []
    try:
        prepared = prepared_for(records, params)
    except ai.ActivityInputError as e:
        return [f"input rejected: {e.problems[:3]}"]
    res = infer(prepared, params)
    text = json.dumps(res)
    for bad in ("typed", "pasted", "clipboard", "keystrokes"):
        if f'"{bad}' in text:
            out.append(f"output carries a {bad!r} field")
    return out


def check_ids(infer, records, params):
    """Activity ids are the hash of the earliest member, whatever the order, duplicates or timestamp translation."""
    res = run(infer, records, params)
    starts = _starts(records)
    out = []
    for a in res["activities"]:
        if a["id"] != ac.activity_id(ac.earliest_member(starts, a["members"])):
            out.append(f"{a['id']} is not the hash of its earliest member")
    base = {a["id"]: sorted(a["members"]) for a in res["activities"]}
    for name, rec, clock in (("shuffled", shuffled(records, 3), True), ("translated by +777 s", translate(records, 777.0), False),
                             ("translated by -9e6 s", translate(records, -9.0e6), False),
                             ("scaled by 2 (with its parameters)", scale(records, 2), False),
                             ("duplicated", with_duplicates(records, 4), True)):
        p2 = params.scaled(2) if name.startswith("scaled") else params
        other = {a["id"]: sorted(a["members"]) for a in run(infer, rec, p2, capture_clock=clock)["activities"]}
        if other != base:
            out.append(f"activity ids or members change when the input is {name}")
    return out


def check_cannot_links(infer, records, params, pairs_):
    """No activity contains a cannot-linked pair of visits."""
    part = ac.partition(run(infer, records, params))
    return [f"cannot-linked visits {a} and {b} share an activity" for a, b in pairs_ if any(a in g and b in g for g in part)]


def check_must_pairs(infer, records, params, merge=(), not_merge=()):
    """For fixtures a real inference should satisfy; stand-ins are not expected to."""
    part = ac.partition(run(infer, records, params))
    together = lambda a, b: any(a in g and b in g for g in part)
    return ([f"{a} and {b} should be together" for a, b in merge if not together(a, b)]
            + [f"{a} and {b} should be apart" for a, b in not_merge if together(a, b)])


def outcome_series(infer, make_records, params, values, observe):
    """Directional helper for one time parameter: run `make_records(v)` for each v and collect observe(result)."""
    return [observe(run(infer, make_records(v), params)) for v in values]


def check_directional(series, expected):
    """`expected`: a list of the same length, e.g. [True, True, False]; reports each mismatch."""
    return [f"step {i}: expected {e!r}, saw {s!r}" for i, (s, e) in enumerate(zip(series, expected)) if s != e]


def check_time_params_declared():
    out = [f"undeclared time parameter {n}" for n in ac.undeclared_time_params()]
    out += [f"declared but not a field: {n}" for n in ac.declared_but_missing()]
    return out


def run_all(infer, records, params, forget_visits=()):
    """Every check that needs no per-fixture expectation. {name: violations}."""
    res = {"contract": check_contract(infer, records, params), "determinism": check_determinism(infer, records, params),
           "order_duplicates": check_order_and_duplicates(infer, records, params),
           "translation": check_translation(infer, records, params), "joint_scaling": check_joint_scaling(infer, records, params),
           "status_independence": check_status_independence(infer, records, params),
           "app_rename": check_app_rename(infer, records, params), "no_typed_text": check_no_typed_text(infer, records, params),
           "ids": check_ids(infer, records, params), "time_params": check_time_params_declared()}
    if forget_visits:
        res["forget"] = check_forget(infer, records, params, forget_visits)
    return res

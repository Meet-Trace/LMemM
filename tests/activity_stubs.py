"""Stand-ins for an inference, used only to exercise the invariant harness (Step 2, PR 2A). TEST-ONLY.

None of these infers anything: the good ones restate the structural baselines of activity_eval (sittings, items) in the
result contract; the bad ones each break exactly one invariant on purpose, so the harness is shown to be able to fail.
"""

import activity_contract as ac
import activity_input as ai

_MEMORY = []          # shared state used by the leaky stub
_CALLS = []           # and by the hidden-state stub


def _build(prepared, groups, flags=None, states=None):
    """Result from groups of visit ids. `states`: {visit_id: (state, reason)} overrides for visits left unassigned."""
    states = states or {}
    flags = flags or {}
    start = {v.visit_id: v.t_start for v in prepared.visits}
    rows, acts = [], []
    for g in groups:
        aid = ac.activity_id(ac.earliest_member(start, g))
        acts.append({"id": aid, "members": sorted(g)})
        for vid in g:
            rows.append({"visit_id": vid, "state": ac.ASSIGNED, "activity_id": aid,
                         "reason": "seed" if len(g) == 1 else None, "candidates": [], "internal_shift": flags.get(vid)})
    for vid, (st, why) in states.items():
        rows.append({"visit_id": vid, "state": st, "activity_id": None, "reason": why, "candidates": [],
                     "internal_shift": flags.get(vid)})
    return {"visits": rows, "activities": acts, "relations": {}}


def _sittings(visits, gap):
    groups, cur, end = [], [], None
    for v in visits:
        if cur and v.t_start - end > gap:
            groups.append(cur)
            cur = []
        cur.append(v.visit_id)
        end = max(end or v.t_end, v.t_end)
    if cur:
        groups.append(cur)
    return groups


def sittings_infer(records, params):
    p = ai.prepare(records)
    return _build(p, _sittings(p.visits, params.t_resume_s))


def item_infer(records, params):
    p = ai.prepare(records)
    by = {}
    for v in p.visits:
        by.setdefault(v.item_id, []).append(v.visit_id)
    return _build(p, [by[k] for k in sorted(by)])


def abstaining_infer(records, params):
    """Sittings, but glances abstain and visits whose first and last moment share no word are flagged internal_shift."""
    p = ai.prepare(records)
    glance = {v.visit_id for v in p.visits if v.dwell_s < params.glance_dwell_s}
    flags = {}
    for v in p.visits:
        if len(v.moments) >= 2:
            a = set(v.moments[0]["digest"].get("terms") or [])
            b = set(v.moments[-1]["digest"].get("terms") or [])
            if a and b and not (a & b):
                flags[v.visit_id] = {"from_moment_id": v.moments[0]["id"], "to_moment_id": v.moments[-1]["id"]}
    return _build(p, _sittings([v for v in p.visits if v.visit_id not in glance], params.t_resume_s), flags,
                  {g: (ac.UNASSIGNED, "no_substance") for g in sorted(glance)})


# ---------------------------------------------------------------- deliberately broken

def hardcoded_gap_infer(records, params):
    """Breaks joint scaling: the sitting gap is a constant, not a parameter."""
    p = ai.prepare(records)
    return _build(p, _sittings(p.visits, 900.0))


def time_of_day_infer(records, params):
    """Breaks translation: depends on the time of day."""
    p = ai.prepare(records)
    am = [v.visit_id for v in p.visits if int(v.t_start) % 86400 < 43200]
    pm = [v.visit_id for v in p.visits if int(v.t_start) % 86400 >= 43200]
    return _build(p, [g for g in (am, pm) if g])


def order_dependent_infer(records, params):
    """Breaks order invariance: the first record decides which visit gets its own group."""
    p = ai.prepare(list(records))
    first = records[0]["visit_id"]
    rest = [v.visit_id for v in p.visits if v.visit_id != first]
    return _build(p, [[first]] + ([rest] if rest else []))


def counter_id_infer(records, params):
    """Breaks id rules: ids come from a counter, not the earliest member."""
    p = ai.prepare(records)
    res = _build(p, [[v.visit_id] for v in p.visits])
    ids = {}
    for i, a in enumerate(res["activities"]):
        ids[a["id"]] = f"act-{i}"
        a["id"] = f"act-{i}"
    for r in res["visits"]:
        r["activity_id"] = ids.get(r["activity_id"])
    return res


def status_leak_infer(records, params):
    """Breaks status independence: a reporting horizon sets the sitting gap."""
    p = ai.prepare(records)
    return _build(p, _sittings(p.visits, params.status_active_s))


def leaky_infer(records, params):
    """Breaks forgetting: remembers earlier inputs between runs and mentions them."""
    p = ai.prepare(records)
    res = _build(p, _sittings(p.visits, params.t_resume_s))
    _MEMORY.extend(v.visit_id for v in p.visits)
    res["relations"] = {"seen_before": sorted(set(_MEMORY))}
    return res


def typed_infer(records, params):
    """Breaks the no-typed-text rule: the output carries a typed field."""
    p = ai.prepare(records)
    res = _build(p, _sittings(p.visits, params.t_resume_s))
    res["visits"][0]["typed_text"] = "x"
    return res


def partial_infer(records, params):
    """Breaks totality: drops the last visit from the output."""
    res = sittings_infer(records, params)
    res["visits"] = res["visits"][:-1]
    return res


def tentative_leak_infer(records, params):
    """Breaks 'only assigned is predicted': a tentative visit still carries an activity id."""
    res = sittings_infer(records, params)
    res["visits"][0].update(state=ac.TENTATIVE, reason="artifact_only")
    return res


def hidden_state_infer(records, params):
    """Keeps a call counter: the same input gives different answers on successive calls (breaks determinism)."""
    p = ai.prepare(records)
    _CALLS.append(1)
    if len(_CALLS) % 2:
        return _build(p, _sittings(p.visits, params.t_resume_s))
    return _build(p, [[v.visit_id] for v in p.visits])

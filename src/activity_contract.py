"""LMemM - the shape of an activity replay's result, its parameters, and the checks every result must pass.

Step 2, PR 2A (docs/adr/0003-activity-inference-design.md). No inference lives here. This file fixes the CONTRACT that
the later inference PRs implement and that the invariant harness (tests/activity_harness.py) checks, so the
contract can be tested against trivial stand-ins first.

    Result    {"visits": [VisitResult], "activities": [ActivityResult], "relations": {...}}
    VisitResult   visit_id, state, activity_id, reason, candidates, confidence, internal_shift
    state     assigned | tentative | ambiguous | unassigned. Only `assigned` has an activity_id and only
              `assigned` visits appear in predictions; the other three are scored as singletons (13.7).
    activity id   "act-" + hash(earliest FINAL member visit_id), earliest by (t_start, visit_id) (13.1)

Interface. The inference is `infer(prepared, params) -> result`, where `prepared` is activity_input.Prepared (records
validated, de-duplicated and canonically ordered). `replay(infer, records, params)` is the records-in entry point.

Parameters. Every parameter in seconds is declared in ASSIGNMENT_TIME or REPORTING_TIME. `ReplayParams.scaled(k)`
multiplies all of them, which is what the joint-scaling invariant needs, and `undeclared_time_params()` is the
check that no seconds-valued field was added without being declared (13.2).
"""

import hashlib
from dataclasses import dataclass, fields, replace

ASSIGNED, TENTATIVE, AMBIGUOUS, UNASSIGNED = "assigned", "tentative", "ambiguous", "unassigned"
STATES = (ASSIGNED, TENTATIVE, AMBIGUOUS, UNASSIGNED)
REASONS = frozenset({
    "seed", "no_substance", "no_candidate", "adjacency_only", "artifact_only", "lineage_only", "margin_too_small",
    "shared_artifact", "purpose_conflict", "cannot_link", "insufficient_families"})
ID_PREFIX = "act-"
ID_SALT = "lmemm.activity.v1|"


def activity_id(earliest_visit_id):
    """A deterministic id from the earliest member's visit_id only: no counter, no run-dependent value."""
    return ID_PREFIX + hashlib.sha256((ID_SALT + earliest_visit_id).encode("utf-8")).hexdigest()[:12]


def earliest_member(starts, members):
    """The member with the smallest (t_start, visit_id). `starts` maps visit_id -> t_start."""
    return min(members, key=lambda v: (starts[v], v))


# ---------------------------------------------------------------- the interface

def replay(infer, records, params):
    """The public entry: moment records in, result out. Always strict: the records are validated with every integrity
    rule, including the absolute part of V4, and there is no way to relax that through this function. `infer(prepared,
    params)` is the inference proper and receives input that activity_input.prepare has already validated, ordered and
    de-duplicated (design 14.9). Only the invariant harness, which prepares deliberately re-timed copies itself and
    calls `infer(prepared, params)` directly, ever skips the absolute part of V4."""
    import activity_input
    prepared = activity_input.prepare(records, max_intra_visit_gap_s=params.max_intra_visit_gap_s)
    return infer(prepared, params)


# ---------------------------------------------------------------- parameters

ASSIGNMENT_TIME = ("t_resume_s", "interruption_max_s", "decay_half_life_s", "candidate_horizon_s", "long_gap_s",
                   "glance_dwell_s", "seed_dwell_s", "unknown_app_seed_dwell_s", "internal_shift_min_dwell_s",
                   "max_intra_visit_gap_s", "neighbour_window_s")
REPORTING_TIME = ("status_active_s", "status_paused_s")      # dormant = beyond paused


@dataclass(frozen=True)
class ReplayParams:
    """Placeholders (design 4 and 13.2). Seconds-valued fields end in _s and MUST be declared above."""
    t_resume_s: float = 900.0
    interruption_max_s: float = 120.0
    decay_half_life_s: float = 1200.0
    candidate_horizon_s: float = 7200.0
    long_gap_s: float = 1800.0
    glance_dwell_s: float = 30.0
    seed_dwell_s: float = 30.0
    unknown_app_seed_dwell_s: float = 120.0
    internal_shift_min_dwell_s: float = 120.0
    max_intra_visit_gap_s: float = 300.0
    neighbour_window_s: float = 300.0
    status_active_s: float = 900.0
    status_paused_s: float = 21600.0

    def scaled(self, k):
        """Every seconds-valued parameter times k (assignment and reporting)."""
        return replace(self, **{n: getattr(self, n) * k for n in ASSIGNMENT_TIME + REPORTING_TIME})

    def with_reporting(self, **kw):
        """Only the reporting group may be changed here: it must never move an assignment."""
        bad = set(kw) - set(REPORTING_TIME)
        if bad:
            raise ValueError(f"not reporting parameters: {sorted(bad)}")
        return replace(self, **kw)


def undeclared_time_params(cls=ReplayParams):
    """Seconds-valued fields (named *_s) missing from both declared groups. Must be empty."""
    declared = set(ASSIGNMENT_TIME) | set(REPORTING_TIME)
    return sorted(f.name for f in fields(cls) if f.name.endswith("_s") and f.name not in declared)


def declared_but_missing(cls=ReplayParams):
    names = {f.name for f in fields(cls)}
    return sorted((set(ASSIGNMENT_TIME) | set(REPORTING_TIME)) - names)


# ---------------------------------------------------------------- results

def validate_result(result, visit_starts):
    """Problems with a result against the prepared visits (`visit_starts`: visit_id -> t_start). [] when sound.
    Totality (exactly one state per visit), state/activity consistency, reasons, ids, no visit in two activities."""
    out = []
    rows = result.get("visits") or []
    ids = [r.get("visit_id") for r in rows]
    if len(ids) != len(set(ids)):
        out.append("a visit appears more than once")
    if set(ids) != set(visit_starts):
        out.append(f"visits missing={sorted(set(visit_starts) - set(ids))[:5]} unknown={sorted(set(ids) - set(visit_starts))[:5]}")
    members = {}
    for r in rows:
        v, st, act, why = r.get("visit_id"), r.get("state"), r.get("activity_id"), r.get("reason")
        if st not in STATES:
            out.append(f"{v}: bad state {st!r}")
        elif st == ASSIGNED:
            if not act:
                out.append(f"{v}: assigned without activity_id")
            else:
                members.setdefault(act, []).append(v)
        elif act:
            out.append(f"{v}: {st} must not carry an activity_id")
        if st != ASSIGNED and why not in REASONS:
            out.append(f"{v}: {st} needs a reason code, got {why!r}")
        if st == ASSIGNED and why not in (None, "seed"):
            out.append(f"{v}: assigned reason must be null or 'seed', got {why!r}")
    declared = {a.get("id"): list(a.get("members") or []) for a in (result.get("activities") or [])}
    if set(declared) != set(members):
        out.append(f"activities listed {sorted(declared)[:5]} differ from activities used {sorted(members)[:5]}")
    for act, mem in declared.items():
        if sorted(mem) != sorted(members.get(act, [])):
            out.append(f"{act}: members differ from the visits assigned to it")
        known = [v for v in mem if v in visit_starts]
        if known and act != activity_id(earliest_member(visit_starts, known)):
            out.append(f"{act}: id is not the hash of its earliest member")
    return out


def predictions(result, moments_of):
    """{moment_id: activity_id} for ASSIGNED visits only. `moments_of`: visit_id -> [moment ids]."""
    pred = {}
    for r in result.get("visits") or []:
        if r.get("state") == ASSIGNED:
            for m in moments_of[r["visit_id"]]:
                pred[m] = r["activity_id"]
    return pred


def visit_predictions(result):
    """{visit_id: activity_id} for ASSIGNED visits only."""
    return {r["visit_id"]: r["activity_id"] for r in (result.get("visits") or []) if r.get("state") == ASSIGNED}


def partition(result):
    """The grouping as a set of frozensets of visit ids (assigned only), independent of activity ids."""
    groups = {}
    for v, a in visit_predictions(result).items():
        groups.setdefault(a, set()).add(v)
    return {frozenset(g) for g in groups.values()}

"""LMemM - reading persisted Moments for an offline replay: dedupe, order, and refuse corrupted input.

Step 2, PR 2A (docs/adr/0003-activity-inference-design.md). This is INPUT HANDLING for the later inference PRs, not
inference: it reads moment records, drops identical duplicates, fails loudly on anything that would make the
visit structure untrustworthy, and returns the records in one canonical order. Pure Python, no models.

Rules (each has a test):
    I1  Identical duplicate ids (equal after canonical JSON) are dropped and counted.
    I2  The same id with different content fails loudly (ConflictingMomentError). Only field NAMES are reported.
    I3  File order, line order and record order never matter: records are sorted by (t_start, seq, visit_id, id).
    I4  Unsupported schema versions and fields outside the Moment schema fail loudly. Nothing typed, pasted or
        clipboard-derived can enter this way.
    I5  Visit integrity (V1 to V5, below) is checked per visit_id. Any violation fails the whole run: no output,
        exit code 3. Messages carry the visit_id, a check code, field names and moment ids, never screen content.
    I6  Half-written JSON lines are skipped and counted (the store does the same); strict=True fails instead.

Visit checks:
    V1  all moments of a visit share item_id, app, bundle_id, ref, kind, target, url, domain, window
        (the Moment layer copies them from the visit's first moment)
    V2  each id is <visit_id>.<seq>; seq values are unique integers forming one contiguous range. The range may
        start above 0 (retention drops the oldest moments) and may end early (partial forget drops the newest).
    V3  t_start <= t_end, moments do not overlap, and no gap inside the visit exceeds max_intra_visit_gap_s.
        Float slack is tol = TOL_REL * max_intra_visit_gap_s (TOL_REL = 1e-6), and nothing else: a negative duration
        is t_end - t_start < -tol, an overlap is t_start(next) - t_end(prev) < -tol, a gap is t_start(next) - t_end(prev)
        > max_intra_visit_gap_s + tol. Each test compares a DIFFERENCE of two timestamps with tol, so a translation
        (t -> t + c) cancels exactly; and tol is proportional to a time-valued parameter, so under t -> k t with
        max_intra_visit_gap_s -> k max_intra_visit_gap_s every difference and tol both scale by k and every
        comparison keeps its outcome for any k > 0. No absolute floor, no dependence on the size of a timestamp.
        The slack is about a millionth of the permitted gap (300 s -> 0.3 ms), far below any real overlap or gap.
        Numerical limitation: the relative tolerance accounts for floating-point rounding only while timestamp
        magnitudes stay small relative to max_intra_visit_gap_s (roughly below 4e9 * max_intra_visit_gap_s); beyond this
        range, floating-point precision may cause valid boundary cases to be rejected.
    V4  visit-id coherence. A visit id (<prefix><second>-<n>) is written once, when the visit is created, from the
        capture clock (moments.py: f"{prefix}{int(t)}-{n}", t = the visit's first frame = its first moment's t_start),
        and is never rewritten, so it is an immutable label. Two parts:
        ABSOLUTE, the primary check, ON BY DEFAULT (check_capture_clock=True): a visit that still has its first
        moment has int(t_start) == its embedded second; a visit whose oldest moments were dropped has an embedded
        second no later than int(t_start) of its first remaining moment. This validates data as the capture clock
        wrote it. It is switched off only for records a test has deliberately re-timed (the invariant harness).
        RELATIVE, always on, unchanged by translating or scaling time. What it guarantees, and nothing more:
          - the id has the form <prefix><second>-<n> and n >= 1;
          - among visits that still have their first moment and share a prefix, if one starts strictly later than
            another, its embedded second is not smaller. The comparison is strict and uses no tolerance, so it
            holds under every positive scaling of time.
        What it does NOT guarantee. It does not detect: counter suffixes swapped between visits that start in the same
        second; ids exchanged between visits whose seconds are equal or whose order stays consistent; ids that were
        all shifted together; foreign ids whose seconds happen to fit; anything about visits whose first moment was
        dropped (they take no part in the relative comparison); or a duplicate counter across tracker restarts
        (that shows up as a conflicting id or as V1). With the absolute check off, a relabelling that keeps the
        start order is accepted. test_relative_v4_limitations pins these limits.
    V5  for seq > 0, prev_moment_id is the previous moment's id when that moment is present, and
        same_item_before is identical across the visit and never points at the visit itself
"""

import json
import os
import re
from dataclasses import dataclass, field

import moments

EXIT_INPUT_INTEGRITY = 3
SCHEMAS = (1,)
RETENTION_HOURS = 48.0
MAX_INTRA_VISIT_GAP_S = 300.0       # above the capture layer's 90 s visit gap; a time parameter (scaled with the others)
TOL_REL = 1e-6                      # float slack as a FRACTION of max_intra_visit_gap_s (see V3 in the module docstring)
MAX_REPORTED = 20

RECORD_KEYS = frozenset(moments.Moment.__dataclass_fields__) | {"dwell_s"}
DIGEST_KEYS = frozenset({"terms", "identifiers", "lines", "text_hash"})
INTERACTION_KEYS = frozenset({"category", "seconds", "ages"})
CONTEXT_KEYS = frozenset({"prev_moment_id", "gap_s", "same_item_before", "trigger", "pinned"})
SAME_ITEM_KEYS = frozenset({"visit_id", "ended_at", "gap_s", "visits_before"})
VISIT_CONSTANT_FIELDS = ("item_id", "app", "bundle_id", "ref", "kind", "target", "url", "domain", "window")
VISIT_ID_RE = re.compile(r"^(?P<prefix>.*?)(?P<second>\d+)-(?P<n>\d+)$")


# ---------------------------------------------------------------- errors

class ActivityInputError(Exception):
    """Base of every refusal. `problems` holds coded one-liners; none contains screen content."""
    exit_code = EXIT_INPUT_INTEGRITY

    def __init__(self, problems):
        self.problems = list(problems)
        shown = self.problems[:MAX_REPORTED]
        more = len(self.problems) - len(shown)
        text = "\n".join(shown) + (f"\n... and {more} more" if more > 0 else "")
        super().__init__(text)


class ConflictingMomentError(ActivityInputError):
    pass


class VisitIntegrityError(ActivityInputError):
    pass


class UnsupportedRecordError(ActivityInputError):
    pass


class MalformedLineError(ActivityInputError):
    pass


# ---------------------------------------------------------------- canonical form

def _normalise(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return {k: _normalise(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_normalise(x) for x in v]
    return v


def canonical(rec):
    """One string per record: sorted keys, numbers normalised (1 and 1.0 are equal)."""
    return json.dumps(_normalise(rec), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def differing_fields(a, b):
    """Names of the top-level fields in which two records differ. Never the values."""
    return sorted(k for k in set(a) | set(b) if _normalise(a.get(k)) != _normalise(b.get(k)))


def sort_key(rec):
    return (rec["t_start"], rec["seq"], rec["visit_id"], rec["id"])


# ---------------------------------------------------------------- reading

def parse_lines(lines, source="", strict=False, report=None):
    """Records from JSONL lines. A line that is not valid JSON is skipped and counted (strict: refused)."""
    out = []
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            if strict:
                raise MalformedLineError([f"LINE_MALFORMED file={os.path.basename(source) or '-'} line={n}"])
            if report is not None:
                report["malformed_lines"] += 1
            continue
        out.append(rec)
    return out


def new_report():
    return {"files": 0, "records_read": 0, "malformed_lines": 0, "duplicates_dropped": 0, "outside_window": 0,
            "records": 0, "visits": 0, "truncated_visits": []}


def read_folder(directory, strict=False, report=None):
    """Every *.jsonl under `directory` (read-only). Files are read in name order, but order never matters."""
    report = report if report is not None else new_report()
    recs = []
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".jsonl"))
    except OSError:
        names = []
    for name in names:
        report["files"] += 1
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as fh:
            recs += parse_lines(fh, path, strict, report)
    return recs, report


# ---------------------------------------------------------------- validation

def _check_record(rec, problems):
    """Shape of one record. Returns True when usable."""
    if not isinstance(rec, dict):
        problems.append("REC_NOT_OBJECT")
        return False
    rid = rec.get("id") if isinstance(rec.get("id"), str) else "?"
    ok = True
    for k in ("id", "visit_id"):
        if not isinstance(rec.get(k), str) or not rec.get(k):
            problems.append(f"REC_FIELD id={rid} field={k}")
            ok = False
    if not isinstance(rec.get("seq"), int) or isinstance(rec.get("seq"), bool):
        problems.append(f"REC_FIELD id={rid} field=seq")
        ok = False
    for k in ("t_start", "t_end"):
        if not isinstance(rec.get(k), (int, float)) or isinstance(rec.get(k), bool):
            problems.append(f"REC_FIELD id={rid} field={k}")
            ok = False
    if not ok:
        return False
    if rec.get("schema", 1) not in SCHEMAS:
        problems.append(f"SCHEMA_UNSUPPORTED id={rid} schema={rec.get('schema')!r}")
    extra = sorted(set(rec) - RECORD_KEYS)
    if extra:
        problems.append(f"FIELD_UNKNOWN id={rid} fields={','.join(extra)}")
    for key, allowed in (("digest", DIGEST_KEYS), ("interaction", INTERACTION_KEYS), ("context", CONTEXT_KEYS)):
        sub = rec.get(key)
        if isinstance(sub, dict):
            bad = sorted(set(sub) - allowed)
            if bad:
                problems.append(f"FIELD_UNKNOWN id={rid} fields=" + ",".join(f"{key}.{b}" for b in bad))
    sib = (rec.get("context") or {}).get("same_item_before")
    if isinstance(sib, dict):
        bad = sorted(set(sib) - SAME_ITEM_KEYS)
        if bad:
            problems.append(f"FIELD_UNKNOWN id={rid} fields=" + ",".join(f"context.same_item_before.{b}" for b in bad))
    return True


def _visit_problems(vid, recs, max_gap):
    """V1 to V5 for one visit (records in any order)."""
    out = []
    by_seq = sorted(recs, key=lambda r: (r["seq"], r["id"]))
    # V1
    for f in VISIT_CONSTANT_FIELDS:
        vals = {json.dumps(r.get(f), sort_keys=True) for r in by_seq}
        if len(vals) > 1:
            out.append(f"V1 {vid} field={f} differs across moments={','.join(r['id'] for r in by_seq)}")
    # V2
    seqs = [r["seq"] for r in by_seq]
    for r in by_seq:
        if r["id"] != f"{vid}.{r['seq']}":
            out.append(f"V2 {vid} id_mismatch moment={r['id']} seq={r['seq']}")
    if len(set(seqs)) != len(seqs):
        out.append(f"V2 {vid} duplicate_seq seqs={sorted(s for s in set(seqs) if seqs.count(s) > 1)}")
    elif seqs and seqs != list(range(seqs[0], seqs[0] + len(seqs))):
        holes = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs))
        out.append(f"V2 {vid} seq_hole missing={holes[:10]}")
    # V3 (differences only, tolerance proportional to the time parameter; see the module docstring)
    tol = TOL_REL * max_gap
    for a, b in zip(by_seq, by_seq[1:]):
        gap = b["t_start"] - a["t_end"]
        if gap < -tol:
            out.append(f"V3 {vid} overlap moments={a['id']},{b['id']}")
        elif gap > max_gap + tol:
            out.append(f"V3 {vid} gap_too_large moments={a['id']},{b['id']} limit_s={max_gap:g}")
    for r in by_seq:
        if r["t_end"] - r["t_start"] < -tol:
            out.append(f"V3 {vid} negative_duration moment={r['id']}")
    # V5
    sibs = {json.dumps((r.get("context") or {}).get("same_item_before"), sort_keys=True) for r in by_seq}
    if len(sibs) > 1:
        out.append(f"V5 {vid} same_item_before differs across moments={','.join(r['id'] for r in by_seq)}")
    present = {r["seq"]: r["id"] for r in by_seq}
    for r in by_seq:
        ctx = r.get("context") or {}
        if r["seq"] > 0 and (r["seq"] - 1) in present and ctx.get("prev_moment_id") != present[r["seq"] - 1]:
            out.append(f"V5 {vid} prev_moment_id wrong moment={r['id']}")
        sib = ctx.get("same_item_before")
        if isinstance(sib, dict) and sib.get("visit_id") == vid:
            out.append(f"V5 {vid} same_item_before_points_at_itself moment={r['id']}")
    return out


def _clock_problems(groups, absolute):
    """V4 across visits. Relative part always; absolute part only when `absolute` (see the module docstring)."""
    out, rows = [], {}
    for vid, ms in groups.items():
        m = VISIT_ID_RE.match(vid)
        if not m:
            out.append(f"V4 {vid} id_format")            # moments.py cannot produce an id without <second>-<counter>
            continue
        second, n = int(m.group("second")), int(m.group("n"))
        first = min(ms, key=lambda r: r["seq"])
        complete = first["seq"] == 0
        if n < 1:
            out.append(f"V4 {vid} counter_below_one")
        if absolute:
            floor = int(first["t_start"] // 1)
            if complete and second != floor:
                out.append(f"V4 {vid} id_second_differs_from_first_moment moment={first['id']}")
            elif not complete and second > floor:
                out.append(f"V4 {vid} id_second_after_first_remaining_moment moment={first['id']}")
        if complete:
            rows.setdefault(m.group("prefix"), []).append((first["t_start"], second, vid))
    for prefix, items in sorted(rows.items()):
        items.sort()
        for (ta, sa, va), (tb, sb, vb) in zip(items, items[1:]):
            if sb < sa and tb > ta:                  # strict, no tolerance: genuine data cannot produce it (tb < sb + 1 <= sa <= ta)
                out.append(f"V4 {va},{vb} id_order_conflicts_with_start_order")
    return out


# ---------------------------------------------------------------- the entry point

@dataclass
class Visit:
    visit_id: str
    moments: list
    item_id: str = None

    @property
    def t_start(self):
        return self.moments[0]["t_start"]

    @property
    def t_end(self):
        return max(m["t_end"] for m in self.moments)

    @property
    def dwell_s(self):
        return sum(max(0.0, m["t_end"] - m["t_start"]) for m in self.moments)


@dataclass
class Prepared:
    records: list                      # canonical order
    visits: list                       # Visit objects, ordered by (t_start, visit_id)
    report: dict = field(default_factory=new_report)

    def visit(self, visit_id):
        return next((v for v in self.visits if v.visit_id == visit_id), None)


def prepare(records, retention_hours=RETENTION_HOURS, max_intra_visit_gap_s=MAX_INTRA_VISIT_GAP_S, report=None,
            check_capture_clock=True):
    """Raw record dicts -> Prepared, or an ActivityInputError (exit code 3). Pure: no I/O, no clock.
    check_capture_clock=False skips only the ABSOLUTE part of V4, for data a test has re-timed (see V4 above)."""
    if not (isinstance(max_intra_visit_gap_s, (int, float)) and 0 < max_intra_visit_gap_s < float("inf")):
        raise ValueError("max_intra_visit_gap_s must be a positive finite number")      # the V3 tolerance is relative to it
    report = report if report is not None else new_report()
    report["records_read"] += len(records)
    problems = []
    usable = [r for r in records if _check_record(r, problems)]
    if problems:
        raise UnsupportedRecordError(sorted(problems))

    seen, conflicts = {}, []
    for r in usable:
        prev = seen.get(r["id"])
        if prev is None:
            seen[r["id"]] = r
        elif canonical(prev) == canonical(r):
            report["duplicates_dropped"] += 1
        else:
            conflicts.append(f"ID_CONFLICT id={r['id']} fields={','.join(differing_fields(prev, r))}")
    if conflicts:
        raise ConflictingMomentError(sorted(set(conflicts)))

    recs = list(seen.values())
    if recs and retention_hours is not None:
        cutoff = max(r["t_end"] for r in recs) - retention_hours * 3600.0
        kept = [r for r in recs if r["t_end"] >= cutoff]
        report["outside_window"] += len(recs) - len(kept)
        recs = kept
    recs.sort(key=sort_key)

    groups = {}
    for r in recs:
        groups.setdefault(r["visit_id"], []).append(r)
    bad = []
    for vid in sorted(groups):
        bad += _visit_problems(vid, groups[vid], max_intra_visit_gap_s)
    bad += _clock_problems(groups, check_capture_clock)
    if bad:
        raise VisitIntegrityError(bad)

    visits = []
    for vid, ms in groups.items():
        ms.sort(key=lambda r: r["seq"])
        visits.append(Visit(vid, ms, ms[0].get("item_id")))
        if ms[0]["seq"] > 0:
            report["truncated_visits"].append(vid)
    visits.sort(key=lambda v: (v.t_start, v.visit_id))
    report["truncated_visits"].sort()
    report["records"], report["visits"] = len(recs), len(visits)
    return Prepared(recs, visits, report)


def load(directory, strict=False, **kw):
    """Folder of *.jsonl -> Prepared. Read-only; writes nothing."""
    report = new_report()
    recs, report = read_folder(directory, strict, report)
    return prepare(recs, report=report, **kw)

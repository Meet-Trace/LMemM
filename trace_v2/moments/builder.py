import json
import math
import sqlite3
import time
import uuid
from collections import Counter

from ..observations.queries import _decode
from ..observations.derived import derived_rollback
from ..observations.store import encode_raw, now_iso
from . import features as F
from .similarity import PARAMS, S, Rep, centroid, profile_of, rep_similarity, similarity

ALGORITHM = "moments/1"
NAMESPACE = uuid.UUID("6f1d5b0e-3c0a-4c53-9a53-6d2f1a8e7c10")
DF_WINDOW = 500


class Idf:
    def __init__(self, rows):
        self.df, self.n = Counter(), 0
        for features_json in rows:
            self.add(json.loads(features_json))

    def add(self, f):
        self.n += 1
        self.df.update(f["tok"].keys())

    def __call__(self, token):
        return math.log((self.n + 1) / (self.df.get(token, 0) + 1)) + 1.0


def moment_uuid(*parts):
    return str(uuid.uuid5(NAMESPACE, ":".join((ALGORITHM, *parts))))


def ready_batch(conn, now_ns, params, since_ns, limit):
    rows = conn.execute(
        "SELECT o.observation_id FROM observations o WHERE o.captured_at_ns >= ? AND o.captured_at_ns <= ? "
        "AND NOT EXISTS (SELECT 1 FROM observation_features f WHERE f.observation_id=o.observation_id AND f.algorithm=?) "
        "AND NOT EXISTS (SELECT 1 FROM moment_skips k WHERE k.observation_id=o.observation_id AND k.algorithm=?) "
        "ORDER BY o.captured_at_ns, o.observation_id LIMIT ?", (since_ns, now_ns, F.ALGORITHM, F.ALGORITHM, limit)).fetchall()
    out = []
    for (oid,) in rows:
        inputs = F.read_inputs(conn, oid)
        if inputs is None:
            continue
        age = (now_ns - inputs["obs"][0]) / S
        ocr, structure = inputs["ocr"], inputs["structure"]
        if inputs["shot"] == "stored":
            ocr_unfinished = ocr is None or ocr[1] == "pending"
            wait = params["ocr_wait_s"] if ocr_unfinished else params["ready_wait_s"] if structure is None else 0
            if age < wait:
                break
        out.append((oid, inputs))
    return out


LOAD_CHUNK = 500


def features_of(conn, oids):
    out = {}
    for i in range(0, len(oids), LOAD_CHUNK):
        chunk = oids[i:i + LOAD_CHUNK]
        rows = conn.execute("SELECT observation_id, features_json FROM observation_features WHERE algorithm=? AND "
                            f"observation_id IN ({','.join('?' * len(chunk))})", (F.ALGORITHM, *chunk))
        out.update({oid: json.loads(blob) for oid, blob in rows})
    return out


def load_rep(conn, params, moment_id, revision, enc, blob):
    rep = Rep(moment_id, params)
    rep.revision, rep.origin, rep.parent, rep.created = revision, None, None, None
    rep.state, rep.merged_into, rep.dirty = "provisional", None, False
    members = _decode(enc, blob)
    feats = features_of(conn, [m["o"] for m in members])
    for m in members:
        if m["o"] in feats:
            rep.add(m["o"], feats[m["o"]], m["role"], m["s"])
    return rep


def active_rows(conn):
    return conn.execute(
        "SELECT r.moment_id, r.revision, r.members_encoding, r.members FROM moment_revisions r JOIN moments m USING (moment_id) "
        "WHERE m.algorithm=? AND r.state='provisional' AND r.revision=(SELECT max(x.revision) FROM moment_revisions x "
        "WHERE x.moment_id=r.moment_id) ORDER BY r.moment_id", (ALGORITHM,)).fetchall()


def load_active(conn, params, cache=None):
    """The provisional Moments, latest revision each. With a `cache` (moment_id -> Rep kept between passes) only Moments
    that are not in it, or whose stored revision differs from the cached one, are read from the database; a cached Moment
    that was never written yet is kept as it is."""
    if cache is None:
        return [load_rep(conn, params, *row[:2], *row[2:]) for row in active_rows(conn)]
    stored = {r[0]: r for r in conn.execute(
        "SELECT r.moment_id, r.revision FROM moment_revisions r JOIN moments m USING (moment_id) WHERE m.algorithm=? "
        "AND r.state='provisional' AND r.revision=(SELECT max(x.revision) FROM moment_revisions x "
        "WHERE x.moment_id=r.moment_id)", (ALGORITHM,))}
    for moment_id in list(cache):
        rep = cache[moment_id]
        if rep.revision > 0 and (moment_id not in stored or stored[moment_id][1] != rep.revision):
            del cache[moment_id]
    for moment_id, (_, revision) in stored.items():
        if moment_id not in cache:
            row = conn.execute("SELECT moment_id, revision, members_encoding, members FROM moment_revisions WHERE moment_id=? "
                               "AND revision=?", (moment_id, revision)).fetchone()
            cache[moment_id] = load_rep(conn, params, *row)
    return [cache[k] for k in sorted(cache)]


def unassigned(conn, since_ns):
    """Observations with stored features that no Moment holds: left by a pass that stopped before its revisions were written."""
    return [r[0] for r in conn.execute(
        "SELECT f.observation_id FROM observation_features f JOIN observations o USING (observation_id) "
        "LEFT JOIN moment_member_index i ON i.observation_id=f.observation_id WHERE f.algorithm=? AND i.observation_id IS NULL "
        "AND o.captured_at_ns >= ? ORDER BY o.captured_at_ns, o.observation_id", (F.ALGORITHM, since_ns))]


def new_rep(moment_id, params, origin="formed", parent=None):
    rep = Rep(moment_id, params)
    rep.revision, rep.origin, rep.parent, rep.created = 0, origin, parent, None
    rep.state, rep.dirty, rep.merged_into = "provisional", True, None
    return rep


def sealed(rep, t, params):
    """True when an observation arriving at `t` should not join `rep`: the Moment is long enough and work paused before it,
    or it reached the maximum length and any short pause will do, or it is far past the maximum."""
    length, gap = (rep.end - rep.start) / S, (t - rep.end) / S
    if length >= params["max_length_s"] + params["hard_slack_s"]:
        return True
    if length >= params["max_length_s"]:
        return gap >= params["cap_pause_s"]
    return length >= params["min_length_s"] and gap >= params["pause_s"]


def seal(rep, params):
    rep.state = "stable" if len(rep.core()) >= params["min_members"] else "insufficient"
    rep.dirty = rep.structural = True


def assign(reps, oid, f, idf, params):
    horizon = params["candidate_horizon_s"] * S
    scored = []
    for rep in reps:
        if rep.state != "provisional" or not rep.has_core() or rep.end < f["t"] - horizon:
            continue
        if sealed(rep, f["t"], params):
            seal(rep, params)
            continue
        score, ctx = rep.score(f, idf)
        scored.append((score, rep))
    scored.sort(key=lambda x: (-x[0], x[1].moment_id))
    best = scored[0] if scored and scored[0][0] >= params["join"] else None
    if best is not None:
        best[1].add(oid, f, "core", best[0])
        best[1].dirty = True
        touched = [best[1]]
    else:
        rep = new_rep(moment_uuid(oid), params)
        rep.add(oid, f, "core", 1.0)
        reps.append(rep)
        touched = [rep]
    for score, rep in scored:
        if rep is not touched[0] and score >= params["secondary"]:
            rep.add(oid, f, "secondary", score)
            rep.dirty = True
    return touched[0]


def merge_pass(reps, touched, idf, params):
    horizon = params["candidate_horizon_s"] * S
    changed = True
    while changed:
        changed = False
        live = sorted((r for r in reps if r.state == "provisional" and r.has_core()), key=lambda r: (r.start, r.moment_id))
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if a.state != "provisional" or b.state != "provisional":
                    continue
                if a not in touched and b not in touched:
                    continue
                if b.start - a.end > horizon:
                    continue
                if rep_similarity(a, b, idf, params) >= params["merge"]:
                    have = {m["o"]: m for m in a.members}
                    for m in sorted(b.members, key=lambda m: (m["t"], m["o"])):
                        if m["o"] in have:
                            if m["role"] == "core" and have[m["o"]]["role"] != "core":
                                a.promote(m["o"], m["s"])
                            continue
                        a.add(m["o"], b.features[m["o"]], m["role"], m["s"])
                    a.dirty = a.structural = True
                    b.state, b.merged_into, b.dirty = "merged", a.moment_id, True
                    touched.add(a)
                    changed = True
                    break
            if changed:
                break


def _slice_rep(rep, members, moment_id, params, origin, parent):
    out = new_rep(moment_id, params, origin, parent)
    for m in members:
        out.add(m["o"], rep.features[m["o"]], m["role"], m["s"])
    return out


def split_pass(reps, touched, idf, params):
    queue = [r for r in reps if r in touched and r.state == "provisional"]
    while queue:
        rep = queue.pop(0)
        core = sorted(rep.core(), key=lambda m: (m["t"], m["o"]))
        side = params["split_min_side"]
        if len(core) < 2 * side or len(core) > params["max_split_members"]:
            continue
        chain = []
        for i in range(len(core) - 1):
            if i + 1 < side or len(core) - (i + 1) < side:
                continue
            chain.append((similarity(profile_of(rep.features[core[i]["o"]]), profile_of(rep.features[core[i + 1]["o"]]),
                                     idf, params), i + 1))
        chain.sort()
        best = None
        for _, cut in chain[:params["split_candidates"]]:
            left = centroid([rep.features[m["o"]] for m in core[:cut]], params["cell_presence"])
            right = centroid([rep.features[m["o"]] for m in core[cut:]], params["cell_presence"])
            sim = similarity(left, right, idf, params)
            if best is None or sim < best[0]:
                best = (sim, cut)
        if best is None or best[0] >= params["split"]:
            continue
        cut_t = core[best[1]]["t"]
        keep = [m for m in rep.members if m["t"] < cut_t or (m["t"] == cut_t and m["o"] in {x["o"] for x in core[:best[1]]})]
        move = [m for m in rep.members if m not in keep]
        suffix_id = moment_uuid(rep.moment_id, core[best[1]]["o"])
        tail = _slice_rep(rep, move, suffix_id, params, "split", rep.moment_id)
        head = _slice_rep(rep, keep, rep.moment_id, params, "formed", None)
        rep.clear()
        for m in keep:
            rep.add(m["o"], head.features[m["o"]], m["role"], m["s"])
        rep.dirty = rep.structural = True
        reps.append(tail)
        touched.add(tail)
        queue.extend([rep, tail])


def stabilise(reps, now_ns, params):
    for rep in reps:
        if rep.state != "provisional" or not rep.has_core():
            continue
        if now_ns - rep.end >= params["stabilise_s"] * S:
            rep.state = "stable" if len(rep.core()) >= params["min_members"] else "insufficient"
            rep.dirty = True


def summary(rep, idf):
    core = rep.core()
    apps = Counter(rep.features[m["o"]]["app"] for m in core if rep.features[m["o"]]["app"])
    tok = Counter()
    for m in core:
        tok.update(rep.features[m["o"]]["tok"])
    weight = {k: math.sqrt(v) * idf(k) for k, v in tok.items()}
    top = [k for k, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    return {"apps": dict(apps.most_common()), "top_tokens": top, "core": len(core),
            "secondary": len(rep.members) - len(core), "duration_s": round(((rep.end or 0) - (rep.start or 0)) / S, 1)}


def due(rep, now_ns, params):
    """A provisional Moment that only grew is written at most every `revision_every_s`; a new one, a state change, a merge
    or a split is written at once. What is not written yet is not lost: its observations have stored features, and the
    next process assigns them again (`unassigned`)."""
    if rep.state != "provisional" or rep.structural or rep.written_ns is None:
        return True
    return now_ns - rep.written_ns >= params["revision_every_s"] * S


def write(store, reps, new_features, skips, idf, params, now_ns=None):
    now = now_iso()
    now_ns = now_ns if now_ns is not None else time.time_ns()
    written = []
    with store.derived_lock:
        store.derived.execute("BEGIN IMMEDIATE")
        try:
            for oid, f, ocr_id, structure_id in new_features:
                store.derived.execute("INSERT INTO observation_features VALUES (?,?,?,?,?,?)",
                                 (oid, F.ALGORITHM, json.dumps(f, sort_keys=True, separators=(",", ":")), ocr_id, structure_id, now))
            for oid, reason in skips:
                store.derived.execute("INSERT INTO moment_skips VALUES (?,?,?,?)", (oid, F.ALGORITHM, reason, now))
            for rep in reps:
                if not rep.dirty or not due(rep, now_ns, params):
                    continue
                latest = store.derived.execute("SELECT max(revision) FROM moment_revisions WHERE moment_id=?",
                                          (rep.moment_id,)).fetchone()[0] or 0
                if latest != rep.revision:
                    raise sqlite3.IntegrityError("stale moment revision")
                if rep.revision == 0:
                    store.derived.execute("INSERT INTO moments VALUES (?,?,?,?,?)",
                                     (rep.moment_id, ALGORITHM, rep.origin, rep.parent, rep.start))
                merged = rep.state == "merged"
                members = [] if merged else sorted(rep.members, key=lambda m: (m["t"], m["o"]))
                enc, blob, sha, _ = encode_raw(members)
                start, end = (rep.start, rep.end)
                store.derived.executemany("INSERT OR IGNORE INTO moment_member_index VALUES (?,?)",
                                     ((rep.moment_id, m["o"]) for m in rep.members))
                store.derived.execute("INSERT INTO moment_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (rep.moment_id, rep.revision + 1, rep.state, rep.merged_into, start, end,
                                  len(members), json.dumps(params, sort_keys=True), json.dumps(summary(rep, idf), sort_keys=True),
                                  enc, blob, sha, now))
                written.append(rep)
            store.derived.execute("COMMIT")
        except BaseException:
            derived_rollback(store)
            raise
    for rep in written:
        rep.revision, rep.dirty, rep.structural, rep.written_ns = rep.revision + 1, False, False, now_ns
    return len(written)


def run_pass(store, reader, now_ns, params=PARAMS, limit=300, since_ns=0, state=None):
    """One pass. `state` (a dict kept by the caller between passes) holds the provisional Moments in memory, so a pass
    reads only what is new; without it every pass starts from the database."""
    cache = None if state is None else state.setdefault("reps", {})
    first = state is None or not state.get("recovered")
    batch = ready_batch(reader, now_ns, params, since_ns, limit)
    reps = load_active(reader, params, cache)
    idf = Idf(r[0] for r in reader.execute(
        "SELECT features_json FROM observation_features WHERE algorithm=? ORDER BY rowid DESC LIMIT ?", (F.ALGORITHM, DF_WINDOW)))
    new_features, skips, touched = [], [], set()
    if first:
        lost = unassigned(reader, since_ns)
        stored = features_of(reader, lost)
        for oid in lost:
            if oid in stored:
                touched.add(assign(reps, oid, stored[oid], idf, params))
        if state is not None:
            state["recovered"] = True
    for oid, inputs in batch:
        f, reason = F.extract(inputs)
        if f is None or reason is not None:
            skips.append((oid, reason))
            continue
        ocr, structure = inputs["ocr"], inputs["structure"]
        new_features.append((oid, f, ocr[0] if ocr else None, structure[0] if structure else None))
        idf.add(f)
        touched.add(assign(reps, oid, f, idf, params))
    merge_pass(reps, touched, idf, params)
    split_pass(reps, touched, idf, params)
    stabilise(reps, now_ns, params)
    for rep in reps:
        if rep.state == "merged":
            rep.dirty = True
    try:
        written = write(store, reps, new_features, skips, idf, params, now_ns)
    except sqlite3.IntegrityError:
        if cache is not None:
            cache.clear()
            state["recovered"] = False
        return {"stale": 1, "processed": 0, "skipped": 0, "revisions": 0}
    if cache is not None:
        cache.clear()
        cache.update({r.moment_id: r for r in reps if r.state == "provisional"})
    return {"processed": len(new_features), "skipped": len(skips), "revisions": written}

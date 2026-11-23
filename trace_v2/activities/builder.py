import json
import sqlite3
import time
import uuid

import numpy as np

from ..observations.derived import derived_rollback
from ..observations.store import now_iso

ALGORITHM = "activities/1"
NAMESPACE = uuid.UUID("0b8f6a2e-5d1c-4c0e-9a77-3e5b1c2d4f60")
S = 1_000_000_000
PARAMS = {"cross_pairs": 4, "cross_min_pairs": 2, "cross_members": 30, "floor": 0.35, "neighbours": 6, "share": 0.75, "min_moments": 2, "horizon_s": 7 * 86400.0,
          "archive_after_s": 7 * 86400.0, "interpret_per_pass": 4, "assign_per_pass": 8, "max_attempts": 3}


def activity_uuid(moment_id):
    return str(uuid.uuid5(NAMESPACE, f"{ALGORITHM}:{moment_id}"))


def pack(vector):
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack(blob):
    return np.frombuffer(blob, dtype=np.float32)


def stable_moments(conn, model, version, limit, description_model=None):
    """Stable Moments the model has not interpreted yet. With `description_model`, only those that have a description."""
    from ..descriptions.vision import PROMPT_VERSION as DESCRIPTION_VERSION
    join, extra, args = "", "", [model, version]
    if description_model:
        join = "JOIN moment_descriptions d ON d.moment_id=r.moment_id AND d.model=? AND d.prompt_version=? AND d.status='ok' "
        args = [description_model, DESCRIPTION_VERSION] + args
    rows = conn.execute(
        "SELECT r.moment_id, r.start_ns, r.end_ns, r.summary_json, r.observation_count" + (", d.description" if description_model else ", NULL") +
        " FROM moment_revisions r " + join +
        "WHERE r.state='stable' AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) "
        "AND NOT EXISTS (SELECT 1 FROM activity_interpretations a WHERE a.moment_id=r.moment_id AND a.model=? AND a.prompt_version=?) "
        "ORDER BY r.end_ns, r.moment_id LIMIT ?", (*args, limit)).fetchall()
    return rows


def interpret_pending(store, reader, interpreter, params, attempts):
    done = failed = 0
    for moment_id, start, end, summary, count, description in stable_moments(
            reader, interpreter.model, interpreter.prompt_version, params["interpret_per_pass"],
            getattr(interpreter, "description_model", None)):
        moment = {"moment_id": moment_id, "start_ns": start, "end_ns": end, "summary": json.loads(summary), "observations": count,
                  "description": description}
        try:
            out = interpreter.interpret(moment)
            status = "clear" if out["clear"] and out["purpose"] else "unclear"
            vector = pack(interpreter.embed([out["purpose"]])[0]) if status == "clear" else None
        except Exception:
            attempts[moment_id] = attempts.get(moment_id, 0) + 1
            if attempts[moment_id] < params["max_attempts"]:
                failed += 1
                continue
            out, status, vector = {"action": "", "purpose": "", "why": ""}, "failed", None
        with store.derived_lock:
            store.derived.execute("INSERT OR IGNORE INTO activity_interpretations VALUES (?,?,?,?,?,?,?,?,?)",
                             (moment_id, interpreter.model, interpreter.prompt_version, status, out.get("action", ""),
                              out.get("purpose", ""), out.get("why", ""), vector, now_iso()))
            store.derived.commit()
        attempts.pop(moment_id, None)
        done += 1
    return done, failed


def unassigned(conn, model, version, limit):
    return conn.execute(
        "SELECT i.moment_id, r.start_ns, r.end_ns, i.action, i.purpose, i.why, i.vector FROM activity_interpretations i "
        "JOIN moment_revisions r ON r.moment_id=i.moment_id AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=i.moment_id) "
        "WHERE i.status='clear' AND i.model=? AND i.prompt_version=? "
        "AND NOT EXISTS (SELECT 1 FROM activity_member_index m WHERE m.moment_id=i.moment_id) "
        "ORDER BY r.start_ns, i.moment_id LIMIT ?", (model, version, limit)).fetchall()


def neighbours_of(conn, model, version, start_ns, horizon_ns):
    return conn.execute(
        "SELECT i.moment_id, m.activity_id, r.start_ns, r.end_ns, i.action, i.purpose, i.why, i.vector FROM activity_interpretations i "
        "JOIN activity_member_index m ON m.moment_id=i.moment_id "
        "JOIN moment_revisions r ON r.moment_id=i.moment_id AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=i.moment_id) "
        "WHERE i.status='clear' AND i.model=? AND i.prompt_version=? AND r.end_ns >= ? "
        "AND (SELECT state FROM activity_revisions q WHERE q.activity_id=m.activity_id ORDER BY revision DESC LIMIT 1) NOT IN ('archived', 'merged')",
        (model, version, start_ns - horizon_ns)).fetchall()


def verdict_for(store, reader, interpreter, a, b):
    """-> (verdict, reason), stored so the same pair is never judged twice. Raises when the judge fails."""
    first, second = (a, b) if a["moment_id"] < b["moment_id"] else (b, a)
    row = reader.execute("SELECT verdict, reason FROM activity_verdicts WHERE moment_a=? AND moment_b=? AND model=? AND prompt_version=?",
                         (first["moment_id"], second["moment_id"], interpreter.model, interpreter.prompt_version)).fetchone()
    if row:
        return row[0], row[1]
    verdict, reason = interpreter.same(a, b)
    verdict = verdict if verdict in ("SAME", "DIFFERENT") else "UNKNOWN"
    with store.derived_lock:
        store.derived.execute("INSERT OR IGNORE INTO activity_verdicts VALUES (?,?,?,?,?,?,?)",
                         (first["moment_id"], second["moment_id"], interpreter.model, interpreter.prompt_version, verdict, reason, now_iso()))
        store.derived.commit()
    return verdict, reason


def decide(store, reader, interpreter, moment, params):
    """-> (activity_id or None, evidence). Raises when a judge call failed, so the Moment is tried again later."""
    own = unpack(moment["vector"])
    pool = []
    for row in neighbours_of(reader, interpreter.model, interpreter.prompt_version, moment["start_ns"], int(params["horizon_s"] * S)):
        if row[0] == moment["moment_id"]:
            continue
        sim = float(own @ unpack(row[7]))
        if sim >= params["floor"]:
            pool.append((sim, row))
    pool.sort(key=lambda p: -p[0])
    checked = {}
    for sim, row in pool[:params["neighbours"]]:
        other = dict(zip(("moment_id", "activity_id", "start_ns", "end_ns", "action", "purpose", "why"), row[:7]))
        verdict, reason = verdict_for(store, reader, interpreter, moment, other)
        checked.setdefault(other["activity_id"], []).append(
            {"moment": other["moment_id"], "similarity": round(sim, 3), "verdict": verdict, "reason": reason})
    qualifying = []
    for activity_id, entries in checked.items():
        same = sum(1 for e in entries if e["verdict"] == "SAME")
        share = same / len(entries)
        if same and share >= params["share"]:
            qualifying.append((same, share, activity_id))
    qualifying.sort(reverse=True)
    best = qualifying[0] if qualifying else None
    evidence = {"checked": checked, "joined": best[2] if best else None, "share": best[1] if best else None}
    return (best[2] if best else None), evidence, [q[2] for q in qualifying]


def members_of(reader, interpreter, activity_id, limit):
    return reader.execute(
        "SELECT i.moment_id, r.start_ns, r.end_ns, i.action, i.purpose, i.why, i.vector FROM activity_member_index m "
        "JOIN activity_interpretations i ON i.moment_id=m.moment_id AND i.model=? AND i.prompt_version=? AND i.status='clear' "
        "JOIN moment_revisions r ON r.moment_id=i.moment_id AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=i.moment_id) "
        "WHERE m.activity_id=? ORDER BY r.end_ns DESC LIMIT ?", (interpreter.model, interpreter.prompt_version, activity_id, limit)).fetchall()


def cross_check(store, reader, interpreter, a_id, b_id, params):
    """Do the two Activities serve one goal? Judges the most similar member pairs across them. -> (agree, evidence)."""
    keys = ("moment_id", "start_ns", "end_ns", "action", "purpose", "why", "vector")
    a = [dict(zip(keys, r)) for r in members_of(reader, interpreter, a_id, params["cross_members"])]
    b = [dict(zip(keys, r)) for r in members_of(reader, interpreter, b_id, params["cross_members"])]
    pairs = sorted(((float(unpack(x["vector"]) @ unpack(y["vector"])), x, y) for x in a for y in b), key=lambda p: -p[0])
    pairs = [p for p in pairs if p[0] >= params["floor"]][:params["cross_pairs"]]
    if len(pairs) < params["cross_min_pairs"]:
        return False, {"pairs": len(pairs), "reason": "too little cross evidence"}
    judged = []
    for sim, x, y in pairs:
        verdict, reason = verdict_for(store, reader, interpreter, x, y)
        judged.append({"a": x["moment_id"], "b": y["moment_id"], "similarity": round(sim, 3), "verdict": verdict, "reason": reason})
    same = sum(1 for j in judged if j["verdict"] == "SAME")
    return same / len(judged) >= params["share"], {"pairs": judged, "share": same / len(judged)}


def merge_into(store, winner, losers, moment, evidence, params):
    """One transaction: the winner takes the losers' Moments and the new Moment; each loser gets a closing 'merged' revision."""
    now = now_iso()
    with store.derived_lock:
        db = store.derived
        db.execute("BEGIN IMMEDIATE")
        try:
            last = lambda a: db.execute("SELECT revision, state, start_ns, end_ns, moment_count FROM activity_revisions WHERE activity_id=? "
                                        "ORDER BY revision DESC LIMIT 1", (a,)).fetchone()
            top = last(winner)
            if top is None or top[1] in ("archived", "merged"):
                raise sqlite3.IntegrityError("winner is closed")
            added, start, end, count = [moment["moment_id"]], min(top[2], moment["start_ns"]), max(top[3], moment["end_ns"]), top[4] + 1
            for loser in losers:
                row = last(loser)
                if row is None or row[1] in ("archived", "merged"):
                    raise sqlite3.IntegrityError("loser is closed")
                members = [r[0] for r in db.execute("SELECT moment_id FROM activity_member_index WHERE activity_id=?", (loser,))]
                added += members
                start, end, count = min(start, row[2]), max(end, row[3]), count + row[4]
                db.execute("INSERT INTO activity_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (loser, row[0] + 1, "merged", row[2], row[3], row[4], "[]", json.dumps({"merged_into": winner}),
                            json.dumps(params, sort_keys=True), now, winner))
            db.executemany("INSERT OR IGNORE INTO activity_member_index VALUES (?,?)", ((winner, m) for m in added))
            db.execute("INSERT INTO activity_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (winner, top[0] + 1, "active" if count >= params["min_moments"] else "forming", start, end, count,
                        json.dumps(added), json.dumps({**evidence, "merged": list(losers)}, sort_keys=True),
                        json.dumps(params, sort_keys=True), now, None))
            db.execute("COMMIT")
        except BaseException:
            derived_rollback(store)
            raise


def append_revision(store, activity_id, added, start_ns, end_ns, evidence, params, state=None):
    now = now_iso()
    with store.derived_lock:
        store.derived.execute("BEGIN IMMEDIATE")
        try:
            last = store.derived.execute("SELECT revision, state, start_ns, end_ns, moment_count FROM activity_revisions WHERE activity_id=? "
                                    "ORDER BY revision DESC LIMIT 1", (activity_id,)).fetchone()
            if last is None:
                store.derived.execute("INSERT INTO activities VALUES (?,?,?)", (activity_id, ALGORITHM, start_ns))
                revision, count, start, end = 1, len(added), start_ns, end_ns
            else:
                revision, count = last[0] + 1, last[4] + len(added)
                start, end = min(last[2], start_ns), max(last[3], end_ns)
                if last[1] in ("archived", "merged"):
                    raise sqlite3.IntegrityError("activity is closed")
            store.derived.executemany("INSERT OR IGNORE INTO activity_member_index VALUES (?,?)", ((activity_id, m) for m in added))
            state = state or ("active" if count >= params["min_moments"] else "forming")
            store.derived.execute("INSERT INTO activity_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (activity_id, revision, state, start, end, count, json.dumps(added), json.dumps(evidence, sort_keys=True),
                              json.dumps(params, sort_keys=True), now, None))
            store.derived.execute("COMMIT")
        except BaseException:
            derived_rollback(store)
            raise


def archive_idle(store, reader, now_ns, params):
    rows = reader.execute(
        "SELECT r.activity_id, r.start_ns, r.end_ns FROM activity_revisions r WHERE r.state IN ('forming','active') "
        "AND r.end_ns < ? AND r.revision=(SELECT max(x.revision) FROM activity_revisions x WHERE x.activity_id=r.activity_id)",
        (now_ns - int(params["archive_after_s"] * S),)).fetchall()
    for activity_id, start, end in rows:
        append_revision(store, activity_id, [], start, end, {"archived": "idle"}, params, state="archived")
    return len(rows)


def run_pass(store, reader, interpreter, now_ns=None, params=PARAMS, state=None):
    now_ns = now_ns if now_ns is not None else time.time_ns()
    state = {} if state is None else state
    attempts = state.setdefault("attempts", {})
    out = {"interpreted": 0, "interpret_failed": 0, "joined": 0, "created": 0, "deferred": 0, "archived": 0, "merged": 0}
    out["interpreted"], out["interpret_failed"] = interpret_pending(store, reader, interpreter, params, attempts)
    for row in unassigned(reader, interpreter.model, interpreter.prompt_version, params["assign_per_pass"]):
        moment = dict(zip(("moment_id", "start_ns", "end_ns", "action", "purpose", "why", "vector"), row))
        try:
            target, evidence, qualifying = decide(store, reader, interpreter, moment, params)
            losers = []
            if len(qualifying) > 1:
                counts = {a: (reader.execute("SELECT moment_count FROM activity_revisions WHERE activity_id=? ORDER BY revision DESC LIMIT 1", (a,)).fetchone() or (0,))[0] for a in qualifying}
                target = max(qualifying, key=lambda a: (counts[a], a == target))
                evidence["merge_checks"] = {}
                for other in qualifying:
                    if other == target:
                        continue
                    agree, proof = cross_check(store, reader, interpreter, target, other, params)
                    evidence["merge_checks"][other] = proof
                    if agree:
                        losers.append(other)
        except Exception:
            out["deferred"] += 1
            continue
        if target is None:
            append_revision(store, activity_uuid(moment["moment_id"]), [moment["moment_id"]], moment["start_ns"], moment["end_ns"], evidence, params)
            out["created"] += 1
        elif losers:
            merge_into(store, target, losers, moment, evidence, params)
            out["joined"] += 1
            out["merged"] += len(losers)
        else:
            append_revision(store, target, [moment["moment_id"]], moment["start_ns"], moment["end_ns"], evidence, params)
            out["joined"] += 1
    out["archived"] = archive_idle(store, reader, now_ns, params)
    return out

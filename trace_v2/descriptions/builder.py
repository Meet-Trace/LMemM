import json
import time

from ..observations.store import now_iso
from .vision import PROMPT_VERSION, describe

PARAMS = {"per_pass": 1, "max_attempts": 3}


def pending(conn, model, limit):
    return conn.execute(
        "SELECT r.moment_id, r.start_ns, r.end_ns FROM moment_revisions r WHERE r.state='stable' "
        "AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) "
        "AND NOT EXISTS (SELECT 1 FROM moment_descriptions d WHERE d.moment_id=r.moment_id AND d.model=? AND d.prompt_version=?) "
        "ORDER BY r.end_ns, r.moment_id LIMIT ?", (model, PROMPT_VERSION, limit)).fetchall()


def record(store, moment_id, model, result, status):
    with store.derived_lock:
        store.derived.execute(
            "INSERT OR IGNORE INTO moment_descriptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (moment_id, model, PROMPT_VERSION, status, result.get("description") if status == "ok" else None,
             float(result.get("confidence") or 0.0), result.get("alternative"), json.dumps(result.get("evidence") or []),
             json.dumps(result.get("observation_ids") or []), int(result.get("images_used") or 0), result.get("seconds"),
             json.dumps(result.get("errors") or []), now_iso()))
        store.derived.commit()


def run_pass(store, reader, client, model, params=PARAMS, state=None, gate=None, options=None):
    state = {} if state is None else state
    attempts = state.setdefault("attempts", {})
    out = {"described": 0, "insufficient": 0, "failed": 0, "retry": 0, "paused": 0}
    for moment_id, start, end in pending(reader, model, params["per_pass"]):
        reason = gate() if gate else None
        if reason:
            state["paused"] = reason
            out["paused"] = 1
            break
        state.pop("paused", None)
        try:
            result = describe(store, reader, client, moment_id, start, end, options)
            if result["status"] == "invalid":
                raise ValueError("; ".join(result.get("errors") or ["invalid model answer"]))
        except Exception:
            attempts[moment_id] = attempts.get(moment_id, 0) + 1
            if attempts[moment_id] < params["max_attempts"]:
                out["retry"] += 1
                continue
            result = {"status": "failed", "errors": ["gave up after repeated failures"]}
        status = {"ok": "ok", "insufficient": "insufficient"}.get(result["status"], "failed")
        record(store, moment_id, model, result, status)
        attempts.pop(moment_id, None)
        out[{"ok": "described", "insufficient": "insufficient", "failed": "failed"}[status]] += 1
    return out

"""User-controlled, audited forgetting of Observations: the only path that deletes structured evidence.

forget(store, selector, dry_run=False) where selector is exactly one of:
    {"observation_ids": [...]}
    {"start_ns": a, "end_ns": b}             captured_at_ns in [a, b)
    {"app_bundle_id": "com.example", "start_ns"?: a, "end_ns"?: b}
    {"everything": True}

One transaction:
    1. forget_requests row (who: 'user', when, the selector, how many matched).
    2. deletion_grants rows for exactly the matched observations. The DELETE triggers refuse every row
       without a grant, so raw SQL or a bug elsewhere still cannot delete evidence.
    3. Delete events, extractions (every revision), screenshot deletion records, screenshot rows and the
       observations; write a forget_tombstone per observation (its id and pending file path, no evidence);
       remove the grants.
Then, outside the transaction: remove the screenshot files and write forget_finishes. A crash after the commit
leaves tombstones whose request has no finish; resume_pending() (run when the store opens) removes those files.
SQLite runs with secure_delete=ON for this, and the WAL is checkpointed and truncated afterwards so freed pages
do not linger. Migration backups (*.bak) are older copies of the database: `everything` removes them, and a
partial forget lists them in its result so the user can decide.
Tombstones keep the observation id so later layers can tell "forgotten on request" from "never existed".
"""

import uuid

from .store import canonical, now_iso

MAX_IDS = 10_000


class InvalidSelector(ValueError):
    pass


def _where(selector):
    if not isinstance(selector, dict) or not selector:
        raise InvalidSelector("selector must be a non-empty object")
    keys = set(selector)
    if keys == {"everything"}:
        if selector["everything"] is not True:
            raise InvalidSelector("everything must be true")
        return "1=1", []
    if keys == {"observation_ids"}:
        ids = selector["observation_ids"]
        if not isinstance(ids, list) or not ids or len(ids) > MAX_IDS or not all(isinstance(i, str) for i in ids):
            raise InvalidSelector(f"observation_ids must be a list of 1..{MAX_IDS} ids")
        return f"observation_id IN ({','.join('?' * len(ids))})", list(ids)
    if not keys <= {"app_bundle_id", "start_ns", "end_ns"} or not keys & {"app_bundle_id", "start_ns", "end_ns"}:
        raise InvalidSelector("selector must name observation_ids, a time range, an app, or everything")
    clauses, args = [], []
    for k, op in (("start_ns", ">="), ("end_ns", "<")):
        if k in selector:
            v = selector[k]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise InvalidSelector(f"{k} must be nanoseconds")
            clauses.append(f"captured_at_ns {op} ?")
            args.append(v)
    if "start_ns" in selector and "end_ns" in selector and selector["start_ns"] >= selector["end_ns"]:
        raise InvalidSelector("start_ns must be before end_ns")
    if "app_bundle_id" in selector:
        if not isinstance(selector["app_bundle_id"], str) or not selector["app_bundle_id"]:
            raise InvalidSelector("app_bundle_id must be a string")
        clauses.append("app_bundle_id = ?")
        args.append(selector["app_bundle_id"])
    return " AND ".join(clauses), args


def _backups(store):
    return sorted(str(p) for p in store.root.glob("*.bak"))


def forget(store, selector, dry_run=False):
    where, args = _where(selector)
    with store.lock:
        ids = [r[0] for r in store.db.execute(
            f"SELECT observation_id FROM observations WHERE {where} ORDER BY captured_at_ns, observation_id", args)]
        if dry_run:
            return {"dry_run": True, "matched": len(ids), "observation_ids": ids}
        request_id = str(uuid.uuid4())
        store.db.execute("PRAGMA secure_delete=ON")
        store.db.execute("BEGIN IMMEDIATE")
        try:
            store.db.execute("INSERT INTO forget_requests VALUES (?,?,?,?,?)",
                             (request_id, now_iso(), "user", canonical(selector), len(ids)))
            store.db.executemany("INSERT INTO deletion_grants VALUES (?,?)", ((i, request_id) for i in ids))
            store.db.execute(
                "INSERT INTO forget_tombstones SELECT s.observation_id, ?, "
                "CASE WHEN s.status='stored' AND d.observation_id IS NULL THEN s.relative_path END "
                "FROM screenshots s JOIN deletion_grants g USING (observation_id) "
                "LEFT JOIN screenshot_deletions d USING (observation_id) WHERE g.request_id=?",
                (request_id, request_id))
            granted = "observation_id IN (SELECT observation_id FROM deletion_grants WHERE request_id=?)"
            affected = "moment_id IN (SELECT moment_id FROM moment_member_index WHERE " + granted + ")"
            former = [r[0] for r in store.db.execute(
                "SELECT DISTINCT observation_id FROM moment_member_index WHERE " + affected, (request_id,))]
            held = ("activity_id IN (SELECT activity_id FROM activity_member_index WHERE moment_id IN "
                    "(SELECT moment_id FROM moment_member_index WHERE " + granted + "))")
            for table in ("activity_revisions", "activities", "activity_member_index"):
                store.db.execute(f"DELETE FROM {table} WHERE {held}", (request_id,))
            moment_held = "moment_id IN (SELECT moment_id FROM moment_member_index WHERE " + granted + ")"
            store.db.execute(f"DELETE FROM activity_interpretations WHERE {moment_held}", (request_id,))
            store.db.execute(f"DELETE FROM moment_descriptions WHERE {moment_held}", (request_id,))
            store.db.execute(f"DELETE FROM activity_verdicts WHERE moment_a IN (SELECT moment_id FROM moment_member_index WHERE {granted}) "
                             f"OR moment_b IN (SELECT moment_id FROM moment_member_index WHERE {granted})", (request_id, request_id))
            store.db.execute(f"DELETE FROM activity_member_index WHERE {moment_held}", (request_id,))
            for table in ("moment_revisions", "moments", "moment_member_index"):
                store.db.execute(f"DELETE FROM {table} WHERE {affected}", (request_id,))
            store.db.executemany("DELETE FROM observation_features WHERE observation_id=?", ((i,) for i in former))
            store.db.execute(f"DELETE FROM observation_features WHERE {granted}", (request_id,))
            store.db.execute(f"DELETE FROM moment_skips WHERE {granted}", (request_id,))
            for table in ("ocr_jobs", "interaction_events", "extractions", "screenshot_deletions", "screenshots", "observations"):
                store.db.execute(f"DELETE FROM {table} WHERE {granted}", (request_id,))
            store.db.execute("DELETE FROM deletion_grants WHERE request_id=?", (request_id,))
            store.db.execute("COMMIT")
        except BaseException:
            store._rollback()
            raise
        result = _finish(store, request_id)
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        removed_backups = []
        if selector == {"everything": True}:
            for path in _backups(store):
                store.root.joinpath(path).unlink(missing_ok=True)
                removed_backups.append(path)
    return {"dry_run": False, "request_id": request_id, "matched": len(ids), "observation_ids": ids, **result,
            "backups_removed": removed_backups, "backups_remaining": _backups(store)}


def _finish(store, request_id):
    removed = missing = 0
    for (rel,) in store.db.execute("SELECT screenshot_path FROM forget_tombstones WHERE request_id=? "
                                   "AND screenshot_path IS NOT NULL ORDER BY observation_id", (request_id,)).fetchall():
        if store.assets.delete(rel):
            removed += 1
        else:
            missing += 1
    store.db.execute("INSERT INTO forget_finishes VALUES (?,?,?,?)", (request_id, now_iso(), removed, missing))
    return {"files_removed": removed, "files_missing": missing}


def resume_pending(store):
    """Finish forget requests interrupted after their commit (files not yet removed). Returns their ids."""
    with store.lock:
        pending = [r[0] for r in store.db.execute(
            "SELECT r.request_id FROM forget_requests r LEFT JOIN forget_finishes f USING (request_id) "
            "WHERE f.request_id IS NULL ORDER BY r.requested_at")]
        for request_id in pending:
            _finish(store, request_id)
    return pending

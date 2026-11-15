"""Screenshot retention. Deletes screenshot FILES only; every structured record stays.

Policy (settings key 'screenshot_retention'):
    {"mode": "permanent"}                                     default
    {"mode": "duration", "preset": "5d"|"7d"|"14d"|"30d"}
    {"mode": "duration", "preset": "custom", "seconds": N}    N >= 3600

run(store, now_ns, dry_run=False) is deterministic for a given database, policy and now_ns:
    - writes a retention_runs row first, then a retention_run_finishes row at the end, so an interrupted
      run is visible (a start without a finish);
    - candidates are stored, not-yet-deleted screenshots with captured_at_ns < now_ns - duration,
      processed in (captured_at_ns, observation_id) order;
    - for each: the file is removed first, then a screenshot_deletions row is committed. A crash in between
      leaves a missing file and no row; the next run records it with file_existed = 0. The opposite order could
      leave a row claiming deletion while the image is still on disk, which is the unsafe failure;
    - dry_run records the run and what it would delete, and deletes nothing.
Observations, OCR, accessibility output, interaction events and the screenshots metadata row are never touched.
"""

import json
import uuid

from .store import canonical, now_iso

POLICY_KEY = "screenshot_retention"
PRESETS = {"5d": 5 * 86400, "7d": 7 * 86400, "14d": 14 * 86400, "30d": 30 * 86400}
PERMANENT = {"mode": "permanent"}
MIN_CUSTOM_SECONDS = 3600


class InvalidPolicy(ValueError):
    pass


def normalise(policy):
    if policy == PERMANENT:
        return dict(PERMANENT)
    if not isinstance(policy, dict) or policy.get("mode") != "duration":
        raise InvalidPolicy("policy must be {'mode': 'permanent'} or a duration")
    preset = policy.get("preset")
    if preset in PRESETS:
        if set(policy) - {"mode", "preset", "seconds"} or policy.get("seconds", PRESETS[preset]) != PRESETS[preset]:
            raise InvalidPolicy(f"preset {preset} has a fixed duration")
        return {"mode": "duration", "preset": preset, "seconds": PRESETS[preset]}
    seconds = policy.get("seconds")
    if preset != "custom" or set(policy) != {"mode", "preset", "seconds"}:
        raise InvalidPolicy("preset must be 5d, 7d, 14d, 30d or custom")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < MIN_CUSTOM_SECONDS:
        raise InvalidPolicy(f"custom duration must be a whole number of seconds >= {MIN_CUSTOM_SECONDS}")
    return {"mode": "duration", "preset": "custom", "seconds": seconds}


def get_policy(store):
    row = store.db.execute("SELECT value_json FROM settings WHERE key=?", (POLICY_KEY,)).fetchone()
    return json.loads(row[0]) if row else dict(PERMANENT)


def set_policy(store, policy):
    value = normalise(policy)
    with store.lock:
        store.db.execute("BEGIN IMMEDIATE")
        try:
            at = now_iso()
            store.db.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                             "value_json=excluded.value_json, updated_at=excluded.updated_at",
                             (POLICY_KEY, canonical(value), at))
            store.db.execute("INSERT INTO settings_history VALUES (?,?,?,?)",
                             (str(uuid.uuid4()), POLICY_KEY, canonical(value), at))
            store.db.execute("COMMIT")
        except BaseException:
            store._rollback()
            raise
    return value


def candidates(store, cutoff_ns):
    return store.db.execute(
        "SELECT s.observation_id, s.relative_path, s.byte_size FROM screenshots s "
        "LEFT JOIN screenshot_deletions d USING (observation_id) "
        "WHERE s.status='stored' AND d.observation_id IS NULL AND s.captured_at_ns < ? "
        "ORDER BY s.captured_at_ns, s.observation_id", (cutoff_ns,)).fetchall()


def run(store, now_ns, dry_run=False):
    with store.lock:
        policy = get_policy(store)
        cutoff = None if policy["mode"] == "permanent" else now_ns - policy["seconds"] * 1_000_000_000
        run_id = str(uuid.uuid4())
        store.db.execute("INSERT INTO retention_runs VALUES (?,?,?,?,?,?)",
                         (run_id, now_iso(), now_ns, canonical(policy), cutoff, int(dry_run)))
        rows = [] if cutoff is None else candidates(store, cutoff)
        counts = {"examined": len(rows), "deleted": 0, "already_missing": 0, "failed": 0, "bytes_freed": 0}
        affected = []
        for oid, rel, size in rows:
            if dry_run:
                affected.append(oid)
                continue
            try:
                existed = store.assets.delete(rel)
            except OSError:
                counts["failed"] += 1
                continue
            store.db.execute("INSERT INTO screenshot_deletions VALUES (?,?,?,?,?)",
                             (oid, now_iso(), "retention", run_id, int(existed)))
            affected.append(oid)
            if existed:
                counts["deleted"] += 1
                counts["bytes_freed"] += size or 0
            else:
                counts["already_missing"] += 1
        store.db.execute("INSERT INTO retention_run_finishes VALUES (?,?,?,?,?,?,?)",
                         (run_id, now_iso(), counts["examined"], counts["deleted"], counts["already_missing"],
                          counts["failed"], counts["bytes_freed"]))
    return {"run_id": run_id, "policy": policy, "cutoff_ns": cutoff, "dry_run": dry_run,
            "observation_ids": affected, **counts}

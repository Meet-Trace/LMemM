"""Screenshot retention. Deletes screenshot FILES only; every structured record stays.

Policy (settings key 'screenshot_retention'):
    {"mode": "thin", "seconds": S, "grace_seconds": G}        default: 7 days (S), grace 7 days (G)
    {"mode": "permanent"}
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
"thin" deletes the same files as a duration policy, but first keeps one small JPEG (screenshot_thumbnails, 640 px) for
each stable Moment: the middle observation whose screenshot still exists. While vision descriptions are in use, the
screenshots of a stable Moment that has no description yet are kept for up to `grace_seconds` more, so the model can still
see them. Dry runs create no thumbnails.
Observations, OCR, accessibility output, interaction events and the screenshots metadata row are never touched.
"""

import uuid

from . import ocr_queue, settings
from .store import canonical, now_iso

POLICY_KEY = "screenshot_retention"
PRESETS = {"5d": 5 * 86400, "7d": 7 * 86400, "14d": 14 * 86400, "30d": 30 * 86400}
PERMANENT = {"mode": "permanent"}
THIN = {"mode": "thin", "seconds": 7 * 86400, "grace_seconds": 7 * 86400}
THUMB_SIDE, THUMB_QUALITY = 640, 70
MIN_CUSTOM_SECONDS = 3600


class InvalidPolicy(ValueError):
    pass


def normalise(policy):
    if policy == PERMANENT:
        return dict(PERMANENT)
    if isinstance(policy, dict) and policy.get("mode") == "thin":
        if set(policy) - {"mode", "seconds", "grace_seconds"}:
            raise InvalidPolicy("thin takes only seconds and grace_seconds")
        out = {**THIN, **policy}
        for key, low in (("seconds", MIN_CUSTOM_SECONDS), ("grace_seconds", 0)):
            if isinstance(out[key], bool) or not isinstance(out[key], int) or out[key] < low:
                raise InvalidPolicy(f"{key} must be a whole number of seconds >= {low}")
        return out
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
    return settings.get(store, POLICY_KEY, dict(THIN))


def set_policy(store, policy):
    return settings.put(store, POLICY_KEY, normalise(policy))


def candidates(store, cutoff_ns):
    return store.db.execute(
        "SELECT s.observation_id, s.relative_path, s.byte_size FROM screenshots s "
        "LEFT JOIN screenshot_deletions d USING (observation_id) "
        "WHERE s.status='stored' AND d.observation_id IS NULL AND s.captured_at_ns < ? "
        "AND s.observation_id NOT IN (SELECT observation_id FROM ocr_jobs WHERE state IN ('queued','processing')) "
        "AND NOT EXISTS (SELECT 1 FROM extractions e WHERE e.observation_id = s.observation_id AND e.reader='ocr' "
        "AND e.status='pending' AND NOT EXISTS (SELECT 1 FROM extractions n WHERE n.observation_id = e.observation_id "
        "AND n.reader='ocr' AND n.revision > e.revision)) "
        "ORDER BY s.captured_at_ns, s.observation_id", (cutoff_ns,)).fetchall()


def protected(store, cutoff_ns):
    """Old enough to delete but kept because their OCR has not finished."""
    held = ocr_queue.protected(store)
    return [r[0] for r in store.db.execute(
        "SELECT s.observation_id FROM screenshots s LEFT JOIN screenshot_deletions d USING (observation_id) "
        "WHERE s.status='stored' AND d.observation_id IS NULL AND s.captured_at_ns < ? ORDER BY s.captured_at_ns, "
        "s.observation_id", (cutoff_ns,)) if r[0] in held]


def stable_moments_before(store, cutoff_ns):
    with store.derived_lock:
        return store.derived.execute(
            "SELECT r.moment_id, r.end_ns, r.members_encoding, r.members FROM moment_revisions r WHERE r.state='stable' AND r.end_ns < ? "
            "AND r.revision=(SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) ORDER BY r.end_ns", (cutoff_ns,)).fetchall()


def described(store, moment_id):
    with store.derived_lock:
        return store.derived.execute("SELECT 1 FROM moment_descriptions WHERE moment_id=? AND status='ok'", (moment_id,)).fetchone() is not None


def vision_in_use(store):
    with store.derived_lock:
        return store.derived.execute("SELECT 1 FROM moment_descriptions LIMIT 1").fetchone() is not None


def make_thumbnail(store, moment_id, members):
    """Keep a small JPEG of the member closest to the middle whose screenshot still exists. -> True when one was kept."""
    import hashlib
    import io
    from PIL import Image
    n = len(members)
    for i in sorted(range(n), key=lambda i: abs(i - n // 2)):
        oid = members[i]
        row = store.db.execute("SELECT s.relative_path, s.captured_at_ns FROM screenshots s LEFT JOIN screenshot_deletions d USING (observation_id) "
                               "WHERE s.observation_id=? AND s.status='stored' AND d.observation_id IS NULL", (oid,)).fetchone()
        if row is None or store.assets.is_quarantined(row[0]):
            continue
        try:
            with Image.open(io.BytesIO(store.assets.read(row[0]))) as image:
                image = image.convert("RGB")
                image.thumbnail((THUMB_SIDE, THUMB_SIDE))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=THUMB_QUALITY)
                width, height = image.size
        except Exception:                                   # noqa: BLE001 - an unreadable frame: try the next member
            continue
        data = buffer.getvalue()
        rel = store.assets.thumbnail_path(oid, row[1])
        store.assets.write(rel, data)
        try:
            store.db.execute("INSERT INTO screenshot_thumbnails VALUES (?,?,?,?,?,?,?,?,?)",
                             (oid, moment_id, rel, "jpeg", width, height, len(data), hashlib.sha256(data).hexdigest(), now_iso()))
        except BaseException:
            store.assets.delete(rel)
            raise
        return True
    return False


def thin_prepare(store, policy, now_ns, cutoff, dry_run):
    """-> (observation ids to keep for now, thumbnails made). Keeps the screenshots of Moments still waiting for a description."""
    from .queries import _decode
    kept, made = set(), 0
    use_vision = vision_in_use(store)
    done = {r[0] for r in store.db.execute("SELECT moment_id FROM screenshot_thumbnails")}
    grace_cutoff = now_ns - (policy["seconds"] + policy["grace_seconds"]) * 1_000_000_000
    for moment_id, end_ns, encoding, blob in stable_moments_before(store, cutoff):
        if moment_id in done:
            continue
        members = sorted((m for m in _decode(encoding, blob) if m["role"] == "core"), key=lambda m: (m["t"], m["o"]))
        ids = [m["o"] for m in members]
        if use_vision and end_ns >= grace_cutoff and not described(store, moment_id):
            kept.update(ids)
            continue
        if not dry_run and ids and make_thumbnail(store, moment_id, ids):
            made += 1
    return kept, made


def run(store, now_ns, dry_run=False):
    with store.lock:
        policy = get_policy(store)
        cutoff = None if policy["mode"] == "permanent" else now_ns - policy["seconds"] * 1_000_000_000
        run_id = str(uuid.uuid4())
        store.db.execute("INSERT INTO retention_runs VALUES (?,?,?,?,?,?)",
                         (run_id, now_iso(), now_ns, canonical(policy), cutoff, int(dry_run)))
        rows = [] if cutoff is None else candidates(store, cutoff)
        held = [] if cutoff is None else protected(store, cutoff)
        thumbnails = 0
        if policy["mode"] == "thin":
            waiting, thumbnails = thin_prepare(store, policy, now_ns, cutoff, dry_run)
            rows = [r for r in rows if r[0] not in waiting]
            held = held + sorted(o for o in waiting if o not in held)
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
            "observation_ids": affected, "thumbnails": thumbnails, "protected": len(held), "protected_observation_ids": held, **counts}

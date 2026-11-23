"""Raw output trimming. After `raw_retention_days` (default 30) the bulky raw reader output of an observation (OCR
positions, accessibility tree, layout) is emptied. The extraction row, its status, its plain text, the observation, its
screenshot metadata and events are untouched, and so are Moments, descriptions and Activities (derived.sqlite3).

Every trimmed row is recorded in `raw_trims` (with the size and checksum of what was removed) before the update, and the
extractions update trigger allows only that change. A row that a younger observation still points at (extraction_links) is
left for a later run. Trimming never runs on a dry run; the dry run only counts.
"""

from . import settings
from .store import now_iso

KEY = "raw_retention_days"
DEFAULT_DAYS = 30
BATCH = 500


def get_days(store):
    value = settings.get(store, KEY, DEFAULT_DAYS)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("raw_retention_days must be a whole number of days >= 1, or null to keep everything")
    return value


def set_days(store, days):
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 1):
        raise ValueError("raw_retention_days must be a whole number of days >= 1, or null to keep everything")
    return settings.put(store, KEY, days)


_DUE = ("FROM extractions e JOIN observations o USING (observation_id) "
        "WHERE e.raw IS NOT NULL AND o.captured_at_ns < ? "
        "AND NOT EXISTS (SELECT 1 FROM extraction_links l JOIN extractions le ON le.extraction_id = l.extraction_id "
        "JOIN observations lo ON lo.observation_id = le.observation_id "
        "WHERE l.target_extraction_id = e.extraction_id AND lo.captured_at_ns >= ?)")


def run(store, now_ns, dry_run=False, batch=BATCH):
    """-> {"days", "trimmed", "bytes_freed"} for one pass of at most `batch` rows (call again for the rest)."""
    days = get_days(store)
    if days is None:
        return {"days": None, "trimmed": 0, "bytes_freed": 0}
    cutoff = now_ns - days * 86400 * 1_000_000_000
    with store.lock:
        if dry_run:
            n, size = store.db.execute(f"SELECT count(*), coalesce(sum(e.raw_bytes), 0) {_DUE}", (cutoff, cutoff)).fetchone()
            return {"days": days, "trimmed": 0, "bytes_freed": 0, "would_trim": n, "would_free": size}
        rows = store.db.execute(f"SELECT e.extraction_id, e.raw_bytes, e.raw_sha256 {_DUE} "
                                "ORDER BY o.captured_at_ns, e.extraction_id LIMIT ?", (cutoff, cutoff, batch)).fetchall()
        if not rows:
            return {"days": days, "trimmed": 0, "bytes_freed": 0}
        store.db.execute("BEGIN IMMEDIATE")
        try:
            at = now_iso()
            store.db.executemany("INSERT INTO raw_trims VALUES (?,?,?,?)", ((eid, at, size, sha) for eid, size, sha in rows))
            ids = [(eid,) for eid, _, _ in rows]
            store.db.executemany("DELETE FROM extraction_raw_copies WHERE extraction_id=?", ids)
            store.db.executemany("UPDATE extractions SET raw=NULL, raw_encoding=NULL, raw_sha256=NULL, raw_bytes=NULL "
                                 "WHERE extraction_id=?", ids)
            store.db.execute("COMMIT")
        except BaseException:
            store._rollback()
            raise
    return {"days": days, "trimmed": len(rows), "bytes_freed": sum(r[1] or 0 for r in rows)}

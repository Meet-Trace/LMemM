"""Read side of the Observation store. Assembles the logical Observation contract from the tables."""

import json
import zlib

from .assets import AssetError
from .models import READERS


class ScreenshotUnavailable(LookupError):
    """The screenshot cannot be returned; `reason` is 'failed', 'withheld', 'deleted', 'missing' or 'corrupt'."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason


def _iso(ns):
    from .store import iso
    return None if ns is None else iso(ns)


def _decode(encoding, blob):
    if blob is None:
        return None
    if encoding != "json+zlib":
        raise ValueError(f"unknown raw encoding {encoding!r}")
    return json.loads(zlib.decompress(blob))


EXTRACTION_COLS = ("extraction_id", "revision", "supersedes", "reader_version", "status", "error_code",
                   "error_message", "started_at_ns", "finished_at_ns", "text", "raw_encoding", "raw",
                   "raw_sha256", "raw_bytes", "recorded_at", "source_extraction_id", "privacy_json")


def extractions(store, oid, reader, include_raw=True):
    rows = store.db.execute(f"SELECT {','.join(EXTRACTION_COLS)} FROM extractions WHERE observation_id=? "
                            "AND reader=? ORDER BY revision", (oid, reader)).fetchall()
    out = []
    for row in rows:
        r = dict(zip(EXTRACTION_COLS, row))
        enc, blob = r.pop("raw_encoding"), r.pop("raw")
        r["privacy"] = None if r.pop("privacy_json") is None else json.loads(row[-1])
        if include_raw:
            r["raw"] = _decode(enc, blob)
        out.append(r)
    return out


def events(store, oid):
    return [{"event_id": eid, "seq": seq, "timestamp_ns": t, "timestamp": _iso(t), "type": kind,
             "payload": json.loads(payload)}
            for seq, eid, t, kind, payload in store.db.execute(
                "SELECT seq, event_id, occurred_at_ns, type, payload_json FROM interaction_events "
                "WHERE observation_id=? ORDER BY seq", (oid,))]


SHOT_COLS = ("status", "relative_path", "format", "width", "height", "byte_size", "sha256", "captured_at_ns",
             "error_code", "error_message")


def _screenshot(store, oid):
    row = store.db.execute(f"SELECT {','.join('s.' + c for c in SHOT_COLS)}, d.deleted_at, d.reason, d.run_id "
                           "FROM screenshots s LEFT JOIN screenshot_deletions d USING (observation_id) "
                           "WHERE s.observation_id=?", (oid,)).fetchone()
    shot = dict(zip(SHOT_COLS, row[:len(SHOT_COLS)]))
    shot["captured_at"] = _iso(shot["captured_at_ns"])
    shot["reference"] = shot.pop("relative_path")
    shot["deletion"] = None if row[-3] is None else {"deleted_at": row[-3], "reason": row[-2], "run_id": row[-1]}
    shot["available"] = shot["status"] == "stored" and shot["deletion"] is None
    return shot


def get(store, oid, include_raw=True, include_events=True):
    row = store.db.execute("SELECT captured_at_ns, context_json, capture_status_json, capture_version, capture_key, "
                           "events_window_start_ns, events_window_end_ns, recorded_at, privacy_json FROM observations "
                           "WHERE observation_id=?", (oid,)).fetchone()
    if row is None:
        raise KeyError(f"no observation {oid}")
    t, ctx, status, version, key, w0, w1, recorded, privacy = row
    status = json.loads(status)
    shot = _screenshot(store, oid)
    revisions = {r: extractions(store, oid, r, include_raw) for r in READERS}
    original = {"ocr": revisions["ocr"][-1], "accessibility": revisions["accessibility"][0]}
    earlier = {"ocr": revisions["ocr"][:-1], "accessibility": revisions["accessibility"][1:]}
    record = {
        "observation_id": oid, "timestamp": _iso(t), "timestamp_ns": t,
        "context": json.loads(ctx),
        "evidence": {"screenshot": shot, **original},
        "metadata": {
            "reader_versions": {r: original[r]["reader_version"] for r in READERS},
            "capture_version": version, "capture_key": key, "recorded_at": recorded,
            "events_window_ns": [w0, w1], "privacy": json.loads(privacy),
            "extraction_status": {
                "context": status["context"], "events": status["events"],
                "screenshot": {"status": shot["status"], "error_code": shot["error_code"]},
                **{r: {"status": original[r]["status"], "error_code": original[r]["error_code"]} for r in READERS}},
            "later_extractions": earlier,
        },
    }
    if include_events:
        record["interaction_events"] = events(store, oid)
    return record


def list_observations(store, start_ns, end_ns, app_bundle_id, limit, after):
    """Keyset-paginated summaries ordered by (timestamp, id). Pass the last row's (timestamp_ns, id) as `after`."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    where, args = [], []
    if start_ns is not None:
        where.append("captured_at_ns >= ?"); args.append(start_ns)
    if end_ns is not None:
        where.append("captured_at_ns < ?"); args.append(end_ns)
    if app_bundle_id is not None:
        where.append("app_bundle_id = ?"); args.append(app_bundle_id)
    if after is not None:
        where.append("(captured_at_ns, observation_id) > (?, ?)"); args.extend(after)
    sql = ("SELECT observation_id, captured_at_ns, app_bundle_id, app_name, window_title FROM observations"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY captured_at_ns, observation_id LIMIT ?")
    return [{"observation_id": i, "timestamp_ns": t, "timestamp": _iso(t), "app_bundle_id": b, "app_name": n,
             "window_title": w} for i, t, b, n, w in store.db.execute(sql, (*args, limit))]


def screenshot_bytes(store, oid):
    row = store.db.execute("SELECT s.status, s.relative_path, s.sha256, d.observation_id FROM screenshots s "
                           "LEFT JOIN screenshot_deletions d USING (observation_id) WHERE s.observation_id=?",
                           (oid,)).fetchone()
    if row is None:
        raise KeyError(f"no observation {oid}")
    status, rel, digest, deleted = row
    if status != "stored":
        raise ScreenshotUnavailable(status)              # 'failed' or 'withheld'
    if deleted:
        raise ScreenshotUnavailable("deleted")
    try:
        return store.assets.read(rel, digest)
    except FileNotFoundError:
        raise ScreenshotUnavailable("missing") from None
    except AssetError as error:
        raise ScreenshotUnavailable("corrupt", str(error)) from None


def thumbnail_bytes(store, oid):
    """The small JPEG kept for this observation when its full screenshot was thinned out."""
    row = store.db.execute("SELECT relative_path, sha256 FROM screenshot_thumbnails WHERE observation_id=?", (oid,)).fetchone()
    if row is None:
        raise ScreenshotUnavailable("no_thumbnail")
    try:
        return store.assets.read(row[0], row[1])
    except FileNotFoundError:
        raise ScreenshotUnavailable("missing") from None


def audit(store):
    """Files on disk without a row (left by a crash between file write and commit), and rows whose file is gone
    without a recorded deletion. Read-only: reports, never deletes."""
    live = {rel for (rel,) in store.db.execute(
        "SELECT s.relative_path FROM screenshots s LEFT JOIN screenshot_deletions d USING (observation_id) "
        "WHERE s.status='stored' AND d.observation_id IS NULL")}
    known = {rel for (rel,) in store.db.execute("SELECT relative_path FROM screenshots WHERE relative_path IS NOT NULL")}
    on_disk = store.assets.all_relative_paths()
    return {"orphan_files": sorted(on_disk - known), "missing_files": sorted(live - on_disk)}

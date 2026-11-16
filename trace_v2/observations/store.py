"""The Observation store: one atomic, append-only write per Observation, plus versioned reader re-runs.

ingest(draft) -> IngestResult(observation_id, created)
    1. Validates the draft (InvalidCapture: nothing stored).
    2. Duplicate check on capture_key: the same key with the same content returns the existing id
       (created=False, a safe retry); the same key with different content raises DuplicateObservation.
    3. Writes the screenshot file (if one was captured). A failed file write is recorded as a failed
       screenshot; it does not stop the Observation.
    4. One transaction inserts the observation, its screenshot row, both reader results (revision 1)
       and every interaction event. On any failure it rolls back and removes the file written in step 3.
add_extraction(observation_id, reader, result) appends revision n+1; revision 1 is never replaced.
Reads live in queries.py and are exposed here as methods.
"""

import hashlib
import json
import logging
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

from . import queries
from .assets import AssetError, ScreenshotStore, magic_matches, sha256
from .db import open_database
from .models import (READERS, InvalidCapture, ReaderResult, ScreenshotCapture, ScreenshotWithheld, short_error)
from .. import paths

log = logging.getLogger("trace_v2.observations")


class DuplicateObservation(RuntimeError):
    """capture_key was already used for a different capture."""


@dataclass(frozen=True)
class IngestResult:
    observation_id: str
    created: bool


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec="microseconds")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def encode_raw(raw):
    """-> (encoding, blob, sha256 of the JSON, JSON byte count), or all None when there is no output."""
    if raw is None:
        return None, None, None, None
    text = json.dumps(raw, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return "json+zlib", zlib.compress(text, 6), sha256(text), len(text)


def content_hash(draft):
    shot = draft.screenshot
    shot_view = ({"sha256": sha256(bytes(shot.data)), "format": shot.format, "w": shot.width, "h": shot.height,
                  "t": shot.captured_at_ns} if isinstance(shot, ScreenshotCapture)
                 else {"withheld": shot.reason} if isinstance(shot, ScreenshotWithheld) else {"failed": shot.error_code})
    readers = {r: {k: getattr(getattr(draft, r), k) for k in ("reader_version", "status", "raw", "text", "error_code")}
               for r in READERS}
    return hashlib.sha256(canonical({
        "t": draft.captured_at_ns, "context": [draft.context.application, draft.context.window, draft.context.display],
        "shot": shot_view, "readers": readers, "capture_version": draft.capture_version,
        "events": [[e.type, e.occurred_at_ns, e.payload] for e in draft.events], "privacy": draft.privacy,
    }).encode()).hexdigest()


class ObservationStore:
    def __init__(self, root, legacy_archive=paths.LEGACY_ARCHIVE):
        self.root = paths.check_data_dir(root, legacy_archive)
        self.db = open_database(paths.database_path(self.root))
        self.assets = ScreenshotStore(paths.assets_dir(self.root))
        self.lock = threading.RLock()
        self.clock_ns = time.time_ns
        self.ocr_max_attempts = 4
        from .forget import resume_pending     # finish any forget interrupted after its commit
        resume_pending(self)

    def close(self):
        self.db.close()

    # ---------------------------------------------------------------- writes

    def ingest(self, draft):
        draft.validate()
        digest = content_hash(draft)
        with self.lock:
            existing = self._existing(draft.capture_key, digest)
            if existing:
                return IngestResult(existing, False)
            oid = str(uuid.uuid4())
            shot_row, written = self._store_screenshot(oid, draft.screenshot)
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self._insert(oid, draft, digest, shot_row, self._ocr_plan(draft, shot_row))
                self.db.execute("COMMIT")
            except BaseException:
                self._rollback()
                if written:
                    self.assets.delete(written)
                if self._existing(draft.capture_key, digest):     # lost a race to an identical retry
                    return IngestResult(self._existing(draft.capture_key, digest), False)
                raise
        return IngestResult(oid, True)

    def add_extraction(self, observation_id, reader, result):
        """Record a re-run of one reader. Returns the new extraction_id."""
        if reader not in READERS or not isinstance(result, ReaderResult):
            raise InvalidCapture("reader must be 'ocr' or 'accessibility' with a ReaderResult")
        result.validate(reader)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                prev = self.db.execute(
                    "SELECT extraction_id, revision FROM extractions WHERE observation_id=? AND reader=? "
                    "ORDER BY revision DESC LIMIT 1", (observation_id, reader)).fetchone()
                if prev is None:
                    raise KeyError(f"no observation {observation_id}")
                eid = self._insert_extraction(observation_id, reader, result, prev[1] + 1, prev[0])
                self.db.execute("COMMIT")
            except BaseException:
                self._rollback()
                raise
        return eid

    def _rollback(self):
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")

    def _existing(self, capture_key, digest):
        row = self.db.execute("SELECT observation_id, content_sha256 FROM observations WHERE capture_key=?",
                              (capture_key,)).fetchone()
        if row is None:
            return None
        if row[1] != digest:
            raise DuplicateObservation(f"capture_key {capture_key!r} was already used for a different capture")
        return row[0]

    def _store_screenshot(self, oid, shot):
        """-> (row values for the screenshots table, relative path written or None)."""
        if isinstance(shot, ScreenshotWithheld):
            return dict(status="withheld", captured_at_ns=shot.captured_at_ns, error_code=shot.reason), None
        if not isinstance(shot, ScreenshotCapture):
            return dict(status="failed", captured_at_ns=shot.captured_at_ns, error_code=shot.error_code,
                        error_message=short_error(shot.error_message)), None
        data = bytes(shot.data)
        if not magic_matches(shot.format, data):
            return dict(status="failed", captured_at_ns=shot.captured_at_ns, error_code="format_mismatch",
                        error_message=f"bytes are not {shot.format}"), None
        rel = self.assets.relative_path(oid, shot.captured_at_ns, shot.format)
        try:
            self.assets.write(rel, data)
        except (OSError, AssetError) as error:
            log.warning("screenshot write failed: %s", type(error).__name__)
            return dict(status="failed", captured_at_ns=shot.captured_at_ns, error_code="asset_write_failed",
                        error_message=short_error(type(error).__name__)), None
        return dict(status="stored", relative_path=rel, format=shot.format, width=shot.width, height=shot.height,
                    byte_size=len(data), sha256=sha256(data), captured_at_ns=shot.captured_at_ns), rel

    def _ocr_plan(self, draft, shot_row):
        """-> (OCR result for revision 1, enqueue a job). Only a stored screenshot is queued; a full queue skips OCR."""
        ocr = draft.ocr
        if ocr.status != "pending":
            return ocr, False
        if shot_row["status"] != "stored":
            return ReaderResult(ocr.reader_version, "skipped", error_code="no_screenshot"), False
        from . import ocr_queue
        from .settings import get_ocr_queue_cap
        if ocr_queue.depth(self) >= get_ocr_queue_cap(self):
            return ReaderResult(ocr.reader_version, "skipped", error_code="QueueFull"), False
        return ocr, True

    def _insert(self, oid, draft, digest, shot_row, plan):
        ctx = draft.context
        app, win, disp = ctx.application, ctx.window, ctx.display
        status = {"context": {"status": "failed" if ctx.error_code else "succeeded", "error_code": ctx.error_code},
                  "events": draft.event_stats}
        self.db.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, draft.captured_at_ns, iso(draft.captured_at_ns), draft.capture_key, digest,
             app.get("bundle_id"), app.get("name"), app.get("pid") if isinstance(app.get("pid"), int) else None,
             win.get("title"), None if disp.get("id") is None else str(disp.get("id")),
             canonical({"application": app, "window": win, "display": disp}),
             draft.events_window[0], draft.events_window[1], canonical(status), canonical(draft.privacy), draft.capture_version, now_iso()))
        cols = ("status", "relative_path", "format", "width", "height", "byte_size", "sha256",
                "captured_at_ns", "error_code", "error_message")
        self.db.execute(f"INSERT INTO screenshots(observation_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})",
                        (oid, *(shot_row.get(c) for c in cols)))
        ocr, enqueue = plan
        self._insert_extraction(oid, "ocr", ocr, 1, None)
        self._insert_extraction(oid, "accessibility", draft.accessibility, 1, None)
        if enqueue:
            from . import ocr_queue
            ocr_queue.enqueue(self, oid, self.clock_ns(), self.ocr_max_attempts)
        self.db.executemany(
            "INSERT INTO interaction_events VALUES (?,?,?,?,?,?)",
            ((oid, seq, e.event_id or str(uuid.uuid4()), e.occurred_at_ns, e.type, canonical(e.payload))
             for seq, e in enumerate(draft.events)))

    def _insert_extraction(self, oid, reader, r, revision, supersedes, privacy_json=None, source_extraction_id=None):
        eid = str(uuid.uuid4())
        encoding, blob, raw_sha, raw_bytes = encode_raw(r.raw)
        self.db.execute(
            "INSERT INTO extractions(extraction_id, observation_id, reader, revision, supersedes, reader_version, status, "
            "error_code, error_message, started_at_ns, finished_at_ns, text, raw_encoding, raw, raw_sha256, raw_bytes, "
            "recorded_at, privacy_json, source_extraction_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, oid, reader, revision, supersedes, r.reader_version, r.status, r.error_code,
             short_error(r.error_message), r.started_at_ns, r.finished_at_ns, r.text, encoding, blob,
             raw_sha, raw_bytes, now_iso(), privacy_json, source_extraction_id))
        return eid

    # ---------------------------------------------------------------- reads (queries.py)
    # One connection serves every thread, so reads take the same lock as writes.

    def get(self, observation_id, include_raw=True, include_events=True):
        with self.lock:
            return queries.get(self, observation_id, include_raw, include_events)

    def list(self, start_ns=None, end_ns=None, app_bundle_id=None, limit=100, after=None):
        with self.lock:
            return queries.list_observations(self, start_ns, end_ns, app_bundle_id, limit, after)

    def events(self, observation_id):
        with self.lock:
            return queries.events(self, observation_id)

    def extractions(self, observation_id, reader):
        with self.lock:
            return queries.extractions(self, observation_id, reader, include_raw=True)

    def screenshot_bytes(self, observation_id):
        with self.lock:
            return queries.screenshot_bytes(self, observation_id)

    def audit(self):
        with self.lock:
            return queries.audit(self)

    def ocr_queue_stats(self):
        from . import ocr_queue
        return ocr_queue.stats(self, self.clock_ns())

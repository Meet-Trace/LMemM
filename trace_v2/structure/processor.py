import logging
import sqlite3
import threading
import time

from .. import paths
from ..observations.derived import derived_path
from ..observations.models import ReaderResult, short_error
from ..observations.queries import ScreenshotUnavailable, _decode, raw_of
from .analyze import ALGORITHM, StructureError, analyze

log = logging.getLogger("trace_v2.structure")

S = 1_000_000_000
READER = "structure"

_LATEST_OCR = ("(SELECT y.extraction_id FROM extractions y WHERE y.observation_id=o.observation_id AND y.reader='ocr' "
               "ORDER BY y.revision DESC LIMIT 1)")
_NEEDED = f"""
SELECT o.observation_id FROM observations o
JOIN screenshots s ON s.observation_id=o.observation_id AND s.status='stored'
LEFT JOIN screenshot_deletions d ON d.observation_id=o.observation_id
WHERE d.observation_id IS NULL AND o.captured_at_ns >= ?
  AND NOT EXISTS (SELECT 1 FROM extractions st WHERE st.observation_id=o.observation_id AND st.reader='structure'
                  AND st.source_extraction_id IS {_LATEST_OCR}
                  AND st.revision=(SELECT max(x.revision) FROM extractions x
                                   WHERE x.observation_id=o.observation_id AND x.reader='structure'))
  AND NOT (o.captured_at_ns > ? AND EXISTS (SELECT 1 FROM extractions p WHERE p.extraction_id={_LATEST_OCR}
                                            AND p.status='pending'))
ORDER BY o.captured_at_ns, o.observation_id LIMIT ?
"""


def needed(store, now_ns, wait_for_ocr_s=30.0, since_ns=0, limit=16, connection=None):
    args = (since_ns, now_ns - int(wait_for_ocr_s * S), limit)
    if connection is not None:
        return [r[0] for r in connection.execute(_NEEDED, args)]
    with store.lock:
        return [r[0] for r in store.db.execute(_NEEDED, args)]


def read_connection(store):
    connection = sqlite3.connect(f"file:{paths.database_path(store.root)}?mode=ro", uri=True, check_same_thread=False)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("ATTACH DATABASE ? AS derived", (f"file:{derived_path(store.root)}?mode=ro",))
    return connection


def _latest(db, oid, reader):
    return db.execute("SELECT extraction_id, revision, reader_version, status, raw_encoding, raw FROM extractions "
                      "WHERE observation_id=? AND reader=? ORDER BY revision DESC LIMIT 1", (oid, reader)).fetchone()


def _inputs(store, oid):
    with store.lock:
        shot = store.db.execute("SELECT sha256, width, height FROM screenshots WHERE observation_id=?",
                                (oid,)).fetchone()
        ocr = _latest(store.db, oid, "ocr")
    if shot is None or ocr is None:
        return None, None, None
    source = {"observation_id": oid, "screenshot_sha256": shot[0]}
    ocr_info = {"extraction_id": ocr[0], "revision": ocr[1], "reader_version": ocr[2], "status": ocr[3],
                "raw": raw_of(store.db, ocr[0], ocr[4], ocr[5]) if ocr[3] == "succeeded" else None}
    return source, ocr_info, shot


def build(store, oid, clock_ns=time.time_ns):
    source, ocr, _ = _inputs(store, oid)
    if source is None:
        return "gone", None
    started = clock_ns()
    try:
        data = store.screenshot_bytes(oid)
    except ScreenshotUnavailable as error:
        result = ReaderResult(ALGORITHM, "unavailable", None, None, "ScreenshotUnavailable", str(error), started,
                              clock_ns())
    except KeyError:
        return "gone", None
    else:
        try:
            raw = analyze(data, ocr, source)
            result = ReaderResult(ALGORITHM, "succeeded", raw, None, None, None, started, clock_ns())
        except StructureError as error:
            result = ReaderResult(ALGORITHM, "failed", None, None, error.code, str(error), started, clock_ns())
        except Exception as error:
            log.exception("structure analysis failed for %s", oid)
            result = ReaderResult(ALGORITHM, "failed", None, None, "AnalysisError",
                                  short_error(f"{type(error).__name__}: {error}"), started, clock_ns())
        finally:
            del data
    return append(store, oid, result, ocr["extraction_id"])


def append(store, oid, result, source_extraction_id):
    with store.lock:
        store.db.execute("BEGIN IMMEDIATE")
        try:
            ocr = _latest(store.db, oid, "ocr")
            if ocr is None:
                store.db.execute("ROLLBACK")
                return "gone", None
            if ocr[0] != source_extraction_id:
                store.db.execute("ROLLBACK")
                return "stale", None
            prev = store.db.execute("SELECT extraction_id, revision, source_extraction_id FROM extractions "
                                    "WHERE observation_id=? AND reader='structure' ORDER BY revision DESC LIMIT 1",
                                    (oid,)).fetchone()
            if prev is not None and prev[2] == source_extraction_id:
                store.db.execute("ROLLBACK")
                return "duplicate", prev[0]
            eid = store._insert_extraction(oid, READER, result, (prev[1] + 1) if prev else 1,
                                           prev[0] if prev else None, None, source_extraction_id)
            store.db.execute("COMMIT")
        except BaseException:
            store._rollback()
            raise
    return result.status, eid


class StructureProcessor:
    def __init__(self, store, wait_for_ocr_s=30.0, lookback_s=6 * 3600, idle_s=2.0, clock_ns=time.time_ns):
        self.store, self.wait_for_ocr_s, self.lookback_s = store, wait_for_ocr_s, lookback_s
        self.idle_s, self.clock_ns = idle_s, clock_ns
        self.counts = {}
        self.timing_ms = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._reader = None

    def _count(self, name):
        with self._lock:
            self.counts[name] = self.counts.get(name, 0) + 1

    def stats(self):
        with self._lock:
            t = sorted(self.timing_ms)
        out = dict(self.counts)
        if t:
            out["ms_p50"] = round(t[len(t) // 2], 1)
            out["ms_max"] = round(t[-1], 1)
        return out

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="trace-v2-structure", daemon=True)
            self._thread.start()

    def stop(self, grace_s=3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(grace_s)
        if (self._thread is None or not self._thread.is_alive()) and self._reader is not None:
            self._reader.close()
            self._reader = None

    def run_once(self, limit=16):
        now = self.clock_ns()
        since = 0 if self.lookback_s is None else now - int(self.lookback_s * S)
        if self._reader is None:
            self._reader = read_connection(self.store)
        ids = needed(self.store, now, self.wait_for_ocr_s, since, limit, self._reader)
        for oid in ids:
            if self._stop.is_set():
                break
            t = time.perf_counter()
            outcome, _ = build(self.store, oid, self.clock_ns)
            with self._lock:
                self.timing_ms = (self.timing_ms + [(time.perf_counter() - t) * 1000])[-500:]
            self._count(outcome)
        return len(ids)

    def _run(self):
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.idle_s)
            except Exception:
                log.exception("structure processor iteration failed")
                self._stop.wait(self.idle_s)

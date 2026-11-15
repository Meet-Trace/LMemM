"""Mutable configuration (kept apart from evidence). Every change is appended to settings_history."""

import json
import math
import uuid

from .store import canonical, now_iso


def get(store, key, default):
    with store.lock:
        row = store.db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(store, key, value):
    with store.lock:
        store.db.execute("BEGIN IMMEDIATE")
        try:
            at = now_iso()
            store.db.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                             "value_json=excluded.value_json, updated_at=excluded.updated_at", (key, canonical(value), at))
            store.db.execute("INSERT INTO settings_history VALUES (?,?,?,?)", (str(uuid.uuid4()), key, canonical(value), at))
            store.db.execute("COMMIT")
        except BaseException:
            store._rollback()
            raise
    return value


CAPTURE_INTERVAL_KEY = "capture_interval_s"
DEFAULT_CAPTURE_INTERVAL_S = 3.0
MIN_CAPTURE_INTERVAL_S, MAX_CAPTURE_INTERVAL_S = 0.5, 3600.0


def _valid_interval(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not MIN_CAPTURE_INTERVAL_S <= value <= MAX_CAPTURE_INTERVAL_S:
        raise ValueError(f"capture interval must be a number of seconds between {MIN_CAPTURE_INTERVAL_S} "
                         f"and {MAX_CAPTURE_INTERVAL_S}")
    return float(value)


def get_capture_interval(store):
    return _valid_interval(get(store, CAPTURE_INTERVAL_KEY, DEFAULT_CAPTURE_INTERVAL_S))


def set_capture_interval(store, seconds):
    return put(store, CAPTURE_INTERVAL_KEY, _valid_interval(seconds))


CURSOR_SAMPLE_KEY = "cursor_sample_ms"
DEFAULT_CURSOR_SAMPLE_MS = 150
MIN_CURSOR_SAMPLE_MS, MAX_CURSOR_SAMPLE_MS = 100, 200


def _valid_sample_ms(value):
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_CURSOR_SAMPLE_MS <= value <= MAX_CURSOR_SAMPLE_MS:
        raise ValueError(f"cursor sample interval must be a whole number of milliseconds between "
                         f"{MIN_CURSOR_SAMPLE_MS} and {MAX_CURSOR_SAMPLE_MS}")
    return value


def get_cursor_sample_ms(store):
    return _valid_sample_ms(get(store, CURSOR_SAMPLE_KEY, DEFAULT_CURSOR_SAMPLE_MS))


def set_cursor_sample_ms(store, milliseconds):
    return put(store, CURSOR_SAMPLE_KEY, _valid_sample_ms(milliseconds))


SCREENSHOT_SCOPE_KEY = "screenshot_scope"
SCREENSHOT_SCOPES = ("front_app", "display")
DEFAULT_SCREENSHOT_SCOPE = "front_app"


def _valid_scope(value):
    if value not in SCREENSHOT_SCOPES:
        raise ValueError(f"screenshot_scope must be one of {SCREENSHOT_SCOPES}")
    return value


def get_screenshot_scope(store):
    """front_app (default): only the front application's windows are composited. display: the whole display."""
    return _valid_scope(get(store, SCREENSHOT_SCOPE_KEY, DEFAULT_SCREENSHOT_SCOPE))


def set_screenshot_scope(store, scope):
    return put(store, SCREENSHOT_SCOPE_KEY, _valid_scope(scope))


# Vision OCR isolation (trace_v2.macos.ocr_client): where Vision runs and how its worker process is bounded.
OCR_ISOLATION_KEY = "ocr_isolation"
OCR_ISOLATIONS = ("process", "inprocess")
DEFAULT_OCR_ISOLATION = "process"


def _valid_isolation(value):
    if value not in OCR_ISOLATIONS:
        raise ValueError(f"ocr_isolation must be one of {OCR_ISOLATIONS}")
    return value


def get_ocr_isolation(store):
    """process (default): Vision runs in a separate killable worker. inprocess: on a thread of the capture process."""
    return _valid_isolation(get(store, OCR_ISOLATION_KEY, DEFAULT_OCR_ISOLATION))


def set_ocr_isolation(store, isolation):
    return put(store, OCR_ISOLATION_KEY, _valid_isolation(isolation))


OCR_TIMEOUT_KEY = "ocr_timeout_s"
DEFAULT_OCR_TIMEOUT_S = 10.0
MIN_OCR_TIMEOUT_S, MAX_OCR_TIMEOUT_S = 1.0, 120.0


def _valid_ocr_timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not MIN_OCR_TIMEOUT_S <= value <= MAX_OCR_TIMEOUT_S:
        raise ValueError(f"ocr_timeout_s must be a number of seconds between {MIN_OCR_TIMEOUT_S} and {MAX_OCR_TIMEOUT_S}")
    return float(value)


def get_ocr_timeout(store):
    """Longest a capture waits for text recognition; a worker that exceeds it is killed and replaced."""
    return _valid_ocr_timeout(get(store, OCR_TIMEOUT_KEY, DEFAULT_OCR_TIMEOUT_S))


def set_ocr_timeout(store, seconds):
    return put(store, OCR_TIMEOUT_KEY, _valid_ocr_timeout(seconds))


OCR_WORKER_MAX_RSS_KEY = "ocr_worker_max_rss_mb"
DEFAULT_OCR_WORKER_MAX_RSS_MB = 800
MIN_OCR_WORKER_MAX_RSS_MB, MAX_OCR_WORKER_MAX_RSS_MB = 200, 16384


def _valid_worker_rss(value):
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_OCR_WORKER_MAX_RSS_MB <= value <= MAX_OCR_WORKER_MAX_RSS_MB:
        raise ValueError(f"ocr_worker_max_rss_mb must be a whole number of MB between {MIN_OCR_WORKER_MAX_RSS_MB} "
                         f"and {MAX_OCR_WORKER_MAX_RSS_MB}")
    return value


def get_ocr_worker_max_rss_mb(store):
    """The OCR worker is replaced between jobs once its resident memory exceeds this."""
    return _valid_worker_rss(get(store, OCR_WORKER_MAX_RSS_KEY, DEFAULT_OCR_WORKER_MAX_RSS_MB))


def set_ocr_worker_max_rss_mb(store, megabytes):
    return put(store, OCR_WORKER_MAX_RSS_KEY, _valid_worker_rss(megabytes))


OCR_WORKER_MAX_JOBS_KEY = "ocr_worker_max_jobs"
DEFAULT_OCR_WORKER_MAX_JOBS = 500
MIN_OCR_WORKER_MAX_JOBS, MAX_OCR_WORKER_MAX_JOBS = 10, 100000


def _valid_worker_jobs(value):
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_OCR_WORKER_MAX_JOBS <= value <= MAX_OCR_WORKER_MAX_JOBS:
        raise ValueError(f"ocr_worker_max_jobs must be a whole number between {MIN_OCR_WORKER_MAX_JOBS} "
                         f"and {MAX_OCR_WORKER_MAX_JOBS}")
    return value


def get_ocr_worker_max_jobs(store):
    """The OCR worker is replaced after this many jobs (Vision and its caches only ever grow)."""
    return _valid_worker_jobs(get(store, OCR_WORKER_MAX_JOBS_KEY, DEFAULT_OCR_WORKER_MAX_JOBS))


def set_ocr_worker_max_jobs(store, jobs):
    return put(store, OCR_WORKER_MAX_JOBS_KEY, _valid_worker_jobs(jobs))

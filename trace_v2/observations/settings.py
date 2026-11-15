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

"""Mutable configuration (kept apart from evidence). Every change is appended to settings_history."""

import json
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

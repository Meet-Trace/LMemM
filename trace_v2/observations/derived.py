"""The second database file: everything derived from Observations (features, Moments, descriptions, Activities).

It has its own connection and lock (`store.derived`, `store.derived_lock`), so a slow derived write never waits for, or
blocks, capture. It holds no evidence: observation ids are plain text, and the evidence file is the only place they must
exist. Forgetting removes derived rows first, then evidence; a crash between the two leaves an observation whose derived
rows are gone (they are rebuilt), never derived rows of a forgotten observation. `reconcile` also removes any derived row
whose observation is missing from the evidence file.
"""

import sqlite3
from pathlib import Path

from .db import DERIVED_MIGRATIONS_DIR, connect, migrate, migrations

LEGACY_TABLES = ("observation_features", "moment_skips", "moments", "moment_revisions", "moment_member_index",
                 "activity_interpretations", "activity_verdicts", "activities", "activity_revisions", "activity_member_index",
                 "moment_descriptions")
ORDER = {"moment_revisions": " ORDER BY moment_id, revision", "activity_revisions": " ORDER BY activity_id, revision"}
CHUNK = 500


def derived_path(root):
    return Path(root) / "derived.sqlite3"


def open_derived(root):
    path = derived_path(root)
    connection = connect(path)
    try:
        fresh = connection.execute("PRAGMA user_version").fetchone()[0] == 0
        migrate(connection, None if fresh else path, migrations(DERIVED_MIGRATIONS_DIR))     # nothing to back up in a new file
    except BaseException:
        connection.close()
        raise
    return connection


def adopt_legacy(root, evidence_path):
    """Databases made before the split kept these tables in the evidence file. Copy them across once, before the evidence
    migration that drops them."""
    evidence_path = Path(evidence_path)
    if not evidence_path.exists():
        return 0
    probe = sqlite3.connect(f"file:{evidence_path}?mode=ro", uri=True)
    try:
        present = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        probe.close()
    tables = [t for t in LEGACY_TABLES if t in present]
    if not tables:
        return 0
    open_derived(root).close()
    copied = 0
    connection = sqlite3.connect(derived_path(root), isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("ATTACH DATABASE ? AS legacy", (str(evidence_path),))
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            before = connection.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
            columns = ", ".join(r[1] for r in connection.execute(f"PRAGMA legacy.table_info({table})"))
            connection.execute(f"INSERT OR IGNORE INTO main.{table}({columns}) SELECT {columns} FROM legacy.{table}{ORDER.get(table, '')}")
            copied += connection.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0] - before
        connection.execute("COMMIT")
    finally:
        connection.close()
    return copied


def purge(store, observation_ids, request_id="purge"):
    """Delete every derived row that came from these observations, and every Activity or Moment they were part of."""
    ids = list(observation_ids)
    if not ids:
        return 0
    with store.derived_lock:
        db = store.derived
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany("INSERT OR IGNORE INTO deletion_grants VALUES (?,?)", ((i, request_id) for i in ids))
            granted = "observation_id IN (SELECT observation_id FROM deletion_grants WHERE request_id=?)"
            moment_held = "moment_id IN (SELECT moment_id FROM moment_member_index WHERE " + granted + ")"
            held = "activity_id IN (SELECT activity_id FROM activity_member_index WHERE " + moment_held + ")"
            for table in ("activity_revisions", "activities", "activity_member_index"):
                db.execute(f"DELETE FROM {table} WHERE {held}", (request_id,))
            db.execute(f"DELETE FROM activity_interpretations WHERE {moment_held}", (request_id,))
            db.execute(f"DELETE FROM moment_descriptions WHERE {moment_held}", (request_id,))
            db.execute(f"DELETE FROM moment_identities WHERE {moment_held}", (request_id,))
            affected = "(SELECT moment_id FROM moment_member_index WHERE " + granted + ")"
            db.execute(f"DELETE FROM activity_verdicts WHERE moment_a IN {affected} OR moment_b IN {affected}", (request_id, request_id))
            db.execute(f"DELETE FROM activity_member_index WHERE {moment_held}", (request_id,))
            former = [r[0] for r in db.execute(
                "SELECT DISTINCT observation_id FROM moment_member_index WHERE " + moment_held, (request_id,))]
            for table in ("moment_revisions", "moments", "moment_member_index"):
                db.execute(f"DELETE FROM {table} WHERE {moment_held}", (request_id,))
            db.executemany("DELETE FROM observation_features WHERE observation_id=?", ((i,) for i in former))
            db.execute(f"DELETE FROM observation_features WHERE {granted}", (request_id,))
            db.execute(f"DELETE FROM moment_skips WHERE {granted}", (request_id,))
            db.execute("DELETE FROM deletion_grants WHERE request_id=?", (request_id,))
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    return len(ids)


def derived_rollback(store):
    if store.derived.in_transaction:
        store.derived.execute("ROLLBACK")


def checkpoint(store):
    with store.derived_lock:
        store.derived.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def reconcile(store):
    """Remove derived rows whose observation is not in the evidence file (a forget that stopped half way)."""
    with store.derived_lock:
        known = [r[0] for r in store.derived.execute(
            "SELECT observation_id FROM observation_features UNION SELECT observation_id FROM moment_skips "
            "UNION SELECT observation_id FROM moment_member_index")]
    missing = []
    for i in range(0, len(known), CHUNK):
        chunk = known[i:i + CHUNK]
        marks = ",".join("?" * len(chunk))
        with store.lock:
            present = {r[0] for r in store.db.execute(f"SELECT observation_id FROM observations WHERE observation_id IN ({marks})", chunk)}
        missing.extend(o for o in chunk if o not in present)
    purge(store, missing, "reconcile")
    return len(missing)

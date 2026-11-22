"""Trace V2 Observation schema migrations, refusal of unsafe databases, and crash recovery."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trace_v2.observations import db
from v2_observation_helpers import T0, TempStore, draft


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "obs.sqlite3"

    def tearDown(self):
        self.dir.cleanup()

    def test_fresh_database_reaches_the_latest_version_with_a_private_file(self):
        conn = db.open_database(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.LATEST_VERSION)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"observations", "screenshots", "extractions", "interaction_events", "settings",
                         "retention_runs", "screenshot_deletions", "schema_migrations"} <= tables)
        conn.close()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_reopening_applies_nothing_and_keeps_data(self):
        conn = db.open_database(self.path)
        conn.execute("INSERT INTO settings VALUES ('k','1','now')")
        conn.close()
        conn = db.connect(self.path)
        self.assertEqual(db.migrate(conn, self.path), [])
        self.assertEqual(conn.execute("SELECT value_json FROM settings").fetchone()[0], "1")
        conn.close()

    def test_newer_database_is_refused(self):
        db.open_database(self.path).close()
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version={db.LATEST_VERSION + 1}")
        conn.close()
        with self.assertRaises(db.SchemaError):
            db.open_database(self.path)

    def test_edited_migration_is_refused(self):
        db.open_database(self.path).close()
        known = [(v, n, sql + "\n-- edited", "different") for v, n, sql, _ in db.migrations()]
        conn = db.connect(self.path)
        with self.assertRaises(db.SchemaError):
            db.migrate(conn, self.path, known)
        conn.close()

    def test_failed_migration_rolls_back_and_leaves_a_backup(self):
        conn = db.open_database(self.path)
        conn.execute("INSERT INTO settings VALUES ('k','1','now')")
        broken = db.migrations() + [(db.LATEST_VERSION + 1, "9999_broken",
                                     "CREATE TABLE extra(x);\nINSERT INTO no_such_table VALUES (1);", "sha")]
        with self.assertRaises(sqlite3.OperationalError):
            db.migrate(conn, self.path, broken)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.LATEST_VERSION)
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='extra'").fetchone())
        backup = self.path.with_name(self.path.name + f".pre-v{db.LATEST_VERSION + 1}.bak")
        copy = sqlite3.connect(backup)
        self.assertEqual(copy.execute("SELECT value_json FROM settings").fetchone()[0], "1")
        copy.close()
        conn.close()
        db.open_database(self.path).close()          # still opens normally afterwards

    def test_statement_splitter_keeps_trigger_bodies_whole(self):
        sql = db.migrations()[0][2]
        triggers = [s for s in db.statements(sql) if s.startswith("CREATE TRIGGER")]
        self.assertTrue(all(s.rstrip().endswith("END;") for s in triggers))
        self.assertGreaterEqual(len(triggers), 17)


class RecoveryTest(unittest.TestCase):
    def test_failed_commit_leaves_no_partial_observation_and_no_file(self):
        with TempStore() as ts:
            real = ts.store.db

            class Wrapper:
                def __getattr__(self, name):
                    return getattr(real, name)

                def execute(self, sql, *args):
                    return real.execute(sql, *args)

                def executemany(self, sql, rows):
                    raise sqlite3.OperationalError("disk I/O error")

            with mock.patch.object(ts.store, "db", Wrapper()), self.assertRaises(sqlite3.OperationalError):
                ts.store.ingest(draft())
            self.assertEqual(ts.store.list(), [])
            self.assertEqual(real.execute("SELECT count(*) FROM extractions").fetchone()[0], 0)
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})
            self.assertTrue(ts.store.ingest(draft()).created)      # the same capture can be retried

    def test_crash_between_file_write_and_commit_is_found_by_audit(self):
        with TempStore() as ts:
            ts.store.assets.write("screenshots/2027/01/15/orphan.png", b"\x89PNG\r\n\x1a\n")
            self.assertEqual(ts.store.audit()["orphan_files"], ["screenshots/2027/01/15/orphan.png"])

    def test_committed_observations_survive_reopening(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            before = ts.store.get(oid)
            self.assertEqual(ts.reopen().get(oid), before)
            self.assertEqual(ts.store.list()[0]["timestamp_ns"], T0)


if __name__ == "__main__":
    unittest.main()

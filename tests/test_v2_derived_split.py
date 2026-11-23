import tempfile
import threading
import unittest
from pathlib import Path

from test_v2_moments import CODE, live, run, sequence
from trace_v2 import paths
from trace_v2.observations import ObservationStore, derived
from trace_v2.observations.db import connect, migrate, migrations
from v2_observation_helpers import T0, TempStore


class SplitTest(unittest.TestCase):
    def test_derived_data_is_in_its_own_file(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 8, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 300 * 10**9)
            names = lambda db: {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("moment_revisions", names(ts.store.derived))
            self.assertNotIn("moment_revisions", names(ts.store.db))
            self.assertNotIn("observations", names(ts.store.derived))
            self.assertTrue(derived.derived_path(ts.store.root).exists())

    def test_a_held_derived_write_does_not_block_capture(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 2, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
            held, release, done = threading.Event(), threading.Event(), []

            def hold():
                with ts.store.derived_lock:
                    ts.store.derived.execute("BEGIN IMMEDIATE")
                    held.set()
                    release.wait(10)
                    ts.store.derived.execute("ROLLBACK")
            thread = threading.Thread(target=hold)
            thread.start()
            held.wait(5)
            writer = threading.Thread(target=lambda: done.append(sequence(ts.store, 10, 3, "com.apple.dt.Xcode", "shader.cpp", CODE, 50)))
            writer.start()
            writer.join(5)
            finished = bool(done)
            release.set()
            thread.join()
            writer.join()
            self.assertTrue(finished)

    def test_a_forget_that_stopped_before_the_evidence_is_safe_and_rebuilds(self):
        with TempStore() as ts:
            ids = sequence(ts.store, 0, 8, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 300 * 10**9)
            derived.purge(ts.store, [ids[0]])
            self.assertEqual(live(ts), [])
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0], 8)
            run(ts, T0 + 300 * 10**9)
            self.assertEqual(len(live(ts)), 1)

    def test_reconcile_removes_derived_rows_of_missing_observations(self):
        with TempStore() as ts:
            sequence(ts.store, 0, 8, "com.apple.dt.Xcode", "shader.cpp", CODE, 0)
            run(ts, T0 + 300 * 10**9)
            ts.store.derived.execute("INSERT INTO observation_features VALUES ('ghost', 'x', '{}', NULL, NULL, 'now')")
            self.assertEqual(derived.reconcile(ts.store), 1)
            self.assertEqual(ts.store.derived.execute("SELECT count(*) FROM observation_features WHERE observation_id='ghost'").fetchone()[0], 0)
            self.assertEqual(len(live(ts)), 1)

    def test_a_database_made_before_the_split_is_carried_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "v2"
            root.mkdir()
            old = connect(paths.database_path(root))
            migrate(old, paths.database_path(root), migrations()[:8])
            moment = "11111111-1111-1111-1111-111111111111"
            old.execute("INSERT INTO moments VALUES (?, 'moments/1', 'formed', NULL, 5)", (moment,))
            old.execute("INSERT INTO moment_revisions VALUES (?,1,'provisional',NULL,1,2,0,'{}','{}','json+zlib',x'00','sha','now')", (moment,))
            old.execute("INSERT INTO moment_revisions VALUES (?,2,'stable',NULL,1,2,0,'{}','{}','json+zlib',x'00','sha','now')", (moment,))
            old.close()
            store = ObservationStore(root)
            try:
                self.assertEqual(store.derived.execute("SELECT count(*), max(revision) FROM moment_revisions").fetchone(), (2, 2))
                tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("moments", tables)
            finally:
                store.close()
            again = ObservationStore(root)
            try:
                self.assertEqual(again.derived.execute("SELECT count(*) FROM moment_revisions").fetchone()[0], 2)
            finally:
                again.close()


if __name__ == "__main__":
    unittest.main()

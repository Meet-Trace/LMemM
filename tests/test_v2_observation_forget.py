"""Trace V2 forget: user-requested, audited deletion of structured evidence."""

import sqlite3
import unittest
from unittest import mock

from trace_v2.observations import forget, retention
from v2_observation_helpers import DAY, PNG, SEC, T0, TempStore, click, draft, ocr_ok


def fill(store):
    ids = {}
    for i, app in enumerate(["com.a", "com.b", "com.a", "com.c"]):
        ids[i] = store.ingest(draft(key=f"s:{i}", t=T0 + i * SEC, app=app, events=[click(T0 + i * SEC - 1)])).observation_id
    store.add_extraction(ids[0], "ocr", ocr_ok("rerun"))
    return ids


def counts(store):
    return {t: store.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("observations", "screenshots", "extractions", "interaction_events")}


class ForgetTest(unittest.TestCase):
    def test_forgetting_an_app_removes_all_of_its_evidence_and_nothing_else(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            files = {i: ts.store.get(o)["evidence"]["screenshot"]["reference"] for i, o in ids.items()}
            result = forget.forget(ts.store, {"app_bundle_id": "com.a"})
            self.assertEqual((result["matched"], result["files_removed"]), (2, 2))
            self.assertEqual(sorted(result["observation_ids"]), sorted([ids[0], ids[2]]))
            self.assertEqual(counts(ts.store), {"observations": 2, "screenshots": 2, "extractions": 4,
                                                "interaction_events": 2})
            for i in (0, 2):
                with self.assertRaises(KeyError):
                    ts.store.get(ids[i])
                self.assertFalse(ts.store.assets.exists(files[i]))
            for i in (1, 3):
                self.assertEqual(ts.store.screenshot_bytes(ids[i]), PNG)
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_the_request_is_audited_and_tombstones_hold_no_evidence(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            result = forget.forget(ts.store, {"observation_ids": [ids[1]]})
            req = ts.store.db.execute("SELECT requested_by, selector_json, matched FROM forget_requests").fetchone()
            self.assertEqual(req, ("user", f'{{"observation_ids":["{ids[1]}"]}}', 1))
            tomb = ts.store.db.execute("SELECT * FROM forget_tombstones").fetchall()
            self.assertEqual([(t[0], t[1]) for t in tomb], [(ids[1], result["request_id"])])
            fin = ts.store.db.execute("SELECT files_removed, files_missing FROM forget_finishes").fetchone()
            self.assertEqual(fin, (1, 0))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM deletion_grants").fetchone()[0], 0)
            for row in ts.store.db.execute("SELECT * FROM forget_tombstones"):
                self.assertNotIn("hello", str(row))

    def test_time_range_everything_and_dry_run(self):
        with TempStore() as ts:
            fill(ts.store)
            dry = forget.forget(ts.store, {"start_ns": T0 + SEC, "end_ns": T0 + 3 * SEC}, dry_run=True)
            self.assertEqual((dry["matched"], counts(ts.store)["observations"]), (2, 4))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM forget_requests").fetchone()[0], 0)
            forget.forget(ts.store, {"start_ns": T0 + SEC, "end_ns": T0 + 3 * SEC})
            self.assertEqual(counts(ts.store)["observations"], 2)
            forget.forget(ts.store, {"everything": True})
            self.assertEqual(set(counts(ts.store).values()), {0})
            self.assertEqual(ts.store.assets.all_relative_paths(), set())

    def test_invalid_selectors_delete_nothing(self):
        with TempStore() as ts:
            fill(ts.store)
            for bad in ({}, {"everything": "yes"}, {"observation_ids": []}, {"start_ns": 5, "end_ns": 1},
                        {"app_bundle_id": "com.a", "observation_ids": ["x"]}, {"where": "1=1"}):
                with self.subTest(bad), self.assertRaises(forget.InvalidSelector):
                    forget.forget(ts.store, bad)
            self.assertEqual(counts(ts.store)["observations"], 4)

    def test_raw_deletes_without_a_grant_are_still_refused(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            forget.forget(ts.store, {"observation_ids": [ids[0]]})
            for table in ("observations", "screenshots", "extractions", "interaction_events"):
                with self.subTest(table), self.assertRaises(sqlite3.IntegrityError):
                    ts.store.db.execute(f"DELETE FROM {table}")
            with self.assertRaises(sqlite3.IntegrityError):
                ts.store.db.execute("DELETE FROM forget_requests")

    def test_a_forgotten_id_can_never_be_reused(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            forget.forget(ts.store, {"observation_ids": [ids[0]]})
            with self.assertRaises(sqlite3.IntegrityError):
                ts.store.db.execute("INSERT INTO observations(observation_id,captured_at_ns,captured_at,capture_key,"
                                    "content_sha256,context_json,capture_status_json,privacy_json,capture_version,"
                                    "recorded_at) VALUES (?,1,'x','k','h','{}','{}','{}','v','x')", (ids[0],))

    def test_failure_inside_the_transaction_changes_nothing(self):
        with TempStore() as ts:
            fill(ts.store)
            real = ts.store.db

            class Wrapper:
                def __getattr__(self, name):
                    return getattr(real, name)

                def execute(self, sql, *args):
                    if sql.startswith("DELETE FROM screenshots"):
                        raise sqlite3.OperationalError("disk I/O error")
                    return real.execute(sql, *args)

            with mock.patch.object(ts.store, "db", Wrapper()), self.assertRaises(sqlite3.OperationalError):
                forget.forget(ts.store, {"everything": True})
            self.assertEqual(counts(ts.store)["observations"], 4)
            self.assertEqual(real.execute("SELECT count(*) FROM forget_requests").fetchone()[0], 0)
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_interrupted_file_removal_is_finished_when_the_store_reopens(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            ref = ts.store.get(ids[3])["evidence"]["screenshot"]["reference"]
            with mock.patch.object(forget, "_finish", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                forget.forget(ts.store, {"observation_ids": [ids[3]]})
            self.assertTrue(ts.store.assets.exists(ref))          # committed, files not yet removed
            store = ts.reopen()
            self.assertFalse(store.assets.exists(ref))
            self.assertEqual(store.db.execute("SELECT count(*) FROM forget_finishes").fetchone()[0], 1)

    def test_everything_also_removes_migration_backups(self):
        with TempStore() as ts:
            fill(ts.store)
            backup = ts.root / "observations.sqlite3.pre-v2.bak"
            backup.write_bytes(b"old copy")
            partial = forget.forget(ts.store, {"app_bundle_id": "com.c"})
            self.assertEqual(partial["backups_remaining"], [str(backup)])
            full = forget.forget(ts.store, {"everything": True})
            self.assertEqual((full["backups_removed"], backup.exists()), ([str(backup)], False))


class RetentionIndependenceTest(unittest.TestCase):
    def test_structured_evidence_has_no_automatic_expiry_and_survives_screenshot_retention(self):
        with TempStore() as ts:
            ids = fill(ts.store)
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            before = counts(ts.store)
            result = retention.run(ts.store, T0 + 3650 * DAY)
            self.assertEqual(result["deleted"], 4)
            self.assertEqual(counts(ts.store), before)          # ten years later every structured row is still there
            for oid in ids.values():
                self.assertIn(ts.store.get(oid)["evidence"]["ocr"]["text"], ("hello world", "rerun"))

    def test_forget_after_retention_handles_already_deleted_screenshots(self):
        with TempStore() as ts:
            fill(ts.store)
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            retention.run(ts.store, T0 + 30 * DAY)
            result = forget.forget(ts.store, {"everything": True})
            self.assertEqual((result["matched"], result["files_removed"], result["files_missing"]), (4, 0, 0))
            self.assertEqual(set(counts(ts.store).values()), {0})


if __name__ == "__main__":
    unittest.main()

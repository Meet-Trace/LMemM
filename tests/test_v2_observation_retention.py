"""Trace V2 screenshot retention: policies, deletion of files only, audit, determinism, crash safety."""

import unittest
from unittest import mock

from trace_v2.observations import ScreenshotUnavailable, retention
from v2_observation_helpers import DAY, SEC, T0, TempStore, click, draft


def fill(store, ages_days):
    """One observation per age (days before T0 + 100 days)."""
    now = T0 + 100 * DAY
    ids = [store.ingest(draft(key=f"s:{i}", t=now - int(age * DAY), events=[click(now - int(age * DAY) - 1)])).observation_id
           for i, age in enumerate(ages_days)]
    return now, ids


class PolicyTest(unittest.TestCase):
    def test_default_is_permanent_and_deletes_nothing(self):
        with TempStore() as ts:
            now, ids = fill(ts.store, [400, 40, 1])
            self.assertEqual(retention.get_policy(ts.store), {"mode": "permanent"})
            result = retention.run(ts.store, now)
            self.assertEqual((result["examined"], result["deleted"], result["cutoff_ns"]), (0, 0, None))
            for oid in ids:
                ts.store.screenshot_bytes(oid)

    def test_presets_and_custom_durations(self):
        with TempStore() as ts:
            for preset, days in (("5d", 5), ("7d", 7), ("14d", 14), ("30d", 30)):
                self.assertEqual(retention.set_policy(ts.store, {"mode": "duration", "preset": preset})["seconds"],
                                 days * 86400)
            custom = retention.set_policy(ts.store, {"mode": "duration", "preset": "custom", "seconds": 3 * 3600})
            self.assertEqual(retention.get_policy(ts.store), custom)
            history = ts.store.db.execute("SELECT count(*) FROM settings_history").fetchone()[0]
            self.assertEqual(history, 5)

    def test_invalid_policies_are_refused(self):
        bad = [{"mode": "duration", "preset": "3d"}, {"mode": "duration", "preset": "custom", "seconds": 10},
               {"mode": "duration", "preset": "custom", "seconds": 1.5 * 3600}, {"mode": "forever"},
               {"mode": "duration", "preset": "7d", "seconds": 1}, None]
        with TempStore() as ts:
            for policy in bad:
                with self.subTest(policy), self.assertRaises(retention.InvalidPolicy):
                    retention.set_policy(ts.store, policy)
            self.assertEqual(retention.get_policy(ts.store), {"mode": "permanent"})


class CleanupTest(unittest.TestCase):
    def test_expired_files_go_and_structured_evidence_stays(self):
        with TempStore() as ts:
            now, (old, edge, fresh) = fill(ts.store, [8, 7, 6.5])
            before = {oid: ts.store.get(oid) for oid in (old, edge, fresh)}
            retention.set_policy(ts.store, {"mode": "duration", "preset": "7d"})
            result = retention.run(ts.store, now)
            # captured exactly 7 days ago is not older than the cutoff, so it stays
            self.assertEqual((result["deleted"], result["observation_ids"]), (1, [old]))
            with self.assertRaises(ScreenshotUnavailable) as caught:
                ts.store.screenshot_bytes(old)
            self.assertEqual(caught.exception.reason, "deleted")
            ts.store.screenshot_bytes(edge)
            after = ts.store.get(old)
            for part in ("context", "interaction_events", "timestamp_ns"):
                self.assertEqual(after[part], before[old][part])
            for reader in ("ocr", "accessibility"):
                self.assertEqual(after["evidence"][reader], before[old]["evidence"][reader])
            shot = after["evidence"]["screenshot"]
            self.assertEqual((shot["sha256"], shot["reference"], shot["available"]),
                             (before[old]["evidence"]["screenshot"]["sha256"], before[old]["evidence"]["screenshot"]["reference"], False))
            self.assertEqual(shot["deletion"]["run_id"], result["run_id"])
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_runs_are_idempotent_and_audited(self):
        with TempStore() as ts:
            now, _ = fill(ts.store, [40, 20, 1])
            retention.set_policy(ts.store, {"mode": "duration", "preset": "14d"})
            first = retention.run(ts.store, now)
            second = retention.run(ts.store, now)
            self.assertEqual((first["deleted"], second["examined"], second["deleted"]), (2, 0, 0))
            runs = ts.store.db.execute("SELECT count(*) FROM retention_runs r JOIN retention_run_finishes f "
                                       "USING (run_id)").fetchone()[0]
            self.assertEqual(runs, 2)

    def test_dry_run_reports_and_deletes_nothing(self):
        with TempStore() as ts:
            now, ids = fill(ts.store, [40, 20, 1])
            retention.set_policy(ts.store, {"mode": "duration", "preset": "30d"})
            result = retention.run(ts.store, now, dry_run=True)
            self.assertEqual((result["observation_ids"], result["deleted"]), ([ids[0]], 0))
            ts.store.screenshot_bytes(ids[0])

    def test_same_database_policy_and_time_give_the_same_deletions(self):
        outcomes = []
        for _ in range(2):
            with TempStore() as ts:
                now, ids = fill(ts.store, [9, 3, 12, 8, 1, 30])
                retention.set_policy(ts.store, {"mode": "duration", "preset": "7d"})
                result = retention.run(ts.store, now)
                outcomes.append([ids.index(o) for o in result["observation_ids"]])
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0], [5, 2, 0, 3])          # oldest first

    def test_crash_after_file_removal_is_recorded_on_the_next_run(self):
        with TempStore() as ts:
            now, (oid,) = fill(ts.store, [10])
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            real = ts.store.db

            class Crash(Exception):
                pass

            class Wrapper:
                def __getattr__(self, name):
                    return getattr(real, name)

                def execute(self, sql, *args):
                    if sql.startswith("INSERT INTO screenshot_deletions"):
                        raise Crash()
                    return real.execute(sql, *args)

            with mock.patch.object(ts.store, "db", Wrapper()), self.assertRaises(Crash):
                retention.run(ts.store, now)
            self.assertEqual(ts.store.audit()["missing_files"], [ts.store.get(oid)["evidence"]["screenshot"]["reference"]])
            unfinished = real.execute("SELECT count(*) FROM retention_runs LEFT JOIN retention_run_finishes f "
                                      "USING (run_id) WHERE f.run_id IS NULL").fetchone()[0]
            self.assertEqual(unfinished, 1)
            result = retention.run(ts.store, now)
            self.assertEqual((result["already_missing"], result["deleted"]), (1, 0))
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_a_file_that_cannot_be_removed_is_counted_and_retried(self):
        with TempStore() as ts:
            now, (oid,) = fill(ts.store, [10])
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            with mock.patch.object(ts.store.assets, "delete", side_effect=PermissionError):
                self.assertEqual(retention.run(ts.store, now)["failed"], 1)
            ts.store.screenshot_bytes(oid)
            self.assertEqual(retention.run(ts.store, now + SEC)["deleted"], 1)


if __name__ == "__main__":
    unittest.main()

import unittest

from trace_v2.observations import forget, trim
from v2_observation_helpers import SEC, T0, TempStore, draft, ocr_ok

DAY = 86400 * SEC


class RawTrimTest(unittest.TestCase):
    def add(self, ts, key, t):
        return ts.store.ingest(draft(key=key, t=t, ocr=ocr_ok("some text on screen"))).observation_id

    def raw_null(self, ts, oid, reader="ocr"):
        return ts.store.db.execute("SELECT raw IS NULL, text FROM extractions WHERE observation_id=? AND reader=?", (oid, reader)).fetchone()

    def test_only_old_raw_output_is_emptied_and_text_stays(self):
        with TempStore() as ts:
            old, new = self.add(ts, "old", T0), self.add(ts, "new", T0 + 40 * DAY)
            out = trim.run(ts.store, T0 + 45 * DAY)
            self.assertGreaterEqual(out["trimmed"], 2)
            self.assertEqual(self.raw_null(ts, old), (1, "some text on screen"))
            self.assertEqual(self.raw_null(ts, new)[0], 0)
            self.assertIsNone(ts.store.extractions(old, "ocr")[-1]["raw"])
            self.assertTrue(ts.store.extractions(new, "ocr")[-1]["raw"])
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM raw_trims").fetchone()[0], out["trimmed"])
            self.assertEqual(trim.run(ts.store, T0 + 45 * DAY)["trimmed"], 0)

    def test_dry_run_and_keep_forever_change_nothing(self):
        with TempStore() as ts:
            old = self.add(ts, "old", T0)
            self.assertGreater(trim.run(ts.store, T0 + 45 * DAY, dry_run=True)["would_trim"], 0)
            trim.set_days(ts.store, None)
            self.assertEqual(trim.run(ts.store, T0 + 45 * DAY)["trimmed"], 0)
            self.assertEqual(self.raw_null(ts, old)[0], 0)

    def test_other_updates_are_still_refused(self):
        import sqlite3
        with TempStore() as ts:
            old = self.add(ts, "old", T0)
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.db.execute("UPDATE extractions SET text='x' WHERE observation_id=?", (old,))
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.db.execute("UPDATE extractions SET raw=NULL, raw_encoding=NULL, raw_sha256=NULL, raw_bytes=NULL WHERE observation_id=?", (old,))

    def test_a_frame_a_younger_one_still_points_at_is_kept(self):
        from test_v2_screenshot_dedup import QuietCaptureTest, png
        with TempStore() as ts:
            helper = QuietCaptureTest()
            a = helper.take(ts, 0, png())
            b = helper.take(ts, 35 * 86400 // 3, png(box=(300, 300, 700, 440)), reason="heartbeat")
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM extraction_links").fetchone()[0], 1)
            trim.run(ts.store, T0 + 40 * DAY)
            self.assertEqual(self.raw_null(ts, a, "ocr")[0], 0)
            self.assertTrue(ts.store.extractions(b, "ocr")[-1]["raw"])
            trim.run(ts.store, T0 + 70 * DAY)
            self.assertEqual(self.raw_null(ts, a, "ocr")[0], 1)

    def test_forget_removes_the_trim_record(self):
        with TempStore() as ts:
            old = self.add(ts, "old", T0)
            trim.run(ts.store, T0 + 45 * DAY)
            forget.forget(ts.store, {"observation_ids": [old]}, dry_run=False)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM raw_trims").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

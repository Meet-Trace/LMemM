"""A screenshot whose secret scan (OCR) has not finished is held in quarantine: kept only after a clean scan."""

import os
import unittest

from trace_v2.observations import ScreenshotCapture, ocr_queue
from trace_v2.observations.assets import QUARANTINE
from test_v2_async_ocr import FAKE_KEY, POLICY, Env, FakeReader, Timeout, pdraft
from test_v2_screenshot_dedup import png, row
from v2_observation_helpers import PNG, SEC, T0, TempStore, draft, ocr_ok


def where(ts, oid):
    rel = row(ts, oid)[1]
    root = ts.store.assets.root
    return (root / rel).exists(), (root / QUARANTINE / rel).exists()


def reason(ts, oid):
    r = ts.store.db.execute("SELECT reason FROM screenshot_deletions WHERE observation_id=?", (oid,)).fetchone()
    return r[0] if r else None


class QuarantineLifecycle(unittest.TestCase):
    def test_a_pending_screenshot_is_quarantined_and_still_readable(self):
        with TempStore() as ts:
            oid = ts.store.ingest(pdraft()).observation_id
            self.assertEqual(where(ts, oid), (False, True))
            self.assertEqual(ts.store.screenshot_bytes(oid), PNG)
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_a_clean_scan_releases_it_to_its_final_place(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            self.assertEqual(where(ts, oid), (True, False))
            self.assertEqual(ts.store.screenshot_bytes(oid), PNG)
            self.assertIsNone(reason(ts, oid))

    def test_a_secret_in_the_scan_removes_it(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(f"key {FAKE_KEY}"))
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            self.assertEqual(where(ts, oid), (False, False))
            self.assertEqual(reason(ts, oid), "secret_on_screen_late")

    def test_a_scan_that_cannot_finish_removes_it_as_unscanned(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(*[Timeout("slow")] * 5))
            oid = ts.store.ingest(pdraft()).observation_id
            for _ in range(3):
                env.proc.run_once()
                env.clock.advance(500)
            self.assertEqual(where(ts, oid), (False, False))
            self.assertEqual(reason(ts, oid), "unscanned")

    def test_a_job_that_expires_removes_it_as_unscanned(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            env.clock.advance(POLICY.max_age_s + 1)
            ocr_queue.sweep(ts.store, env.clock(), POLICY)
            self.assertEqual(where(ts, oid), (False, False))
            self.assertEqual(reason(ts, oid), "unscanned")

    def test_a_file_already_in_its_final_place_is_not_removed_by_a_failed_scan(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(ValueError("cannot decode image")))
            oid = ts.store.ingest(pdraft()).observation_id
            ts.store.assets.release(row(ts, oid)[1])
            env.proc.run_once()
            self.assertEqual(where(ts, oid), (True, False))
            self.assertIsNone(reason(ts, oid))

    def test_a_synchronous_capture_with_text_is_stored_in_its_final_place(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(ocr=ocr_ok())).observation_id
            self.assertEqual(where(ts, oid), (True, False))

    def test_a_full_queue_keeps_no_pixels(self):
        from trace_v2.observations import settings
        with TempStore() as ts:
            settings.set_ocr_queue_cap(ts.store, 1)
            first = ts.store.ingest(pdraft(key="s:0", t=T0)).observation_id
            second = ts.store.ingest(pdraft(key="s:1", t=T0 + SEC)).observation_id
            self.assertEqual(row(ts, first)[0], "stored")
            self.assertEqual(row(ts, second)[0], "withheld")
            self.assertEqual(os.listdir(ts.store.assets.root / QUARANTINE / "screenshots" / "2027" / "01" / "15").__len__(), 1)


class QuarantineWithDedup(unittest.TestCase):
    def test_an_unchanged_frame_while_the_scan_is_pending_is_quarantined_too_and_scanned_on_its_own(self):
        with TempStore() as ts:
            env = Env(ts)
            data = png()
            first = ts.store.ingest(pdraft(key="s:0", t=T0, shot=ScreenshotCapture(data, "png", 720, 450, T0))).observation_id
            second = ts.store.ingest(pdraft(key="s:1", t=T0 + 3 * SEC,
                                            shot=ScreenshotCapture(data, "png", 720, 450, T0 + 3 * SEC))).observation_id
            self.assertEqual((where(ts, first), where(ts, second)), ((False, True), (False, True)))
            env.proc.run_once()
            env.proc.run_once()
            self.assertEqual((where(ts, first), where(ts, second)), ((True, False), (True, False)))

    def test_a_late_secret_removes_only_its_own_name_and_the_sibling_is_scanned_separately(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(f"key {FAKE_KEY}", "all clear"))
            data = png()
            first = ts.store.ingest(pdraft(key="s:0", t=T0, shot=ScreenshotCapture(data, "png", 720, 450, T0))).observation_id
            second = ts.store.ingest(pdraft(key="s:1", t=T0 + 3 * SEC,
                                            shot=ScreenshotCapture(data, "png", 720, 450, T0 + 3 * SEC))).observation_id
            env.proc.run_once()
            self.assertEqual((where(ts, first), where(ts, second)), ((False, False), (False, True)))
            env.proc.run_once()
            self.assertEqual(where(ts, second), (True, False))

    def test_a_frame_that_reuses_clean_text_is_linked_straight_into_its_final_place(self):
        with TempStore() as ts:
            env = Env(ts)
            data = png()
            first = ts.store.ingest(pdraft(key="s:0", t=T0, shot=ScreenshotCapture(data, "png", 720, 450, T0))).observation_id
            env.proc.run_once()
            second = ts.store.ingest(pdraft(key="s:1", t=T0 + 3 * SEC,
                                            shot=ScreenshotCapture(data, "png", 720, 450, T0 + 3 * SEC))).observation_id
            self.assertEqual((where(ts, first), where(ts, second)), ((True, False), (True, False)))


if __name__ == "__main__":
    unittest.main()

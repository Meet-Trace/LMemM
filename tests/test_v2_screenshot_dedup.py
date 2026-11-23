import io
import os
import unittest

from PIL import Image, ImageDraw

from trace_v2.observations import InteractionEvent, ReaderResult, ScreenshotCapture, dedup
from v2_observation_helpers import SEC, T0, TempStore, draft, ocr_ok


def png(box=None, extra=None, size=(720, 450)):
    image = Image.new("RGB", size, (250, 250, 250))
    d = ImageDraw.Draw(image)
    for i in range(8):
        d.rectangle((40, 40 + i * 40, 500, 60 + i * 40), fill=(30, 30, 30))
    if box:
        d.rectangle(box, fill=(200, 40, 40))
    if extra:
        d.rectangle(extra, fill=(40, 40, 200))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def shot(data, t):
    return ScreenshotCapture(data, "png", 720, 450, t)


def key(t):
    return InteractionEvent("keypress", t, {"phase": "down", "key_class": "letter"})


def capture(ts, i, data, events=(), app="com.example.editor", ocr=None):
    t = T0 + i * 3 * SEC
    return ts.store.ingest(draft(key=f"s:{i}", t=t, shot=shot(data, t), events=events, app=app, ocr=ocr)).observation_id


def row(ts, oid):
    return ts.store.db.execute("SELECT status, relative_path, sha256 FROM screenshots WHERE observation_id=?", (oid,)).fetchone()


def deleted(ts, oid):
    r = ts.store.db.execute("SELECT reason FROM screenshot_deletions WHERE observation_id=?", (oid,)).fetchone()
    return r[0] if r else None


class QuietCaptureTest(unittest.TestCase):
    def take(self, ts, i, data, reason=None, app="com.example.editor"):
        from dataclasses import replace
        t = T0 + i * 3 * SEC
        pending = ReaderResult("vision-ocr/1", "pending")
        d = replace(draft(key=f"q:{i}", t=t, shot=shot(data, t), app=app, ocr=ocr_ok("first screen text") if i == 0 else pending),
                    capture_reason=reason)
        return ts.store.ingest(d).observation_id

    def ocr_status(self, ts, oid):
        return ts.store.db.execute("SELECT status, text FROM extractions WHERE observation_id=? AND reader='ocr' ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()

    def test_a_heartbeat_capture_reuses_the_file_and_the_text_without_a_new_scan(self):
        with TempStore() as ts:
            a = self.take(ts, 0, png())
            b = self.take(ts, 1, png(box=(300, 300, 700, 440)), reason="heartbeat")
            root = ts.store.assets.root
            self.assertEqual(os.stat(root / row(ts, a)[1]).st_ino, os.stat(root / row(ts, b)[1]).st_ino)
            self.assertEqual(self.ocr_status(ts, b), ("succeeded", "first screen text"))
            self.assertEqual(ts.store.dedup_stats["quiet"], 1)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM ocr_jobs WHERE observation_id=?", (b,)).fetchone()[0], 0)

    def test_a_real_change_is_scanned_normally(self):
        with TempStore() as ts:
            self.take(ts, 0, png())
            b = self.take(ts, 1, png(box=(300, 300, 700, 440)), reason="screen")
            self.assertEqual(self.ocr_status(ts, b)[0], "pending")
            self.assertEqual(ts.store.dedup_stats.get("quiet", 0), 0)

    def test_a_heartbeat_in_another_app_is_not_treated_as_quiet(self):
        with TempStore() as ts:
            self.take(ts, 0, png())
            b = self.take(ts, 1, png(box=(300, 300, 700, 440)), reason="heartbeat", app="com.example.other")
            self.assertEqual(self.ocr_status(ts, b)[0], "pending")

    def test_a_reused_ocr_stores_a_link_not_a_second_copy(self):
        with TempStore() as ts:
            a = self.take(ts, 0, png())
            b = self.take(ts, 1, png(box=(300, 300, 700, 440)), reason="heartbeat")
            stored = ts.store.db.execute("SELECT raw IS NULL FROM extractions WHERE observation_id=? AND reader='ocr'", (b,)).fetchone()[0]
            self.assertEqual(stored, 1)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM extraction_links").fetchone()[0], 1)
            first = ts.store.extractions(a, "ocr")[-1]["raw"]
            self.assertTrue(first)
            self.assertEqual(ts.store.extractions(b, "ocr")[-1]["raw"], first)

    def test_forgetting_the_first_frame_keeps_the_raw_text_of_the_others(self):
        from trace_v2.observations import forget
        with TempStore() as ts:
            a = self.take(ts, 0, png())
            b = self.take(ts, 1, png(box=(300, 300, 700, 440)), reason="heartbeat")
            c = self.take(ts, 2, png(box=(300, 300, 700, 440)), reason="heartbeat")
            first = ts.store.extractions(a, "ocr")[-1]["raw"]
            forget.forget(ts.store, {"observation_ids": [a]}, dry_run=False)
            self.assertEqual(ts.store.extractions(b, "ocr")[-1]["raw"], first)
            self.assertEqual(ts.store.extractions(c, "ocr")[-1]["raw"], first)
            forget.forget(ts.store, {"observation_ids": [b]}, dry_run=False)
            self.assertEqual(ts.store.extractions(c, "ocr")[-1]["raw"], first)
            forget.forget(ts.store, {"observation_ids": [c]}, dry_run=False)
            for table in ("extraction_links", "extraction_raw_copies"):
                self.assertEqual(ts.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)


class UnchangedTest(unittest.TestCase):
    def test_identical_screens_share_one_file(self):
        with TempStore() as ts:
            data = png()
            a = capture(ts, 0, data)
            b = capture(ts, 1, data)
            self.assertNotEqual(row(ts, a)[1], row(ts, b)[1])
            self.assertEqual(row(ts, a)[2], row(ts, b)[2])
            root = ts.store.assets.root
            self.assertEqual(os.stat(root / row(ts, a)[1]).st_ino, os.stat(root / row(ts, b)[1]).st_ino)
            self.assertEqual(ts.store.screenshot_bytes(b), data)
            self.assertEqual(ts.store.dedup_stats["unchanged"], 1)

    def test_flipping_between_two_screens_reuses_the_earlier_files(self):
        with TempStore() as ts:
            one, two = png(), png(box=(300, 300, 700, 440))
            oids = [capture(ts, i, one if i % 2 == 0 else two, app=f"com.example.app{i % 2}") for i in range(6)]
            inodes = {os.stat(ts.store.assets.root / row(ts, o)[1]).st_ino for o in oids}
            self.assertEqual(len(inodes), 2)
            self.assertEqual(ts.store.dedup_stats["unchanged"], 4)
            self.assertEqual(ts.store.screenshot_bytes(oids[4]), one)

    def test_small_noise_counts_as_unchanged(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            b = capture(ts, 1, png(extra=(700, 2, 706, 8)))
            self.assertEqual(row(ts, a)[2], row(ts, b)[2])

    def test_observations_and_events_are_still_recorded(self):
        with TempStore() as ts:
            data = png()
            capture(ts, 0, data)
            b = capture(ts, 1, data, events=[key(T0 + 4 * SEC)])
            self.assertEqual(len(ts.store.get(b)["interaction_events"]), 1)
            self.assertEqual(len(ts.store.list()), 2)

    def test_ocr_text_is_reused(self):
        with TempStore() as ts:
            data = png()
            a = capture(ts, 0, data, ocr=ocr_ok())
            b = capture(ts, 1, data, ocr=ReaderResult("vision-ocr/1", "pending"))
            first = ts.store.get(a)["evidence"]["ocr"]
            second = ts.store.get(b)["evidence"]["ocr"]
            self.assertEqual(second["status"], "succeeded")
            self.assertEqual(first["text"], second["text"])


class TypingTest(unittest.TestCase):
    def test_typing_keeps_only_the_latest_screenshot(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            b = capture(ts, 1, png(box=(50, 300, 120, 316)), events=[key(T0 + 4 * SEC)])
            c = capture(ts, 2, png(box=(50, 300, 200, 316)), events=[key(T0 + 7 * SEC)])
            self.assertEqual(deleted(ts, a), "superseded_typing")
            self.assertEqual(deleted(ts, b), "superseded_typing")
            self.assertIsNone(deleted(ts, c))
            self.assertFalse((ts.store.assets.root / row(ts, a)[1]).exists())
            self.assertTrue((ts.store.assets.root / row(ts, c)[1]).exists())
            self.assertEqual(len(ts.store.list()), 3)
            self.assertEqual(ts.store.dedup_stats["typing"], 2)

    def test_small_change_without_keys_keeps_both(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            capture(ts, 1, png(box=(50, 300, 120, 316)))
            self.assertIsNone(deleted(ts, a))

    def test_large_change_keeps_both(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            capture(ts, 1, png(box=(20, 20, 600, 400)), events=[key(T0 + 4 * SEC)])
            self.assertIsNone(deleted(ts, a))

    def test_two_far_apart_changes_keep_both(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            capture(ts, 1, png(box=(50, 300, 120, 316), extra=(600, 20, 700, 60)), events=[key(T0 + 4 * SEC)])
            self.assertIsNone(deleted(ts, a))

    def test_other_app_keeps_both(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            capture(ts, 1, png(box=(50, 300, 120, 316)), events=[key(T0 + 4 * SEC)], app="com.other")
            self.assertIsNone(deleted(ts, a))

    def test_typing_after_identical_removes_every_name_of_the_old_file(self):
        with TempStore() as ts:
            data = png()
            a = capture(ts, 0, data)
            b = capture(ts, 1, data)
            c = capture(ts, 2, png(box=(50, 300, 120, 316)), events=[key(T0 + 7 * SEC)])
            self.assertEqual(deleted(ts, a), "superseded_typing")
            self.assertEqual(deleted(ts, b), "superseded_typing")
            self.assertTrue((ts.store.assets.root / row(ts, c)[1]).exists())

    def test_pending_ocr_keeps_the_earlier_screenshot(self):
        with TempStore() as ts:
            a = capture(ts, 0, png(), ocr=ReaderResult("vision-ocr/1", "pending"))
            capture(ts, 1, png(box=(50, 300, 120, 316)), events=[key(T0 + 4 * SEC)])
            self.assertIsNone(deleted(ts, a))


class SettingsTest(unittest.TestCase):
    def test_switched_off_stores_everything(self):
        with TempStore() as ts:
            dedup.set_params(ts.store, {"enabled": False})
            data = png()
            a = capture(ts, 0, data)
            b = capture(ts, 1, data)
            root = ts.store.assets.root
            self.assertNotEqual(os.stat(root / row(ts, a)[1]).st_ino, os.stat(root / row(ts, b)[1]).st_ino)

    def test_bad_settings_are_refused(self):
        with TempStore() as ts:
            with self.assertRaises(ValueError):
                dedup.set_params(ts.store, {"enabled": "yes"})
            with self.assertRaises(ValueError):
                dedup.set_params(ts.store, {"nope": 1})

    def test_unreadable_image_is_kept(self):
        with TempStore() as ts:
            a = capture(ts, 0, png())
            capture(ts, 1, b"\x89PNG\r\n\x1a\n" + b"x" * 40)
            self.assertIsNone(deleted(ts, a))


if __name__ == "__main__":
    unittest.main()

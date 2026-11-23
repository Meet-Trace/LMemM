import io
import unittest
from dataclasses import replace

from PIL import Image

from test_v2_moments import CODE, MONEY, context, live, run, text
from trace_v2.observations import ReaderResult, ScreenshotCapture, ScreenshotUnavailable, forget, retention
from v2_observation_helpers import DAY, SEC, T0, TempStore, draft


def real_png(seed):
    image = Image.new("RGB", (1600, 1000), (seed % 200, 120, 200 - seed % 150))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def add(ts, i, t, vocab, seed):
    body = text(vocab, i)
    ocr = ReaderResult("vision-ocr/1", "succeeded", {"lines": [{"text": body, "confidence": 0.9, "box": [0, 0, 10, 10]}]}, body)
    d = replace(draft(key=f"k{i}", t=t, ocr=ocr, shot=ScreenshotCapture(real_png(seed), "png", 1600, 1000, t)),
                context=context("com.apple.dt.Xcode", "shader.cpp"))
    return ts.store.ingest(d).observation_id


def build(ts, vocab=CODE, base=0, count=8):
    return [add(ts, base + i, T0 + (base + i * 3) * SEC, vocab, base * 7 + i * 31) for i in range(count)]


class ThinningTest(unittest.TestCase):
    NOW = T0 + 20 * DAY

    def test_one_small_image_is_kept_per_stable_moment_and_the_rest_deleted(self):
        with TempStore() as ts:
            ids = build(ts)
            run(ts, self.NOW)
            self.assertEqual(live(ts)[0]["state"], "stable")
            result = retention.run(ts.store, self.NOW)
            self.assertEqual(result["thumbnails"], 1)
            for oid in ids:
                with self.assertRaises(ScreenshotUnavailable):
                    ts.store.screenshot_bytes(oid)
            row = ts.store.db.execute("SELECT observation_id, relative_path, width, height, byte_size FROM screenshot_thumbnails").fetchone()
            self.assertIn(row[0], ids)
            self.assertLessEqual(max(row[2], row[3]), retention.THUMB_SIDE)
            data = ts.store.assets.read(row[1])
            self.assertEqual(data[:3], b"\xff\xd8\xff")
            self.assertEqual(retention.run(ts.store, self.NOW)["thumbnails"], 0)

    def test_forget_removes_the_thumbnail(self):
        with TempStore() as ts:
            ids = build(ts)
            run(ts, self.NOW)
            retention.run(ts.store, self.NOW)
            oid, rel = ts.store.db.execute("SELECT observation_id, relative_path FROM screenshot_thumbnails").fetchone()
            self.assertIn(oid, ids)
            forget.forget(ts.store, {"observation_ids": [oid]})
            self.assertFalse(ts.store.assets.exists(rel))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM screenshot_thumbnails").fetchone()[0], 0)

    def test_screenshots_wait_for_the_vision_description_within_the_grace_period(self):
        with TempStore() as ts:
            ids = build(ts)
            soon = T0 + 10 * DAY
            run(ts, soon)
            moment = live(ts)[0]["id"]
            ts.store.derived.execute("INSERT INTO moments VALUES (?,?,?,?,?)", ("22222222-2222-2222-2222-222222222222", "moments/1", "formed", None, 1))
            ts.store.derived.execute("INSERT INTO moment_descriptions VALUES ('22222222-2222-2222-2222-222222222222','m','v','ok','x',0.5,NULL,'[]','[]',1,1.0,'[]','now')")
            held = retention.run(ts.store, soon)
            self.assertEqual((held["thumbnails"], held["deleted"]), (0, 0))
            ts.store.screenshot_bytes(ids[0])
            late = retention.run(ts.store, soon + 8 * DAY)
            self.assertEqual(late["thumbnails"], 1)
            self.assertGreater(late["deleted"], 0)
            self.assertTrue(moment)

    def test_a_dry_run_makes_no_thumbnails(self):
        with TempStore() as ts:
            build(ts)
            run(ts, self.NOW)
            retention.run(ts.store, self.NOW, dry_run=True)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM screenshot_thumbnails").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

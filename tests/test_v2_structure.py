import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


from trace_v2.observations import ObservationStore, ScreenshotCapture, db, forget
from trace_v2.structure import ALGORITHM, StructureError, StructureProcessor, analyze, build, needed
import sys
import trace_v2.structure.analyze
A = sys.modules["trace_v2.structure.analyze"]
from trace_v2.structure import processor as P
from test_v2_async_ocr import Env, pdraft
from v2_observation_helpers import SEC, T0, TempStore, draft, ocr_ok
from v2_structure_screens import H, LAYOUTS, W, Canvas, blank, dashboard, dense_grid, form, front_app_only

SEMANTIC = {"button", "chart", "toolbar", "spreadsheet", "navigation", "editor", "table", "menu", "icon", "label",
            "role", "kind", "type", "category"}


def ok(raw, extraction_id="e" * 36, revision=2):
    return {"extraction_id": extraction_id, "revision": revision, "status": "succeeded",
            "reader_version": "vision-ocr/1", "raw": raw}


def boxes(rep):
    for key in ("text", "regions", "elements", "lines"):
        for item in rep[key]:
            yield key, item


class AnalyzerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.screens = {}
        for name, make in LAYOUTS.items():
            c = make()
            cls.screens[name] = (c, c.png())

    def check_valid(self, rep, width=W, height=H):
        self.assertEqual(rep["algorithm"], ALGORITHM)
        self.assertEqual(rep["source"]["image_size"], [width, height])
        ids = set()
        for key, item in boxes(rep):
            x, y, w, h = item["bbox"]
            self.assertTrue(all(isinstance(v, int) for v in item["bbox"]), (key, item["bbox"]))
            self.assertTrue(w > 0 and h > 0 and x >= 0 and y >= 0, (key, item["bbox"]))
            self.assertLessEqual(x + w, width, (key, item["bbox"]))
            self.assertLessEqual(y + h, height, (key, item["bbox"]))
            self.assertNotIn(item["id"], ids)
            ids.add(item["id"])
            self.assertFalse(SEMANTIC & set(item), (key, sorted(item)))
        for _, item in boxes(rep):
            for other, gap in item.get("neighbours", []):
                self.assertIn(other, ids)
                self.assertGreaterEqual(gap, 0)
            if item.get("region") is not None:
                self.assertIn(item["region"], ids)
        for r in rep["regions"]:
            if r["parent"] is not None:
                self.assertIn(r["parent"], ids)
        json.dumps(rep, allow_nan=False)

    def test_every_layout_uses_the_same_algorithm_and_gives_valid_geometry(self):
        for name, (c, png) in self.screens.items():
            with self.subTest(layout=name):
                rep = analyze(png, ok(c.ocr()), {"observation_id": "o1"})
                self.check_valid(rep)
                self.assertEqual(rep["coordinates"]["working_scale"], 4)
                self.assertEqual(rep["coordinates"]["working_size"], [720, 450])
                self.assertGreaterEqual(len(rep["regions"]), 1)
                self.assertEqual(len(rep["text"]), len(c.lines))

    def test_output_is_deterministic(self):
        c, png = self.screens["dashboard"]
        self.assertEqual(analyze(png, ok(c.ocr())), analyze(png, ok(c.ocr())))

    def test_text_keeps_order_confidence_geometry_and_is_not_flattened(self):
        c, png = self.screens["code_editor"]
        rep = analyze(png, ok(c.ocr()))
        self.assertEqual([t["text"] for t in rep["text"]], [l["text"] for l in c.lines])
        self.assertEqual([t["order"] for t in rep["text"]], list(range(len(c.lines))))
        self.assertEqual([t["source_line"] for t in rep["text"]], list(range(len(c.lines))))
        for t, line in zip(rep["text"], c.lines):
            self.assertEqual(t["confidence"], round(line["confidence"], 4))
            for got, want in zip(t["bbox"], line["box"]):
                self.assertLessEqual(abs(got - want), 1)
        self.assertNotIn("text", {k for k, v in rep.items() if isinstance(v, str)})

    def test_ocr_boxes_in_another_scale_are_mapped_to_screenshot_pixels(self):
        c, png = self.screens["form"]
        rep = analyze(png, ok(c.ocr(scale=2.0)))
        for t, line in zip(rep["text"], c.lines):
            self.assertEqual(t["source_box"], [round(v / 2.0, 2) for v in line["box"]])
            for got, want in zip(t["bbox"], line["box"]):
                self.assertLessEqual(abs(got - want), 1)

    def test_dense_grid_separators_are_found(self):
        c, png = self.screens["dense_grid"]
        rep = analyze(png, ok(c.ocr()))
        h = [l for l in rep["lines"] if l["orientation"] == "h"]
        v = [l for l in rep["lines"] if l["orientation"] == "v"]
        self.assertGreaterEqual(len(h), 40)
        self.assertGreaterEqual(len(v), 11)
        for l in h:
            self.assertLessEqual(l["bbox"][3], 16)

    def test_non_text_shapes_are_found_with_features(self):
        c, png = self.screens["form"]
        rep = analyze(png, ok(c.ocr()))
        hits = [e for e in rep["elements"] if e["bbox"][0] <= 1710 and e["bbox"][1] <= 1610
                and e["bbox"][0] + e["bbox"][2] >= 1970 and e["bbox"][1] + e["bbox"][3] >= 1680]
        self.assertTrue(hits)
        filled = hits[0]
        for key in ("background", "foreground", "edge_density", "luminance_std", "aspect_ratio",
                    "border_edge_fraction", "colour_count", "text_inside", "region"):
            self.assertIn(key, filled)
        self.assertEqual(filled["text_inside"], 1)

    def test_little_or_no_text(self):
        c, png = self.screens["blank"]
        rep = analyze(png, ok(c.ocr()))
        self.assertEqual(rep["text"], [])
        self.assertGreaterEqual(len(rep["elements"]), 2)
        ellipse = [e for e in rep["elements"] if 1280 <= e["bbox"][0] <= 1320 and 740 <= e["bbox"][1] <= 780]
        self.assertTrue(ellipse)

    def test_unfamiliar_layout_is_handled_like_any_other(self):
        c, png = self.screens["unfamiliar"]
        rep = analyze(png, ok(c.ocr()))
        self.check_valid(rep)
        self.assertGreaterEqual(len(rep["elements"]), 1)
        self.assertEqual(rep["text"][0]["text"], c.lines[0]["text"])

    def test_missing_pending_or_failed_ocr_still_gives_visual_structure(self):
        c, png = self.screens["dashboard"]
        with_text = analyze(png, ok(c.ocr()))
        for ocr in (None, {"extraction_id": "p" * 36, "revision": 1, "status": "pending", "reader_version": "v", "raw": None},
                    {"extraction_id": "f" * 36, "revision": 2, "status": "failed", "reader_version": "v", "raw": None},
                    {"extraction_id": "u" * 36, "revision": 1, "status": "unavailable", "reader_version": "v", "raw": None}):
            with self.subTest(ocr=None if ocr is None else ocr["status"]):
                rep = analyze(png, ocr)
                self.check_valid(rep)
                self.assertEqual(rep["text"], [])
                self.assertEqual(rep["source"]["ocr"], None if ocr is None else
                                 {k: ocr[k] for k in ("extraction_id", "revision", "status", "reader_version")})
                self.assertEqual(rep["lines"], with_text["lines"])
                self.assertGreaterEqual(len(rep["elements"]), len(with_text["elements"]))

    def test_malformed_ocr_lines_are_skipped(self):
        c, png = self.screens["blank"]
        raw = {"lines": [{"text": "a", "box": [10, 10, 0, 5]}, {"text": "b", "box": None}, {"text": "c"},
                         {"text": "d", "box": [W + 50, 10, 20, 20]}, {"text": "e", "box": ["x", 1, 2, 3]},
                         {"text": "f", "confidence": 0.5, "box": [-5, -5, 30, 30]}]}
        rep = analyze(png, ok(raw))
        self.assertEqual([t["text"] for t in rep["text"]], ["f"])
        self.assertEqual(rep["text"][0]["bbox"], [0, 0, 25, 25])
        self.assertEqual(rep["text"][0]["source_line"], 5)

    def test_transparent_outside_the_front_app_is_ignored(self):
        c = front_app_only()
        rep = analyze(c.png(), ok(c.ocr()))
        self.check_valid(rep)
        vx, vy, vw, vh = rep["visible_bbox"]
        self.assertTrue(abs(vx - 600) <= 4 and abs(vy - 300) <= 4)
        self.assertTrue(abs(vx + vw - 2201) <= 4 and abs(vy + vh - 1401) <= 4)
        self.assertAlmostEqual(rep["visible_fraction"], 1601 * 1101 / (W * H), delta=0.01)
        for _, item in boxes(rep):
            x, y, w, h = item["bbox"]
            self.assertTrue(x >= vx - 4 and y >= vy - 4 and x + w <= vx + vw + 4 and y + h <= vy + vh + 4, item)

    def test_coordinate_mapping_for_other_sizes(self):
        for size, scale in (((1001, 777), 2), ((640, 400), 1), ((3456, 2234), 5), ((5120, 2880), 8)):
            with self.subTest(size=size):
                c = Canvas((240, 240, 240), size=size)
                c.draw.rectangle([size[0] // 3, size[1] // 3, size[0] // 2, size[1] // 2], fill=(20, 20, 20))
                c.text((10, 10), "corner", (0, 0, 0))
                rep = analyze(c.png(), ok(c.ocr()))
                self.check_valid(rep, *size)
                self.assertEqual(rep["coordinates"]["working_scale"], scale)
                big = max(rep["elements"], key=lambda e: e["bbox"][2] * e["bbox"][3])
                x, y, w, h = big["bbox"]
                self.assertLessEqual(abs(x - size[0] // 3), 2 * scale + 1)
                self.assertLessEqual(abs(y - size[1] // 3), 2 * scale + 1)
                self.assertLessEqual(abs(x + w - (size[0] // 2 + 1)), 2 * scale + 1)
                self.assertLessEqual(abs(y + h - (size[1] // 2 + 1)), 2 * scale + 1)

    def test_original_screenshot_is_not_changed(self):
        c, png = self.screens["dashboard"]
        before = hashlib.sha256(png).hexdigest()
        analyze(png, ok(c.ocr()))
        self.assertEqual(hashlib.sha256(png).hexdigest(), before)

    def test_corrupt_screenshot_is_reported(self):
        with self.assertRaises(StructureError) as caught:
            analyze(b"\x89PNG\r\n\x1a\nnot really")
        self.assertEqual(caught.exception.code, "ScreenshotCorrupt")

    def test_output_is_bounded(self):
        old = A.MAX_ELEMENTS, A.MAX_REGIONS, A.MAX_LINES
        A.MAX_ELEMENTS, A.MAX_REGIONS, A.MAX_LINES = 3, 5, 4
        try:
            c, png = self.screens["dense_grid"]
            rep = analyze(png, None)
            self.assertLessEqual(len(rep["elements"]), 3)
            self.assertLessEqual(len(rep["regions"]), 5)
            self.assertLessEqual(len(rep["lines"]), 4)
            self.assertTrue(rep["truncated"]["lines"])
        finally:
            A.MAX_ELEMENTS, A.MAX_REGIONS, A.MAX_LINES = old


def shot(canvas, t=T0):
    return ScreenshotCapture(canvas.png(), "png", canvas.image.width, canvas.image.height, t)


def structure_rows(store, oid):
    return store.db.execute("SELECT revision, supersedes, status, source_extraction_id, reader_version, text "
                            "FROM extractions WHERE observation_id=? AND reader='structure' ORDER BY revision",
                            (oid,)).fetchall()


def ocr_id(store, oid, revision=None):
    q = "SELECT extraction_id FROM extractions WHERE observation_id=? AND reader='ocr' "
    if revision is None:
        return store.db.execute(q + "ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()[0]
    return store.db.execute(q + "AND revision=?", (oid, revision)).fetchone()[0]


class PersistenceTest(unittest.TestCase):
    def test_structure_is_appended_to_the_same_observation_with_provenance(self):
        with TempStore() as ts:
            c = form()
            oid = ts.store.ingest(draft(shot=shot(c))).observation_id
            before = ts.store.get(oid)
            png_before = ts.store.screenshot_bytes(oid)
            self.assertEqual(needed(ts.store, T0 + SEC), [oid])
            status, eid = build(ts.store, oid)
            self.assertEqual(status, "succeeded")
            after = ts.store.get(oid)
            self.assertEqual(before, after)
            self.assertEqual(ts.store.screenshot_bytes(oid), png_before)
            rows = structure_rows(ts.store, oid)
            self.assertEqual(rows, [(1, None, "succeeded", ocr_id(ts.store, oid), ALGORITHM, None)])
            rec = ts.store.extractions(oid, "structure")[0]
            self.assertEqual(rec["extraction_id"], eid)
            raw = rec["raw"]
            self.assertEqual(raw["source"]["observation_id"], oid)
            self.assertEqual(raw["source"]["screenshot_sha256"], hashlib.sha256(png_before).hexdigest())
            self.assertEqual(raw["source"]["ocr"]["extraction_id"], ocr_id(ts.store, oid))
            self.assertEqual(raw["text"][0]["text"], "hello world")
            self.assertEqual(needed(ts.store, T0 + SEC), [])

    def test_no_duplicates_and_stale_sources_are_refused(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(shot=shot(blank()))).observation_id
            build(ts.store, oid)
            again = P.append(ts.store, oid, P.ReaderResult(ALGORITHM, "succeeded", {"x": 1}), ocr_id(ts.store, oid))
            self.assertEqual(again[0], "duplicate")
            stale = P.append(ts.store, oid, P.ReaderResult(ALGORITHM, "succeeded", {"x": 1}), "0" * 36)
            self.assertEqual(stale, ("stale", None))
            self.assertEqual(len(structure_rows(ts.store, oid)), 1)

    def test_pending_ocr_waits_then_builds_then_revises_when_ocr_completes(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft(shot=shot(dashboard()))).observation_id
            self.assertEqual(needed(ts.store, T0 + 5 * SEC, wait_for_ocr_s=30), [])
            self.assertEqual(needed(ts.store, T0 + 31 * SEC, wait_for_ocr_s=30), [oid])
            build(ts.store, oid)
            first = ts.store.extractions(oid, "structure")[0]
            self.assertEqual(first["raw"]["source"]["ocr"]["status"], "pending")
            self.assertEqual(first["raw"]["text"], [])
            self.assertTrue(env.proc.run_once())
            self.assertEqual(needed(ts.store, T0 + 31 * SEC), [oid])
            build(ts.store, oid)
            rows = structure_rows(ts.store, oid)
            self.assertEqual([(r[0], r[2]) for r in rows], [(1, "succeeded"), (2, "succeeded")])
            self.assertEqual(rows[1][1], first["extraction_id"])
            self.assertEqual(rows[1][3], ocr_id(ts.store, oid, 2))
            second = ts.store.extractions(oid, "structure")[1]
            self.assertEqual(second["raw"]["source"]["ocr"]["revision"], 2)
            self.assertEqual([t["text"] for t in second["raw"]["text"]], ["recognised text"])
            self.assertEqual(needed(ts.store, T0 + 31 * SEC), [])

    def test_failed_ocr_gives_structure_without_text(self):
        with TempStore() as ts:
            from v2_observation_helpers import failed
            oid = ts.store.ingest(draft(shot=shot(dashboard()), ocr=failed("vision-ocr/1"))).observation_id
            self.assertEqual(build(ts.store, oid)[0], "succeeded")
            raw = ts.store.extractions(oid, "structure")[0]["raw"]
            self.assertEqual((raw["source"]["ocr"]["status"], raw["text"]), ("failed", []))
            self.assertGreater(len(raw["elements"]), 0)

    def test_missing_screenshot_file_is_recorded_once_as_unavailable(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(shot=shot(blank()))).observation_id
            rel = ts.store.db.execute("SELECT relative_path FROM screenshots WHERE observation_id=?", (oid,)).fetchone()[0]
            ts.store.assets.delete(rel)
            self.assertEqual(build(ts.store, oid)[0], "unavailable")
            self.assertEqual(needed(ts.store, T0 + SEC), [])
            self.assertEqual(structure_rows(ts.store, oid)[0][2], "unavailable")

    def test_corrupt_screenshot_is_recorded_as_failed(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            status, _ = build(ts.store, oid)
            self.assertIn(status, ("failed", "unavailable"))
            self.assertEqual(needed(ts.store, T0 + SEC), [])

    def test_structure_is_immutable_and_forgotten_with_its_observation(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(shot=shot(blank()))).observation_id
            build(ts.store, oid)
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.db.execute("UPDATE extractions SET status='failed' WHERE reader='structure'")
            with self.assertRaises(sqlite3.DatabaseError):
                ts.store.db.execute("DELETE FROM extractions WHERE reader='structure'")
            forget.forget(ts.store, {"observation_ids": [oid]}, dry_run=False)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM extractions").fetchone()[0], 0)

    def test_source_must_be_the_same_observations_ocr(self):
        with TempStore() as ts:
            a = ts.store.ingest(draft(key="a", shot=shot(blank()))).observation_id
            b = ts.store.ingest(draft(key="b", t=T0 + SEC, shot=shot(blank(), T0 + SEC))).observation_id
            ax = ts.store.db.execute("SELECT extraction_id FROM extractions WHERE observation_id=? AND reader='accessibility'",
                                     (a,)).fetchone()[0]
            for src in (ax, ocr_id(ts.store, b)):
                with self.assertRaises(sqlite3.IntegrityError):
                    ts.store._insert_extraction(a, "structure", P.ReaderResult(ALGORITHM, "succeeded", {"x": 1}),
                                                1, None, None, src)
            with self.assertRaises(sqlite3.IntegrityError):
                ts.store._insert_extraction(a, "ocr", ocr_ok(), 2, ocr_id(ts.store, a), None, ocr_id(ts.store, a))

    def test_processor_thread_builds_and_stops(self):
        with TempStore() as ts:
            oids = [ts.store.ingest(draft(key=f"k{i}", t=T0 + i * SEC, shot=shot(form(), T0 + i * SEC))).observation_id
                    for i in range(3)]
            proc = StructureProcessor(ts.store, lookback_s=None, idle_s=0.05, clock_ns=lambda: T0 + 10 * SEC)
            self.assertEqual(proc.run_once(), 3)
            self.assertEqual(proc.stats()["succeeded"], 3)
            self.assertEqual(proc.run_once(), 0)
            proc.start()
            proc.stop()
            for oid in oids:
                self.assertEqual(len(structure_rows(ts.store, oid)), 1)

    def test_lookback_window_limits_the_background_scan(self):
        with TempStore() as ts:
            ts.store.ingest(draft(shot=shot(blank())))
            proc = StructureProcessor(ts.store, lookback_s=60, clock_ns=lambda: T0 + 3600 * SEC)
            self.assertEqual(proc.run_once(), 0)
            proc.stop()


class RunnerTest(unittest.TestCase):
    def test_runner_runs_the_processor_only_when_asked(self):
        from test_v2_runner import make_runner
        with TempStore() as ts:
            stats = make_runner(ts.store).run(duration_s=0.3)
            self.assertIsNone(stats["structure"])
            runner = make_runner(ts.store, structure=True)
            stats = runner.run(duration_s=1.5)
            self.assertIsNotNone(stats["structure"])
            self.assertFalse(runner.structure_processor._thread.is_alive())
            done = ts.store.db.execute("SELECT count(*) FROM extractions WHERE reader='structure'").fetchone()[0]
            self.assertEqual(done, sum(v for k, v in stats["structure"].items() if not k.startswith("ms_")))
            self.assertGreater(stats["observations"], 0)
            self.assertGreater(done, 0)


class MigrationTest(unittest.TestCase):
    def test_v2_database_upgrades_and_keeps_extractions(self):
        from trace_v2 import paths
        with tempfile.TemporaryDirectory() as tmp:
            root, legacy = Path(tmp).resolve() / "data", Path(tmp).resolve() / "Legacy-V1"
            paths.check_data_dir(root, legacy)
            path = paths.database_path(root)
            conn = db.connect(path)
            db.migrate(conn, path, db.migrations()[:2])
            oid = "0" * 36
            conn.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (oid, T0, "t", "k:0", "h", "b", "n", 1, "w", "d", "{}", None, None,
                          '{"context":{"status":"succeeded","error_code":null},"events":{}}', "{}", "v", "now"))
            e1, e2 = "1" * 36, "2" * 36
            conn.execute("INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                         "recorded_at,privacy_json) VALUES (?,?,'ocr',1,NULL,'v','pending','now',NULL)", (e1, oid))
            conn.execute("INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                         "text,recorded_at,privacy_json) VALUES (?,?,'ocr',2,?,'v','succeeded','later','now','{}')",
                         (e2, oid, e1))
            before = conn.execute("SELECT * FROM extractions ORDER BY revision").fetchall()
            conn.close()
            store = ObservationStore(root, legacy)
            try:
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], db.LATEST_VERSION)
                self.assertTrue(list(path.parent.glob("*.pre-v3.bak")))
                after = store.db.execute("SELECT * FROM extractions ORDER BY revision").fetchall()
                self.assertEqual([r[:-1] for r in after], before)
                self.assertEqual([r[-1] for r in after], [None, None])
                names = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
                self.assertTrue({"extractions_immutable_u", "extractions_immutable_d", "extractions_revision_chain",
                                 "extractions_structure_source"} <= names)
            finally:
                store.close()


class StructureCliTest(unittest.TestCase):
    def test_cli_backfills(self):
        from trace_v2.structure.__main__ import main
        with TempStore() as ts:
            oid = ts.store.ingest(draft(shot=shot(blank()))).observation_id
            root, legacy = ts.root, ts.legacy
            ts.store.close()
            import contextlib
            out = io.StringIO()
            from trace_v2 import paths
            original = paths.check_data_dir
            paths.check_data_dir = lambda d, legacy_archive=legacy: original(d, legacy)
            try:
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(["--data-dir", str(root)]), 0)
            finally:
                paths.check_data_dir = original
            self.assertEqual(json.loads(out.getvalue())["outcomes"], {"succeeded": 1})
            ts.store = ObservationStore(root, legacy_archive=legacy)
            self.assertEqual(len(structure_rows(ts.store, oid)), 1)


if __name__ == "__main__":
    unittest.main()

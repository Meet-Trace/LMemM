"""Trace V2 Observation store: creation, evidence, events, immutability, duplicates, scale."""

import sqlite3
import time
import unittest
import zlib

from trace_v2 import paths
from trace_v2.observations import (DuplicateObservation, InteractionEvent, InvalidCapture, ObservationStore,
                                   ReaderResult, ScreenshotCapture, ScreenshotFailure, ScreenshotUnavailable)
from v2_observation_helpers import DAY, PNG, SEC, T0, TempStore, ax_ok, click, draft, failed, ocr_ok


class CreationTest(unittest.TestCase):
    def test_one_observation_unifies_all_evidence(self):
        with TempStore() as ts:
            r = ts.store.ingest(draft(events=[click(T0 - 3 * SEC), click(T0 - 1 * SEC)]))
            self.assertTrue(r.created)
            o = ts.store.get(r.observation_id)
            self.assertEqual(len(r.observation_id), 36)
            self.assertEqual(o["timestamp_ns"], T0)
            self.assertEqual(o["context"]["application"]["bundle_id"], "com.example.editor")
            self.assertEqual(o["context"]["display"]["id"], "main")
            self.assertEqual(o["evidence"]["screenshot"]["status"], "stored")
            self.assertEqual(o["evidence"]["ocr"]["status"], "succeeded")
            self.assertEqual(o["evidence"]["accessibility"]["status"], "succeeded")
            self.assertEqual(len(o["interaction_events"]), 2)
            self.assertEqual(o["metadata"]["reader_versions"], {"ocr": "vision-ocr/1", "accessibility": "ax-tree/1"})
            self.assertEqual(o["metadata"]["capture_version"], "test-capture/1")
            # one Observation row; OCR and accessibility are evidence of it, not observations of their own
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM extractions WHERE observation_id=?",
                                                 (r.observation_id,)).fetchone()[0], 2)

    def test_invalid_draft_stores_nothing(self):
        with TempStore() as ts:
            with self.assertRaises(InvalidCapture):
                ts.store.ingest(draft(key="bad key with spaces"))
            with self.assertRaises(InvalidCapture):
                ts.store.ingest(draft(ocr=ReaderResult("v", "succeeded", raw=None)))
            self.assertEqual(ts.store.list(), [])
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})

    def test_list_filters_by_time_and_app_with_keyset_pages(self):
        with TempStore() as ts:
            for i in range(5):
                ts.store.ingest(draft(key=f"s:{i}", t=T0 + i * SEC, app="com.a" if i % 2 else "com.b"))
            self.assertEqual([o["app_bundle_id"] for o in ts.store.list(app_bundle_id="com.a")], ["com.a", "com.a"])
            first = ts.store.list(limit=2)
            rest = ts.store.list(limit=10, after=(first[-1]["timestamp_ns"], first[-1]["observation_id"]))
            self.assertEqual(len(first) + len(rest), 5)
            self.assertEqual(len(ts.store.list(start_ns=T0 + SEC, end_ns=T0 + 3 * SEC)), 2)


class ScreenshotTest(unittest.TestCase):
    def test_original_bytes_retrievable_by_observation_id(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            self.assertEqual(ts.store.screenshot_bytes(oid), PNG)
            shot = ts.store.get(oid)["evidence"]["screenshot"]
            self.assertEqual((shot["format"], shot["width"], shot["height"], shot["byte_size"]), ("png", 32, 32, len(PNG)))
            self.assertTrue(shot["reference"].startswith("screenshots/2027/01/15/"))
            path = ts.root / "observation-assets" / shot["reference"]
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupted_file_is_detected_by_checksum(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            path = ts.root / "observation-assets" / ts.store.get(oid)["evidence"]["screenshot"]["reference"]
            path.write_bytes(PNG[:-1] + b"\x00")
            with self.assertRaises(ScreenshotUnavailable) as caught:
                ts.store.screenshot_bytes(oid)
            self.assertEqual(caught.exception.reason, "corrupt")

    def test_screenshot_failure_and_format_mismatch_still_record_the_observation(self):
        with TempStore() as ts:
            a = ts.store.ingest(draft(key="s:a", shot=ScreenshotFailure("CaptureDenied", "no permission", T0)))
            b = ts.store.ingest(draft(key="s:b", shot=ScreenshotCapture(b"not a png", "png", 1, 1, T0)))
            for oid, code in ((a.observation_id, "CaptureDenied"), (b.observation_id, "format_mismatch")):
                shot = ts.store.get(oid)["evidence"]["screenshot"]
                self.assertEqual((shot["status"], shot["error_code"], shot["available"]), ("failed", code, False))
                with self.assertRaises(ScreenshotUnavailable):
                    ts.store.screenshot_bytes(oid)


class ReaderEvidenceTest(unittest.TestCase):
    def test_raw_ocr_and_accessibility_output_round_trip(self):
        with TempStore() as ts:
            o = ts.store.get(ts.store.ingest(draft()).observation_id)
            self.assertEqual(o["evidence"]["ocr"]["raw"]["lines"][0]["box"], [10, 20, 300, 18])
            self.assertEqual(o["evidence"]["ocr"]["text"], "hello world")
            self.assertEqual(o["evidence"]["accessibility"]["raw"]["children"][0]["role"], "AXTextArea")
            self.assertEqual(o["evidence"]["ocr"]["started_at_ns"], T0)

    def test_large_raw_output_is_stored_whole(self):
        lines = [{"text": f"line {i} " * 20, "box": [0, i, 10, 10]} for i in range(3000)]
        with TempStore() as ts:
            o = ts.store.get(ts.store.ingest(draft(ocr=ReaderResult("v", "succeeded", {"lines": lines}, "x"))).observation_id)
            self.assertEqual(o["evidence"]["ocr"]["raw"]["lines"], lines)

    def test_each_reader_fails_independently(self):
        cases = {"ocr_fails": (failed("vision-ocr/1"), ax_ok(), ("failed", "succeeded")),
                 "ax_fails": (ocr_ok(), failed("ax-tree/1"), ("succeeded", "failed")),
                 "both_fail": (failed("vision-ocr/1"), failed("ax-tree/1"), ("failed", "failed"))}
        with TempStore() as ts:
            for i, (name, (ocr, ax, expect)) in enumerate(cases.items()):
                with self.subTest(name):
                    o = ts.store.get(ts.store.ingest(draft(key=f"s:{i}", ocr=ocr, ax=ax)).observation_id)
                    st = o["metadata"]["extraction_status"]
                    self.assertEqual((st["ocr"]["status"], st["accessibility"]["status"]), expect)
                    self.assertEqual(st["screenshot"]["status"], "stored")
                    for reader, status in zip(("ocr", "accessibility"), expect):
                        ev = o["evidence"][reader]
                        if status == "failed":
                            self.assertEqual((ev["error_code"], ev["raw"]), ("ReaderCrashed", None))

    def test_rerun_appends_a_revision_and_keeps_the_original(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(ocr=failed("vision-ocr/1"))).observation_id
            eid = ts.store.add_extraction(oid, "ocr", ocr_ok("second pass"))
            o = ts.store.get(oid)
            self.assertEqual((o["evidence"]["ocr"]["revision"], o["evidence"]["ocr"]["status"], o["evidence"]["ocr"]["extraction_id"]),
                             (2, "succeeded", eid))
            earlier = o["metadata"]["later_extractions"]["ocr"]
            self.assertEqual([(x["revision"], x["status"]) for x in earlier], [(1, "failed")])
            self.assertEqual(o["evidence"]["ocr"]["supersedes"], earlier[0]["extraction_id"])

    def test_revision_chain_cannot_be_forged(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            with self.assertRaises(sqlite3.IntegrityError):
                ts.store.db.execute(
                    "INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,"
                    "status,error_code,recorded_at) VALUES ('00000000-0000-0000-0000-000000000000',?,'ocr',3,NULL,'v',"
                    "'failed','x','now')", (oid,))


class EventTest(unittest.TestCase):
    def test_order_and_timestamps_preserved_exactly_as_received(self):
        # deliberately out of time order: arrival order is the record, it is never re-sorted
        times = [T0 - 5, T0 - 9, T0 - 1, T0 - 1]
        evs = [InteractionEvent("cursor_move", times[0], {"x": 1.5, "y": 2.5}),
               InteractionEvent("scroll", times[1], {"dx": 0, "dy": -3.0, "phase": "changed"}),
               InteractionEvent("keypress", times[2], {"phase": "down", "key_class": "character", "modifiers": ["shift"]}),
               click(times[3])]
        with TempStore() as ts:
            stored = ts.store.events(ts.store.ingest(draft(events=evs)).observation_id)
            self.assertEqual([e["seq"] for e in stored], [0, 1, 2, 3])
            self.assertEqual([e["timestamp_ns"] for e in stored], times)
            self.assertEqual([e["type"] for e in stored], ["cursor_move", "scroll", "keypress", "click"])
            self.assertEqual(stored[2]["payload"], {"phase": "down", "key_class": "character", "modifiers": ["shift"]})
            self.assertEqual(len({e["event_id"] for e in stored}), 4)

    def test_events_belong_to_their_own_observation(self):
        with TempStore() as ts:
            a = ts.store.ingest(draft(key="s:a", events=[click(T0 - 2)])).observation_id
            b = ts.store.ingest(draft(key="s:b", t=T0 + SEC, events=[click(T0 + 1), click(T0 + 2), click(T0 + 3)]))
            self.assertEqual(len(ts.store.events(a)), 1)
            self.assertEqual(len(ts.store.events(b.observation_id)), 3)

    def test_large_event_batch_is_stored_in_one_transaction(self):
        n = 100_000
        evs = [InteractionEvent("cursor_move", T0 - n + i, {"x": float(i % 1440), "y": float(i % 900)}) for i in range(n)]
        with TempStore() as ts:
            started = time.monotonic()
            oid = ts.store.ingest(draft(events=evs)).observation_id
            elapsed = time.monotonic() - started
            self.assertEqual(ts.store.db.execute("SELECT count(*), min(seq), max(seq) FROM interaction_events "
                                                 "WHERE observation_id=?", (oid,)).fetchone(), (n, 0, n - 1))
            self.assertLess(elapsed, 20.0, f"100k events took {elapsed:.1f}s")


class ImmutabilityTest(unittest.TestCase):
    STATEMENTS = (
        "UPDATE observations SET captured_at_ns = captured_at_ns + 1",
        "UPDATE observations SET context_json = '{}'",
        "DELETE FROM observations",
        "UPDATE screenshots SET sha256 = 'x'",
        "DELETE FROM screenshots",
        "UPDATE extractions SET text = 'contaminated'",
        "UPDATE extractions SET raw = NULL, raw_sha256 = NULL",
        "DELETE FROM extractions",
        "UPDATE interaction_events SET occurred_at_ns = 0",
        "DELETE FROM interaction_events",
    )

    def test_committed_evidence_cannot_be_changed_even_with_raw_sql(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft(events=[click(T0 - 1)])).observation_id
            before = ts.store.get(oid)
            for sql in self.STATEMENTS:
                with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError) as caught:
                    ts.store.db.execute(sql)
                self.assertIn("immutable", str(caught.exception))
            self.assertEqual(ts.store.get(oid), before)

    def test_store_has_no_update_api(self):
        public = {n for n in dir(ObservationStore) if not n.startswith("_")}
        self.assertFalse({n for n in public if n.startswith(("update", "set", "delete", "replace"))})

    def test_raw_output_is_compressed_but_byte_identical_after_decode(self):
        with TempStore() as ts:
            oid = ts.store.ingest(draft()).observation_id
            blob, enc = ts.store.db.execute("SELECT raw, raw_encoding FROM extractions WHERE observation_id=? AND "
                                            "reader='ocr'", (oid,)).fetchone()
            self.assertEqual(enc, "json+zlib")
            self.assertIn(b'"box":[10,20,300,18]', zlib.decompress(blob))


class DuplicateTest(unittest.TestCase):
    def test_retry_with_same_key_and_content_returns_the_same_observation(self):
        with TempStore() as ts:
            a = ts.store.ingest(draft(events=[click(T0 - 1)]))
            b = ts.store.ingest(draft(events=[click(T0 - 1)]))
            self.assertEqual((a.observation_id, a.created, b.created), (b.observation_id, True, False))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM interaction_events").fetchone()[0], 1)
            self.assertEqual(len(ts.store.assets.all_relative_paths()), 1)

    def test_same_key_with_different_content_is_refused(self):
        with TempStore() as ts:
            ts.store.ingest(draft())
            with self.assertRaises(DuplicateObservation):
                ts.store.ingest(draft(ocr=ocr_ok("something else")))
            self.assertEqual(len(ts.store.list()), 1)
            self.assertEqual(len(ts.store.assets.all_relative_paths()), 1)

    def test_identical_screens_at_different_times_are_separate_observations(self):
        with TempStore() as ts:
            ts.store.ingest(draft(key="s:0"))
            ts.store.ingest(draft(key="s:1", t=T0 + DAY))
            self.assertEqual(len(ts.store.list()), 2)


class ConcurrencyTest(unittest.TestCase):
    def test_parallel_writers_and_readers_share_one_store_safely(self):
        import threading
        with TempStore() as ts:
            errors = []

            def work(w):
                try:
                    for i in range(25):
                        oid = ts.store.ingest(draft(key=f"w{w}:{i}", t=T0 + (w * 100 + i) * SEC)).observation_id
                        ts.store.get(oid)
                        ts.store.list(limit=5)
                except Exception as error:      # noqa: BLE001 - surfaced by the assertion below
                    errors.append(error)

            threads = [threading.Thread(target=work, args=(w,)) for w in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0], 100)


class DataDirGuardTest(unittest.TestCase):
    def test_refuses_the_legacy_archive_and_v1_data(self):
        with TempStore() as ts:
            legacy = ts.legacy
            (legacy / "memory").mkdir(parents=True)
            with self.assertRaises(paths.LegacyDataError):
                ObservationStore(legacy, legacy_archive=legacy)
            with self.assertRaises(paths.LegacyDataError):
                ObservationStore(legacy / "memory", legacy_archive=legacy)
            v1 = ts.root.parent / "v1-style"
            (v1 / "memory").mkdir(parents=True)
            (v1 / "memory" / "memory.json").write_text("{}")
            with self.assertRaises(paths.LegacyDataError):
                ObservationStore(v1, legacy_archive=legacy)
            with self.assertRaises(paths.LegacyDataError):
                ObservationStore(paths.REPO_ROOT / "data", legacy_archive=legacy)    # V1's default folder

    def test_v2_has_its_own_default_data_directory(self):
        mac = paths.default_data_dir("darwin", {})
        self.assertEqual(mac.parts[-3:], ("Application Support", "Trace", "V2"))
        self.assertEqual(mac.name, "V2")
        self.assertEqual(mac.parent.name, "Trace")
        self.assertNotEqual(mac, paths.LEGACY_ARCHIVE)
        self.assertNotIn(paths.LEGACY_ARCHIVE, mac.parents)
        self.assertEqual(paths.default_data_dir("linux", {"XDG_DATA_HOME": "/x"}), paths.Path("/x/trace/v2"))
        self.assertEqual(paths.data_dir({"TRACE_V2_DATA_DIR": "/y"}), paths.Path("/y"))
        self.assertNotEqual(paths.data_dir({}), paths.REPO_ROOT / "data")


if __name__ == "__main__":
    unittest.main()

"""Asynchronous OCR enrichment: one capture = one Observation; OCR is appended to it later, durably and exactly once."""

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from trace_v2 import paths
from trace_v2.observations import (InvalidCapture, ObservationStore, ReaderResult, ScreenshotCapture, ScreenshotWithheld,
                                   db, forget, ocr_processor, ocr_queue, retention)
from trace_v2.observations.ocr_processor import OcrProcessor
from trace_v2.observations.ocr_queue import QueuePolicy
from trace_v2.observations.pipeline import ContinuousObserver, ReaderUnavailable
from test_v2_observation_pipeline import Sources, buffer
from v2_observation_helpers import DAY, PNG, SEC, T0, TempStore, click, draft

POLICY = QueuePolicy(max_attempts=3, backoff_base_s=10, backoff_cap_s=100, deferral_s=2, max_deferrals=2,
                     lease_slack_s=5, max_age_s=600)
FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"


class Timeout(TimeoutError):
    pass


class WorkerCrashed(RuntimeError):
    pass


class WorkerStarting(ReaderUnavailable):
    pass


class FakeClock:
    def __init__(self, ns=T0):
        self.ns = ns

    def __call__(self):
        return self.ns

    def advance(self, seconds):
        self.ns += int(seconds * SEC)


class FakeReader:
    version = "vision-ocr/1"

    def __init__(self, *script, text="recognised text"):
        self.script, self.text, self.calls = list(script), text, 0

    def read(self, shot, context):
        self.calls += 1
        step = self.script.pop(0) if self.script else None
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        text = step if isinstance(step, str) else self.text
        return {"lines": [{"text": text, "confidence": 0.9, "box": [0, 0, 10, 10]}]}, text


def pending(t=T0):
    return ReaderResult("vision-ocr/1", "pending")


def pdraft(key="s:0", t=T0, **kw):
    return draft(key=key, t=t, ocr=pending(t), **kw)


def view(o):
    e = o["evidence"]
    return (e["screenshot"], e["accessibility"], o["interaction_events"], o["context"], o["metadata"]["privacy"],
            o["timestamp_ns"], o["metadata"]["capture_key"], o["metadata"]["events_window_ns"])


class Env:
    def __init__(self, ts, reader=None, policy=POLICY, timeout_s=10.0):
        self.ts, self.store, self.clock = ts, ts.store, FakeClock()
        self.store.clock_ns = self.clock
        self.store.ocr_max_attempts = policy.max_attempts
        self.reader = reader or FakeReader()
        self.proc = OcrProcessor(self.store, self.reader, timeout_s, policy, self.clock)

    def ocr(self, oid):
        return self.store.get(oid)["evidence"]["ocr"]

    def job(self, oid):
        return self.store.db.execute("SELECT state, attempts, deferrals, last_error_code FROM ocr_jobs WHERE observation_id=?",
                                     (oid,)).fetchone()


class CompletionTest(unittest.TestCase):
    def test_observation_is_readable_at_once_then_enriched_in_place(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft(events=[click(T0 - SEC)])).observation_id
            before = ts.store.get(oid)
            self.assertEqual((before["evidence"]["ocr"]["status"], before["evidence"]["ocr"]["revision"]), ("pending", 1))
            self.assertEqual(before["metadata"]["extraction_status"]["ocr"]["status"], "pending")
            self.assertEqual(before["metadata"]["later_extractions"]["ocr"], [])
            self.assertEqual(env.job(oid)[0], "queued")
            self.assertTrue(env.proc.run_once())
            after = ts.store.get(oid)
            self.assertEqual(after["observation_id"], oid)
            self.assertEqual(view(before), view(after))
            ocr = after["evidence"]["ocr"]
            self.assertEqual((ocr["status"], ocr["revision"], ocr["text"]), ("succeeded", 2, "recognised text"))
            self.assertIsNotNone(ocr["finished_at_ns"])
            self.assertEqual([x["status"] for x in after["metadata"]["later_extractions"]["ocr"]], ["pending"])
            self.assertEqual(after["metadata"]["extraction_status"]["ocr"]["status"], "succeeded")
            self.assertEqual(ts.store.screenshot_bytes(oid), PNG)
            self.assertEqual(env.job(oid)[0], "done")
            self.assertFalse(env.proc.run_once())

    def test_retrying_the_same_capture_returns_the_same_observation_after_enrichment(self):
        with TempStore() as ts:
            env = Env(ts)
            first = ts.store.ingest(pdraft())
            env.proc.run_once()
            again = ts.store.ingest(pdraft())
            self.assertEqual((again.observation_id, again.created), (first.observation_id, False))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0], 1)

    def test_a_manual_rerun_appends_after_the_async_result(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            ts.store.add_extraction(oid, "ocr", ReaderResult("vision-ocr/2", "succeeded", {"lines": []}, "again"))
            o = ts.store.get(oid)
            self.assertEqual((o["evidence"]["ocr"]["revision"], o["evidence"]["ocr"]["text"]), (3, "again"))
            self.assertEqual([x["revision"] for x in o["metadata"]["later_extractions"]["ocr"]], [1, 2])

    def test_only_a_stored_screenshot_gets_a_job(self):
        with TempStore() as ts:
            for i, shot in enumerate((ScreenshotWithheld("excluded_app", T0), ScreenshotCapture(b"not a png", "png", 4, 4, T0))):
                o = ts.store.get(ts.store.ingest(pdraft(key=f"s:{i}", shot=shot)).observation_id)
                self.assertEqual((o["evidence"]["ocr"]["status"], o["evidence"]["ocr"]["error_code"]), ("skipped", "no_screenshot"))
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0], 0)

    def test_pending_cannot_carry_output_and_evidence_stays_immutable(self):
        with TempStore() as ts:
            with self.assertRaises(InvalidCapture):
                ReaderResult("v", "pending", {"x": 1}).validate("ocr")
            Env(ts).proc
            oid = ts.store.ingest(pdraft()).observation_id
            for sql in ("UPDATE extractions SET text='x'", "DELETE FROM extractions", "UPDATE screenshots SET width=9"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    ts.store.db.execute(sql)
            ts.store.db.execute("UPDATE ocr_jobs SET attempts=attempts")
            self.assertEqual(ts.store.get(oid)["evidence"]["ocr"]["status"], "pending")


class DurabilityTest(unittest.TestCase):
    def test_restart_invalidates_a_live_claim_before_the_old_worker_can_commit(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            old = ocr_queue.claim(ts.store, env.clock(), 10, POLICY)
            self.assertEqual(ocr_queue.recover_after_restart(ts.store, env.clock(), POLICY)["reclaimed"], 1)
            late = ReaderResult("vision-ocr/1", "succeeded", {"lines": []}, "late result")
            self.assertEqual(ocr_queue.finish(ts.store, old, late, T0, POLICY), "stale")
            self.assertEqual(env.ocr(oid)["status"], "pending")
            self.assertEqual(env.job(oid)[0], "queued")

    def test_backlog_survives_a_restart_and_a_dead_processing_job_is_recovered(self):
        with TempStore() as ts:
            env = Env(ts)
            ids = [ts.store.ingest(pdraft(key=f"s:{i}", t=T0 + i)).observation_id for i in range(3)]
            dead = ocr_queue.claim(ts.store, env.clock(), 10, POLICY)          # a process that died holding this job
            self.assertEqual(dead.observation_id, ids[0])
            store = ts.reopen()
            env2 = Env(ts)
            env2.clock.ns = env.clock.ns
            self.assertEqual(store.ocr_queue_stats()["queued"], 2)
            self.assertEqual(store.ocr_queue_stats()["processing"], 1)
            env2.proc.recover()
            self.assertEqual(store.ocr_queue_stats()["processing"], 1)           # lease not over yet
            env2.clock.advance(10 + 5 + 1)
            self.assertEqual(env2.proc.recover()["reclaimed"], 1)
            while env2.proc.run_once():
                pass
            self.assertEqual([store.get(i)["evidence"]["ocr"]["status"] for i in ids], ["succeeded"] * 3)
            self.assertEqual(env2.reader.calls, 3)

    def test_crash_restart_rejects_the_old_workers_late_result_and_keeps_the_new_one(self):
        with TempStore() as ts:
            gate_a, gate_b = threading.Event(), threading.Event()
            started_a, started_b = threading.Event(), threading.Event()

            def slow(started, gate, text):
                def step():
                    started.set()
                    gate.wait(10)
                    return {"lines": [{"text": text, "confidence": 0.9, "box": [0, 0, 1, 1]}]}, text
                return step
            clock = FakeClock()
            store_a = ts.store
            store_a.clock_ns = clock
            oid = store_a.ingest(pdraft()).observation_id
            original = view(store_a.get(oid))
            proc_a = OcrProcessor(store_a, FakeReader(slow(started_a, gate_a, "result of A")), 30, POLICY, clock)
            worker_a = threading.Thread(target=proc_a.run_once, daemon=True)
            worker_a.start()
            self.assertTrue(started_a.wait(5))                                   # (a) A holds the claim
            store_b = ObservationStore(ts.root, legacy_archive=ts.legacy)        # (b) restart: A is abandoned, never released
            self.addCleanup(store_b.close)
            store_b.clock_ns = clock
            proc_b = OcrProcessor(store_b, FakeReader(slow(started_b, gate_b, "result of B")), 30, POLICY, clock)
            self.assertEqual(ocr_queue.recover_after_restart(store_b, clock(), POLICY), {"reclaimed": 1, "lease_failed": 0})   # (c)
            self.assertEqual(store_b.db.execute("SELECT state, claim_token FROM ocr_jobs").fetchone(), ("queued", None))
            worker_b = threading.Thread(target=proc_b.run_once, daemon=True)
            worker_b.start()
            self.assertTrue(started_b.wait(5))                                   # (d) B claims the same job
            self.assertEqual(store_b.db.execute("SELECT state, attempts FROM ocr_jobs").fetchone(), ("processing", 2))
            gate_a.set()                                                         # (e) A answers late
            worker_a.join(5)
            self.assertEqual(proc_a.stats().get("stale"), 1)                     # (f) rejected and counted, nothing appended
            self.assertNotIn("completed", proc_a.stats())
            self.assertEqual(store_b.db.execute("SELECT count(*) FROM extractions WHERE reader='ocr' AND status='succeeded'").fetchone()[0], 0)
            gate_b.set()                                                         # (g) B's result is the one that lands
            worker_b.join(5)
            self.assertEqual(proc_b.stats().get("completed"), 1)
            rows = store_b.db.execute("SELECT observation_id, revision, text FROM extractions WHERE reader='ocr' "
                                      "AND status='succeeded'").fetchall()
            self.assertEqual(rows, [(oid, 2, "result of B")])
            self.assertEqual(store_b.db.execute("SELECT state, attempts, claim_token FROM ocr_jobs").fetchone(), ("done", 2, None))
            self.assertEqual(store_b.db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
            self.assertEqual(view(store_b.get(oid)), original)
            self.assertEqual(store_b.get(oid)["observation_id"], oid)

    def _crash_cycle(self, ts, clock, n_jobs):
        store = ts.store
        store.clock_ns = clock
        while ocr_queue.claim(store, clock(), 10, POLICY) is not None:
            pass
        store = ts.reopen()
        store.clock_ns = clock
        return ocr_queue.recover_after_restart(store, clock(), POLICY)

    def test_repeated_restarts_keep_every_job_recoverable_until_it_finishes(self):
        with TempStore() as ts:
            clock = FakeClock()
            ts.store.clock_ns = clock
            ts.store.ocr_max_attempts = POLICY.max_attempts
            ids = [ts.store.ingest(pdraft(key=f"s:{i}", t=T0 + i)).observation_id for i in range(3)]
            for crash in (1, 2):                      # POLICY.max_attempts == 3: each crash consumes one attempt
                self.assertEqual(self._crash_cycle(ts, clock, 3), {"reclaimed": 3, "lease_failed": 0})
                rows = ts.store.db.execute("SELECT state, attempts, claim_token, last_error_code FROM ocr_jobs").fetchall()
                self.assertEqual(rows, [("queued", crash, None, "WorkerRestarted")] * 3)
                self.assertEqual(ts.store.ocr_queue_stats()["processing"], 0)
            env = Env(ts)
            env.clock.ns = clock.ns
            while env.proc.run_once():
                pass
            self.assertEqual(ts.store.db.execute("SELECT state, attempts FROM ocr_jobs").fetchall(), [("done", 3)] * 3)
            for oid in ids:
                self.assertEqual(ts.store.db.execute(
                    "SELECT count(*) FROM extractions WHERE observation_id=? AND reader='ocr' AND status='succeeded'",
                    (oid,)).fetchone()[0], 1)
                self.assertEqual(env.ocr(oid)["status"], "succeeded")
            self.assertEqual(env.reader.calls, 3)

    def test_a_job_that_crashes_every_attempt_ends_terminal_at_the_last_restart(self):
        with TempStore() as ts:
            clock = FakeClock()
            ts.store.clock_ns = clock
            ts.store.ocr_max_attempts = POLICY.max_attempts
            ids = [ts.store.ingest(pdraft(key=f"s:{i}", t=T0 + i)).observation_id for i in range(3)]
            for _ in range(2):
                self.assertEqual(self._crash_cycle(ts, clock, 3)["reclaimed"], 3)
            self.assertEqual(self._crash_cycle(ts, clock, 3), {"reclaimed": 0, "lease_failed": 3})    # attempts == max
            self.assertEqual(ts.store.db.execute("SELECT state, attempts, claim_token, last_error_code FROM ocr_jobs").fetchall(),
                             [("failed", 3, None, "WorkerRestarted")] * 3)
            for oid in ids:
                ocr = ts.store.get(oid)["evidence"]["ocr"]
                self.assertEqual((ocr["status"], ocr["error_code"], ocr["revision"]), ("failed", "WorkerRestarted", 2))
            self.assertEqual(ts.store.ocr_queue_stats()["processing"] + ts.store.ocr_queue_stats()["queued"], 0)
            self.assertEqual(self._crash_cycle(ts, clock, 3), {"reclaimed": 0, "lease_failed": 0})    # nothing left to recover

    def test_recovery_leaves_a_live_unexpired_claim_to_the_lease_sweep(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            live = ocr_queue.claim(ts.store, env.clock(), 10, POLICY)
            env.clock.advance(5)
            self.assertEqual(ocr_queue.sweep(ts.store, env.clock(), POLICY)["reclaimed"], 0)
            self.assertEqual(env.job(oid)[0], "processing")
            ok = ReaderResult("vision-ocr/1", "succeeded", {"lines": []}, "fine")
            self.assertEqual(ocr_queue.finish(ts.store, live, ok, env.clock(), POLICY), "completed")
            old = ocr_queue.claim(ts.store, env.clock(), 10, POLICY)
            self.assertIsNone(old)

    def test_threaded_processor_drains_the_queue(self):
        with TempStore() as ts:
            env = Env(ts)
            env.proc = OcrProcessor(ts.store, env.reader, 10, POLICY, env.clock, idle_s=0.01)
            ids = [ts.store.ingest(pdraft(key=f"s:{i}", t=T0 + i)).observation_id for i in range(4)]
            env.proc.start()
            deadline = time.time() + 10
            while ts.store.ocr_queue_stats()["done"] < 4 and time.time() < deadline:
                time.sleep(0.02)
            env.proc.stop()
            self.assertEqual([ts.store.get(i)["evidence"]["ocr"]["status"] for i in ids], ["succeeded"] * 4)


class RetryTest(unittest.TestCase):
    def test_transient_failures_back_off_then_succeed(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(Timeout("slow"), WorkerCrashed("died")))
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            self.assertEqual(env.job(oid)[:2], ("queued", 1))
            self.assertFalse(env.proc.run_once())                                # backing off
            env.clock.advance(10)
            env.proc.run_once()
            self.assertEqual(env.job(oid)[:2], ("queued", 2))
            self.assertFalse(env.proc.run_once())
            env.clock.advance(19)
            self.assertFalse(env.proc.run_once())
            env.clock.advance(1)                                                 # 10 * 2^1 = 20 s
            env.proc.run_once()
            self.assertEqual((env.job(oid)[:2], env.ocr(oid)["status"]), (("done", 3), "succeeded"))

    def test_exhausted_attempts_end_in_a_failed_revision_and_release_the_screenshot(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(*[Timeout("slow")] * 5))
            oid = ts.store.ingest(pdraft()).observation_id
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            for _ in range(3):
                env.proc.run_once()
                env.clock.advance(500)
            ocr = env.ocr(oid)
            self.assertEqual((ocr["status"], ocr["error_code"], ocr["revision"]), ("failed", "Timeout", 2))
            self.assertEqual(env.job(oid)[0], "failed")
            self.assertEqual(env.reader.calls, 3)
            self.assertEqual(retention.run(ts.store, T0 + 9 * DAY)["deleted"], 1)

    def test_deterministic_failure_is_terminal_at_once(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(ValueError("cannot decode image")))
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            self.assertEqual((env.ocr(oid)["error_code"], env.job(oid)[:2]), ("ValueError", ("failed", 1)))
            self.assertFalse(env.proc.run_once())

    def test_missing_screenshot_file_is_terminal(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            rel = ts.store.db.execute("SELECT relative_path FROM screenshots").fetchone()[0]
            ts.store.assets.delete(rel)
            env.proc.run_once()
            self.assertEqual((env.ocr(oid)["error_code"], env.reader.calls), ("ScreenshotMissing", 0))

    def test_starting_worker_defers_without_burning_attempts_and_is_bounded(self):
        with TempStore() as ts:
            env = Env(ts, FakeReader(*[WorkerStarting("warming up")] * 5))
            oid = ts.store.ingest(pdraft()).observation_id
            for _ in range(2):
                env.proc.run_once()
                env.clock.advance(2)
            self.assertEqual(env.job(oid)[:3], ("queued", 0, 2))
            env.proc.run_once()
            self.assertEqual((env.job(oid)[0], env.ocr(oid)["error_code"]), ("failed", "WorkerStarting"))

    def test_a_job_that_outlives_its_maximum_age_is_failed_even_if_never_claimed(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            env.clock.advance(601)
            self.assertEqual(env.proc.recover()["expired"], 1)
            self.assertEqual((env.ocr(oid)["error_code"], env.job(oid)[0]), ("JobExpired", "failed"))

    def test_a_hung_reader_is_abandoned_and_the_next_job_still_runs(self):
        block = threading.Event()
        old = ocr_processor.HARD_GRACE_S
        ocr_processor.HARD_GRACE_S = 0.05
        try:
            with TempStore() as ts:
                env = Env(ts, FakeReader(lambda: block.wait(30)), timeout_s=0.05)
                first = ts.store.ingest(pdraft(key="s:0")).observation_id
                second = ts.store.ingest(pdraft(key="s:1", t=T0 + 1)).observation_id
                env.proc.run_once()
                self.assertEqual(env.proc.stats()["abandoned"], 1)
                env.proc.run_once()
                self.assertEqual(env.ocr(second)["status"], "succeeded")
                self.assertEqual(env.job(first)[:2], ("queued", 1))
                block.set()
        finally:
            ocr_processor.HARD_GRACE_S = old
            block.set()


class DuplicateAndStaleTest(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.addCleanup(self.ts.__exit__, None, None, None)
        self.env = Env(self.ts)
        self.oid = self.ts.store.ingest(pdraft()).observation_id
        self.ok = ReaderResult("vision-ocr/1", "succeeded", {"lines": []}, "text", None, None, T0, T0)

    def test_one_job_per_observation_revision_and_one_claim(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.ts.store.db.execute("INSERT INTO ocr_jobs(observation_id,target_revision,state,max_attempts,"
                                     "not_before_ns,enqueued_at_ns) VALUES (?,2,'queued',3,0,0)", (self.oid,))
        job = ocr_queue.claim(self.ts.store, T0, 10, POLICY)
        self.assertIsNone(ocr_queue.claim(self.ts.store, T0, 10, POLICY))
        self.assertEqual(ocr_queue.finish(self.ts.store, job, self.ok, T0, POLICY), "completed")
        self.assertEqual(ocr_queue.finish(self.ts.store, job, self.ok, T0, POLICY), "stale")
        self.assertEqual([r["revision"] for r in self.ts.store.extractions(self.oid, "ocr")], [1, 2])

    def test_wrong_claim_token_is_discarded(self):
        job = ocr_queue.claim(self.ts.store, T0, 10, POLICY)
        forged = ocr_queue.Job(job.job_id, job.observation_id, 2, "not-the-token", 1, 0, 3)
        self.assertEqual(ocr_queue.finish(self.ts.store, forged, self.ok, T0, POLICY), "stale")
        self.assertEqual(self.env.ocr(self.oid)["status"], "pending")

    def test_a_result_after_lease_expiry_and_reclaim_is_discarded(self):
        old = ocr_queue.claim(self.ts.store, T0, 10, POLICY)
        later = T0 + 20 * SEC
        self.assertEqual(ocr_queue.sweep(self.ts.store, later, POLICY)["reclaimed"], 1)
        new = ocr_queue.claim(self.ts.store, later, 10, POLICY)
        self.assertEqual(ocr_queue.finish(self.ts.store, old, self.ok, later, POLICY), "stale")
        self.assertEqual(ocr_queue.finish(self.ts.store, new, self.ok, later, POLICY), "completed")
        self.assertEqual(self.env.ocr(self.oid)["revision"], 2)

    def test_a_result_for_a_forgotten_observation_is_discarded(self):
        job = ocr_queue.claim(self.ts.store, T0, 10, POLICY)
        forget.forget(self.ts.store, {"everything": True})
        self.assertEqual(self.ts.store.db.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0], 0)
        self.assertEqual(ocr_queue.finish(self.ts.store, job, self.ok, T0, POLICY), "gone")
        self.assertEqual(self.ts.store.db.execute("SELECT count(*) FROM extractions").fetchone()[0], 0)

    def test_stopping_releases_the_in_flight_job_and_its_late_result_is_discarded(self):
        job = ocr_queue.claim(self.ts.store, T0, 10, POLICY)
        self.env.proc._current = job
        self.env.proc.stop(0)
        self.assertEqual(self.env.job(self.oid)[:2], ("queued", 0))
        self.assertEqual(ocr_queue.finish(self.ts.store, job, self.ok, T0, POLICY), "stale")
        self.assertTrue(self.env.proc.run_once())
        self.assertEqual(self.env.ocr(self.oid)["status"], "succeeded")


class RetentionAndBacklogTest(unittest.TestCase):
    def test_screenshots_are_protected_until_their_ocr_is_terminal(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            retention.set_policy(ts.store, {"mode": "duration", "preset": "5d"})
            late = T0 + 30 * DAY
            dry = retention.run(ts.store, late, dry_run=True)
            self.assertEqual((dry["observation_ids"], dry["protected_observation_ids"]), ([], [oid]))
            ocr_queue.claim(ts.store, T0, 10, POLICY)                              # processing is protected too
            self.assertEqual(retention.run(ts.store, late)["protected"], 1)
            self.assertTrue(ts.store.screenshot_bytes(oid))
            env.clock.advance(30)
            ocr_queue.sweep(ts.store, env.clock(), POLICY)
            env.proc.run_once()
            run = retention.run(ts.store, late)
            self.assertEqual((run["observation_ids"], run["protected"]), ([oid], 0))

    def test_queue_cap_skips_ocr_for_new_observations_without_a_job(self):
        from trace_v2.observations import settings
        with TempStore() as ts:
            settings.set_ocr_queue_cap(ts.store, 2)
            ids = [ts.store.ingest(pdraft(key=f"s:{i}", t=T0 + i)).observation_id for i in range(4)]
            codes = [ts.store.get(i)["evidence"]["ocr"] for i in ids]
            self.assertEqual([c["status"] for c in codes], ["pending", "pending", "skipped", "skipped"])
            self.assertEqual(codes[2]["error_code"], "QueueFull")
            self.assertEqual(ts.store.ocr_queue_stats()["queued"], 2)


class CaptureIndependenceTest(unittest.TestCase):
    def test_capture_never_waits_for_a_hung_reader(self):
        block = threading.Event()

        class Hang:
            version = "vision-ocr/1"
            calls = 0

            def read(self, *_):
                Hang.calls += 1
                block.wait(60)
        old = ocr_processor.HARD_GRACE_S
        ocr_processor.HARD_GRACE_S = 0.05
        try:
            with TempStore() as ts:
                sources = Sources()
                sources.ocr = Hang()
                proc = OcrProcessor(ts.store, sources.ocr, 0.05, POLICY, time.time_ns, idle_s=0.01)
                proc.start()
                observer = ContinuousObserver(ts.store, sources, buffer(), interval_s=1, ocr_mode="async")
                started = time.perf_counter()
                for _ in range(40):
                    self.assertTrue(observer.tick())
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 5)
                self.assertEqual(observer.stats["observations"], 40)
                self.assertEqual(ts.store.db.execute("SELECT count(*) FROM observations").fetchone()[0], 40)
                proc.stop(0.2)
                self.assertGreaterEqual(Hang.calls, 1)
        finally:
            ocr_processor.HARD_GRACE_S = old
            block.set()

    def test_async_mode_never_calls_ocr_on_the_capture_thread(self):
        with TempStore() as ts:
            sources = Sources()
            observer = ContinuousObserver(ts.store, sources, buffer(), interval_s=1, ocr_mode="async")
            observer.tick()
            self.assertEqual(ts.store.ocr_queue_stats()["queued"], 1)
            sources.ocr.error = AssertionError("OCR ran on the capture thread")
            observer.tick()
            self.assertEqual(ts.store.ocr_queue_stats()["queued"], 2)


class LateRedactionTest(unittest.TestCase):
    def test_a_secret_found_by_ocr_is_redacted_and_the_screenshot_is_deleted_with_an_audit_row(self):
        with TempStore() as ts:
            text = f"aws_access_key_id = {FAKE_KEY}"
            env = Env(ts, FakeReader(text))
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            o = ts.store.get(oid)
            ocr = o["evidence"]["ocr"]
            self.assertEqual(ocr["status"], "succeeded")
            self.assertNotIn(FAKE_KEY, json.dumps(ocr))
            self.assertIn("[REDACTED:", ocr["text"])
            self.assertTrue(ocr["privacy"]["late_withheld"] and ocr["privacy"]["redactions"])
            shot = o["evidence"]["screenshot"]
            self.assertFalse(shot["available"])
            self.assertEqual(shot["deletion"]["reason"], "secret_on_screen_late")
            self.assertEqual(ts.store.db.execute("SELECT count(*) FROM retention_run_finishes").fetchone()[0], 1)
            self.assertEqual(ts.store.audit(), {"orphan_files": [], "missing_files": []})
            self.assertEqual(ts.store.assets.all_relative_paths(), set())
            self.assertEqual(o["evidence"]["accessibility"]["status"], "succeeded")
            dump = b"".join(p.read_bytes() for p in ts.root.glob("*.sqlite3*"))
            self.assertNotIn(FAKE_KEY.encode(), dump)

    def test_clean_text_keeps_the_screenshot(self):
        with TempStore() as ts:
            env = Env(ts)
            oid = ts.store.ingest(pdraft()).observation_id
            env.proc.run_once()
            self.assertTrue(ts.store.get(oid)["evidence"]["screenshot"]["available"])
            self.assertIsNone(env.ocr(oid)["privacy"])


class ForgetTest(unittest.TestCase):
    def test_forget_removes_observation_revisions_and_jobs(self):
        with TempStore() as ts:
            env = Env(ts)
            a = ts.store.ingest(pdraft(key="s:0")).observation_id
            ts.store.ingest(pdraft(key="s:1", t=T0 + 1))
            env.proc.run_once()
            forget.forget(ts.store, {"observation_ids": [a]})
            db_ = ts.store.db
            self.assertEqual(db_.execute("SELECT count(*) FROM ocr_jobs WHERE observation_id=?", (a,)).fetchone()[0], 0)
            self.assertEqual(db_.execute("SELECT count(*) FROM extractions WHERE observation_id=?", (a,)).fetchone()[0], 0)
            self.assertEqual(db_.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0], 1)


class RunnerTest(unittest.TestCase):
    def test_runner_in_async_mode_enriches_observations_and_reports_the_queue(self):
        from test_v2_runner import InFlight, make_runner, wait_for
        with TempStore() as ts:
            sources = Sources()
            runner = make_runner(ts.store, sources, 0.05, ocr_mode="async")
            with InFlight(runner) as flight:
                self.assertTrue(wait_for(lambda: ts.store.ocr_queue_stats()["done"] >= 3))
                stats = runner.stats()
            self.assertEqual(stats["ocr_queue"]["processor"]["completed"] >= 3, True)
            self.assertIn("oldest_pending_age_s", stats["ocr_queue"])
            ocr = [ts.store.get(o["observation_id"])["evidence"]["ocr"]["status"] for o in ts.store.list()]
            self.assertEqual(ocr.count("succeeded") >= 3, True)
            self.assertNotIn("pending", ocr[:3])
            del flight

    def test_runner_default_stays_synchronous(self):
        from test_v2_runner import InFlight, make_runner, wait_for
        with TempStore() as ts:
            runner = make_runner(ts.store, Sources(), 0.05)
            with InFlight(runner):
                self.assertTrue(wait_for(lambda: ts.store.list()))
            self.assertEqual(ts.store.ocr_queue_stats()["queued"], 0)
            self.assertEqual(ts.store.get(ts.store.list()[0]["observation_id"])["evidence"]["ocr"]["revision"], 1)


class Migration0002Test(unittest.TestCase):
    def test_populated_v1_database_migrates_and_keeps_every_guarantee(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, legacy = Path(tmp).resolve() / "data", Path(tmp).resolve() / "Legacy-V1"
            paths.check_data_dir(root, legacy)
            path = paths.database_path(root)
            conn = db.connect(path)
            known = db.migrations()
            db.migrate(conn, path, known[:1])
            oid = "0" * 36
            conn.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (oid, T0, "t", "k:0", "h", "b", "n", 1, "w", "d", "{}", None, None,
                          '{"context":{"status":"succeeded","error_code":null},"events":{}}', "{}", "v", "now"))
            conn.execute("INSERT INTO screenshots(observation_id,status,relative_path,format,width,height,byte_size,sha256,"
                         "captured_at_ns) VALUES (?,?,?,?,?,?,?,?,?)", (oid, "stored", "a/b.png", "png", 1, 1, 5, "x", T0))
            e1, e2, e3 = ("1" * 36, "2" * 36, "3" * 36)
            conn.execute("INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                         "error_code,text,recorded_at) VALUES (?,?,'ocr',1,NULL,'v','failed','X','',?)", (e1, oid, "now"))
            conn.execute("INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                         "text,recorded_at) VALUES (?,?,'ocr',2,?,'v','succeeded','later','now')", (e2, oid, e1))
            conn.execute("INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                         "error_code,recorded_at) VALUES (?,?,'accessibility',1,NULL,'v','skipped','c','now')", (e3, oid))
            conn.execute("INSERT INTO retention_runs VALUES (?,?,?,?,?,?)", ("4" * 36, "now", T0, "{}", None, 0))
            conn.execute("INSERT INTO screenshot_deletions VALUES (?,?,?,?,?)", (oid, "now", "retention", "4" * 36, 1))
            conn.close()
            store = ObservationStore(root, legacy)
            try:
                self.assertTrue(list(path.parent.glob("*.pre-v2.bak")))
                self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], db.LATEST_VERSION)
                o = store.get(oid)
                self.assertEqual((o["evidence"]["ocr"]["text"], o["evidence"]["ocr"]["revision"]), ("later", 2))
                self.assertEqual(o["metadata"]["later_extractions"]["ocr"][0]["extraction_id"], e1)
                self.assertEqual(o["evidence"]["screenshot"]["deletion"]["reason"], "retention")
                for sql in ("UPDATE extractions SET text='x'", "DELETE FROM extractions", "UPDATE screenshot_deletions SET reason='retention'",
                            "DELETE FROM screenshot_deletions", "INSERT INTO screenshot_deletions VALUES "
                            "('9','n','bogus','" + "4" * 36 + "',1)",
                            "INSERT INTO extractions(extraction_id,observation_id,reader,revision,supersedes,reader_version,status,"
                            f"recorded_at) VALUES ('{'5' * 36}','{oid}','ocr',9,'{e1}','v','failed','now')"):
                    with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                        store.db.execute(sql)
                new = store.ingest(pdraft())
                self.assertEqual(store.get(new.observation_id)["evidence"]["ocr"]["status"], "pending")
                self.assertEqual(forget.forget(store, {"everything": True})["matched"], 2)
                for table in ("observations", "extractions", "screenshots", "screenshot_deletions", "ocr_jobs"):
                    self.assertEqual(store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
            finally:
                store.close()

    def test_a_leftover_backup_still_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "o.sqlite3"
            conn = db.connect(path)
            db.migrate(conn, path, db.migrations()[:1])
            (Path(tmp) / "o.sqlite3.pre-v2.bak").write_bytes(b"x")
            with self.assertRaises(db.SchemaError):
                db.migrate(conn, path)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()

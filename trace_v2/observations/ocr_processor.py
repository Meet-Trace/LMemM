"""Background OCR for stored Observations: claims durable jobs (ocr_queue), reads each screenshot with the OCR reader
(`read(screenshot, context) -> (raw, text)`: the Vision worker client or in-process Vision) and appends the result to the
same Observation. It never runs on the capture thread, never holds the store lock while the reader runs, and a hung reader
costs one abandoned helper thread, not the queue."""

import logging
import threading
import time

from . import ocr_queue
from .models import ReaderResult
from .pipeline import run_reader

log = logging.getLogger("trace_v2.ocr_processor")

MAX_ABANDONED = 2
HARD_GRACE_S = 5.0


class OcrProcessor:
    def __init__(self, store, reader, timeout_s=10.0, policy=ocr_queue.QueuePolicy(), clock_ns=time.time_ns,
                 idle_s=0.5, sweep_every_s=5.0):
        self.store, self.reader, self.timeout_s, self.policy = store, reader, timeout_s, policy
        self.clock_ns, self.idle_s, self.sweep_every_s = clock_ns, idle_s, sweep_every_s
        self.counts = {}
        self._stop = threading.Event()
        self._thread = None
        self._current = None
        self._abandoned = []
        self._lock = threading.Lock()

    def _count(self, name, n=1):
        with self._lock:
            self.counts[name] = self.counts.get(name, 0) + n

    def stats(self):
        with self._lock:
            return {**self.counts, "abandoned_alive": len([t for t in self._abandoned if t.is_alive()])}

    def start(self):
        restarted = ocr_queue.recover_after_restart(self.store, self.clock_ns(), self.policy)
        for key in ("reclaimed", "lease_failed"):
            if restarted[key]:
                self._count(key, restarted[key])
        self.recover()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="trace-v2-ocr-processor", daemon=True)
            self._thread.start()

    def stop(self, grace_s=3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(grace_s)
        job = self._current
        if job is not None and ocr_queue.release(self.store, job, self.clock_ns()):
            self._count("released_at_stop")

    def recover(self):
        out = ocr_queue.sweep(self.store, self.clock_ns(), self.policy)
        for key in ("reclaimed", "lease_failed", "expired"):
            if out[key]:
                self._count(key, out[key])
        return out

    def _run(self):
        next_sweep = time.monotonic() + self.sweep_every_s
        while not self._stop.is_set():
            try:
                if time.monotonic() >= next_sweep:
                    self.recover()
                    next_sweep = time.monotonic() + self.sweep_every_s
                if not self.run_once():
                    self._stop.wait(self.idle_s)
            except Exception:                    # noqa: BLE001 - the processor must outlive any one failure
                log.exception("OCR processor iteration failed")
                self._stop.wait(self.idle_s)

    def run_once(self):
        """Claim and process one due job. -> True when a job was handled."""
        self._abandoned = [t for t in self._abandoned if t.is_alive()]
        if len(self._abandoned) >= MAX_ABANDONED:
            return False
        job = ocr_queue.claim(self.store, self.clock_ns(), self.timeout_s, self.policy)
        if job is None:
            return False
        self._current = job
        try:
            self._count("claimed")
            outcome = self._process(job)
            self._count(outcome)
        finally:
            self._current = None
        return True

    def _process(self, job):
        try:
            shot = ocr_queue.load_screenshot(self.store, job)
        except ocr_queue.JobError as error:
            return ocr_queue.fail_terminal(self.store, job, error.code, str(error), self.clock_ns())
        result = self._read(shot)
        if self._stop.is_set() and result.status != "succeeded":
            ocr_queue.release(self.store, job, self.clock_ns())
            return "released"
        return ocr_queue.finish(self.store, job, result, self.clock_ns(), self.policy)

    def _read(self, shot):
        box = {}

        def work():
            box["result"] = run_reader(self.reader, self.clock_ns, shot, None)
        thread = threading.Thread(target=work, name="trace-v2-ocr-read", daemon=True)
        thread.start()
        thread.join(self.timeout_s + HARD_GRACE_S)
        if thread.is_alive():
            self._abandoned.append(thread)
            self._count("abandoned")
            now = self.clock_ns()
            return ReaderResult(getattr(self.reader, "version", None) or "unknown", "failed", None, None, "Timeout",
                                "the OCR reader did not return", now, now)
        return box["result"]

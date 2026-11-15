"""OcrWorkerClient: Vision OCR in a separate, killable process, behind the same reader interface as VisionOCR
(`version`, `read(screenshot, context) -> (raw, text)`), so MacSources and the pipeline are unchanged. Pure Python: no
pyobjc is imported here (the worker process imports it), so it works and is tested on any platform.

Guarantees (each has a test):
  * `read` returns or raises within `timeout_s` plus a few milliseconds, whatever the worker is doing: starting, wedged,
    dead, writing garbage or not reading its pipe. No wait in this module is unbounded, including process reaping.
  * A job that times out kills the worker (SIGTERM, then SIGKILL after `grace_s`, reaped) and a fresh one starts at once on
    the supervisor thread, not on the capture thread. The failed read is recorded as `Timeout` like an in-process one.
  * Association. The pipeline builds a draft synchronously: `run_reader` calls `read` for the screenshot of THAT capture
    and the returned (raw, text) become that draft's extraction before `store.ingest` assigns the observation id. Nothing
    is attached later, by id or by queue, so no result can reach another observation. Inside this client every job has a
    client-wide increasing `job_id` and an idempotency key `sha256(png):captured_at_ns`; a result is accepted only when
    both match the one job in flight. Anything else (a late answer, a duplicate, a result of a replaced worker, which has
    its own inbox that is dropped with it) is discarded and counted. Reading the same key again returns the first result
    without running Vision again (the last two keys are remembered).
  * Bounded memory. At most ONE job exists at any time (queue depth 1, no waiting line): the observer is single-threaded
    and a screenshot waiting in a queue would be stale, so a second concurrent `read` is refused at once with `WorkerBusy`
    (recorded `failed`). Bytes in flight are therefore at most one screenshot, itself refused above `max_image_bytes`
    (`ImageTooLarge`). The worker is replaced when its RSS exceeds `max_rss_mb` or after `max_jobs` jobs.
  * Not ready / cooling off: reads raise `WorkerStarting`, `WorkerCircuitOpen` or `WorkerClosed` (ReaderUnavailable, recorded
    `unavailable` with that error code), immediately. Warm-up happens inside the worker, so the first capture is never
    delayed by it. Unexpected deaths (crash, garbage, not ready in time) back off, and `breaker_crashes` of them within
    `breaker_window_s` open the circuit for `breaker_cooloff_s`. Timeouts and planned restarts do not count: they are
    recovered from immediately.
  * `close` stops accepting jobs, lets the current one finish within a short grace, then terminates the worker. The
    worker also exits by itself when this process dies (see ocr_worker), so there are no orphans.
Error codes (the class name, as for in-process readers): Timeout, WorkerCrashed, WorkerProtocolError, WorkerBusy,
ImageTooLarge, WorkerStarting, WorkerCircuitOpen, WorkerClosed, plus the worker's own class names (ValueError, ...).
"""

import hashlib
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from trace_v2.observations.pipeline import ReaderUnavailable

from . import ocr_protocol as proto
from .ocr import READER_VERSION, Timeout

log = logging.getLogger("trace_v2.ocr_worker")

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RSS_MB = 800
DEFAULT_MAX_JOBS = 500
MAX_IMAGE_BYTES = 64 * 1024 * 1024
GRACE_S = 0.5
READY_TIMEOUT_S = 180.0              # warm-up has taken 66 s on a fresh machine
BREAKER_CRASHES, BREAKER_WINDOW_S, BREAKER_COOLOFF_S = 5, 60.0, 60.0
RSS_CHECK_S = 2.0
REMEMBERED_RESULTS = 2
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


class WorkerBusy(RuntimeError):
    """A job is already in flight; the queue has depth 1 and is full."""


class WorkerCrashed(RuntimeError):
    """The worker process died while it had the job."""


class WorkerProtocolError(RuntimeError):
    """The worker sent something that is not a valid frame."""


class ImageTooLarge(ValueError):
    """The screenshot is larger than the bytes allowed in flight."""


class WorkerStarting(ReaderUnavailable):
    """The worker is starting, warming up or being replaced."""


class WorkerCircuitOpen(ReaderUnavailable):
    """The worker crashed repeatedly; OCR is off until the cool-off ends."""


class WorkerClosed(ReaderUnavailable):
    """The client is shutting down."""


_REMOTE = {}


def _remote_error(name, message):
    """An exception whose class name is the worker's, so the recorded error_code equals the in-process one."""
    ok = isinstance(name, str) and name.isidentifier() and len(name) <= 64
    cls = _REMOTE.setdefault(name, type(name, (RuntimeError,), {})) if ok and len(_REMOTE) < 64 else RuntimeError
    return cls(message)


def ps_rss_mb(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout
        return round(int(out.split()[0]) / 1024, 1)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def worker_command():
    return [sys.executable, "-m", "trace_v2.macos.ocr_worker"]


class _Worker:
    def __init__(self, generation, proc):
        self.generation, self.proc, self.pid = generation, proc, proc.pid
        self.inbox = queue.Queue(maxsize=64)         # frames of the current and past jobs; bounded
        self.outbox = queue.Queue(maxsize=2)
        self.state = "starting"                      # starting | ready | dead
        self.started_at = time.monotonic()
        self.jobs, self.warmup_s, self.rss_mb = 0, None, None
        self.eof, self.end_reason = False, None      # set by the reader thread when the pipe ends or breaks
        self.expected_death = False


class OcrWorkerClient:
    version = READER_VERSION

    def __init__(self, timeout_s=DEFAULT_TIMEOUT_S, *, command=None, env=None, max_rss_mb=DEFAULT_MAX_RSS_MB,
                 max_jobs=DEFAULT_MAX_JOBS, max_image_bytes=MAX_IMAGE_BYTES, grace_s=GRACE_S,
                 ready_timeout_s=READY_TIMEOUT_S, breaker=(BREAKER_CRASHES, BREAKER_WINDOW_S, BREAKER_COOLOFF_S),
                 rss_probe=ps_rss_mb, rss_check_s=RSS_CHECK_S, clock=time.monotonic, start=True):
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s, self.max_rss_mb, self.max_jobs = timeout_s, max_rss_mb, max_jobs
        self.max_image_bytes, self.grace_s, self.ready_timeout_s = max_image_bytes, grace_s, ready_timeout_s
        self._breaker, self._rss_probe, self._rss_check_s, self._now = breaker, rss_probe, rss_check_s, clock
        self._command = list(command) if command else worker_command()
        self._env = env
        self._cv = threading.Condition()
        self._worker, self._doomed, self._generation = None, [], 0
        self._spawn_at, self._circuit_until, self._crashes = 0.0, 0.0, deque()
        self._busy, self._job_id, self._in_flight = False, 0, 0
        self._results = deque(maxlen=REMEMBERED_RESULTS)       # (key, raw, text)
        self._closed, self._rss_checked, self._spawn_failures = False, 0.0, 0
        self._stats = {"jobs": 0, "succeeded": 0, "reader_errors": 0, "timeouts": 0, "crashes": 0, "protocol_errors": 0,
                       "spawns": 0, "restarts": {}, "busy_refused": 0, "not_ready_skipped": 0, "circuit_open_skipped": 0,
                       "circuit_opens": 0, "late_results_discarded": 0, "idempotent_hits": 0, "too_large": 0,
                       "peak_in_flight_bytes": 0, "peak_worker_rss_mb": 0.0, "last_ready_s": None}
        self._thread = None
        if start:
            self.start()

    # ---- public ----

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._supervise, name="trace-v2-ocr-supervisor", daemon=True)
            self._thread.start()

    def read(self, screenshot, context=None):
        """-> (raw, text) exactly as VisionOCR.read would return them, or raises (see the module docstring)."""
        if screenshot.format != "png":
            raise ValueError(f"OCR reads PNG screenshots, not {screenshot.format!r}")
        data = screenshot.data if isinstance(screenshot.data, bytes) else bytes(screenshot.data)
        if len(data) > self.max_image_bytes:
            self._count("too_large")
            raise ImageTooLarge(f"screenshot of {len(data)} bytes exceeds the {self.max_image_bytes} bytes allowed in flight")
        key = f"{hashlib.sha256(data).hexdigest()}:{screenshot.captured_at_ns}"
        with self._cv:
            for known, raw, text in self._results:
                if known == key:
                    self._stats["idempotent_hits"] += 1
                    return raw, text
            worker, job_id = self._claim(len(data))
        try:
            return self._run(worker, job_id, key, data)
        finally:
            with self._cv:
                self._busy, self._in_flight = False, 0
                self._cv.notify_all()
            self._maybe_retire(worker, None)             # worn out: replace it now, between jobs, not at the next poll

    def wait_ready(self, timeout):
        """True once a worker is ready (for tests and tools; capture never waits for it)."""
        end = self._now() + timeout
        with self._cv:
            while not (self._worker and self._worker.state == "ready"):
                left = end - self._now()
                if left <= 0 or self._closed:
                    return False
                self._cv.wait(min(left, 0.1))
        return True

    def stats(self):
        with self._cv:
            w = self._worker
            now = self._now()
            return {**self._stats, "restarts": dict(self._stats["restarts"]), "queue_depth_max": 1,
                    "queue_depth": int(self._busy), "in_flight_bytes": self._in_flight,
                    "isolation": "process", "timeout_s": self.timeout_s, "max_rss_mb": self.max_rss_mb,
                    "max_jobs": self.max_jobs, "max_image_bytes": self.max_image_bytes,
                    "circuit_open": now < self._circuit_until, "closed": self._closed,
                    "worker": None if w is None else {"pid": w.pid, "state": w.state, "jobs": w.jobs,
                                                      "rss_mb": w.rss_mb, "age_s": round(now - w.started_at, 1),
                                                      "warmup_s": w.warmup_s}}

    def worker_pid(self):
        with self._cv:
            return self._worker.pid if self._worker else None

    def close(self, grace_s=2.0):
        """Stop accepting jobs, give the one in flight `grace_s` to finish, then terminate the worker. Idempotent."""
        with self._cv:
            if self._closed:
                return
            self._closed = True
            end = self._now() + grace_s
            while self._busy and self._now() < end:
                self._cv.wait(max(0.0, min(0.05, end - self._now())))
            workers = [w for w in [self._worker, *self._doomed] if w]
            self._worker, self._doomed = None, []
            for w in workers:
                w.expected_death, w.state = True, "dead"
            self._cv.notify_all()
        for w in workers:
            self._terminate(w, polite=True)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(5.0)

    # ---- one job ----

    def _count(self, name, by=1):
        with self._cv:
            self._stats[name] += by

    def _claim(self, size):
        """Under the lock: take the single job slot or refuse at once."""
        if self._closed:
            raise WorkerClosed("OCR worker is shut down")
        if self._now() < self._circuit_until:
            self._stats["circuit_open_skipped"] += 1
            raise WorkerCircuitOpen("OCR worker crashed repeatedly; cooling off")
        if self._busy:
            self._stats["busy_refused"] += 1
            raise WorkerBusy("an OCR job is already in flight (queue depth 1)")
        w = self._worker
        if w is None or w.state != "ready" or w.eof:
            self._stats["not_ready_skipped"] += 1
            raise WorkerStarting("OCR worker is starting" if w else "OCR worker is restarting")
        self._busy, self._job_id, self._in_flight = True, self._job_id + 1, size
        self._stats["jobs"] += 1
        self._stats["peak_in_flight_bytes"] = max(self._stats["peak_in_flight_bytes"], size)
        return w, self._job_id

    def _drain_stale(self, w):
        stale, ended = 0, None
        while True:
            try:
                kind, header = w.inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "frame":
                stale += 1
            else:
                ended = (kind, header)                               # the pipe already ended: keep that news
        if ended:
            w.inbox.put_nowait(ended)
        if stale:
            self._count("late_results_discarded", stale)

    def _run(self, w, job_id, key, data):
        self._drain_stale(w)
        deadline = self._now() + self.timeout_s
        header, body = proto.encode_parts({"type": "job", "job_id": job_id, "key": key, "format": "png"}, data)
        try:
            w.outbox.put_nowait((header, body))
        except queue.Full:
            self._doom(w, "protocol", unexpected=True)
            raise WorkerProtocolError("the worker is not taking jobs") from None
        while True:
            left = deadline - self._now()
            if left <= 0:
                self._count("timeouts")
                self._doom(w, "timeout", unexpected=False)
                raise Timeout(f"text recognition did not finish in {self.timeout_s}s")
            try:
                kind, header = w.inbox.get(timeout=left)
            except queue.Empty:
                continue
            if kind != "frame":
                reason = "crash" if kind == "eof" else "protocol"
                self._doom(w, reason, unexpected=True)
                if kind == "eof":
                    raise WorkerCrashed(f"the OCR worker died during the job ({self._exit_status(w)})")
                raise WorkerProtocolError("the OCR worker sent an invalid frame")
            if header["type"] not in ("result", "error") or header.get("job_id") != job_id or header.get("key") != key:
                self._count("late_results_discarded")        # a late, duplicate or foreign answer: never attached
                continue
            return self._finish(w, header, key)

    def _finish(self, w, header, key):
        if header["type"] == "error":
            with self._cv:
                w.jobs += 1
                self._stats["reader_errors"] += 1
            message = header.get("message")
            if header.get("unavailable"):
                raise ReaderUnavailable(message)
            raise _remote_error(header.get("error_type"), message)
        raw, text = header.get("raw"), header.get("text")
        if not isinstance(raw, dict) or not (text is None or isinstance(text, str)):
            self._doom(w, "protocol", unexpected=True)
            raise WorkerProtocolError("the OCR worker returned a malformed result")
        with self._cv:
            w.jobs += 1
            self._stats["succeeded"] += 1
            self._results.append((key, raw, text))
        return raw, text

    @staticmethod
    def _exit_status(w):
        try:
            code = w.proc.wait(0.2)
        except subprocess.TimeoutExpired:
            return "exit status unknown"
        return f"signal {-code}" if code < 0 else f"exit {code}"

    # ---- supervision: spawn, reap, restart policy ----

    def _doom(self, w, reason, unexpected):
        """Retire a worker: nothing can use it from now on; the supervisor kills and reaps it and starts the next one."""
        with self._cv:
            if w.state == "dead":
                return
            was_current = w is self._worker
            w.state, w.expected_death = "dead", (w.expected_death or not unexpected)
            if was_current:
                self._worker = None
            if w not in self._doomed:
                self._doomed.append(w)
            self._stats["restarts"][reason] = self._stats["restarts"].get(reason, 0) + 1
            now = self._now()
            if unexpected:
                self._stats["crashes" if reason in ("crash", "ready_timeout") else "protocol_errors"] += 1
                self._crashes.append(now)
                while self._crashes and now - self._crashes[0] > self._breaker[1]:
                    self._crashes.popleft()
                if len(self._crashes) >= self._breaker[0]:
                    self._circuit_until, self._spawn_at = now + self._breaker[2], now + self._breaker[2]
                    self._stats["circuit_opens"] += 1
                    self._crashes.clear()
                    log.error("OCR worker failed %d times in %.0f s; OCR is off for %.0f s", self._breaker[0],
                              self._breaker[1], self._breaker[2])
                else:
                    self._spawn_at = now + min(5.0, 0.25 * 2 ** (len(self._crashes) - 1))
            else:
                self._spawn_at = now                                 # planned or timeout: replace immediately
            self._cv.notify_all()

    def _supervise(self):
        while True:
            with self._cv:
                if self._closed:
                    return
                doomed, self._doomed = self._doomed, []
                spawn = self._worker is None and self._now() >= self._spawn_at and self._now() >= self._circuit_until
                current = self._worker
            for w in doomed:
                self._terminate(w)
            if spawn:
                self._spawn()
            elif current is not None:
                self._check(current)
            with self._cv:
                if not self._closed and not self._doomed:
                    self._cv.wait(0.1)

    def _check(self, w):
        now = self._now()
        if w.eof and w.state != "dead" and not self._busy:           # died or broke while idle
            self._doom(w, "crash" if w.end_reason == "eof" else "protocol", unexpected=True)
        elif w.state == "starting" and now - w.started_at > self.ready_timeout_s:
            self._doom(w, "ready_timeout", unexpected=True)
        elif w.state == "ready" and now - self._rss_checked >= self._rss_check_s:
            self._rss_checked = now
            rss = self._rss_probe(w.pid)
            with self._cv:
                w.rss_mb = rss
                if rss:
                    self._stats["peak_worker_rss_mb"] = max(self._stats["peak_worker_rss_mb"], rss)
            self._maybe_retire(w, rss)
        elif w.state == "ready":
            self._maybe_retire(w, None)

    def _maybe_retire(self, w, rss):
        over = rss is not None and self.max_rss_mb and rss > self.max_rss_mb
        worn = self.max_jobs and w.jobs >= self.max_jobs
        if not (over or worn):
            return
        with self._cv:
            if self._busy or w is not self._worker:                  # retire between jobs, never under one
                return
        self._doom(w, "rss" if over else "max_jobs", unexpected=False)

    def _spawn(self):
        env = dict(self._env if self._env is not None else os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (_PROJECT_ROOT, env.get("PYTHONPATH")) if p)
        try:
            proc = subprocess.Popen([*self._command, "--parent-pid", str(os.getpid())], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=_PROJECT_ROOT,
                                    close_fds=True)
        except OSError as e:
            with self._cv:
                self._spawn_failures += 1
                self._spawn_at = self._now() + min(60.0, 2.0 * 2 ** (self._spawn_failures - 1))
            log.error("could not start the OCR worker: %s", type(e).__name__)
            return
        self._spawn_failures = 0
        with self._cv:
            self._generation += 1
            w = _Worker(self._generation, proc)
            if self._closed:
                w.expected_death = True
            else:
                self._worker = w
                self._stats["spawns"] += 1
        threading.Thread(target=self._read_loop, args=(w,), name=f"ocr-reader-{w.generation}", daemon=True).start()
        threading.Thread(target=self._write_loop, args=(w,), name=f"ocr-writer-{w.generation}", daemon=True).start()
        if self._closed:
            self._terminate(w)

    def _read_loop(self, w):
        fd = w.proc.stdout.fileno()
        reason = "eof"
        try:
            while True:
                header, _ = proto.read_frame(fd)
                if header["type"] == "ready":
                    with self._cv:
                        if w.state == "starting":
                            w.state, w.warmup_s = "ready", header.get("warmup_s")
                            self._stats["last_ready_s"] = round(self._now() - w.started_at, 2)
                            self._cv.notify_all()
                elif header["type"] != "hello":
                    try:
                        w.inbox.put_nowait(("frame", header))
                    except queue.Full:
                        reason = "protocol"
                        return
        except proto.EndOfStream as e:
            reason = "protocol" if e.partial else "eof"
        except (proto.ProtocolError, OSError, ValueError):
            reason = "protocol"
        finally:
            w.end_reason, w.eof = reason, True
            try:
                w.inbox.put_nowait(("eof" if reason == "eof" else "protocol", None))
            except queue.Full:
                pass
            try:
                w.proc.stdout.close()
            except OSError:
                pass

    def _write_loop(self, w):
        fd = w.proc.stdin.fileno()
        try:
            while True:
                item = w.outbox.get()
                if item is None:
                    return
                for part in item:
                    proto.write_all(fd, part)
        except (OSError, ValueError):
            pass                                                     # the pipe broke: the reader sees the death
        finally:
            try:
                w.proc.stdin.close()
            except OSError:
                pass

    def _terminate(self, w, polite=False):
        """SIGTERM, SIGKILL after the grace, reap. Bounded; never raises."""
        try:
            if polite:
                try:
                    w.outbox.put_nowait((proto.encode({"type": "bye"}), b""))
                except queue.Full:
                    pass
            else:
                try:
                    w.outbox.put_nowait(None)
                except queue.Full:
                    pass
            if w.proc.poll() is None:
                if polite:
                    try:
                        w.proc.wait(0.2)                             # the bye frame lets an idle worker leave by itself
                    except subprocess.TimeoutExpired:
                        pass
                if w.proc.poll() is None:
                    w.proc.terminate()
                    try:
                        w.proc.wait(self.grace_s)
                    except subprocess.TimeoutExpired:
                        w.proc.kill()
                        w.proc.wait(3.0)
        except (OSError, subprocess.TimeoutExpired):
            log.error("OCR worker pid %s could not be reaped", w.pid)
        try:
            w.outbox.put_nowait(None)
        except queue.Full:
            pass


def make_reader(isolation="process", timeout_s=DEFAULT_TIMEOUT_S, max_rss_mb=DEFAULT_MAX_RSS_MB, max_jobs=DEFAULT_MAX_JOBS):
    """The OCR reader for MacSources: a worker-process client (default) or the in-process VisionOCR."""
    if isolation == "process":
        return OcrWorkerClient(timeout_s, max_rss_mb=max_rss_mb, max_jobs=max_jobs)
    if isolation == "inprocess":
        from .ocr import VisionOCR
        return VisionOCR(timeout_s=timeout_s)
    raise ValueError(f"unknown OCR isolation {isolation!r}")

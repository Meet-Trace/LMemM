"""The observer runner: opens the V2 store, runs ContinuousObserver with the input tap, and manages its lifecycle.

Platform independent: everything macOS-specific (sources, tap, permission preflights, Vision warm-up, sleep/lock
notifications, the run-loop pump) is injected by trace_v2.run, so the lifecycle logic is testable anywhere.

Startup order: permissions report -> optional warm-up (Vision's first call can take a minute) -> event buffer opens at
the session start -> input tap (if it is unavailable that is logged once and the run goes on without events) ->
retention once -> observer thread. The calling thread then pumps notifications every 0.25 s and handles the timers
(stats every `stats_every_s`, retention hourly) until `request_stop()` or the duration ends. Stopping lets the observer take
its final observation (unless paused, e.g. asleep), stops the tap and writes the last stats.

Lifecycle (`Lifecycle.handle(name, value=None)`):
    sleep / session_resign   pause captures (no failed observations, no final capture) and make the event buffer
                             refuse events until resumed; the time is not counted as missed ticks
    wake / session_active    resume; wake also re-syncs the input clock (uptime stood still during sleep). The first
                             capture after a pause starts a fresh schedule: missed ticks are never replayed.
    screen_locked/unlocked   keep ticking (the privacy gate withholds locked time, so the record shows it) and tell
                             the input gate, so no input event is kept while the screen is locked
    app_activated            the front application changed: the input gate blocks at once for excluded apps
Pausing is by reason, so a wake during a switched-out session does not resume early.

Input is gated twice (see trace_v2.inputs.InputGate): per event from app activations and lock notifications, and per
capture from the full privacy gate's decision (private windows, sites, secure input too).

Stats are not evidence: `runner_stats.json` in the data dir is rewritten atomically (never the evidence tables).
"""

import errno
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from .inputs import InputGate
from .macos.ocr_client import native_rss_mb
from .observations import privacy, retention, settings
from .observations.events import EventBuffer
from .observations.ocr_processor import OcrProcessor
from .moments.processor import MomentProcessor
from .structure.processor import StructureProcessor
from .observations.pipeline import ContinuousObserver

log = logging.getLogger("trace_v2.runner")

LOCK_FILE = "runner.lock"
STATS_FILE = "runner_stats.json"
PERMISSIONS_KEY = "permissions_report"
DEFAULT_STATS_EVERY_S = 300.0
RETENTION_EVERY_S = 3600.0
PUMP_S = 0.25


class AlreadyRunning(RuntimeError):
    def __init__(self, pid):
        self.pid = pid
        super().__init__(f"another Trace V2 runner is already using this data directory (pid {pid or 'unknown'})")


class InstanceLock:
    """Exclusive non-blocking flock on <data dir>/runner.lock; released automatically if the process dies."""

    def __init__(self, directory):
        self.path = Path(directory) / LOCK_FILE
        self._fd = None

    def acquire(self):
        """Take the lock or raise AlreadyRunning. The kernel drops an flock when its holder dies, so a crashed runner's
        leftover file never blocks the next start. release() unlinks the file while still holding the lock; a process
        that opened the old file just before that would hold a lock on a deleted inode, so after locking the path is
        re-checked to still be the locked file, and the open is retried if not."""
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(10):
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                holder = self._read_pid(fd)
                os.close(fd)
                if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise AlreadyRunning(holder) from e
                raise
            try:
                same = os.stat(self.path).st_ino == os.fstat(fd).st_ino
            except FileNotFoundError:
                same = False
            if same:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode())
                self._fd = fd
                return self
            os.close(fd)                                    # the file was replaced under us: lock the new one
        raise RuntimeError("could not take the runner lock: the lock file keeps being replaced")

    @staticmethod
    def _read_pid(fd):
        try:
            return int(os.pread(fd, 32, 0).split()[0])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def held(directory):
        """-> (is_held, pid) without taking the lock."""
        import fcntl
        path = Path(directory) / LOCK_FILE
        if not path.exists():
            return False, None
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False, None
        except OSError:
            return True, InstanceLock._read_pid(fd)
        finally:
            os.close(fd)

    def release(self):
        if self._fd is not None:
            try:
                os.unlink(self.path)                        # still locked: nobody can have locked this inode meanwhile
            except FileNotFoundError:
                pass
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def resolve_interval(store, override=None):
    """--interval for this run only, else the stored capture_interval_s setting (default 3 s)."""
    if override is not None:
        return settings._valid_interval(override)
    return settings.get_capture_interval(store)


class Lifecycle:
    PAUSING = {"sleep": "sleep", "session_resign": "session"}
    RESUMING = {"wake": "sleep", "session_active": "session"}

    def __init__(self, observer, tap_resync=None, buffer=None, gate=None):
        self.observer, self._tap_resync, self.buffer, self.gate = observer, tap_resync, buffer, gate
        self.reasons, self.screen_locked, self.front_app = set(), False, None
        self.counts = {}
        self._lock = threading.Lock()

    def handle(self, name, value=None):
        with self._lock:
            known = self._apply(name, value)
            if not known:
                log.warning("unknown lifecycle event %r", name)
                return
            paused = bool(self.reasons)
            if paused:
                if self.buffer is not None:
                    self.buffer.begin_pause()
                self.observer.pause()
            else:
                if self.buffer is not None:
                    self.buffer.end_pause()                  # after the clock re-sync (done in _apply) and before captures
                self.observer.resume()
        if name != "app_activated":
            log.info("lifecycle: %s (paused: %s, locked: %s)", name, sorted(self.reasons) or "no", self.screen_locked)

    def _apply(self, name, value):
        if name in self.PAUSING:
            self.reasons.add(self.PAUSING[name])
        elif name in self.RESUMING:
            self.reasons.discard(self.RESUMING[name])
            if name == "wake" and self._tap_resync:
                self._tap_resync()
        elif name in ("screen_locked", "screen_unlocked"):
            self.screen_locked = name == "screen_locked"
            if self.gate is not None:
                self.gate.screen_locked(self.screen_locked)
        elif name == "app_activated":
            self.front_app = value
            if self.gate is not None:
                self.gate.app_activated(value)
        else:
            return False
        self.counts[name] = self.counts.get(name, 0) + 1
        return True

    def snapshot(self):
        with self._lock:
            return {"paused_for": sorted(self.reasons), "screen_locked": self.screen_locked, "events": dict(self.counts)}


def _current_rss_mb(pid=None):
    native = native_rss_mb(pid or os.getpid())
    if native is not None:
        return native
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid or os.getpid())], capture_output=True, text=True,
                             timeout=2).stdout
        return round(int(out.split()[0]) / 1024, 1)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _peak_rss_mb():
    import resource
    import sys
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3        # bytes on macOS, KiB elsewhere


SETTINGS_REFRESH_S = 5.0


class Runner:
    def __init__(self, store, sources, interval_s, *, tap_factory=None, cursor_sample_ms=None, warmup=None,
                 permissions=None, pump=None, stats_every_s=DEFAULT_STATS_EVERY_S, stats_path=None,
                 monotonic=time.monotonic, clock_ns=time.time_ns, rss=_current_rss_mb, observer_factory=ContinuousObserver,
                 settings_refresh_s=SETTINGS_REFRESH_S, front_app=None, ocr_mode="sync", ocr_queue_policy=None, structure=False, moments=False):
        self.store, self.sources, self.interval_s = store, sources, interval_s
        self._tap_factory, self._cursor_ms = tap_factory, cursor_sample_ms
        self._warmup, self._permissions = warmup, permissions
        self.pump = pump or (lambda seconds: self._stop.wait(seconds))
        self.stats_every_s, self.settings_refresh_s = stats_every_s, settings_refresh_s
        self.stats_path = Path(stats_path) if stats_path else Path(store.root) / STATS_FILE
        self._monotonic, self._clock_ns, self._rss = monotonic, clock_ns, rss
        self._observer_factory = observer_factory
        self.ocr_mode, self._ocr_queue_policy, self.ocr_processor = ocr_mode, ocr_queue_policy, None
        self.structure, self.structure_processor = structure, None
        self.moments, self.moment_processor = moments, None
        self._front_app = front_app                  # () -> bundle id of the front app; reconciles missed notifications
        self._stop = threading.Event()
        self.tap = self.observer = self.lifecycle = self.buffer = self.gate = None
        self.warmup_result = self.permissions_report = self.observer_error = None
        self.policy = None
        self.session_start_ns = self._t0 = None
        self.retention_runs = 0

    # ---- control ----

    def request_stop(self):
        self._stop.set()

    def notify(self, name, value=None):
        """Lifecycle notification (sleep, wake, app_activated, ...), from any thread."""
        if self.lifecycle is not None:
            self.lifecycle.handle(name, value)

    # ---- startup steps ----

    def _report_permissions(self):
        if not self._permissions:
            return
        self.permissions_report = {**self._permissions(), "at_ns": self._clock_ns()}
        settings.put(self.store, PERMISSIONS_KEY, self.permissions_report)
        log.info("permissions: %s", {k: v for k, v in self.permissions_report.items() if k != "at_ns"})

    def _run_warmup(self):
        """Vision's first recognition can take over a minute. It runs on a thread so a stop request (SIGTERM) is not
        held up by it; the capture loop starts only after it finished or failed."""
        if not self._warmup:
            return
        started, box = self._monotonic(), {}

        def work():
            try:
                self._warmup()
            except BaseException as e:               # noqa: BLE001 - recorded below
                box["error"] = e
        thread = threading.Thread(target=work, name="trace-v2-warmup", daemon=True)
        thread.start()
        while thread.is_alive() and not self._stop.is_set():
            thread.join(0.2)
        seconds = round(self._monotonic() - started, 2)
        if thread.is_alive():
            self.warmup_result = {"ok": False, "seconds": seconds, "error": "interrupted"}
        elif "error" in box:
            self.warmup_result = {"ok": False, "seconds": seconds, "error": type(box["error"]).__name__}
            log.warning("OCR warm-up failed (%s); the first OCR may time out", type(box["error"]).__name__)
        else:
            self.warmup_result = {"ok": True, "seconds": seconds}

    def _start_tap(self):
        if not self._tap_factory:
            return
        self.tap = self._tap_factory(self.buffer, self._cursor_ms, self.gate)
        if self.tap.start() != "running":
            log.warning("input events unavailable (%s); running without events", self.tap.reason)

    def _on_decision(self, decision, context):
        self.gate.capture_decision(decision.withheld, (context.application or {}).get("bundle_id"))

    def _reconcile_front_app(self):
        """Feed the current front app to the input gate, as an activation notification would. Covers the start (before
        any notification) and a notification that was missed. A failed read blocks input until the next good one."""
        if self._front_app is None:
            return
        try:
            self.notify("app_activated", self._front_app())
        except Exception:                            # noqa: BLE001
            log.exception("could not read the front application")
            self.notify("app_activated", None)

    def _refresh_settings(self):
        """Pick up changes made while running (excluded apps, screenshot scope). A failed read keeps the previous values."""
        try:
            self.policy = privacy.load_policy(self.store)
            self.gate.set_excluded(self.policy.all_excluded_apps())
            if self.observer is not None:
                self.observer.policy = self.policy
            if hasattr(self.sources, "screenshot_scope"):
                self.sources.screenshot_scope = settings.get_screenshot_scope(self.store)
        except Exception:                            # noqa: BLE001
            log.exception("could not refresh settings; keeping the previous ones")

    def _run_retention(self):
        try:
            retention.run(self.store, self._clock_ns())
            self.retention_runs += 1
        except Exception:                            # noqa: BLE001 - retention must never stop observation
            log.exception("retention run failed")

    # ---- main loop ----

    def run(self, duration_s=None, once=False):
        """Run until request_stop(), the duration, or one capture (once). -> the final stats dict."""
        started = self._t0 = self._monotonic()
        self._report_permissions()
        self._run_warmup()
        self.session_start_ns = self._clock_ns()
        self.buffer = EventBuffer(start_ns=self.session_start_ns)
        self.gate = InputGate()
        self._refresh_settings()
        self._start_tap()
        extra = {}
        if self.ocr_mode == "async" and not once:
            extra["ocr_mode"] = "async"
            self.ocr_processor = OcrProcessor(
                self.store, self.sources.ocr, getattr(self.sources.ocr, "timeout_s", 10.0),
                **({"policy": self._ocr_queue_policy} if self._ocr_queue_policy else {}), clock_ns=self._clock_ns)
            self.ocr_processor.start()
        if self.structure and not once:
            self.structure_processor = StructureProcessor(self.store, clock_ns=self._clock_ns)
            self.structure_processor.start()
        if self.moments and not once:
            self.moment_processor = MomentProcessor(self.store, clock_ns=self._clock_ns)
            self.moment_processor.start()
        self.observer = self._observer_factory(self.store, self.sources, self.buffer, interval_s=self.interval_s,
                                               clock_ns=self._clock_ns, on_decision=self._on_decision,
                                               policy=self.policy, final_always=True, **extra)
        self.lifecycle = Lifecycle(self.observer, self.tap.resync_clock if self.tap else None, self.buffer, self.gate)
        try:
            self._run_retention()
            self._reconcile_front_app()
            if once:
                self.observer.tick()
            else:
                self._loop(started, duration_s)
        finally:
            self._stop.set()
            if self.ocr_processor is not None:
                self.ocr_processor.stop()
            if self.structure_processor is not None:
                self.structure_processor.stop()
            if self.moment_processor is not None:
                self.moment_processor.stop()
            if self.tap is not None:
                try:
                    self.tap.stop()
                except Exception:                    # noqa: BLE001 - shutdown goes on
                    log.exception("input tap did not stop cleanly")
        return self.write_stats(final=True)

    def _observe(self):
        try:
            self.observer.run(self._stop)
        except BaseException as e:                   # noqa: BLE001 - the main loop sees observer_error and stops the run
            self.observer_error = type(e).__name__
            log.exception("the observer thread died")

    def _loop(self, started, duration_s):
        worker = threading.Thread(target=self._observe, name="trace-v2-observer")
        worker.start()
        now = self._monotonic()
        next_stats, next_retention, next_refresh = now + self.stats_every_s, now + RETENTION_EVERY_S, now + self.settings_refresh_s
        try:
            while not self._stop.is_set():
                try:
                    self.pump(PUMP_S)
                except Exception:                    # noqa: BLE001
                    log.exception("notification pump failed")
                    self._stop.wait(PUMP_S)
                now = self._monotonic()
                if duration_s is not None and now - started >= duration_s:
                    break
                if now >= next_refresh:
                    self._refresh_settings()
                    self._reconcile_front_app()
                    next_refresh = now + self.settings_refresh_s
                if now >= next_stats:
                    self._safe_write_stats()
                    next_stats = now + self.stats_every_s
                if now >= next_retention:
                    self._run_retention()
                    next_retention = now + RETENTION_EVERY_S
                if not worker.is_alive():
                    break
        finally:
            self._stop.set()
            worker.join()                         # the final observation is taken here, unless paused

    # ---- stats ----

    def _safe_write_stats(self):
        try:
            self.write_stats()
        except Exception:                            # noqa: BLE001 - a full disk must not stop observation
            log.exception("could not write runner stats")

    def _evidence_counts(self):
        """What this run stored, read back from the evidence tables (read-only): withheld reasons, reader and screenshot
        failures by code, event accounting, storage."""
        db, since = self.store.db, self.session_start_ns or 0
        with self.store.lock:
            withheld = db.execute("SELECT json_extract(privacy_json, '$.reason'), count(*) FROM observations "
                                  "WHERE captured_at_ns >= ? AND json_extract(privacy_json, '$.withheld') = 1 GROUP BY 1",
                                  (since,)).fetchall()
            shots = db.execute("SELECT status, coalesce(error_code, ''), count(*), coalesce(sum(byte_size), 0) "
                               "FROM screenshots WHERE captured_at_ns >= ? GROUP BY 1, 2", (since,)).fetchall()
            readers = db.execute("SELECT e.reader, e.status, coalesce(e.error_code, ''), count(*) FROM extractions e "
                                 "JOIN observations o USING (observation_id) WHERE o.captured_at_ns >= ? AND "
                                 "e.revision = (SELECT max(m.revision) FROM extractions m WHERE m.observation_id = e.observation_id AND "
                                 "m.reader = e.reader) AND e.status IN ('failed', 'unavailable') GROUP BY 1, 2, 3",
                                 (since,)).fetchall()
            events = db.execute("SELECT j.key, sum(j.value) FROM observations o, json_each(o.capture_status_json, "
                                "'$.events') j WHERE o.captured_at_ns >= ? AND j.type = 'integer' GROUP BY j.key",
                                (since,)).fetchall()
            scopes = db.execute("SELECT coalesce(json_extract(privacy_json, '$.screenshot_scope'), 'none'), count(*) "
                                "FROM observations WHERE captured_at_ns >= ? GROUP BY 1", (since,)).fetchall()
        withheld = {reason or "unknown": n for reason, n in withheld}
        secret = sum(n for status, code, n, _ in shots if status == "withheld" and code == "secret_on_screen")
        if secret:
            withheld["secret_on_screen"] = secret
        db_bytes = sum(p.stat().st_size for p in _db_files(self.store.root) if p.exists())
        return {"withheld_by_reason": withheld,
                "screenshots": {f"{status}{':' + code if code else ''}": n for status, code, n, _ in shots},
                "screenshot_bytes": sum(size for status, _, _, size in shots if status == "stored"),
                "reader_failures": {f"{reader}:{status}:{code or 'no_code'}": n for reader, status, code, n in readers},
                "event_totals": {k: int(v) for k, v in events}, "db_bytes": db_bytes,
                "screenshot_scopes": dict(scopes), "screenshot_dedup": dict(self.store.dedup_stats)}

    def _ocr_stats(self):
        """The OCR reader's own counters (a worker-process client has them; the in-process reader does not)."""
        probe = getattr(getattr(self.sources, "ocr", None), "stats", None)
        try:
            return probe() if callable(probe) else None
        except Exception:                        # noqa: BLE001 - stats must never break the run
            log.exception("could not read the OCR worker stats")
            return None

    def _ocr_queue_stats(self):
        try:
            return {**self.store.ocr_queue_stats(), "processor": self.ocr_processor.stats() if self.ocr_processor else None}
        except Exception:                        # noqa: BLE001 - stats must never break the run
            log.exception("could not read the OCR queue stats")
            return None

    def stats(self, final=False):
        observer = self.observer.stats if self.observer else {}
        now = self._monotonic()
        started = self._t0 if self._t0 is not None else now
        cpu = time.process_time()
        uptime = max(now - started, 1e-9)
        try:
            evidence = self._evidence_counts()
        except Exception:                        # noqa: BLE001 - stats must never break the run
            log.exception("could not read evidence counts for the stats")
            evidence = {}
        return {"at_ns": self._clock_ns(), "final": final, "session_start_ns": self.session_start_ns,
                "interval_s": self.interval_s, "uptime_s": round(uptime, 1), "cpu_s": round(cpu, 2),
                "cpu_pct_avg": round(cpu / uptime * 100, 1), "rss_mb": self._rss(),
                "peak_rss_mb": round(_peak_rss_mb(), 1), "observations": observer.get("observations", 0),
                "failures": observer.get("failures", 0), "missed_ticks": observer.get("missed_ticks", 0),
                "events_left_at_stop": observer.get("events_left_at_stop", 0),
                "capture_timing": self.observer.timing() if self.observer else {},
                "withheld_by_reason": evidence.get("withheld_by_reason", {}), "evidence": evidence,
                "input": self.tap.stats() if self.tap else {"state": "disabled"},
                "lifecycle": self.lifecycle.snapshot() if self.lifecycle else None,
                "observer_error": self.observer_error, "warmup": self.warmup_result,
                "permissions": self.permissions_report, "retention_runs": self.retention_runs,
                "ocr_worker": self._ocr_stats(), "ocr_queue": self._ocr_queue_stats(),
                "structure": self.structure_processor.stats() if self.structure_processor else None,
                "moments": self.moment_processor.stats() if self.moment_processor else None}

    def write_stats(self, final=False):
        stats = self.stats(final)
        tmp = self.stats_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, indent=1, sort_keys=True))
        os.replace(tmp, self.stats_path)
        log.info("stats: %d captures, %d failures, %d missed, cpu %.1f%%, rss %s MB, withheld %s",
                 stats["observations"], stats["failures"], stats["missed_ticks"], stats["cpu_pct_avg"],
                 stats["rss_mb"], stats["withheld_by_reason"] or "-")
        return stats


def _db_files(root):
    from . import paths
    base = paths.database_path(root)
    return [base, base.with_name(base.name + "-wal"), base.with_name(base.name + "-shm")]


def read_status(directory):
    """For --status: lock holder and last stats, without taking the lock."""
    held, pid = InstanceLock.held(directory)
    stats_path = Path(directory) / STATS_FILE
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else None
    return {"running": held, "pid": pid, "stats": stats}

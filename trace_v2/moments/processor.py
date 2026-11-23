import logging
import threading
import time

from ..structure.processor import read_connection
from .builder import run_pass
from .similarity import PARAMS, S

log = logging.getLogger("trace_v2.moments")


class MomentProcessor:
    def __init__(self, store, params=PARAMS, interval_s=60.0, lookback_s=6 * 3600, clock_ns=time.time_ns):
        self.store, self.params, self.interval_s = store, params, interval_s
        self.lookback_s, self.clock_ns = lookback_s, clock_ns
        self.counts = {}
        self.timing_ms = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._reader = None
        self._state = {}

    def _count(self, name, n=1):
        with self._lock:
            self.counts[name] = self.counts.get(name, 0) + n

    def stats(self):
        with self._lock:
            t = sorted(self.timing_ms)
        out = dict(self.counts)
        if t:
            out["ms_p50"] = round(t[len(t) // 2], 1)
            out["ms_max"] = round(t[-1], 1)
        return out

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="trace-v2-moments", daemon=True)
            self._thread.start()

    def stop(self, grace_s=3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(grace_s)
        if (self._thread is None or not self._thread.is_alive()) and self._reader is not None:
            self._reader.close()
            self._reader = None

    def run_once(self):
        if self._reader is None:
            self._reader = read_connection(self.store)
        now = self.clock_ns()
        since = 0 if self.lookback_s is None else now - int(self.lookback_s * S)
        started = time.perf_counter()
        out = run_pass(self.store, self._reader, now, self.params, since_ns=since, state=self._state)
        with self._lock:
            self.timing_ms = (self.timing_ms + [(time.perf_counter() - started) * 1000])[-500:]
        for key, value in out.items():
            if value:
                self._count(key, value)
        return out

    def _run(self):
        while not self._stop.is_set():
            try:
                out = self.run_once()
                busy = out.get("processed", 0) + out.get("skipped", 0) >= 300
            except Exception:
                self._state = {}
                log.exception("moment processor iteration failed")
                busy = False
            if not busy:
                self._stop.wait(self.interval_s)

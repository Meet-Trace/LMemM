import logging
import threading
import time

from ..descriptions.gate import pause_reason
from ..structure.processor import read_connection
from .builder import PARAMS, run_pass

log = logging.getLogger("trace_v2.activities")


class ActivityProcessor:
    def __init__(self, store, interpreter, params=PARAMS, interval_s=120.0, clock_ns=time.time_ns, gate=pause_reason):
        self.gate = gate
        self.store, self.interpreter, self.params, self.interval_s, self.clock_ns = store, interpreter, params, interval_s, clock_ns
        self.counts = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._reader = None
        self._state = {}

    def stats(self):
        with self._lock:
            return {**self.counts, "paused": self._state.get("paused")}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="trace-v2-activities", daemon=True)
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
        reason = self.gate() if self.gate else None
        self._state["paused"] = reason
        if reason:
            with self._lock:
                self.counts["paused"] = self.counts.get("paused", 0) + 1
            return {"interpreted": 0, "interpret_failed": 0, "joined": 0, "created": 0, "deferred": 0, "archived": 0, "merged": 0}
        out = run_pass(self.store, self._reader, self.interpreter, self.clock_ns(), self.params, self._state)
        with self._lock:
            for key, value in out.items():
                if value:
                    self.counts[key] = self.counts.get(key, 0) + value
        return out

    def _run(self):
        while not self._stop.is_set():
            busy = False
            try:
                out = self.run_once()
                busy = out["interpreted"] + out["joined"] + out["created"] >= 4
            except Exception:
                log.exception("activity processor iteration failed")
            if not busy:
                self._stop.wait(self.interval_s)

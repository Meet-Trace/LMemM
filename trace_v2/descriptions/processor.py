import logging
import threading
import time

from ..structure.processor import read_connection
from .builder import PARAMS, run_pass
from .call import Interrupted
from .gate import pause_reason

log = logging.getLogger("trace_v2.descriptions")


class DescriptionProcessor:
    def __init__(self, store, client, model, params=PARAMS, interval_s=60.0, gate=pause_reason, options=None):
        self.store, self.client, self.model, self.params, self.interval_s = store, client, model, params, interval_s
        self.gate, self.options = gate, options
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
            self._thread = threading.Thread(target=self._run, name="trace-v2-descriptions", daemon=True)
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
        out = run_pass(self.store, self._reader, self.client, self.model, self.params, self._state, self.gate, self.options)
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
                busy = bool(out["described"] or out["insufficient"] or out["failed"])
            except Interrupted:
                self._state["paused"] = "you are back"
            except Exception:
                log.exception("description processor iteration failed")
            if not busy:
                self._stop.wait(self.interval_s)

"""Building Observations from capture sources, once or continuously.

Sources are injected (the macOS adapters come separately), so this module has no platform imports:
    sources.capture_version       str
    sources.context()             -> models.Context
    sources.screenshot()          -> models.ScreenshotCapture
    sources.ocr                   reader with .version and .read(screenshot, context) -> (raw, text)
    sources.accessibility         reader with .version and .read(context) -> (raw, text)
A reader raises ReaderUnavailable when it cannot run at all (e.g. permission not granted) and any other
exception when it ran and failed. Each step is isolated: a failing context, screenshot or reader is recorded
with its error and the Observation is still stored.

ContinuousObserver captures on a fixed schedule (every `interval_s`), whether or not anything happened.
It is not event-triggered.
"""

import json
import logging
import threading
import time
import uuid

from .models import CaptureDraft, Context, ReaderResult, ScreenshotCapture, ScreenshotFailure, short_error

log = logging.getLogger("trace_v2.observations")


class ReaderUnavailable(RuntimeError):
    """The reader could not run (missing permission, unsupported app, not installed)."""


def _error(e):
    return type(e).__name__, short_error(e)


def run_reader(reader, clock_ns, *args):
    started = clock_ns()
    version = getattr(reader, "version", None) or "unknown"
    try:
        raw, text = reader.read(*args)
        json.dumps(raw, allow_nan=False)
        if text is not None and not isinstance(text, str):
            raise TypeError("reader text must be a string")
        status, code, message = "succeeded", None, None
        if raw is None:
            status, code, message = "failed", "no_output", "reader returned no raw output"
    except ReaderUnavailable as e:
        raw = text = None
        status, (code, message) = "unavailable", _error(e)
    except Exception as e:                       # noqa: BLE001 - every reader failure is recorded, never raised
        raw = text = None
        status, (code, message) = "failed", _error(e)
    return ReaderResult(version, status, raw, text, code, message, started, clock_ns())


def skipped(reader, code):
    return ReaderResult(getattr(reader, "version", None) or "unknown", "skipped", error_code=code)


def build_draft(sources, buffer, capture_key, clock_ns=time.time_ns):
    t = clock_ns()
    try:
        context = sources.context()
        context.validate()
    except Exception as e:                       # noqa: BLE001
        context = Context({}, {}, {}, error_code=type(e).__name__)
    try:
        shot = sources.screenshot()
        shot.validate()
    except Exception as e:                       # noqa: BLE001
        code, message = _error(e)
        shot = ScreenshotFailure(code, message, t)
    if isinstance(shot, ScreenshotCapture):
        ocr = run_reader(sources.ocr, clock_ns, shot, context)
    else:
        ocr = skipped(sources.ocr, "no_screenshot")
    ax = run_reader(sources.accessibility, clock_ns, context)
    events, window, stats = buffer.drain(t)
    return CaptureDraft(capture_key, t, context, shot, ocr, ax, sources.capture_version,
                        events, window, stats)


def observe_once(store, sources, buffer, capture_key, clock_ns=time.time_ns):
    return store.ingest(build_draft(sources, buffer, capture_key, clock_ns))


class ContinuousObserver:
    def __init__(self, store, sources, buffer, interval_s=2.0, clock_ns=time.time_ns, monotonic=time.monotonic,
                 session_id=None):
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.store, self.sources, self.buffer = store, sources, buffer
        self.interval_s, self.clock_ns, self.monotonic = interval_s, clock_ns, monotonic
        self.session_id = session_id or uuid.uuid4().hex
        self.seq = 0
        self.stats = {"observations": 0, "failures": 0, "missed_ticks": 0}

    def tick(self):
        key = f"{self.session_id}:{self.seq}"
        self.seq += 1
        try:
            observe_once(self.store, self.sources, self.buffer, key, self.clock_ns)
            self.stats["observations"] += 1
        except Exception as e:                   # noqa: BLE001 - keep observing; the failure is counted and logged
            self.stats["failures"] += 1
            log.error("observation %s not stored: %s", key, type(e).__name__)

    def run(self, stop=None, max_ticks=None):
        """Capture every interval until `stop` (a threading.Event) is set or max_ticks is reached.
        A slow capture never causes a burst: missed ticks are skipped and counted."""
        stop = stop or threading.Event()
        next_at = self.monotonic()
        ticks = 0
        while not stop.is_set() and (max_ticks is None or ticks < max_ticks):
            self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            next_at += self.interval_s
            now = self.monotonic()
            if now > next_at:
                missed = int((now - next_at) // self.interval_s) + 1
                self.stats["missed_ticks"] += missed
                next_at += missed * self.interval_s
            stop.wait(max(0.0, next_at - self.monotonic()))
        return self.stats

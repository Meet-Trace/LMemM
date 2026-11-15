"""Building Observations from capture sources, once or continuously.

Sources are injected (the macOS adapters come separately), so this module has no platform imports:
    sources.capture_version       str
    sources.context()             -> models.Context (metadata only: app, window incl. url/private, display)
    sources.secure_input()        -> bool (optional; macOS secure keyboard entry)
    sources.screenshot()          -> models.ScreenshotCapture
    sources.ocr                   reader with .version and .read(screenshot, context) -> (raw, text)
    sources.accessibility         reader with .version and .read(context) -> (raw, text)
A reader raises ReaderUnavailable when it cannot run at all (e.g. permission not granted) and any other
exception when it ran and failed. Each step is isolated: a failing context, screenshot or reader is recorded
with its error and the Observation is still stored.

Order matters for privacy: context -> privacy gate -> (only if allowed) screenshot and readers -> secret
redaction -> ingest. A withheld context never has its pixels or text read.

ContinuousObserver captures on a fixed schedule (every `interval_s`), whether or not anything happened, plus one
final Observation when stopped so the last interval's events are not lost. It is not event-triggered.
"""

import json
import logging
import threading
import time
import uuid

from . import privacy
from .models import (READERS, CaptureDraft, Context, ReaderResult, ScreenshotCapture, ScreenshotFailure,
                     ScreenshotWithheld, short_error)

log = logging.getLogger("trace_v2.observations")


class ReaderUnavailable(RuntimeError):
    """The reader could not run (missing permission, unsupported app, not installed)."""


def _error(e):
    return type(e).__name__, short_error(e)


def _version(reader):
    return getattr(reader, "version", None) or "unknown"


def run_reader(reader, clock_ns, *args):
    started = clock_ns()
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
    return ReaderResult(_version(reader), status, raw, text, code, message, started, clock_ns())


def _not_run(reader, status, code):
    return ReaderResult(_version(reader), status, error_code=code)


def _redact_result(result, counts):
    if result.status != "succeeded":
        return result
    raw, _ = privacy.redact(result.raw, counts)
    text = None if result.text is None else privacy.redact(result.text, counts)[0]
    return ReaderResult(result.reader_version, result.status, raw, text, result.error_code, result.error_message,
                        result.started_at_ns, result.finished_at_ns)


def _redact_context(context, counts):
    window = {k: (privacy.redact(v, counts)[0] if k in ("title", "url") else v) for k, v in context.window.items()}
    return Context(context.application, window, context.display, context.error_code)


def _read_context(sources):
    try:
        context = sources.context()
        context.validate()
        return context
    except Exception as e:                       # noqa: BLE001
        return Context({}, {}, {}, error_code=type(e).__name__)


def _secure_input(sources):
    probe = getattr(sources, "secure_input", None)
    try:
        return bool(probe()) if probe else False
    except Exception:                            # noqa: BLE001 - unknown is treated as on: fail closed
        return True


def _withheld_draft(sources, key, t, context, reason, batch):
    """Committing the batch after this is stored drops its events on purpose; they are only counted."""
    stats = {**batch.stats, "stored": 0, "discarded_privacy": len(batch.events)}
    return CaptureDraft(key, t, privacy.minimal_context(context), ScreenshotWithheld(reason, t),
                        _not_run(sources.ocr, "withheld", reason), _not_run(sources.accessibility, "withheld", reason),
                        sources.capture_version, (), batch.window, stats,
                        {"withheld": True, "reason": reason, "policy_version": privacy.POLICY_VERSION}), batch


def build_draft(sources, buffer, capture_key, policy=None, clock_ns=time.time_ns):
    """-> (CaptureDraft, Batch). Commit the batch once the draft is stored, restore it on failure."""
    policy = policy or privacy.PrivacyPolicy()
    t = clock_ns()
    context = _read_context(sources)
    decision = privacy.gate(context, policy, _secure_input(sources))
    batch = buffer.take(t)
    try:
        return _draft(sources, capture_key, policy, clock_ns, t, context, decision, batch)
    except BaseException:
        batch.restore()
        raise


def _draft(sources, capture_key, policy, clock_ns, t, context, decision, batch):
    if decision.withheld:
        return _withheld_draft(sources, capture_key, t, context, decision.reason, batch)
    try:
        shot = sources.screenshot()
        shot.validate()
    except Exception as e:                       # noqa: BLE001
        code, message = _error(e)
        shot = ScreenshotFailure(code, message, t)
    if isinstance(shot, ScreenshotCapture):
        ocr = run_reader(sources.ocr, clock_ns, shot, context)
    else:
        ocr = _not_run(sources.ocr, "skipped", "no_screenshot")
    ax = run_reader(sources.accessibility, clock_ns, context)
    counts, reader_counts = {}, {}
    if policy.redact_secrets:
        ocr, ax = _redact_result(ocr, reader_counts), _redact_result(ax, reader_counts)
        context = _redact_context(context, counts)
        for k, v in reader_counts.items():
            counts[k] = counts.get(k, 0) + v
    on_screen = {k: v for k, v in reader_counts.items() if k != "secure_field"}
    if on_screen and isinstance(shot, ScreenshotCapture):
        shot = ScreenshotWithheld("secret_on_screen", shot.captured_at_ns)
    record = {"withheld": False, "redactions": counts, "policy_version": privacy.POLICY_VERSION}
    return CaptureDraft(capture_key, t, context, shot, ocr, ax, sources.capture_version,
                        batch.events, batch.window, batch.stats, record), batch


def observe_once(store, sources, buffer, capture_key, policy=None, clock_ns=time.time_ns):
    draft, batch = build_draft(sources, buffer, capture_key, policy, clock_ns)
    try:
        result = store.ingest(draft)
    except BaseException:
        batch.restore()              # the events wait for the next Observation; nothing is lost
        raise
    batch.commit()
    return result


class ContinuousObserver:
    def __init__(self, store, sources, buffer, interval_s=2.0, clock_ns=time.time_ns, monotonic=time.monotonic,
                 session_id=None, policy=None):
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.store, self.sources, self.buffer = store, sources, buffer
        self.interval_s, self.clock_ns, self.monotonic = interval_s, clock_ns, monotonic
        self.session_id = session_id or uuid.uuid4().hex
        self.policy = policy
        self.seq = 0
        self.stats = {"observations": 0, "failures": 0, "missed_ticks": 0, "events_left_at_stop": 0}

    def tick(self):
        key = f"{self.session_id}:{self.seq}"
        self.seq += 1
        try:
            observe_once(self.store, self.sources, self.buffer, key, self.policy, self.clock_ns)
            self.stats["observations"] += 1
            return True
        except Exception as e:                   # noqa: BLE001 - keep observing; the failure is counted and logged
            self.stats["failures"] += 1
            log.error("observation %s not stored: %s", key, type(e).__name__)
            return False

    def run(self, stop=None, max_ticks=None):
        """Capture every interval until `stop` (a threading.Event) is set or max_ticks is reached, then take one
        final Observation for the events since the last one. Missed ticks are skipped and counted."""
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
        if self.buffer.pending():
            self.tick()                          # the shutdown Observation
        self.stats["events_left_at_stop"] = self.buffer.pending()
        return self.stats


__all__ = ["ContinuousObserver", "ReaderUnavailable", "build_draft", "observe_once", "run_reader", "READERS"]

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
final Observation when stopped so the last interval's events are not lost (only when events are pending, or always
with final_always=True, which the runner uses). pause()/resume() stop captures without counting the time as missed.
It is not event-triggered.

A screenshot source may raise models.ScopeUnavailable: the screenshot is then withheld as `scope_unavailable`, and the
scope the capture was taken with is recorded in the observation's privacy record (`screenshot_scope`).
"""

import collections
import json
import logging
import threading
import time
import uuid

from . import privacy
from .models import (READERS, CaptureDraft, Context, ReaderResult, ScopeUnavailable, ScreenshotCapture,
                     ScreenshotFailure, ScreenshotWithheld, short_error)
from .settings import DEFAULT_CAPTURE_INTERVAL_S

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


def _probe(sources):
    """The tiny probe picture, or None when there is no probe source or it failed (then the tick is captured)."""
    probe = getattr(sources, "probe", None)
    if probe is None:
        return None
    try:
        return probe()
    except Exception:                            # noqa: BLE001 - a failed probe only means "cannot tell": capture
        return None


def build_draft(sources, buffer, capture_key, policy=None, clock_ns=time.time_ns, on_decision=None, ocr_mode="sync",
                trigger=None):
    """-> (CaptureDraft, Batch). Commit the batch once the draft is stored, restore it on failure.

    With a `trigger` (trigger.CaptureTrigger) and a source that has `probe()`, a tick where nothing happened since the
    last full capture is skipped: the events stay in the buffer and the result is (None, None). A withheld tick is never
    skipped (it is cheap and records that a gated app was in front).

    `on_decision(decision, context)` is called with the privacy gate's decision before anything is read or captured, so a
    runner can react to it (for example drop input events while a gated app is in front). It never changes the draft;
    an exception in it is logged and ignored."""
    policy = policy or privacy.PrivacyPolicy()
    t = clock_ns()
    context = _read_context(sources)
    decision = privacy.gate(context, policy, _secure_input(sources))
    if on_decision is not None:
        try:
            on_decision(decision, context)
        except Exception:                        # noqa: BLE001 - an observer hook must not break capture
            log.exception("on_decision hook failed")
    batch = buffer.take(t)
    if trigger is not None and not decision.withheld and hasattr(sources, "probe"):
        if trigger.decide(t, context, _probe(sources), batch.events) is None:
            batch.restore()
            return None, None
    try:
        return _draft(sources, capture_key, policy, clock_ns, t, context, decision, batch, ocr_mode)
    except BaseException:
        batch.restore()
        raise


def _draft(sources, capture_key, policy, clock_ns, t, context, decision, batch, ocr_mode):
    if decision.withheld:
        return _withheld_draft(sources, capture_key, t, context, decision.reason, batch)
    scope = getattr(sources, "screenshot_scope", None)          # read once: the scope this capture is taken with
    try:
        shot = sources.screenshot()
        shot.validate()
        scope = getattr(shot, "scope", None) or scope
    except ScopeUnavailable:                     # fail closed: never fall back to a wider capture
        shot = ScreenshotWithheld("scope_unavailable", t)
    except Exception as e:                       # noqa: BLE001
        code, message = _error(e)
        shot = ScreenshotFailure(code, message, t)
    if isinstance(shot, ScreenshotCapture) and ocr_mode == "async":
        ocr = _not_run(sources.ocr, "pending", None)
    elif isinstance(shot, ScreenshotCapture):
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
        if ocr.status == "pending":
            ocr = _not_run(sources.ocr, "withheld", "secret_on_screen")
    record = {"withheld": False, "redactions": counts, "policy_version": privacy.POLICY_VERSION}
    if scope:
        record["screenshot_scope"] = scope
    return CaptureDraft(capture_key, t, context, shot, ocr, ax, sources.capture_version,
                        batch.events, batch.window, batch.stats, record), batch


def observe_once(store, sources, buffer, capture_key, policy=None, clock_ns=time.time_ns, on_decision=None,
                 ocr_mode="sync", trigger=None):
    """-> IngestResult, or None when the trigger skipped the tick."""
    draft, batch = build_draft(sources, buffer, capture_key, policy, clock_ns, on_decision, ocr_mode, trigger)
    if draft is None:
        return None
    try:
        result = store.ingest(draft)
    except BaseException:
        batch.restore()              # the events wait for the next Observation; nothing is lost
        raise
    batch.commit()
    return result


class ContinuousObserver:
    def __init__(self, store, sources, buffer, interval_s=DEFAULT_CAPTURE_INTERVAL_S, clock_ns=time.time_ns,
                 monotonic=time.monotonic, session_id=None, policy=None, on_decision=None, final_always=False, ocr_mode="sync",
                 trigger=None):
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.store, self.sources, self.buffer = store, sources, buffer
        self.interval_s, self.clock_ns, self.monotonic = interval_s, clock_ns, monotonic
        self.session_id = session_id or uuid.uuid4().hex
        self.policy, self.on_decision, self.final_always = policy, on_decision, final_always
        self.ocr_mode, self.trigger = ocr_mode, trigger
        self._paused = threading.Event()
        self._timings = collections.deque(maxlen=5000)       # (process CPU s, wall s) per capture attempt
        self.seq = 0
        self.stats = {"observations": 0, "failures": 0, "missed_ticks": 0, "skipped_ticks": 0, "events_left_at_stop": 0}

    def tick(self, force=False):
        """One probe-and-maybe-capture. `force` takes a full capture whatever the trigger says (the shutdown one)."""
        key = f"{self.session_id}:{self.seq}"
        self.seq += 1
        cpu0, wall0 = time.process_time(), time.perf_counter()
        try:
            stored = observe_once(self.store, self.sources, self.buffer, key, self.policy, self.clock_ns, self.on_decision,
                                  self.ocr_mode, None if force else self.trigger)
            self.stats["skipped_ticks" if stored is None else "observations"] += 1
            return True
        except Exception as e:                   # noqa: BLE001 - keep observing; the failure is counted and logged
            self.stats["failures"] += 1
            log.error("observation %s not stored: %s", key, type(e).__name__)
            return False
        finally:
            self._timings.append((time.process_time() - cpu0, time.perf_counter() - wall0))

    def timing(self):
        """Per-capture cost: process CPU time (all threads, so it includes concurrent input handling) and wall time."""
        rows = list(self._timings)
        if not rows:
            return {}

        def p95(values):
            values = sorted(values)
            return values[min(len(values) - 1, int(0.95 * len(values)))]
        cpu, wall = [r[0] * 1000 for r in rows], [r[1] * 1000 for r in rows]
        return {"captures": len(rows), "cpu_ms_avg": round(sum(cpu) / len(cpu), 1), "cpu_ms_p95": round(p95(cpu), 1),
                "wall_ms_avg": round(sum(wall) / len(wall), 1), "wall_ms_p95": round(p95(wall), 1),
                "wall_ms_max": round(max(wall), 1)}

    def pause(self):
        """No captures (and no final capture on stop) until resume(). Events keep collecting in the buffer."""
        self._paused.set()

    def resume(self):
        self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def run(self, stop=None, max_ticks=None):
        """Capture every interval until `stop` (a threading.Event) is set or max_ticks is reached, then take one
        final Observation for the events since the last one. Missed ticks are skipped and counted."""
        stop = stop or threading.Event()
        next_at = self.monotonic()
        ticks = 0
        while not stop.is_set() and (max_ticks is None or ticks < max_ticks):
            if self._paused.is_set():            # paused time is not "missed": the schedule restarts on resume
                stop.wait(0.2)
                next_at = self.monotonic()
                continue
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
        if (self.final_always or self.buffer.pending()) and not self._paused.is_set():
            self.tick(force=True)                # the shutdown Observation (none while paused, e.g. asleep)
        self.stats["events_left_at_stop"] = self.buffer.pending()
        return self.stats


__all__ = ["ContinuousObserver", "ReaderUnavailable", "build_draft", "observe_once", "run_reader", "READERS"]

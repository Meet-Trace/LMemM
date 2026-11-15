"""Raw interaction events: an allow-listed payload per type, and a buffer that hands them to the next Observation.

Privacy rule: a keypress records only its phase, a coarse key class, modifiers and repeat. Never the
character, the key name or the keycode (a keycode sequence reconstructs typed text). Keypresses while
macOS secure input is on (password fields) are not stored at all, only counted.

Events are never summarised here: every accepted event is stored, in arrival order. The only reduction happens
upstream and is explicit: the input source samples cursor movement (trace_v2.inputs.CursorSampler) and says how many
raw moves each stored `cursor_move` stands for (`coalesced`). Clicks, scrolls and keypresses are never reduced.
"""

import threading
from collections import deque

from .models import InteractionEvent, finite

KEY_CLASSES = ("character", "whitespace", "delete", "return", "tab", "escape",
               "navigation", "function", "modifier", "other")
MODIFIERS = ("command", "shift", "option", "control", "fn", "caps_lock")


class InvalidEvent(ValueError):
    pass


def _num(v):
    return finite(v)


def _str(v):
    return isinstance(v, str) and 0 < len(v) <= 64


def _enum(*allowed):
    return lambda v: v in allowed


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 10


def _coalesced(v):
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1_000_000


def _mods(v):
    return isinstance(v, list) and len(set(v)) == len(v) and all(m in MODIFIERS for m in v)


# type -> (required fields, {field: validator})
SCHEMAS = {
    "cursor_move": ({"x", "y"}, {"x": _num, "y": _num, "display_id": _str, "coalesced": _coalesced,
                                  "final": lambda v: v is True}),
    "click": ({"x", "y", "button", "phase"},
              {"x": _num, "y": _num, "button": _enum("left", "right", "middle", "other"),
               "phase": _enum("down", "up"), "click_count": _count, "display_id": _str}),
    "scroll": ({"dx", "dy"},
               {"dx": _num, "dy": _num, "x": _num, "y": _num, "unit": _enum("line", "pixel"),
                "phase": _enum("began", "changed", "ended", "momentum", "none"), "display_id": _str}),
    "keypress": ({"phase", "key_class"},
                 {"phase": _enum("down", "up"), "key_class": _enum(*KEY_CLASSES),
                  "modifiers": _mods, "is_repeat": lambda v: isinstance(v, bool)}),
}


def validate(event):
    """Raise InvalidEvent unless the event matches its type's allow-list exactly."""
    if not isinstance(event, InteractionEvent) or event.type not in SCHEMAS:
        raise InvalidEvent(f"unknown event type {getattr(event, 'type', None)!r}")
    if isinstance(event.occurred_at_ns, bool) or not isinstance(event.occurred_at_ns, int) or event.occurred_at_ns < 0:
        raise InvalidEvent("occurred_at_ns must be a non-negative integer")
    required, fields = SCHEMAS[event.type]
    payload = event.payload
    if not isinstance(payload, dict):
        raise InvalidEvent("payload must be an object")
    unknown = set(payload) - set(fields)
    if unknown:          # field names only; values may be sensitive
        raise InvalidEvent(f"{event.type} payload has fields outside the allow-list: {sorted(unknown)}")
    missing = required - set(payload)
    if missing:
        raise InvalidEvent(f"{event.type} payload is missing {sorted(missing)}")
    bad = [k for k, v in payload.items() if not fields[k](v)]
    if bad:
        raise InvalidEvent(f"{event.type} payload has invalid values for {sorted(bad)}")
    return event


class EventBuffer:
    """Thread-safe holding area between the input source and the next Observation.

    Attribution rule: every accepted event belongs to exactly one Observation, the first one whose capture
    time is >= the event's timestamp among those taken after the event arrived.
    - Startup: the buffer opens at `start_ns` (the session start). Events stamped before it are rejected and
      counted (`before_start`), so nothing from before the session is attributed to its first Observation.
    - Boundaries: an Observation's window is (previous capture time, its capture time]: an event exactly at a
      capture time belongs to that capture.
    - Late arrivals: an event stamped at or before the window start (delivered after the previous Observation
      was taken) goes to the next Observation and is counted as `late`; its timestamp is never altered.
    - Missed or failed captures: take() hands out a Batch; the caller commit()s it once the Observation is
      stored, or restore()s it on failure, so no event is lost and the window does not advance.
    - Clock steps backwards: a capture time before the window start takes nothing new, keeps the window start,
      and is counted (`clock_regression`).
    - Shutdown: the observer takes one final Observation when stopped; events after that are counted by
      pending() and reported, never silently dropped.
    """

    def __init__(self, start_ns, max_events=200_000):
        if isinstance(start_ns, bool) or not isinstance(start_ns, int) or start_ns < 0:
            raise ValueError("start_ns (the session start) is required")
        self.max_events = max_events
        self._lock = threading.Lock()
        self._held = deque()
        self._since = start_ns
        self._start = start_ns
        self._stats = self._zero()
        self._out = None

    @staticmethod
    def _zero():
        return {"received": 0, "rejected": 0, "suppressed_secure_input": 0, "dropped_overflow": 0,
                "before_start": 0}

    def add(self, event, secure_input=False):
        with self._lock:
            self._stats["received"] += 1
            if secure_input and getattr(event, "type", None) == "keypress":
                self._stats["suppressed_secure_input"] += 1
                return False
            try:
                validate(event)
            except InvalidEvent:
                self._stats["rejected"] += 1
                return False
            if event.occurred_at_ns < self._start:
                self._stats["before_start"] += 1
                return False
            if len(self._held) >= self.max_events:
                self._stats["dropped_overflow"] += 1
                return False
            self._held.append(event)
            return True

    def take(self, until_ns):
        """Remove the events for an Observation captured at until_ns. Returns a Batch to commit or restore."""
        with self._lock:
            if self._out is not None:
                raise RuntimeError("the previous batch was neither committed nor restored")
            regression = until_ns < self._since
            end = self._since if regression else until_ns
            out, keep = [], deque()
            for e in self._held:
                (out if e.occurred_at_ns <= end else keep).append(e)
            self._held = keep
            stats, self._stats = self._stats, self._zero()
            stats.update(stored=len(out), late=sum(1 for e in out if e.occurred_at_ns <= self._since),
                         clock_regression=int(regression))
            batch = Batch(self, tuple(out), (self._since, end), stats)
            self._out = batch
            return batch

    def _commit(self, batch):
        with self._lock:
            self._since = batch.window[1]
            self._out = None

    def _restore(self, batch):
        with self._lock:
            self._held = deque(batch.events) + self._held
            for k, v in batch.stats.items():
                if k in self._stats:
                    self._stats[k] += v
            self._out = None

    def pending(self):
        with self._lock:
            return len(self._held)


class Batch:
    def __init__(self, buffer, events, window, stats):
        self.buffer, self.events, self.window, self.stats = buffer, events, window, stats

    def commit(self):
        self.buffer._commit(self)

    def restore(self):
        self.buffer._restore(self)

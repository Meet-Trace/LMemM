"""Raw interaction events: an allow-listed payload per type, and a buffer that hands them to the next Observation.

Privacy rule: a keypress records only its phase, a coarse key class, modifiers and repeat. Never the
character, the key name or the keycode (a keycode sequence reconstructs typed text). Keypresses while
macOS secure input is on (password fields) are not stored at all, only counted.

Events are never summarised or coalesced: every accepted event is stored, in arrival order.
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


def _mods(v):
    return isinstance(v, list) and len(set(v)) == len(v) and all(m in MODIFIERS for m in v)


# type -> (required fields, {field: validator})
SCHEMAS = {
    "cursor_move": ({"x", "y"}, {"x": _num, "y": _num, "display_id": _str}),
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

    add() validates and keeps accepted events in arrival order. drain(until_ns) removes and returns every
    held event with occurred_at_ns <= until_ns (arrival order preserved), the window it covers, and counts.
    Events after until_ns stay for the next Observation, so each event belongs to exactly one Observation.
    """

    def __init__(self, max_events=200_000, start_ns=None):
        self.max_events = max_events
        self._lock = threading.Lock()
        self._held = deque()
        self._since = start_ns
        self._stats = self._zero()

    @staticmethod
    def _zero():
        return {"received": 0, "rejected": 0, "suppressed_secure_input": 0, "dropped_overflow": 0}

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
            if len(self._held) >= self.max_events:
                self._stats["dropped_overflow"] += 1
                return False
            self._held.append(event)
            return True

    def drain(self, until_ns):
        with self._lock:
            out, keep = [], deque()
            for e in self._held:
                (out if e.occurred_at_ns <= until_ns else keep).append(e)
            self._held = keep
            window = (self._since, until_ns)
            self._since = until_ns
            stats, self._stats = self._stats, self._zero()
            stats["stored"] = len(out)
            return tuple(out), window, stats

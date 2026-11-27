"""Decide whether a tick should take a full capture (screenshot, OCR, accessibility) or be skipped.

Every tick the observer reads the cheap context, runs the privacy gate and takes a tiny probe picture. A full capture
is taken only when something happened:
    first        nothing was captured yet in this session
    context      the application, window title, URL or display changed
    input        a click, key press or scroll arrived (cursor movement alone does not count)
    screen       the probe picture differs from the one at the last full capture by more than `change_fraction`
    heartbeat    nothing happened for `heartbeat_s`; after `idle_after_s` without any change or input the heartbeat
                 slows to `idle_heartbeat_s`
Compared with the last full capture, not the previous probe, so slow drift adds up and is not missed.
A source without a `probe()` method, a failing probe or `enabled: false` means every tick is captured, as before.
"""

import io

import numpy as np

KEY = "capture_trigger"
DEFAULTS = {"enabled": True, "change_fraction": 0.004, "pixel_delta": 12, "heartbeat_s": 30.0,
            "idle_after_s": 300.0, "idle_heartbeat_s": 120.0}
S = 1_000_000_000
QUIET_EVENT_TYPES = frozenset({"cursor_move"})


def normalise(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError(f"{KEY} takes only {sorted(DEFAULTS)}")
    out = dict(DEFAULTS)
    out.update(value)
    if not isinstance(out["enabled"], bool):
        raise ValueError("enabled must be true or false")
    for name, low, high in (("change_fraction", 0.0, 0.5), ("pixel_delta", 1, 128), ("heartbeat_s", 1.0, 3600.0),
                            ("idle_after_s", 1.0, 86400.0), ("idle_heartbeat_s", 1.0, 3600.0)):
        v = out[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
            raise ValueError(f"{name} must be a number between {low} and {high}")
    if out["idle_heartbeat_s"] < out["heartbeat_s"]:
        raise ValueError("idle_heartbeat_s must not be shorter than heartbeat_s")
    return out


def get_params(store):
    from . import settings
    try:
        return normalise(settings.get(store, KEY, dict(DEFAULTS)))
    except ValueError:
        return dict(DEFAULTS)


def set_params(store, value):
    from . import settings
    return settings.put(store, KEY, normalise(value))


def signature(context):
    window = context.window or {}
    return (context.application.get("bundle_id"), window.get("title"), window.get("url"), context.display.get("id"))


def has_input(events):
    return any(getattr(e, "type", None) not in QUIET_EVENT_TYPES for e in events)


def picture(png):
    """-> small greyscale array of the probe picture, or None when it cannot be read."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(bytes(png))) as image:
            image.load()
            return np.asarray(image.convert("L"), dtype=np.int16)
    except Exception:                                    # noqa: BLE001 - an unreadable probe counts as a change
        return None


class CaptureTrigger:
    """Holds what the last full capture looked like. One instance per observer; used from the observer thread only."""

    def __init__(self, params=None):
        self.params = normalise(params or {})
        self.counts = {}
        self.ax_memory = {}                    # the last accessibility read, reused by heartbeat captures (pipeline.py)
        self._last_at = self._active_at = self._signature = self._picture = None
        self.hold = None                       # () -> True while a model call runs: idle heartbeats wait (a screenshot would time out)
        self._changed_at = None                # last capture caused by a real change or input (not a heartbeat or a failed probe)

    def set_params(self, params):
        self.params = normalise(params)

    def decide(self, t_ns, context, probe_png, events):
        """-> the reason to capture now, or None to skip this tick."""
        reason = self._reason(t_ns, context, probe_png, events)
        if reason in ("heartbeat", "idle_heartbeat") and self.hold is not None and self.hold():
            reason = None
        if reason is not None:
            self._remember(t_ns, context, probe_png, reason)
            self.counts[reason] = self.counts.get(reason, 0) + 1
        else:
            self.counts["skipped"] = self.counts.get("skipped", 0) + 1
        return reason

    def _reason(self, t_ns, context, probe_png, events):
        p = self.params
        if not p["enabled"]:
            return "disabled"
        if self._last_at is None:
            return "first"
        if signature(context) != self._signature:
            return "context"
        if has_input(events):
            return "input"
        if probe_png is None:
            return "no_probe"
        now, before = picture(probe_png), self._picture
        if now is None or before is None or now.shape != before.shape:
            return "screen"
        if float((np.abs(now - before) > p["pixel_delta"]).mean()) > p["change_fraction"]:
            return "screen"
        idle = (t_ns - self._active_at) / S >= p["idle_after_s"]
        if (t_ns - self._last_at) / S >= (p["idle_heartbeat_s"] if idle else p["heartbeat_s"]):
            return "idle_heartbeat" if idle else "heartbeat"
        return None

    def screen_idle_s(self, now_ns):
        """Seconds since the last capture caused by a change or input (heartbeats do not count); None before the first."""
        return None if self._changed_at is None else max(0.0, (now_ns - self._changed_at) / S)

    def _remember(self, t_ns, context, probe_png, reason):
        self._last_at, self._signature = t_ns, signature(context)
        if probe_png is not None:
            self._picture = picture(probe_png)
        if reason not in ("heartbeat", "idle_heartbeat"):
            self._active_at = t_ns
        if reason in ("context", "input", "screen"):
            self._changed_at = t_ns

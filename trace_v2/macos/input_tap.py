"""Listen-only CGEventTap feeding events.EventBuffer: mouse (moved/dragged, buttons), scroll, keys, modifier changes.

A session tap (kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly) on its own thread and run
loop. It can observe but never alter or block input. Needs Input Monitoring: `CGPreflightListenEventAccess()` is
checked and `CGRequestListenEventAccess()` is never called, so there is no prompt. Without the permission (or if the
tap cannot be created) `start()` returns "unavailable", `reason` says why, and capture goes on without events.

The callback is cheap: it copies a few integer fields into a dict, then either feeds the cursor sampler (moves) or
decodes and hands the event to the buffer. No I/O. Cursor moves are sampled, everything else is stored one-to-one:
- moves/drags: at most one `cursor_move` per `cursor_sample_ms` (default 150, 100..200) plus a final position when the
  cursor stops, each with the number of raw moves it stands for (`coalesced`);
- clicks, scrolls, keys: every event, with its own timestamp; keys keep phase, key_class, modifiers and is_repeat only.
  The keycode is read to choose key_class and dropped; characters are never read.

Timestamps: CGEventGetTimestamp is nanoseconds on the uptime (mach absolute time) clock - measured here, not ticks, so
the timebase is not applied to it; it is converted to wall-clock ns with an offset captured at start (wall now minus
uptime now, the latter being mach_absolute_time() * timebase). Mach time stops during sleep, so a housekeeping timer re-syncs the offset when the
wall and mach clocks disagree by over a second (after wake, or a clock step); `stats()["clock_resyncs"]` counts them.

Privacy: keypresses are passed with secure_input=IsSecureEventInputEnabled() (True when unknown), so the buffer
suppresses and counts them while a password field has focus. `keyboard_blocked` (set by the runner from each context
decision) drops every keyboard event while a gated app is in front; these are counted as `dropped_gated`. Without that
wiring the capture-level gate still withholds the whole capture's events for a gated context.

If macOS disables the tap (timeout or user input) the callback re-enables it and counts `tap_reenabled`.
"""

import ctypes
import threading
import time

import Quartz
from trace_v2.inputs import CursorSampler, MachClock, decode
from trace_v2.inputs.sampling import DEFAULT_SAMPLE_MS
from trace_v2.observations.models import InteractionEvent

from . import session

_TYPES = {
    Quartz.kCGEventMouseMoved: "mouse_moved", Quartz.kCGEventLeftMouseDragged: "mouse_moved",
    Quartz.kCGEventRightMouseDragged: "mouse_moved", Quartz.kCGEventOtherMouseDragged: "mouse_moved",
    Quartz.kCGEventLeftMouseDown: "mouse_down", Quartz.kCGEventRightMouseDown: "mouse_down",
    Quartz.kCGEventOtherMouseDown: "mouse_down", Quartz.kCGEventLeftMouseUp: "mouse_up",
    Quartz.kCGEventRightMouseUp: "mouse_up", Quartz.kCGEventOtherMouseUp: "mouse_up",
    Quartz.kCGEventScrollWheel: "scroll", Quartz.kCGEventKeyDown: "key_down", Quartz.kCGEventKeyUp: "key_up",
    Quartz.kCGEventFlagsChanged: "flags_changed",
}
_BUTTON_OF = {Quartz.kCGEventLeftMouseDown: 0, Quartz.kCGEventLeftMouseUp: 0, Quartz.kCGEventRightMouseDown: 1,
              Quartz.kCGEventRightMouseUp: 1}
_DISABLED = (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput)
_START_TIMEOUT_S = 2.0


def _mask():
    mask = 0
    for event_type in _TYPES:
        mask |= Quartz.CGEventMaskBit(event_type)
    return mask


def _mach_clock():
    class Timebase(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
    libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
    libc.mach_absolute_time.restype = ctypes.c_uint64
    info = Timebase()
    libc.mach_timebase_info(ctypes.byref(info))
    return MachClock(lambda: libc.mach_absolute_time() * info.numer // info.denom, time.time_ns)


def input_monitoring_granted():
    return bool(Quartz.CGPreflightListenEventAccess())       # never CGRequestListenEventAccess: that would prompt


class InputTap:
    def __init__(self, buffer, cursor_sample_ms=DEFAULT_SAMPLE_MS, secure_input=session.secure_input, clock=None):
        self.buffer, self._secure_input = buffer, secure_input
        self.clock = clock or _mach_clock()
        self.sampler = CursorSampler(self._cursor_sample, cursor_sample_ms)
        self.keyboard_blocked = False
        self.state, self.reason = "stopped", None
        self._thread = self._runloop = self._tap = None
        self._counts, self._stored = {}, {}
        self._dropped_gated = self._reenabled = self._errors = self._callback_ns = 0

    # ---- lifecycle ----

    def start(self):
        """-> "running" or "unavailable" (see `reason`). Idempotent while running."""
        if self.state == "running":
            return self.state
        if not input_monitoring_granted():
            self.state, self.reason = "unavailable", "input_monitoring_not_granted"
            return self.state
        ready, failure = threading.Event(), []
        self._thread = threading.Thread(target=self._run, args=(ready, failure), name="trace-v2-input-tap", daemon=True)
        self._thread.start()
        if not ready.wait(_START_TIMEOUT_S) or failure:
            self.state, self.reason = "unavailable", failure[0] if failure else "tap_start_timeout"
            return self.state
        self.state, self.reason = "running", None
        return self.state

    def stop(self):
        if self._runloop is not None:
            Quartz.CFRunLoopStop(self._runloop)
        if self._thread is not None:
            self._thread.join(5.0)
        self._thread = self._runloop = None
        if self.state == "running":
            self.state = "stopped"

    def stats(self):
        return {"state": self.state, "reason": self.reason, "raw": dict(self._counts), "stored": dict(self._stored),
                "cursor_raw_moves": self.sampler.raw_moves, "cursor_samples": self.sampler.samples,
                "cursor_finals": self.sampler.finals, "tap_reenabled": self._reenabled,
                "dropped_gated": self._dropped_gated, "callback_errors": self._errors,
                "callback_cpu_ns": self._callback_ns, "clock_resyncs": self.clock.resyncs}

    # ---- run loop thread ----

    def _run(self, ready, failure):
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                      Quartz.kCGEventTapOptionListenOnly, _mask(), self._callback, None)
        if tap is None:
            failure.append("tap_create_failed")
            ready.set()
            return
        self._tap, self._runloop = tap, Quartz.CFRunLoopGetCurrent()
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopCommonModes)
        period = max(0.05, self.sampler.interval_ns / 2e9)
        timer = Quartz.CFRunLoopTimerCreate(None, Quartz.CFAbsoluteTimeGetCurrent() + period, period, 0, 0,
                                            self._housekeeping, None)
        Quartz.CFRunLoopAddTimer(self._runloop, timer, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        ready.set()
        Quartz.CFRunLoopRun()
        self.sampler.tick(time.time_ns() + 2 * self.sampler.interval_ns)        # flush the final position
        Quartz.CGEventTapEnable(tap, False)
        Quartz.CFRunLoopTimerInvalidate(timer)
        Quartz.CFMachPortInvalidate(tap)

    def _housekeeping(self, timer, info):
        self.sampler.tick(time.time_ns())
        self.clock.check()

    def _callback(self, proxy, event_type, event, refcon):
        started = time.thread_time_ns()
        try:
            self._handle(event_type, event)
        except Exception:                                   # noqa: BLE001 - a bad event must never break the tap
            self._errors += 1
        self._callback_ns += time.thread_time_ns() - started
        return event

    def _handle(self, event_type, event):
        if event_type in _DISABLED:
            Quartz.CGEventTapEnable(self._tap, True)
            self._reenabled += 1
            return
        kind = _TYPES.get(event_type)
        if kind is None:
            return
        self._counts[kind] = self._counts.get(kind, 0) + 1
        ticks, where = Quartz.CGEventGetTimestamp(event), Quartz.CGEventGetLocation(event)
        if kind == "mouse_moved":
            self.sampler.move(self.clock.to_wall_ns(ticks), where.x, where.y)
            return
        if kind in ("key_down", "key_up", "flags_changed") and self.keyboard_blocked:
            self._dropped_gated += 1
            return
        decoded = decode(self._raw(kind, event_type, event, ticks, where), self.clock)
        if decoded is not None:
            self._add(decoded)

    def _raw(self, kind, event_type, event, ticks, where):
        field = Quartz.CGEventGetIntegerValueField
        raw = {"type": kind, "ticks": ticks, "x": where.x, "y": where.y}
        if kind in ("mouse_down", "mouse_up"):
            raw["button"] = _BUTTON_OF.get(event_type, field(event, Quartz.kCGMouseEventButtonNumber))
            raw["click_count"] = field(event, Quartz.kCGMouseEventClickState)
        elif kind == "scroll":
            raw["continuous"] = bool(field(event, Quartz.kCGScrollWheelEventIsContinuous))
            axes = ((Quartz.kCGScrollWheelEventPointDeltaAxis2, Quartz.kCGScrollWheelEventPointDeltaAxis1)
                    if raw["continuous"] else (Quartz.kCGScrollWheelEventDeltaAxis2, Quartz.kCGScrollWheelEventDeltaAxis1))
            raw["dx"], raw["dy"] = field(event, axes[0]), field(event, axes[1])
            raw["scroll_phase"] = field(event, Quartz.kCGScrollWheelEventScrollPhase)
            raw["momentum_phase"] = field(event, Quartz.kCGScrollWheelEventMomentumPhase)
        else:
            raw["keycode"] = field(event, Quartz.kCGKeyboardEventKeycode)    # read for key_class only, never stored
            raw["flags"] = Quartz.CGEventGetFlags(event)
            raw["autorepeat"] = bool(field(event, Quartz.kCGKeyboardEventAutorepeat))
        return raw

    def _cursor_sample(self, t_ns, payload):
        self._add(InteractionEvent("cursor_move", t_ns, payload))

    def _add(self, event):
        secure = self._secure_input() if event.type == "keypress" else False
        if self.buffer.add(event, secure_input=secure):
            self._stored[event.type] = self._stored.get(event.type, 0) + 1

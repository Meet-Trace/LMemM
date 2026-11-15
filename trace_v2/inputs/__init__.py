"""Platform-independent parts of Trace V2 input capture: cursor sampling, key classes, mach-to-wall time, and the
decoder from a plain raw-event dict to an allow-listed InteractionEvent. No pyobjc: the macOS tap (trace_v2.macos.
input_tap) only reads CGEvents into that dict and feeds the results to events.EventBuffer.

Public interface:
    CursorSampler(interval_ns, emit)    at most one cursor_move per interval while moving, plus a final position
    MachClock(...)                      CGEvent uptime timestamp -> wall-clock ns, re-synced after sleep
    decode(raw, clock)                  raw event dict -> InteractionEvent | None
    key_class(keycode)                  coarse class of a macOS virtual keycode; the keycode itself is never stored
"""

from .decode import decode
from .keys import key_class, modifiers_from_flags
from .sampling import CursorSampler, MachClock

__all__ = ["CursorSampler", "MachClock", "decode", "key_class", "modifiers_from_flags"]

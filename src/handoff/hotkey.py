"""A configurable global hotkey for the handoff panel (Carbon RegisterEventHotKey).

UNVERIFIED ON A MAC. `parse()` and `describe()` are pure and tested; `Hotkey.register()` needs macOS.
It shares the process with the ⌃⌥N note hotkey, so its handler checks the key id and ignores presses
that are not its own.
"""

import ctypes
import re
from ctypes import CFUNCTYPE, POINTER, Structure, byref, c_int32, c_uint32, c_void_p

DEFAULT = "ctrl+opt+h"
SIGNATURE = "LMhf"
KEY_ID = 2                                    # the note hotkey uses id 1

# kVK_ANSI_* for letters and digits
_KEYS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
         "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
         "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
         "n": 45, "m": 46, "space": 49}
_MODS = {"cmd": 256, "command": 256, "shift": 512, "opt": 2048, "option": 2048, "alt": 2048,
         "ctrl": 4096, "control": 4096}
_SYMBOLS = [(4096, "⌃"), (2048, "⌥"), (512, "⇧"), (256, "⌘")]

CONFLICT, FAILED = "conflict", "failed"
EVENT_EXISTS_ERR = -9878                      # eventHotKeyExistsErr: another app owns this combo


def parse(spec):
    """'ctrl+opt+h' -> (keycode, carbon modifiers). Raises ValueError for anything unusable
    (no modifier, unknown key): a bare letter would hijack typing."""
    parts = [p for p in re.split(r"[+\s-]+", (spec or "").lower().strip()) if p]
    if not parts:
        raise ValueError("empty shortcut")
    key, mods = parts[-1], 0
    for p in parts[:-1]:
        if p not in _MODS:
            raise ValueError(f"unknown modifier {p!r}")
        mods |= _MODS[p]
    if key not in _KEYS:
        raise ValueError(f"unknown key {key!r}")
    if not mods & (4096 | 2048 | 256):
        raise ValueError("a shortcut needs Control, Option or Command")
    return _KEYS[key], mods


def describe(spec):
    code, mods = parse(spec)
    key = next(k for k, v in _KEYS.items() if v == code)
    return "".join(sym for bit, sym in _SYMBOLS if mods & bit) + ("Space" if key == "space" else key.upper())


class Hotkey:
    """Register once; `status` is None until registered, then 'ok', 'conflict' or 'failed'."""
    def __init__(self, callback, spec=DEFAULT):
        self.callback, self.spec, self.status, self.error = callback, spec, None, ""
        self._keep = []

    def register(self):
        try:
            code, mods = parse(self.spec)
        except ValueError as e:
            self.status, self.error = FAILED, str(e)
            return self.status
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

        class IdSpec(Structure):
            _fields_ = [("signature", c_uint32), ("id", c_uint32)]

        class EventSpec(Structure):
            _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]
        fourcc = lambda s: int.from_bytes(s.encode(), "big")
        handler_t = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
        carbon.GetApplicationEventTarget.restype = c_void_p
        carbon.InstallEventHandler.argtypes = [c_void_p, handler_t, c_uint32, POINTER(EventSpec), c_void_p, c_void_p]
        carbon.InstallEventHandler.restype = c_int32
        carbon.RegisterEventHotKey.argtypes = [c_uint32, c_uint32, IdSpec, c_void_p, c_uint32, POINTER(c_void_p)]
        carbon.RegisterEventHotKey.restype = c_int32
        carbon.GetEventParameter.argtypes = [c_void_p, c_uint32, c_uint32, c_void_p, c_uint32, c_void_p, c_void_p]
        carbon.GetEventParameter.restype = c_int32

        def handler(_call, event, _data):
            hk = IdSpec()
            ok = carbon.GetEventParameter(event, fourcc("----"), fourcc("hkid"), None, ctypes.sizeof(hk), None, byref(hk))
            if ok != 0 or hk.signature != fourcc(SIGNATURE) or hk.id != KEY_ID:
                return -9874                          # eventNotHandledErr: not ours, let the next handler have it
            try:
                self.callback()
            except Exception as e:
                print(f"  ! handoff hotkey: {e}", flush=True)
            return 0
        cb = handler_t(handler)
        self._keep.append(cb)
        target = carbon.GetApplicationEventTarget()
        spec = EventSpec(fourcc("keyb"), 5)
        if carbon.InstallEventHandler(target, cb, 1, byref(spec), None, None) != 0:
            self.status, self.error = FAILED, "Couldn't install the shortcut handler."
            return self.status
        ref = c_void_p()
        err = carbon.RegisterEventHotKey(code, mods, IdSpec(fourcc(SIGNATURE), KEY_ID), target, 0, byref(ref))
        self._keep.append(ref)
        if err == 0:
            self.status = "ok"
        elif err == EVENT_EXISTS_ERR:
            self.status, self.error = CONFLICT, f"{describe(self.spec)} is already used by another app."
        else:
            self.status, self.error = FAILED, f"Couldn't register {describe(self.spec)} (error {err})."
        return self.status

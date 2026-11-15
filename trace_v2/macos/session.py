"""Session-level state: screen lock and secure keyboard entry."""

import ctypes

import Quartz

LOCK_SCREEN_BUNDLES = frozenset({"com.apple.loginwindow", "com.apple.ScreenSaver.Engine"})


def screen_locked(frontmost_bundle_id):
    """True when the session reports a locked screen, or the lock/screensaver process is frontmost."""
    session = Quartz.CGSessionCopyCurrentDictionary()
    if session is None:                          # no console session: nobody is looking at a desktop
        return True
    return bool(session.get("CGSSessionScreenIsLocked", False)) or frontmost_bundle_id in LOCK_SCREEN_BUNDLES


def secure_input():
    """True when secure keyboard entry is on (a password field has focus) or when that cannot be determined."""
    try:
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        probe = carbon.IsSecureEventInputEnabled
        probe.restype = ctypes.c_bool
        return bool(probe())
    except (OSError, AttributeError):
        return True

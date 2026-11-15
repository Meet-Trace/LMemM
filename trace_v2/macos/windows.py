"""Frontmost application and its front on-screen window (metadata only, never pixels)."""

import AppKit
import Quartz


class NoFrontmostApplication(RuntimeError):
    pass


def frontmost_application():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        raise NoFrontmostApplication("NSWorkspace reports no frontmost application")
    return {"bundle_id": app.bundleIdentifier(), "name": app.localizedName(), "pid": int(app.processIdentifier())}


def front_window(pid):
    """The front-most on-screen layer-0 window of `pid`, or None. The list is ordered front to back.
    `title` is empty unless Screen Recording is granted."""
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if info.get(Quartz.kCGWindowOwnerPID) != pid or info.get(Quartz.kCGWindowLayer) != 0:
            continue
        b = info.get(Quartz.kCGWindowBounds) or {}
        if not b.get("Width") or not b.get("Height") or info.get(Quartz.kCGWindowAlpha, 1) == 0:
            continue
        return {"id": int(info[Quartz.kCGWindowNumber]), "title": str(info.get(Quartz.kCGWindowName) or ""),
                "bounds": [b["X"], b["Y"], b["Width"], b["Height"]]}
    return None

"""LMemM - everything that talks to macOS: the front window, browser tab, displays,
screenshots, input counters, pointer, workspace notifications.

All cheap: the front window is one CGWindowList call (~0.2 ms); browser URLs come
from in-process AppleScript compiled once; idle/input counters are system counters
(seconds since the last key / scroll / click, never which key).
"""

import os
import subprocess
import sys
import tempfile

try:
    import objc
    import Foundation
    from AppKit import NSEvent, NSObject, NSScreen, NSWorkspace
except ImportError:
    sys.exit("Missing dependency. Run:  python3 -m pip install -r requirements.txt")

import config
from memory_content import pick_window

_CG = {}


def cg():
    if not _CG:
        b = objc.loadBundle("CoreGraphics", {},
                            bundle_path="/System/Library/Frameworks/CoreGraphics.framework")
        objc.loadBundleFunctions(b, _CG, [
            ("CGWindowListCopyWindowInfo", b"@II"),
            ("CGEventSourceSecondsSinceLastEventType", b"dII"),
            ("CGPreflightScreenCaptureAccess", b"Z"),
            ("CGRequestScreenCaptureAccess", b"Z"),
        ])
    return _CG


def screen_recording_allowed(request=True):
    c = cg()
    if c["CGPreflightScreenCaptureAccess"]():
        return True
    if request:
        c["CGRequestScreenCaptureAccess"]()
    return False


def idle_seconds(event_type=0xFFFFFFFF):
    """Seconds since the last input event (HID system state); default = any type."""
    return cg()["CGEventSourceSecondsSinceLastEventType"](1, event_type)


def input_ages():
    """Seconds since the last key press, scroll, and click. Counters only."""
    return {
        "key": round(idle_seconds(10), 1),                                  # keyDown
        "scroll": round(idle_seconds(22), 1),                               # scrollWheel
        "click": round(min(idle_seconds(1), idle_seconds(3)), 1),           # left/right mouseDown
    }


def pointer_on(display_frame):
    """Pointer position at capture, in points from the captured display's top-left."""
    p = NSEvent.mouseLocation()
    f = display_frame
    x, y = p.x - f.origin.x, (f.origin.y + f.size.height) - p.y
    if 0 <= x < f.size.width and 0 <= y < f.size.height:
        return {"x": int(x), "y": int(y)}
    return None


def front():
    """Frontmost app + its front window: name, bundle id, pid, window id, title, bounds."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    pid = app.processIdentifier()
    mine = [w for w in cg()["CGWindowListCopyWindowInfo"](1 | 16, 0) or []     # on-screen, front to back
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0]
    win = pick_window(mine)
    return {
        "app": app.localizedName(),
        "bundle_id": app.bundleIdentifier(),
        "pid": pid,
        "win_id": win.get("kCGWindowNumber") if win else None,
        "window": (win.get("kCGWindowName") or None) if win else None,
        "bounds": dict(win.get("kCGWindowBounds")) if win else None,
    }


_SCRIPTS = {}


def browser_info(app):
    """(URL, tab title, private?) of the front tab, or (None, None, False)."""
    if app not in config.BROWSERS:
        return None, None, False
    if app not in _SCRIPTS:
        if app == "Safari":
            src = 'tell application "Safari" to get {URL, name} of front document'
        else:
            src = (f'tell application "{app}" to get {{URL, title}} of active tab of front window'
                   f' & {{mode of front window}}')
        _SCRIPTS[app] = Foundation.NSAppleScript.alloc().initWithSource_(src)
    res, _err = _SCRIPTS[app].executeAndReturnError_(None)
    if res is None:
        return None, None, False
    vals = [res.descriptorAtIndex_(i).stringValue() for i in range(1, res.numberOfItems() + 1)]
    url, title = vals[0], vals[1] if len(vals) > 1 else None
    private = len(vals) > 2 and vals[2] == "incognito"
    return url, title, private


def display_for(bounds):
    """(1-based `screencapture -D` index, NSScreen frame) of the display holding the window."""
    screens = NSScreen.screens()
    if not bounds or len(screens) < 2:
        return 1, screens[0].frame()
    main_h = screens[0].frame().size.height
    cx = bounds["X"] + bounds["Width"] / 2
    cy = main_h - (bounds["Y"] + bounds["Height"] / 2)      # CG top-left -> Cocoa bottom-left
    for i, s in enumerate(screens):
        f = s.frame()
        if f.origin.x <= cx < f.origin.x + f.size.width and f.origin.y <= cy < f.origin.y + f.size.height:
            return i + 1, f
    return 1, screens[0].frame()


def display_bounds(display):
    """A display's frame in CG (top-left origin) coordinates, like window bounds."""
    screens = NSScreen.screens()
    frame = screens[display - 1].frame()
    return {"X": frame.origin.x,
            "Y": screens[0].frame().size.height - frame.origin.y - frame.size.height,
            "Width": frame.size.width, "Height": frame.size.height}


class Frame:
    """One captured screen, held in memory. Nothing touches the disk unless it's kept.

    cg      the CGImage Vision reads
    gray    int16 luminance array, for the pixel diff
    rgba    the pixels (kept until the frame is saved or dropped)
    """

    def __init__(self, cg, rgba, width, height):
        import numpy as np
        self.cg, self.rgba, self.width, self.height = cg, rgba, width, height
        px = np.frombuffer(rgba, np.uint8).reshape(height, width, 4)
        weighted = (px[..., 0].astype(np.int32) * 299 + px[..., 1].astype(np.int32) * 587
                    + px[..., 2].astype(np.int32) * 114)             # int32: 255 * 1000 overflows int16
        self.gray = (weighted // 1000).astype(np.int16)              # same weights as PIL's "L"

    def image(self, long_edge=None):
        """A PIL RGB image of the frame, optionally shrunk so its long edge is `long_edge`."""
        import numpy as np
        from PIL import Image
        img = Image.fromarray(np.frombuffer(self.rgba, np.uint8).reshape(self.height, self.width, 4)[..., :3])
        if long_edge and max(img.size) > long_edge:
            img.thumbnail((long_edge, long_edge), Image.LANCZOS)
        return img

    def save(self, path, long_edge=None, quality=70):
        self.image(long_edge).save(path, quality=quality)

    def release(self):
        self.cg = self.rgba = self.gray = None


def _frame_from_cg(cg):
    import Quartz
    w, h = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    if max(w, h) > config.MAX_PX:                     # very large displays: scale down
        scale = config.MAX_PX / max(w, h)
        w, h = round(w * scale), round(h * scale)
    buf = bytearray(w * h * 4)
    ctx = Quartz.CGBitmapContextCreate(
        buf, w, h, 8, w * 4, Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB),
        Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cg)
    return Frame(Quartz.CGBitmapContextCreateImage(ctx), buf, w, h)


def grab(display):
    """The display as an in-memory Frame (~25 ms), or None if the OS won't give it to us.
    Uses the screen-image API at nominal (1x) resolution, so no resize step is needed."""
    try:
        import Quartz
        b = display_bounds(display)
        cg = Quartz.CGWindowListCreateImage(
            Quartz.CGRectMake(b["X"], b["Y"], b["Width"], b["Height"]),
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageNominalResolution | Quartz.kCGWindowImageBoundsIgnoreFraming)
        return _frame_from_cg(cg) if cg is not None else None
    except Exception:
        return None


STRIP_MIN = 360         # points: a browser's tabs, address and bookmarks bars take ~160 before the page starts


def grab_strip(bounds, height):
    """The top of a window as a CGImage (~5 ms), or None; at least STRIP_MIN points so the
    page's own header (a chat's name) is in it even under a browser's toolbars. Not clipped to the
    window's height: front() can pick a short helper window of the app (a 159 pt toolbar strip). For the quick
    "which chat is open?" read, where the whole screen would be wasted work."""
    try:
        import Quartz
        rect = Quartz.CGRectMake(bounds["X"], bounds["Y"], bounds["Width"], max(height, STRIP_MIN))
        return Quartz.CGWindowListCreateImage(
            rect, Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageNominalResolution | Quartz.kCGWindowImageBoundsIgnoreFraming)
    except Exception:
        return None


def grab_with_screencapture(display):
    """Fallback if the in-process API is unavailable: the old subprocess route, still
    returned as an in-memory Frame (the temp file is deleted at once)."""
    import Quartz
    path = os.path.join(tempfile.mkdtemp(prefix="lmemm-"), "frame.png")
    try:
        subprocess.run(["screencapture", "-x", "-o", "-t", "png", "-D", str(display), path], check=False)
        if not os.path.exists(path):
            return None
        source = Quartz.CGImageSourceCreateWithURL(Foundation.NSURL.fileURLWithPath_(path), None)
        cg = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None) if source else None
        return _frame_from_cg(cg) if cg is not None else None
    finally:
        try:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass


def notify(title, message):
    """A macOS notification banner. Fire and forget; never blocks the tracker."""
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(["osascript", "-e",
                          f'display notification "{esc(message)}" with title "{esc(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ---------------------------------------------------------------- system events

class Events(NSObject):
    """Receives NSWorkspace / distributed notifications on the main run loop and
    forwards them to a handler with .trigger(why) and .set_flag(name, on)."""

    def initWithHandler_(self, handler):
        self = objc.super(Events, self).init()
        self.handler = handler
        return self

    def appSwitched_(self, note):
        self.handler.trigger("app_switch")

    def willSleep_(self, note):
        self.handler.set_flag("asleep", True)

    def didWake_(self, note):
        self.handler.set_flag("asleep", False)

    def screensSlept_(self, note):
        self.handler.set_flag("display_off", True)

    def screensWoke_(self, note):
        self.handler.set_flag("display_off", False)

    def locked_(self, note):
        self.handler.set_flag("locked", True)

    def unlocked_(self, note):
        self.handler.set_flag("locked", False)


def subscribe(handler):
    events = Events.alloc().initWithHandler_(handler)
    nc = NSWorkspace.sharedWorkspace().notificationCenter()
    for sel, name in [
        ("appSwitched:", "NSWorkspaceDidActivateApplicationNotification"),
        ("willSleep:", "NSWorkspaceWillSleepNotification"),
        ("didWake:", "NSWorkspaceDidWakeNotification"),
        ("screensSlept:", "NSWorkspaceScreensDidSleepNotification"),
        ("screensWoke:", "NSWorkspaceScreensDidWakeNotification"),
    ]:
        nc.addObserver_selector_name_object_(events, sel, name, None)
    dc = Foundation.NSDistributedNotificationCenter.defaultCenter()
    dc.addObserver_selector_name_object_(events, "locked:", "com.apple.screenIsLocked", None)
    dc.addObserver_selector_name_object_(events, "unlocked:", "com.apple.screenIsUnlocked", None)
    return events


def next_event(app, timeout=0.25):
    """Deliver one pending AppKit event (hotkey, note window) and run-loop notifications."""
    ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
        0xFFFFFFFFFFFFFFFF, Foundation.NSDate.dateWithTimeIntervalSinceNow_(timeout),
        Foundation.NSDefaultRunLoopMode, True)
    if ev is not None:
        app.sendEvent_(ev)


def wake(app):
    """Wake the main loop out of next_event() from any thread, so it runs a tick now."""
    ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        15, (0, 0), 0, 0, 0, None, 0, 0, 0)               # NSEventTypeApplicationDefined
    app.postEvent_atStart_(ev, True)


def quiet_system_logs():
    """macOS input-method frameworks NSLog noise straight to the terminal whenever a text
    box gets focus. Send the raw stderr fd to /dev/null; keep Python's errors visible."""
    try:
        real = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        sys.stderr = os.fdopen(real, "w", buffering=1)
    except OSError:
        pass

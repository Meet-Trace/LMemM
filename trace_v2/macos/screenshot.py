"""Lossless full-resolution screenshot of one display, as PNG.

Path 1 (preferred, macOS 14+): ScreenCaptureKit SCScreenshotManager. Path 2, CoreGraphics (CGWindowListCreateImage /
...FromArray), is used when SCK is missing or has just timed out (3 s), for SCK_BACKOFF_S: on a Mac SCK hung while a model
ran and every capture waited 20 s. Both paths honour the scope. The PyObjC ScreenCaptureKit wrapper package is
optional and absent here, so the framework is loaded with objc.loadBundle and the two completion-handler block
signatures are registered by hand (`_sck_blocks`).
`ScreenshotSource.last_path` records which one produced the latest image ("screencapturekit" | "coregraphics").

Never prompts: Screen Recording must already be granted (CGPreflightScreenCaptureAccess) or PermissionError is
raised. No downscaling and the cursor is not drawn. The PNG's size is the display's pixel size.

Scope (what the pixels may contain):
    "front_app" (default)  SCContentFilter(display, includingApplications=[the front app], exceptingWindows=[]): only the
                           windows of the application the privacy gates judged are composited, so other apps' windows,
                           notification banners, Notification Center and the Dock are not in the image. The canvas is
                           still the full display size; everything that is not the front app is left as the stream's
                           background. Needs the front app's pid (from the context); if SCK has no running application
                           for it, ScopeUnavailable is raised and the pipeline withholds the screenshot (never a silent
                           fall-back to the whole display). Path 2 composites only that pid's on-screen layer-0 windows
                           (CGWindowListCreateImageFromArray); none is the same ScopeUnavailable.
    "display"              the whole display: every visible window and banner on it. Weaker privacy: the gates judge only
                           the front app.
"""

import threading
import time

import objc
import Quartz
from trace_v2.observations import ScopeUnavailable, ScreenshotCapture
from trace_v2.observations.settings import DEFAULT_SCREENSHOT_SCOPE, SCREENSHOT_SCOPES

from . import displays

TIMEOUT_S = 3.0                      # ScreenCaptureKit answers in ~50 ms; on a Mac it has hung instead (every call 10+ s)
SCK_BACKOFF_S = 600.0                # after a hang, use the CoreGraphics path for this long before trying SCK again
_PNG = "public.png"
PROBE_WIDTH = 96


def _load_sck():
    namespace = {}
    objc.loadBundle("ScreenCaptureKit", namespace,
                    bundle_path="/System/Library/Frameworks/ScreenCaptureKit.framework")
    return {n: objc.lookUpClass(n) for n in ("SCShareableContent", "SCScreenshotManager", "SCContentFilter",
                                             "SCStreamConfiguration")}


def _sck_blocks():
    def block(*arg_types):
        return {"callable": {"retval": {"type": b"v"},
                             "arguments": {i: {"type": t} for i, t in enumerate((b"^v",) + arg_types)}}}
    objc.registerMetaDataForSelector(
        b"SCShareableContent", b"getShareableContentExcludingDesktopWindows:onScreenWindowsOnly:completionHandler:",
        {"arguments": {4: block(b"@", b"@")}})
    objc.registerMetaDataForSelector(
        b"SCScreenshotManager", b"captureImageWithFilter:configuration:completionHandler:",
        {"arguments": {4: block(b"^{CGImage=}", b"@")}})


def _await(start, what):
    """Run start(callback) and wait for callback(value, error); returns value or raises RuntimeError."""
    done, box = threading.Event(), {}

    def callback(value, error):
        box["value"], box["error"] = value, error
        done.set()
    start(callback)
    if not done.wait(TIMEOUT_S):
        raise TimeoutError(f"{what} did not complete in {TIMEOUT_S}s")
    if box["error"] is not None or box["value"] is None:
        raise RuntimeError(f"{what} failed: {box['error']}")
    return box["value"]


def encode_png(cg_image):
    import Foundation
    data = Foundation.NSMutableData.alloc().init()
    dest = Quartz.CGImageDestinationCreateWithData(data, _PNG, 1, None)
    if dest is None:
        raise RuntimeError("could not create a PNG encoder")
    Quartz.CGImageDestinationAddImage(dest, cg_image, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError("PNG encoding failed")
    return bytes(data)


class ScreenshotSource:
    def __init__(self, clock_ns=time.time_ns):
        self.clock_ns = clock_ns
        self.last_path = None
        try:
            self._sck = _load_sck()
            _sck_blocks()
        except (objc.error, ImportError, KeyError):
            self._sck = None
        self._sck_stuck_until = 0.0

    def _image(self, display_id, pixels, scope, pid, bundle_id):
        """ScreenCaptureKit, or CoreGraphics when SCK is missing or has just hung (it did on a Mac, once a model ran,
        and every capture waited 20 s). Both honour the scope: front_app composites only that app's windows."""
        if self._sck and time.monotonic() >= self._sck_stuck_until:
            try:
                return self._capture_sck(display_id, pixels, scope, pid, bundle_id)
            except TimeoutError:
                self._sck_stuck_until = time.monotonic() + SCK_BACKOFF_S
        return self._capture_cg(display_id, scope, pid)

    def screenshot(self, display_id=None, scope=DEFAULT_SCREENSHOT_SCOPE, pid=None, bundle_id=None):
        """-> ScreenshotCapture of `display_id` (default: the main display), limited to `scope` (see the module doc).
        `pid` / `bundle_id` identify the front application for the front_app scope."""
        if scope not in SCREENSHOT_SCOPES:
            raise ValueError(f"unknown screenshot scope {scope!r}")
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionError("Screen Recording is not granted")
        display_id = int(display_id or Quartz.CGMainDisplayID())
        pixels = displays.display_info(display_id)["pixel_size"]
        t = self.clock_ns()
        image = self._image(display_id, pixels, scope, pid, bundle_id)
        width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
        return ScreenshotCapture(encode_png(image), "png", width, height, t, scope)

    def probe(self, display_id=None, scope=DEFAULT_SCREENSHOT_SCOPE, pid=None, bundle_id=None, width=PROBE_WIDTH):
        """-> PNG bytes of a tiny picture (`width` pixels wide) of the same region a full screenshot would show.
        Only for telling whether the screen changed; never stored. Same permission and scope rules as screenshot()."""
        if scope not in SCREENSHOT_SCOPES:
            raise ValueError(f"unknown screenshot scope {scope!r}")
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionError("Screen Recording is not granted")
        display_id = int(display_id or Quartz.CGMainDisplayID())
        full = displays.display_info(display_id)["pixel_size"]
        size = (width, max(1, round(width * full[1] / full[0])))
        return encode_png(_scaled(self._image(display_id, size, scope, pid, bundle_id), size))

    @staticmethod
    def _front_application(content, pid, bundle_id):
        """The SCRunningApplication of the front app: by pid, else by bundle id when that identifies exactly one."""
        apps = list(content.applications())
        by_pid = [a for a in apps if pid is not None and int(a.processID()) == int(pid)]
        if by_pid:
            return by_pid[0]
        by_bundle = [a for a in apps if bundle_id and a.bundleIdentifier() == bundle_id]
        return by_bundle[0] if len(by_bundle) == 1 else None

    def _capture_sck(self, display_id, pixels, scope="display", pid=None, bundle_id=None):
        sck = self._sck
        content = _await(lambda cb: sck["SCShareableContent"]
                         .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                             True, True, cb), "SCShareableContent")
        match = [d for d in content.displays() if d.displayID() == display_id]
        if not match:
            raise LookupError(f"display {display_id} is not shareable")
        config = sck["SCStreamConfiguration"].alloc().init()
        config.setWidth_(pixels[0])
        config.setHeight_(pixels[1])
        config.setShowsCursor_(False)
        if scope == "front_app":
            app = self._front_application(content, pid, bundle_id)
            if app is None:
                raise ScopeUnavailable("the front application has no shareable windows on this display")
            flt = sck["SCContentFilter"].alloc().initWithDisplay_includingApplications_exceptingWindows_(
                match[0], [app], [])
        else:
            flt = sck["SCContentFilter"].alloc().initWithDisplay_excludingWindows_(match[0], [])
        image = _await(lambda cb: sck["SCScreenshotManager"]
                       .captureImageWithFilter_configuration_completionHandler_(flt, config, cb), "SCScreenshotManager")
        self.last_path = "screencapturekit"
        return image

    def _capture_cg(self, display_id, scope="display", pid=None):
        """CoreGraphics capture of the display. front_app: only the on-screen windows of `pid` are composited (the
        same privacy as the SCK filter); no such window is ScopeUnavailable, never a wider picture."""
        rect = Quartz.CGDisplayBounds(display_id)
        options = Quartz.kCGWindowImageBoundsIgnoreFraming
        if scope == "front_app":
            windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
            ids = [w["kCGWindowNumber"] for w in windows
                   if pid is not None and int(w.get("kCGWindowOwnerPID", -1)) == int(pid) and w.get("kCGWindowLayer") == 0]
            if not ids:
                raise ScopeUnavailable("the front application has no on-screen windows")
            image = Quartz.CGWindowListCreateImageFromArray(rect, ids, options)
        else:
            image = Quartz.CGWindowListCreateImage(rect, Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID, options)
        if image is None:
            raise RuntimeError("CoreGraphics returned no image")
        self.last_path = "coregraphics"
        return image


def _scaled(image, size):
    """`image` drawn at `size` pixels (the probe is tiny; SCK already returns that size, CoreGraphics does not)."""
    w, h = int(size[0]), int(size[1])
    if Quartz.CGImageGetWidth(image) == w and Quartz.CGImageGetHeight(image) == h:
        return image
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
                                       Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), image)
    return Quartz.CGBitmapContextCreateImage(ctx)

"""Lossless full-resolution screenshot of one display, as PNG.

Path 1 (preferred, macOS 14+): ScreenCaptureKit SCScreenshotManager. The PyObjC ScreenCaptureKit wrapper package is
optional and absent here, so the framework is loaded with objc.loadBundle and the two completion-handler block
signatures are registered by hand (`_sck_blocks`). Path 2: CGDisplayCreateImage, only when SCK cannot be loaded.
`ScreenshotSource.last_path` records which one produced the latest image ("screencapturekit" | "cgdisplay").

Never prompts: Screen Recording must already be granted (CGPreflightScreenCaptureAccess) or PermissionError is
raised. No downscaling and the cursor is not drawn. The PNG's size is the display's pixel size.
"""

import threading
import time

import objc
import Quartz
from trace_v2.observations import ScreenshotCapture

from . import displays

TIMEOUT_S = 10.0
_PNG = "public.png"


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

    def screenshot(self, display_id=None):
        """-> ScreenshotCapture of `display_id` (default: the main display)."""
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionError("Screen Recording is not granted")
        display_id = int(display_id or Quartz.CGMainDisplayID())
        pixels = displays.display_info(display_id)["pixel_size"]
        t = self.clock_ns()
        image = self._capture_sck(display_id, pixels) if self._sck else self._capture_cgdisplay(display_id)
        width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
        return ScreenshotCapture(encode_png(image), "png", width, height, t)

    def _capture_sck(self, display_id, pixels):
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
        flt = sck["SCContentFilter"].alloc().initWithDisplay_excludingWindows_(match[0], [])
        image = _await(lambda cb: sck["SCScreenshotManager"]
                       .captureImageWithFilter_configuration_completionHandler_(flt, config, cb), "SCScreenshotManager")
        self.last_path = "screencapturekit"
        return image

    def _capture_cgdisplay(self, display_id):
        image = Quartz.CGDisplayCreateImage(display_id)
        if image is None:
            raise RuntimeError("CGDisplayCreateImage returned no image")
        self.last_path = "cgdisplay"
        return image

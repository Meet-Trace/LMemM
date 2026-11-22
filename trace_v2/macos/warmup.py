"""Vision warm-up: the first text recognition on a machine can take over a minute (model compilation, measured 66 s on a
fresh macOS install), which the 10 s capture timeout would record as `Timeout`. warm_up renders a small image of text
in memory and recognises it with a long timeout, before the first capture."""

import time

import AppKit
import Foundation

from .ocr import VisionOCR

WARMUP_TIMEOUT_S = 120.0
_TEXT = "Trace warm-up 2468"


def render_png(width=360, height=90):
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(context)
    try:
        AppKit.NSColor.whiteColor().set()
        AppKit.NSRectFill(Foundation.NSMakeRect(0, 0, width, height))
        attributes = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(32.0),
                      AppKit.NSForegroundColorAttributeName: AppKit.NSColor.blackColor()}
        Foundation.NSString.stringWithString_(_TEXT).drawAtPoint_withAttributes_((16, 28), attributes)
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    return bytes(rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))


def warm_up(timeout_s=WARMUP_TIMEOUT_S):
    """-> seconds taken. Raises (Timeout, ...) if recognition fails; the runner records that and goes on."""
    from trace_v2.observations.models import ScreenshotCapture
    started = time.monotonic()
    png = render_png()
    VisionOCR(timeout_s=timeout_s).read(ScreenshotCapture(png, "png", 360, 90, time.time_ns()), None)
    return time.monotonic() - started

"""Synthetic screenshots for the macOS OCR tests: text drawn with AppKit into an off-screen bitmap, as PNG bytes.
macOS only (imports pyobjc). Nothing is read from the screen."""

import AppKit
import Foundation


def render_png(lines, width=1400, height=500, font_size=34.0, dark=False, font_name="Menlo", margin=40.0,
               leading=1.6):
    """`lines` are drawn top to bottom, one per row; returns PNG bytes of a width x height image (1 pixel = 1 point)."""
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(context)
    try:
        background = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.11 if dark else 1.0, 1.0)
        foreground = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.93 if dark else 0.05, 1.0)
        background.set()
        AppKit.NSRectFill(Foundation.NSMakeRect(0, 0, width, height))
        font = AppKit.NSFont.fontWithName_size_(font_name, font_size) or AppKit.NSFont.systemFontOfSize_(font_size)
        attributes = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: foreground}
        y = height - margin - font_size
        for line in lines:
            Foundation.NSString.stringWithString_(line).drawAtPoint_withAttributes_((margin, y), attributes)
            y -= font_size * leading
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    return bytes(rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))

"""Display geometry from CoreGraphics. Bounds are in points (global, origin top-left of the main display)."""

import Quartz


def _rect(r):
    return [r.origin.x, r.origin.y, r.size.width, r.size.height]


def display_info(display_id):
    """{id, bounds [x,y,w,h] points, pixel_size [w,h], scale}: pixel size of the current mode, not a guess."""
    bounds = Quartz.CGDisplayBounds(display_id)
    mode = Quartz.CGDisplayCopyDisplayMode(display_id)
    pixel_w, pixel_h = Quartz.CGDisplayModeGetPixelWidth(mode), Quartz.CGDisplayModeGetPixelHeight(mode)
    return {"id": int(display_id), "bounds": _rect(bounds), "pixel_size": [int(pixel_w), int(pixel_h)],
            "scale": pixel_w / bounds.size.width}


def _overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def display_for_rect(rect):
    """The active display sharing the most area with `rect` ([x,y,w,h] points); the main display when none does."""
    err, ids, _count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err or not ids:
        return int(Quartz.CGMainDisplayID())
    best = max(ids, key=lambda d: _overlap(rect, _rect(Quartz.CGDisplayBounds(d))))
    if _overlap(rect, _rect(Quartz.CGDisplayBounds(best))) == 0:
        return int(Quartz.CGMainDisplayID())
    return int(best)

"""LMemM - read only the part of the screen that changed.

A frame of the same window as the last one usually differs in one place: a message arrived, you typed a
line, a sidebar row updated. The pixel diff (`activity.diff`) already says where. Reading just those boxes
and keeping what was read before everywhere else costs a fraction of reading the whole screen, and works
the same in every app because it only looks at pixels.

`plan` decides which boxes to read, or None when the whole screen should be read (a scroll, a big change,
nothing to compare with). `merge` puts the new lines in place of the old ones inside those boxes.
Pure functions: tested on any OS.
"""

import config


def touches(a, b, pad=0):
    return a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad


def _union(a, b):
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    return [x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0]


def plan(change, size, max_share=None, pad=None, max_boxes=3):
    """Boxes [x, y, w, h] in frame pixels to read, or None to read everything."""
    max_share = config.OCR_REGION_MAX_SHARE if max_share is None else max_share
    pad = config.OCR_REGION_PAD if pad is None else pad
    if not change or change.get("scroll") or not change.get("regions"):
        return None
    w, h = size
    boxes = []
    for x, y, bw, bh in change["regions"]:
        x0, y0 = max(0, x - pad), max(0, y - pad)
        boxes.append([x0, y0, min(w, x + bw + pad) - x0, min(h, y + bh + pad) - y0])
    merged = True
    while merged:                                   # touching boxes become one: a line and its neighbour are read together
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if touches(boxes[i], boxes[j], pad):
                    boxes[i] = _union(boxes[i], boxes.pop(j))
                    merged = True
                    break
            if merged:
                break
    if len(boxes) > max_boxes:                      # too scattered to be worth it
        return None
    if sum(b[2] * b[3] for b in boxes) > max_share * w * h:
        return None
    return boxes


def merge(previous, boxes, fresh):
    """The objects after a partial read: everything from `previous` outside the boxes, plus `fresh` (read
    inside them). Text that straddles a box edge is dropped from the old set, since the box re-read it."""
    kept = [o for o in previous if not any(touches(o["box"], b) for b in boxes)]
    out = kept + list(fresh)
    out.sort(key=lambda o: (round(o["box"][1] / 8), o["box"][0]))
    return out

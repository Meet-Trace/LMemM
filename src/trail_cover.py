"""LMemM - does the accessibility text explain the screen? If so, there is nothing to OCR.

No app is special-cased. The test is the same for every window on every Mac:

    1. Look at the pixels. A 16 px cell with strong contrast has "ink" (text, icons, lines).
    2. Look at the accessibility tree. Every node that has a position and a size says
       "I am drawn here": text we read, buttons, images, toolbars we skip on purpose, a small
       input box we do not read.
    3. Ink that no node accounts for is the gap. A terminal, a canvas, a game, a PDF page, an
       app with no accessibility: the gap is large, so the screen is read (OCR).
       A chat, a web page, a document, a settings window: the gap is small, so the text the
       tree already gave us is the answer and the 1-4 s OCR pass is skipped.

When in doubt it says "read the screen": an unsure answer is never a wrong one, only a slower one.
Pure functions on arrays and tuples: tested on any OS.
"""

import re
from collections import Counter

import numpy as np

import config

CELL = 8                 # cell edge on the half-resolution grid (= 16 px on the real screen)
INK = 40                 # max-min grey levels inside a cell for it to count as ink
TITLE_BAR = 40           # points at the top of a window: its title and traffic lights, known from the tree
EDGE = 3                 # points at the window's edge: border, shadow
MIN_GAP_CELLS = 12       # a gap this small is an icon or a divider, never a paragraph
PAD = 1                  # cells added around every rectangle (anti-aliasing, rounding)
MIN_WINDOW_PX = 60


def to_px(rect, display, scale):
    """A screen-points rectangle -> pixels of the captured display."""
    x, y, w, h = rect
    return ((x - display["X"]) * scale, (y - display["Y"]) * scale, w * scale, h * scale)


def _cells(box, grid_shape):
    """Half-res grid cells (row0, row1, col0, col1) a pixel box touches, clipped to the grid."""
    x, y, w, h = box
    r0 = max(0, int(y // 2 // CELL) - PAD)
    r1 = min(grid_shape[0], int((y + h) // 2 // CELL) + 1 + PAD)
    c0 = max(0, int(x // 2 // CELL) - PAD)
    c1 = min(grid_shape[1], int((x + w) // 2 // CELL) + 1 + PAD)
    return r0, r1, c0, c1


def ink_grid(gray, box):
    """Boolean grid of cells with ink inside `box` (pixels), and the grid's origin in pixels."""
    x, y, w, h = [int(v) for v in box]
    x, y = max(0, x), max(0, y)
    x1, y1 = min(gray.shape[1], x + max(0, w)), min(gray.shape[0], y + max(0, h))
    if x1 - x < MIN_WINDOW_PX or y1 - y < MIN_WINDOW_PX:
        return None, (x, y)
    half = gray[y:y1:2, x:x1:2]
    rows, cols = half.shape[0] // CELL, half.shape[1] // CELL
    if rows < 1 or cols < 1:
        return None, (x, y)
    cut = half[:rows * CELL, :cols * CELL].reshape(rows, CELL, cols, CELL)
    return (cut.max(axis=(1, 3)) - cut.min(axis=(1, 3))) > INK, (x, y)


def judge(snap, gray, display, scale, max_gap=None):
    """Can the accessibility snapshot stand in for reading this frame?

    Returns {"ok": bool, "why": str, "ink": cells with ink, "gap": ink cells nothing accounts for}.
    """
    out = {"ok": False, "why": "", "ink": 0, "gap": 0}
    max_gap = config.TRAIL_GAP if max_gap is None else max_gap
    if gray is None or snap is None or not snap.window:
        out["why"] = "no window frame"
        return out
    if snap.truncated or snap.nodes < 2:
        out["why"] = "tree incomplete"            # the walk stopped early: it does not describe everything
        return out
    win = to_px(snap.window, display, scale)
    inset = EDGE * scale
    area = (win[0] + inset, win[1] + inset + TITLE_BAR * scale, win[2] - 2 * inset, win[3] - 2 * inset - TITLE_BAR * scale)
    ink, (ox, oy) = ink_grid(gray, area)
    if ink is None:
        out["why"] = "window too small"
        return out
    out["ink"] = int(ink.sum())
    covered = np.zeros_like(ink)
    for rect, _kind in snap.regions:
        box = to_px(rect, display, scale)
        r0, r1, c0, c1 = _cells((box[0] - ox, box[1] - oy, box[2], box[3]), ink.shape)
        if r1 > r0 and c1 > c0:
            covered[r0:r1, c0:c1] = True
    gap = ink & ~covered
    out["gap"] = int(gap.sum())
    if any(kind == "unread" for _r, kind in snap.regions):
        out["why"] = "a large text box was not read"        # a terminal / editor body: that is the content
        return out
    if not snap.items and out["ink"] > MIN_GAP_CELLS:
        out["why"] = "ink but no text from the tree"
        return out
    allowed = max(MIN_GAP_CELLS, max_gap * out["ink"])
    out["ok"] = out["gap"] <= allowed
    out["why"] = "tree explains the screen" if out["ok"] else "ink the tree does not account for"
    return out


def to_res(snap, meta, size, display, scale, seconds=0.0):
    """A resolver-shaped result built from the tree, so everything downstream reads it like OCR output."""
    import trail_engine
    try:
        import resolver
        entities = resolver.entities
    except SystemExit:                                   # no Vision off a Mac: the names/urls pass is skipped
        entities = lambda text: {}
    w, h = size
    headings = set(snap.headings)
    objs = []
    keep = set(trail_engine.redact([t for t, _ in snap.items]))
    for text, rect in snap.items:
        if text not in keep:
            continue
        x, y, bw, bh = to_px(rect, display, scale)
        if bw <= 0 or bh <= 0 or x + bw < 0 or y + bh < 0 or x > w or y > h:
            continue                                     # scrolled out of the picture
        words = text.split()
        kind = "heading" if text in headings else "paragraph" if len(words) >= 8 else "text"
        o = {"kind": kind, "text": text, "box": [int(x), int(y), int(bw), int(bh)], "conf": 1.0}
        ents = entities(text)
        if ents:
            o["entities"] = ents
        objs.append(o)
    objs.sort(key=lambda o: (round(o["box"][1] / 8), o["box"][0]))
    for i, o in enumerate(objs):
        o["id"] = i
    ents = {}
    for o in objs:
        for k, vs in o.get("entities", {}).items():
            ents.setdefault(k, [])
            ents[k].extend(v for v in vs if v not in ents[k])
    return {
        "ts": meta["ts"], "image": meta["image"], "image_size": {"w": w, "h": h},
        "px_per_point": round(scale, 4),
        "context": {"app": meta.get("app"),
                    "site": re.sub(r"^https?://", "", meta.get("url") or "").split("/")[0] or None,
                    "title": meta.get("tab_title") or meta.get("window")},
        "scene": [], "counts": dict(Counter(o["kind"] for o in objs)), "focus": None,
        "entities": ents, "objects": objs,
        "resolver": {"engine": "accessibility", "fast": True, "seconds": round(seconds, 3)},
    }


def ascii_map(snap, gray, display, scale, cols=78):
    """A small text picture of the verdict, for debugging on a real Mac:
    '#' ink the tree explains, 'X' ink nothing explains, '.' nothing drawn, ' ' outside the window."""
    if gray is None or snap is None or not snap.window:
        return "(no window)"
    win = to_px(snap.window, display, scale)
    ink, (ox, oy) = ink_grid(gray, win)
    if ink is None:
        return "(window too small)"
    covered = np.zeros_like(ink)
    for rect, _kind in snap.regions:
        box = to_px(rect, display, scale)
        r0, r1, c0, c1 = _cells((box[0] - ox, box[1] - oy, box[2], box[3]), ink.shape)
        if r1 > r0 and c1 > c0:
            covered[r0:r1, c0:c1] = True
    step = max(1, -(-ink.shape[1] // cols))
    lines = []
    for r in range(0, ink.shape[0], step * 2):
        row = []
        for c in range(0, ink.shape[1], step):
            blk_i = ink[r:r + step * 2, c:c + step]
            blk_c = covered[r:r + step * 2, c:c + step]
            gap = (blk_i & ~blk_c).any()
            row.append("X" if gap else "#" if blk_i.any() else "." if not blk_c.any() else "+")
        lines.append("".join(row))
    return "\n".join(lines)

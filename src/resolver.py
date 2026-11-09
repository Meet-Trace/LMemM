#!/usr/bin/env python3
"""
LMemM - screenshot -> on-screen objects.

Resolves one screenshot into a structured list of what's on screen: tabs, address
bar, bookmarks, sidebar items, buttons, inputs, headings, text, panels - each with
its text, its box and what it contains (emails, links, phone numbers, dates, people).

On-device through Apple's Vision framework (the OCR behind Live Text): no model
download, nothing leaves the machine.

    resolve(meta_path) -> {"objects": [...], "entities": {...}, "image_size": ..., ...}
"""

import json
import os
import re
import sys
import time
from collections import Counter

try:
    import Foundation
    import Vision
except ImportError:
    sys.exit("Missing dependency. Run:  python3 -m pip install -r requirements.txt")

import config


# ---------------------------------------------------------------- config

FAST_OCR = False        # True = Vision's fast mode: ~3x quicker, worse on small text
MIN_TEXT_CONF = 0.3     # drop OCR lines below this

from config import BROWSERS

# placeholder text inside an empty input field
INPUT_RE = re.compile(r"^(?:\W{1,3}|[QO] )?\s?(search|type a|type your|enter|write|ask|message|reply to|add a)\b", re.I)

# words that, alone or leading a short label, almost always mean a control
BUTTON_WORDS = {
    "reply", "forward", "send", "cancel", "save", "submit", "compose", "join",
    "share", "book", "ok", "done", "next", "back", "continue", "sign", "log",
    "login", "open", "close", "delete", "edit", "new", "add", "create", "run",
    "upload", "download", "accept", "decline", "allow", "deny", "apply",
    "search", "learn", "test", "more", "view", "show", "copy", "start", "stop",
}


# ---------------------------------------------------------------- vision

_VN = {}


def vision():
    """Use the framework wrapper so PyObjC knows struct and out-argument types."""
    if not _VN:
        for name in ("VNImageRequestHandler", "VNRecognizeTextRequest",
                     "VNDetectRectanglesRequest", "VNClassifyImageRequest"):
            _VN[name] = getattr(Vision, name)
    return _VN


def _handler(vn, source):
    """A request handler for a file path or an in-memory CGImage. Vision probes optional
    keys, so pass a native dictionary rather than the Python mapping proxy."""
    options = Foundation.NSDictionary.dictionary()
    if isinstance(source, str):
        return vn["VNImageRequestHandler"].alloc().initWithURL_options_(
            Foundation.NSURL.fileURLWithPath_(source), options)
    return vn["VNImageRequestHandler"].alloc().initWithCGImage_options_(source, options)


def run_vision(source, w, h, fast=None):
    """
    OCR lines and rectangle regions for one image (a file path or an in-memory CGImage).
    Boxes come back as [x, y, w, h] in image pixels, top-left origin.
    `fast` picks Vision's fast OCR (~9x cheaper, a little less accurate); None = FAST_OCR.
    """
    vn = vision()
    fast = FAST_OCR if fast is None else fast
    path = source if isinstance(source, str) else "<in-memory frame>"
    handler = _handler(vn, source)

    ocr = vn["VNRecognizeTextRequest"].alloc().init()
    ocr.setRecognitionLevel_(1 if fast else 0)          # 0 accurate, 1 fast
    ocr.setUsesLanguageCorrection_(not fast)

    rects = vn["VNDetectRectanglesRequest"].alloc().init()
    rects.setMaximumObservations_(0)                    # no cap
    rects.setMinimumSize_(0.015)
    rects.setMinimumAspectRatio_(0.05)
    rects.setMinimumConfidence_(0.6)

    success, error = handler.performRequests_error_([ocr, rects], None)
    if not success:
        # Retry OCR separately, preserving the native dictionary and NSError tuple
        # handling required by the official PyObjC Vision bindings.
        geometry_ok, _ = handler.performRequests_error_([rects], None)
        if not geometry_ok:
            rects = None
        for level, auto in ((0, True), (1, False)):
            ocr = vn["VNRecognizeTextRequest"].alloc().init()
            ocr.setRecognitionLevel_(level)
            ocr.setUsesLanguageCorrection_(level == 0)
            ocr.setAutomaticallyDetectsLanguage_(auto)
            retry = _handler(vn, source)
            success, error = retry.performRequests_error_([ocr], None)
            if success:
                break
        else:
            raise RuntimeError(f"Vision failed on {path}: {error or 'no error details returned'}")

    def px(b):
        # Vision: normalised, bottom-left origin -> pixels, top-left origin
        return [round(b.origin.x * w), round((1 - b.origin.y - b.size.height) * h),
                round(b.size.width * w), round(b.size.height * h)]

    lines = []
    for o in ocr.results() or []:
        c = o.topCandidates_(1)
        if not c:
            continue
        c = c[0]
        if c.confidence() < MIN_TEXT_CONF or not c.string().strip():
            continue
        lines.append({"text": c.string().strip(), "conf": round(c.confidence(), 2),
                      "box": px(o.boundingBox())})

    boxes = [px(o.boundingBox()) for o in (rects.results() or [])] if rects else []
    return lines, boxes, []         # (the old scene-label request was never used downstream)


# ---------------------------------------------------------------- geometry

def area(b):
    return b[2] * b[3]


def contains(outer, inner, slack=3):
    return (outer[0] - slack <= inner[0] and outer[1] - slack <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2] + slack
            and inner[1] + inner[3] <= outer[1] + outer[3] + slack)


def cy(b):
    return b[1] + b[3] / 2


def dist(b, x, y):
    """Distance from a point to a box (0 if inside)."""
    dx = max(b[0] - x, 0, x - (b[0] + b[2]))
    dy = max(b[1] - y, 0, y - (b[1] + b[3]))
    return (dx * dx + dy * dy) ** 0.5


# ---------------------------------------------------------------- entities

ENTITY_RES = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "url": re.compile(r"\b(?:https?://)?(?:[\w-]+\.)+(?:com|org|net|io|ai|dev|edu|gov|in|co|app|me)\b(?:/[^\s,)]*)?", re.I),
    "phone": re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)"),
    "time": re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|AM|PM)\b|\b\d{1,2}:\d{2}\b"),
    "date": re.compile(
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?"
        r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
        r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    "money": re.compile(r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|INR|EUR|Rs\.?))"),
    "path": re.compile(r"(?<![\w./:])(?:~|\.{1,2})?/(?:[\w.-]+/)+[\w.-]+"),
}

NOT_NAMES = {
    "The", "This", "That", "More", "New", "Google", "Inbox", "Search", "Sent",
    "Drafts", "Starred", "Snoozed", "Labels", "Categories", "Meeting", "Join",
    "Book", "Learn", "Test", "Share", "Reply", "Forward", "Cancel", "Mail",
    "Chat", "Meet", "All", "Your", "You", "Project", "Hosted", "Booked",
}


def entities(text):
    found = {}
    for kind, rx in ENTITY_RES.items():
        hits = [m.group(0).strip() for m in rx.finditer(text)]
        if kind == "url":       # an email's domain is not a separate link
            hits = [u for u in hits if not re.search(re.escape(u) + r"$", " ".join(found.get("email", [])))]
            hits = [u for u in hits if "@" not in u]
        if kind == "phone":     # skip things that are really dates/ids
            hits = [p for p in hits if sum(ch.isdigit() for ch in p) >= 9
                    and not re.search(r"\d{8}-\d{6}", p)]     # our own frame timestamps
        if hits:
            found[kind] = hits
    # person: a whole line that is 2-3 capitalised words, optionally "- role"
    head = re.split(r"\s+[-–(]", text)[0].strip()
    words = head.split()
    if (2 <= len(words) <= 3 and all(re.fullmatch(r"[A-Z][a-z]+", w) for w in words)
            and not set(words) & NOT_NAMES):
        found["person"] = [head]
    return found


# ---------------------------------------------------------------- resolve

CHROME_KINDS = {"tab", "address_bar", "bookmark", "menu_bar", "button"}


def classify(lines, rects, meta, img_w, img_h):
    """Give every OCR line a kind, and pull panels out of the rectangles."""
    objs = []
    heights = sorted(l["box"][3] for l in lines) or [10]
    median_h = heights[len(heights) // 2]
    is_browser = meta.get("app") in BROWSERS
    url = meta.get("url") or ""
    host = re.sub(r"^https?://", "", url).split("/")[0]

    # browser chrome: find the address bar line, everything above it is tabs,
    # the row right below it is the bookmarks bar
    addr = None
    if is_browser:
        cands = [l for l in lines if cy(l["box"]) < img_h * 0.15 and
                 (("://" in l["text"]) or (host and host in l["text"]))]
        addr = min(cands, key=lambda l: l["box"][1], default=None)
    addr_top = addr["box"][1] if addr else None
    addr_bot = addr["box"][1] + addr["box"][3] if addr else None

    # left rail: short lines stacked at the same x in the left 20%
    left_x = Counter(round(l["box"][0] / 6) for l in lines
                     if l["box"][0] < img_w * 0.2 and len(l["text"].split()) <= 4)
    rail_cols = {k for k, n in left_x.items() if n >= 4}

    # rectangles that tightly wrap a short line -> that line is a button
    small_rects = [r for r in rects if area(r) < img_w * img_h * 0.02]

    for l in lines:
        b, t = l["box"], l["text"]
        words = t.split()
        first = re.sub(r"[^a-z]", "", words[0].lower()) if words else ""
        kind = None

        if addr is not None and l is addr:
            kind = "address_bar"
        elif addr is not None and cy(b) < addr_top:
            kind = "tab"
        elif addr is not None and addr_bot < cy(b) < addr_bot + 3.5 * median_h:
            kind = "bookmark"
        elif not is_browser and cy(b) < img_h * 0.03:
            kind = "menu_bar"
        elif round(b[0] / 6) in rail_cols and len(words) <= 4:
            kind = "sidebar_item"
        else:
            tight = any(contains(r, b) and area(r) < 4 * area(b) + 400 for r in small_rects)
            if INPUT_RE.match(t) and len(words) <= 6:
                kind = "input"
            elif (len(words) <= 4 and not entities(t)
                    and (tight or (first in BUTTON_WORDS and len(words) <= 3))):
                kind = "button"
            elif b[3] >= 1.35 * median_h and len(words) <= 16:
                kind = "heading"
            elif len(words) >= 8:
                kind = "paragraph"
            else:
                kind = "text"

        o = {"kind": kind, "text": t, "box": b, "conf": l["conf"]}
        ents = entities(t)
        if kind in CHROME_KINDS:    # a bookmark called "Gates Scholarship" is not a person
            ents.pop("person", None)
        if ents:
            o["entities"] = ents
            if kind in ("text", "paragraph") and len(words) <= 3 and ({"email", "url"} & ents.keys()):
                o["kind"] = "link"
        objs.append(o)

    # panels: big rectangles that hold several text objects (cards, dialogs, panes)
    panels = []
    for r in sorted(rects, key=area, reverse=True):
        if area(r) < img_w * img_h * 0.02:
            continue
        inside = [o for o in objs if contains(r, o["box"])]
        if len(inside) >= 3:
            panels.append({"kind": "panel", "text": None, "box": r, "conf": 1.0,
                           "children": len(inside)})
    return objs, panels


def resolve(meta_path, fast=None):
    """Resolve the screenshot a capture-metadata file points at."""
    with open(meta_path) as f:
        meta = json.load(f)
    img_path = os.path.join(config.paths().data_dir, meta["image"])
    if not os.path.exists(img_path):
        raise FileNotFoundError(img_path)
    w, h = image_size(img_path)
    return resolve_image(meta, img_path, w, h, fast)


def resolve_frame(meta, frame, fast=None):
    """Resolve an in-memory frame (macos.Frame); nothing is read from or written to disk."""
    return resolve_image(meta, frame.cg, frame.width, frame.height, fast)


def resolve_image(meta, source, w, h, fast=None):
    fast = FAST_OCR if fast is None else fast
    if meta.get("quick"):
        fast = False                # the quick strip is small, so accurate OCR is still cheap, and names need it
    t0 = time.time()
    lines, rects, labels = run_vision(source, w, h, fast)
    objs, panels = classify(lines, rects, meta, w, h)
    return assemble(meta, w, h, objs, panels, labels, fast, t0)


def assemble(meta, w, h, objs, panels, labels, fast, t0, regions=0):
    """Order, number and summarise classified objects into a resolver result."""

    # reading order, then ids; panels first so children can point at them
    objs.sort(key=lambda o: (round(o["box"][1] / 8), o["box"][0]))
    everything = panels + objs
    for i, o in enumerate(everything):
        o["id"] = i
    for o in objs:
        holders = [p for p in panels if contains(p["box"], o["box"])]
        if holders:
            o["parent"] = min(holders, key=lambda p: area(p["box"]))["id"]

    # cursor is logged in screen points; frames are downscaled pixels
    scale = w / meta["screen"]["w"] if meta.get("screen") else 1
    focus = None
    if meta.get("cursor") and objs:
        cx, cyy = meta["cursor"]["x"] * scale, meta["cursor"]["y"] * scale
        near = min(objs, key=lambda o: dist(o["box"], cx, cyy))
        d = dist(near["box"], cx, cyy)
        if d < 60:
            focus = {"id": near["id"], "kind": near["kind"], "text": near["text"],
                     "distance_px": round(d)}

    ents = {}
    for o in objs:
        for k, vs in o.get("entities", {}).items():
            for v in vs:
                ents.setdefault(k, [])
                if v not in ents[k]:
                    ents[k].append(v)

    return {
        "ts": meta["ts"],
        "image": meta["image"],
        "image_size": {"w": w, "h": h},
        "px_per_point": round(scale, 4),
        "context": {
            "app": meta.get("app"),
            "site": re.sub(r"^https?://", "", meta.get("url") or "").split("/")[0] or None,
            "title": meta.get("tab_title") or meta.get("window"),
        },
        "scene": labels,
        "counts": dict(Counter(o["kind"] for o in everything)),
        "focus": focus,
        "entities": ents,
        "objects": everything,
        "resolver": {"engine": "apple-vision", "fast": fast, "regions": regions,
                     "seconds": round(time.time() - t0, 2)},
    }


def resolve_regions(meta, frame, previous, boxes, fast=None):
    """A later frame of the same window, read only inside `boxes` (frame pixels, see ocr_regions.plan);
    everything outside them is carried over from `previous`, the last result for this window."""
    import Quartz
    import ocr_regions
    fast = FAST_OCR if fast is None else fast
    t0 = time.time()
    w, h = frame.width, frame.height
    fresh = []
    for x, y, bw, bh in boxes:
        crop = Quartz.CGImageCreateWithImageInRect(frame.cg, Quartz.CGRectMake(x, y, bw, bh))
        if crop is None:
            return None
        lines, _rects, _labels = run_vision(crop, bw, bh, fast)
        for l in lines:
            l["box"] = [l["box"][0] + x, l["box"][1] + y, l["box"][2], l["box"][3]]
        objs, _panels = classify(lines, [], meta, w, h)
        fresh.extend(objs)
    old = previous["objects"]
    objs = [dict(o) for o in ocr_regions.merge([o for o in old if o["kind"] != "panel"], boxes, fresh)]
    panels = [dict(o) for o in old if o["kind"] == "panel"
              and not any(ocr_regions.touches(o["box"], b) for b in boxes)]
    for o in objs + panels:                # copies: the last result stays as it was
        o.pop("id", None)
        o.pop("parent", None)
    return assemble(meta, w, h, objs, panels, [], fast, t0, regions=len(boxes))


def image_size(path):
    """Pixel size without needing Pillow."""
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(path)
    return int(rep.pixelsWide()), int(rep.pixelsHigh())

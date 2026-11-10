"""Bounded local evidence from visible screen text (the app's own text where the trail read it, else OCR), without inferred decisions."""

import hashlib
import re

import config
import observation

MAX_TEXT = 3000
MAX_EXCERPTS = 12
STRIP_MAX_HEIGHT = 200          # points: a window this short and this wide is a toolbar strip, not a document
STRIP_RATIO = 4
CONTENT_KINDS = {"heading", "paragraph", "text", "link"}
DECISION = re.compile(
    r"^(?:decision\s*:|(?:we|i)\s+(?:have\s+)?(?:decided|chose|agreed|selected)\b)", re.I)


def visible_window_region(window, display):
    """Clip global CG window bounds to a display; return normalized x/y/w/h."""
    if not window or not display or display.get("Width", 0) <= 0 or display.get("Height", 0) <= 0:
        return None
    x = max(window["X"], display["X"])
    y = max(window["Y"], display["Y"])
    right = min(window["X"] + window["Width"], display["X"] + display["Width"])
    bottom = min(window["Y"] + window["Height"], display["Y"] + display["Height"])
    if right <= x or bottom <= y:
        return None
    return [(x - display["X"]) / display["Width"], (y - display["Y"]) / display["Height"],
            (right - x) / display["Width"], (bottom - y) / display["Height"]]


def pick_window(windows):
    """The app's real window from its on-screen windows, front to back (CG window-info dicts).

    A browser also has short, wide helper windows (its ~150 pt toolbar and tab strip) that sit in
    front of the page. Reading those as "the window" makes the saved screen text the browser's own tabs.
    Skip them; among the rest prefer one that has a title, since helpers have none."""
    def bounds(w):
        return w.get("kCGWindowBounds") or {}
    usable = [w for w in windows if bounds(w).get("Height", 0) >= 80 and bounds(w).get("Width", 0) >= 80]
    real = [w for w in usable
            if not (bounds(w)["Height"] <= STRIP_MAX_HEIGHT and bounds(w)["Width"] >= STRIP_RATIO * bounds(w)["Height"])]
    pool = real or usable
    return next((w for w in pool if w.get("kCGWindowName")), pool[0] if pool else None)


def extract_content(res, meta, ax=None, ocr=None):
    """Keep readable lines fully inside the foreground window, excluding UI chrome.

    `ocr`: the same screen as an Observation, if the caller already built one.
    `ax`: the accessibility trail's Observation of this same screen, if it read one (observation.py). Where
    it saw the same line as OCR, its exact text is kept and the OCR original is stored as a variant.

    Older frames have no window region. Do not attribute their entire display
    to the foreground app; they still support the existing activity rules.
    """
    if not meta.get("window_region"):
        return None
    ocr = ocr or observation.from_ocr(res, meta, 0.0)
    merged = observation.merge([ocr, ax] if ax is not None else [ocr])
    lines, seen, kept = [], set(), []
    remaining = MAX_TEXT
    for m in merged:
        if m.source == "ocr" and not (m.in_window and m.kind in CONTENT_KINDS and m.conf >= 0.5):
            continue
        text = re.sub(r"\s+", " ", m.text or "").strip()
        if len(text) < 12 or len(re.findall(r"\w+", text)) < 3 or text in seen:
            continue
        seen.add(text)
        text = text[:remaining]
        if not text:
            break
        lines.append(text)
        kept.append(m)
        remaining -= len(text) + 1
        if remaining <= 0:
            break
    if not lines:
        return None
    out = {"text": "\n".join(lines),
           "decision_quotes": [line for line in lines if DECISION.search(line) and "?" not in line][:5],
           "observed_at": meta["iso"],
           "source": {"app": meta.get("app"), "window": meta.get("window"), "url": meta.get("url")},
           "provenance": observation.counts(kept)}
    variants = [v for m in kept for v in m.variants][:config.MAX_VARIANTS]
    if variants:
        out["variants"] = variants
    return out


def screen_content(log, res, meta, now, fresh=True, eval_path=None):
    """extract_content for a screen both readers may have seen: share our OCR observation in `log`, find the trail's
    read of the same place, and build the excerpt from the two. `eval_path`: also append before/after text there."""
    ocr = observation.from_ocr(res, meta, now, meta.get("image"))
    if fresh:
        log.add(ocr)
    ax = log.match(ocr)
    content = extract_content(res, meta, ax, ocr)
    if eval_path:
        before = extract_content(res, meta, None, ocr)
        report = observation.merge_report([ocr, ax])[1] if ax else {"gate": "no_trail_read"}
        observation.append_eval(eval_path, {
            "t": meta["iso"], "app": meta.get("app"), "bundle_id": meta.get("bundle_id"),
            "window": meta.get("window"), "url": meta.get("url"), "ocr_lines": len(ocr.lines),
            "ax_lines": len(ax.lines) if ax else 0, "ax_near": len(log.near(ocr)), "matched": ax is not None, "gate": report["gate"],
            "lines_matched": report.get("matched", 0),
            "before": before and before["text"], "after": content and content["text"],
            "provenance": content and content.get("provenance"), "variants": content and content.get("variants")})
    return content


def remember_content(item, capture):
    """Append distinct evidence, refresh repeat timestamps, and bound history.

    Return whether the latest text changed, so the tracker can refresh its screenshot
    even when a document's title and other selected state fields stay the same.
    """
    if not capture:
        return False
    content = item.setdefault("content", {"version": 1, "excerpts": []})
    excerpts = content["excerpts"]
    normalized = re.sub(r"\s+", " ", capture["text"]).strip()
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    for index, excerpt in enumerate(excerpts):
        if excerpt["id"] == digest:
            changed = index != len(excerpts) - 1
            excerpt["last_seen"] = capture["observed_at"]
            # Retain the most recently revisited evidence when history fills.
            excerpts.append(excerpts.pop(index))
            return changed
    excerpts.append({"id": digest, "text": capture["text"], "source": capture["source"],
                     "first_seen": capture["observed_at"], "last_seen": capture["observed_at"],
                     "decision_quotes": capture["decision_quotes"],
                     **{k: capture[k] for k in ("provenance", "variants") if capture.get(k)}})
    del excerpts[:-MAX_EXCERPTS]
    return True

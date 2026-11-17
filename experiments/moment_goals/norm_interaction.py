import math

import norm_ax

SCROLL_GAP_S = 2.0
S = 1_000_000_000


def magnitude(units):
    if units < 200:
        return "brief"
    if units < 1500:
        return "moderate"
    return "extensive"


def scroll_phrase(events):
    dy = sum(e["payload"].get("dy", 0) or 0 for e in events)
    dx = sum(e["payload"].get("dx", 0) or 0 for e in events)
    total = sum(abs(e["payload"].get("dy", 0) or 0) + abs(e["payload"].get("dx", 0) or 0) for e in events)
    vertical = sum(abs(e["payload"].get("dy", 0) or 0) for e in events)
    axis = "vertical" if vertical >= total / 2 else "horizontal"
    if axis == "vertical":
        steady = abs(dy) / vertical if vertical else 0
        direction = ("downward" if dy < 0 else "upward") if steady >= 0.7 else "back and forth"
    else:
        steady = abs(dx) / (total - vertical) if total - vertical else 0
        direction = ("leftward" if dx > 0 else "rightward") if steady >= 0.7 else "back and forth"
    return f"{magnitude(total).capitalize()} {axis} scrolling, {direction} ({len(events)} scroll events)"


def locate(point, ax_tree, ocr_items, scale):
    element = norm_ax.element_at(ax_tree, point) if ax_tree else None
    if element:
        return f"'{element['label']}' ({element['role']})"
    px, py = point[0] * scale, point[1] * scale
    best, area = None, None
    for item in ocr_items:
        if item["x"] <= px <= item["x"] + item["w"] and item["y"] <= py <= item["y"] + item["h"]:
            size = item["w"] * item["h"]
            if area is None or size < area:
                best, area = item, size
    if best:
        return f"text '{best['text'][:50]}'"
    return None


def summarize(events, ax_tree=None, ocr_items=(), scale=1.0):
    ordered = sorted(events, key=lambda e: e["t_ns"])
    phrases, counts = [], {"scroll": 0, "click": 0, "typed": 0, "delete": 0, "return": 0, "tab": 0, "navigation": 0, "shortcut": 0, "move": 0}
    pending_scroll, last_scroll = [], None
    typing = {"typed": 0, "delete": 0, "return": 0, "tab": 0}
    moves = []

    def flush_scroll():
        nonlocal pending_scroll
        if pending_scroll:
            phrases.append(scroll_phrase(pending_scroll))
            counts["scroll"] += len(pending_scroll)
            pending_scroll = []

    def flush_typing():
        if any(typing.values()):
            bits = []
            if typing["typed"]:
                bits.append(f"{typing['typed']} characters typed")
            if typing["delete"]:
                bits.append(f"{typing['delete']} deletions")
            if typing["return"]:
                bits.append(f"{typing['return']} Return presses")
            if typing["tab"]:
                bits.append(f"{typing['tab']} Tab presses")
            phrases.append("Keyboard entry: " + ", ".join(bits))
            counts["typed"] += typing["typed"]
            counts["delete"] += typing["delete"]
            counts["return"] += typing["return"]
            counts["tab"] += typing["tab"]
            for k in typing:
                typing[k] = 0

    for e in ordered:
        kind, p = e["type"], e.get("payload") or {}
        if kind == "scroll":
            flush_typing()
            if last_scroll is not None and (e["t_ns"] - last_scroll) / S > SCROLL_GAP_S:
                flush_scroll()
            pending_scroll.append(e)
            last_scroll = e["t_ns"]
            continue
        if kind == "click" and p.get("phase") == "down":
            flush_scroll()
            flush_typing()
            counts["click"] += 1
            where = locate((p.get("x", 0), p.get("y", 0)), ax_tree, ocr_items, scale)
            double = (p.get("click_count") or 1) >= 2
            name = "Double-click" if double else ("Right-click" if p.get("button") == "right" else "Click")
            phrases.append(f"{name} on {where}" if where else f"{name} (target not identifiable)")
        elif kind == "keypress" and p.get("phase") == "down" and not p.get("is_repeat"):
            flush_scroll()
            key = p.get("key_class", "other")
            mods = [m for m in (p.get("modifiers") or []) if m in ("command", "control")]
            if mods and key in ("character", "other"):
                flush_typing()
                counts["shortcut"] += 1
                phrases.append("Keyboard shortcut with " + "/".join(mods))
            elif key in ("character", "whitespace"):
                typing["typed"] += 1
            elif key == "delete":
                typing["delete"] += 1
            elif key == "return":
                typing["return"] += 1
            elif key == "tab":
                typing["tab"] += 1
            elif key == "navigation":
                flush_typing()
                counts["navigation"] += 1
                if not phrases or not phrases[-1].startswith("Navigation keys"):
                    phrases.append("Navigation keys (arrows, page, home/end)")
        elif kind == "cursor_move":
            moves.append((p.get("x", 0), p.get("y", 0)))
    flush_scroll()
    flush_typing()
    counts["move"] = len(moves)
    travel = sum(math.dist(a, b) for a, b in zip(moves, moves[1:]))
    if not phrases and moves:
        phrases.append("Cursor movement only (weak evidence of engagement)" if travel > 5 else "Cursor stationary")
    if not phrases:
        phrases.append("No interaction recorded")
    kinds = []
    if counts["typed"] or counts["delete"]:
        kinds.append("typing")
    if counts["scroll"]:
        kinds.append("scrolling")
    if counts["click"]:
        kinds.append("clicking")
    if counts["navigation"] or counts["shortcut"]:
        kinds.append("keyboard navigation")
    if not kinds:
        kinds.append("cursor only" if moves else "none")
    return {"phrases": phrases, "kinds": kinds, "counts": counts}

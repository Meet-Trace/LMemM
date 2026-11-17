import math

NAVIGATION_KEYS = frozenset({"navigation"})
SCROLL_UNITS_NOISE = 2.0


def empty():
    return {"clicks": 0, "double_clicks": 0, "keys": {}, "keys_total": 0, "deletes": 0, "modifiers": 0,
            "scroll_events": 0, "scroll_net_dy": 0.0, "scroll_abs_dy": 0.0, "scroll_gestures": 0,
            "cursor_moves": 0, "cursor_path_px": 0.0}


def summarize(events):
    out = empty()
    last = None
    seen_scroll = False
    for kind, payload in events:
        if kind == "click" and payload.get("phase") == "down":
            out["clicks"] += 1
            if (payload.get("click_count") or 1) >= 2:
                out["double_clicks"] += 1
        elif kind == "keypress" and payload.get("phase") == "down" and not payload.get("is_repeat"):
            key_class = payload.get("key_class") or "other"
            out["keys"][key_class] = out["keys"].get(key_class, 0) + 1
            out["keys_total"] += 1
            if key_class == "delete":
                out["deletes"] += 1
            if key_class == "modifier":
                out["modifiers"] += 1
        elif kind == "scroll":
            dy = float(payload.get("dy") or 0)
            if payload.get("phase") == "began":
                out["scroll_gestures"] += 1
                seen_scroll = True
            if payload.get("phase") in ("changed", "momentum", "began", "none") and dy:
                out["scroll_events"] += 1
                out["scroll_net_dy"] += dy
                out["scroll_abs_dy"] += abs(dy)
        elif kind == "cursor_move":
            out["cursor_moves"] += 1
            point = (payload.get("x"), payload.get("y"))
            if last is not None and None not in point:
                out["cursor_path_px"] += math.hypot(point[0] - last[0], point[1] - last[1])
            if None not in point:
                last = point
    if out["scroll_events"] and not seen_scroll:
        out["scroll_gestures"] = 1
    out["scroll_net_dy"] = round(out["scroll_net_dy"], 1)
    out["scroll_abs_dy"] = round(out["scroll_abs_dy"], 1)
    out["cursor_path_px"] = round(out["cursor_path_px"], 0)
    return out


def scroll_direction(summary):
    total = summary["scroll_abs_dy"]
    if total < SCROLL_UNITS_NOISE:
        return None
    ratio = summary["scroll_net_dy"] / total
    if ratio <= -0.6:
        return "down"
    if ratio >= 0.6:
        return "up"
    return "back and forth"


def typed_characters(summary):
    keys = summary["keys"]
    return keys.get("character", 0) + keys.get("whitespace", 0)


def level(summary):
    typed = typed_characters(summary)
    if typed >= 3 or summary["deletes"] >= 2:
        return "typing"
    if summary["scroll_events"] >= 3:
        return "scrolling"
    if summary["clicks"]:
        return "clicking"
    if summary["keys_total"] or summary["cursor_path_px"] >= 200:
        return "light"
    return "none"


def describe(summary):
    parts = []
    if summary["clicks"]:
        parts.append(f"{summary['clicks']} click" + ("s" if summary["clicks"] != 1 else "")
                     + (f" ({summary['double_clicks']} double)" if summary["double_clicks"] else ""))
    typed = typed_characters(summary)
    if typed:
        parts.append(f"{typed} character keys typed")
    if summary["deletes"]:
        parts.append(f"{summary['deletes']} delete presses")
    if summary["keys"].get("return"):
        parts.append(f"{summary['keys']['return']} return presses")
    if summary["keys"].get("navigation"):
        parts.append(f"{summary['keys']['navigation']} arrow/navigation key presses")
    if summary["modifiers"]:
        parts.append(f"{summary['modifiers']} modifier key presses")
    direction = scroll_direction(summary)
    if direction:
        parts.append(f"scrolled {direction} {int(summary['scroll_abs_dy'])} px in {max(1, summary['scroll_gestures'])} gesture(s)")
    if summary["cursor_path_px"] >= 100:
        parts.append(f"pointer travelled {int(summary['cursor_path_px'])} px")
    return parts

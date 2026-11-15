"""Raw event dict -> InteractionEvent. The dict is what the macOS tap reads off a CGEvent:

    {"type": "mouse_down"|"mouse_up"|"scroll"|"key_down"|"key_up"|"flags_changed",
     "ticks": event timestamp (uptime ns), "x", "y", "button": 0 left|1 right|2 middle|n other, "click_count",
     "dx", "dy", "continuous": bool, "scroll_phase", "momentum_phase", "keycode", "flags", "autorepeat": bool}

Mouse moves do not come through here (they go to CursorSampler). The keycode is read for key_class only; the
returned event never contains it, nor any character.
"""

from trace_v2.observations.models import InteractionEvent

from .keys import key_class, modifier_phase, modifiers_from_flags

_BUTTONS = {0: "left", 1: "right", 2: "middle"}
_SCROLL_PHASES = {1: "began", 2: "changed", 4: "ended", 8: "ended"}


def decode(raw, clock):
    kind, t = raw["type"], clock.to_wall_ns(raw["ticks"])
    if kind in ("mouse_down", "mouse_up"):
        payload = {"x": raw["x"], "y": raw["y"], "button": _BUTTONS.get(raw.get("button", 0), "other"),
                   "phase": "down" if kind == "mouse_down" else "up"}
        if 1 <= raw.get("click_count", 0) <= 10:
            payload["click_count"] = raw["click_count"]
        return InteractionEvent("click", t, payload)
    if kind == "scroll":
        phase = "momentum" if raw.get("momentum_phase") else _SCROLL_PHASES.get(raw.get("scroll_phase"), "none")
        return InteractionEvent("scroll", t, {
            "dx": raw["dx"], "dy": raw["dy"], "x": raw["x"], "y": raw["y"],
            "unit": "pixel" if raw.get("continuous") else "line", "phase": phase})
    if kind in ("key_down", "key_up"):
        klass = key_class(raw["keycode"])
        payload = {"phase": "down" if kind == "key_down" else "up", "key_class": klass,
                   "modifiers": modifiers_from_flags(raw["flags"], klass)}
        if kind == "key_down":
            payload["is_repeat"] = bool(raw.get("autorepeat"))
        return InteractionEvent("keypress", t, payload)
    if kind == "flags_changed":
        phase = modifier_phase(raw["keycode"], raw["flags"])
        if phase is None:
            return None
        return InteractionEvent("keypress", t, {"phase": phase, "key_class": "modifier",
                                                "modifiers": modifiers_from_flags(raw["flags"], "modifier")})
    return None

import re
import subprocess


def run(command, timeout=10):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_battery(text):
    percent = re.search(r"(\d+)%", text)
    source = "ac" if "AC Power" in text else "battery" if "Battery Power" in text else None
    return {"percent": int(percent.group(1)) if percent else None, "source": source}


def parse_memory_free(text):
    free = re.search(r"free percentage:\s*(\d+)%", text)
    return int(free.group(1)) if free else None


def parse_idle_s(text):
    """Seconds since the last keyboard or mouse input, from `ioreg -c IOHIDSystem`; None when it cannot be read."""
    idle = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', text)
    return int(idle.group(1)) / 1e9 if idle else None


def pause_reason(min_battery=20, min_free_pct=10, min_idle_s=60, runner=run, screen_idle=None):
    """-> why heavy work should wait, or None. Unknown readings never pause (so this is harmless off macOS).
    Model work waits until you have not touched the keyboard or mouse, and the screen has not changed, for `min_idle_s`,
    so it never competes with you (reading or watching a video counts as working). `screen_idle()` -> seconds or None."""
    idle = parse_idle_s(runner(["ioreg", "-c", "IOHIDSystem"]))
    if min_idle_s and idle is not None and idle < min_idle_s:
        return f"you are working (idle {int(idle)} s)"
    seen = screen_idle() if screen_idle else None
    if min_idle_s and seen is not None and seen < min_idle_s:
        return f"the screen is changing (still {int(seen)} s)"
    battery = parse_battery(runner(["pmset", "-g", "batt"]))
    if battery["source"] == "battery" and battery["percent"] is not None and battery["percent"] < min_battery:
        return f"battery at {battery['percent']}%"
    free = parse_memory_free(runner(["memory_pressure"]))
    if free is not None and free < min_free_pct:
        return f"only {free}% memory free"
    return None


def user_active(within_s=3, runner=run, screen_idle=None):
    """True when the keyboard or mouse was used, or the screen changed, in the last `within_s` seconds.
    Unreadable means not active."""
    idle = parse_idle_s(runner(["ioreg", "-c", "IOHIDSystem"]))
    if idle is not None and idle < within_s:
        return True
    seen = screen_idle() if screen_idle else None
    return seen is not None and seen < within_s

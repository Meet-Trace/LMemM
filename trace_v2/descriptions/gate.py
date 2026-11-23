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


def pause_reason(min_battery=20, min_free_pct=10, runner=run):
    """-> why heavy work should wait, or None. Unknown readings never pause (so this is harmless off macOS)."""
    battery = parse_battery(runner(["pmset", "-g", "batt"]))
    if battery["source"] == "battery" and battery["percent"] is not None and battery["percent"] < min_battery:
        return f"battery at {battery['percent']}%"
    free = parse_memory_free(runner(["memory_pressure"]))
    if free is not None and free < min_free_pct:
        return f"only {free}% memory free"
    return None

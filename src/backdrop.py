"""LMemM - the time-aware background: which picture lies behind the window, and how much of each.

Pure logic, no AppKit (the window draws what this decides). Four backgrounds, one for each part of the
day, each a graphite-dark picture with its own light and its grain already in it. Time never switches
them: the picture changes by cross-fading over BLEND_MINUTES around each boundary, so the window is the same
room under slowly different light. A person can pin one instead; "auto" gives the clock back.

Each background is data: file, where it starts, how strongly it is shown (a dimming veil, 0 to 1, over it).
"""

import os

AUTO = "auto"
BLEND_MINUTES = 60                 # the cross-fade at each boundary: 30 minutes before to 30 after

# key, label, starts at (hour), file, veil (0 = as drawn, higher = darker), what it is for
BACKGROUNDS = (
    {"key": "dawn", "label": "Dawn", "start": 5, "file": "01_morning_dawn_amber.png", "veil": 0.0,
     "feel": "Warm amber, soft dawn"},
    {"key": "day", "label": "Day", "start": 10, "file": "02_daytime_ambient_indigo.png", "veil": 0.0,
     "feel": "Neutral indigo, clear and balanced"},
    {"key": "evening", "label": "Evening", "start": 16, "file": "04_night_warm_copper.png", "veil": 0.0,
     "feel": "Warm copper, reflective"},
    {"key": "night", "label": "Night", "start": 20, "file": "03_evening_cinematic_blue.png", "veil": 0.0,
     "feel": "Deep indigo and violet, calm"},
)
KEYS = tuple(b["key"] for b in BACKGROUNDS)
ASSET_DIR = os.path.join("assets", "app_bg", "trace_cinematic_backgrounds")


def clean(choice):
    """A stored choice made safe: "auto" or one of the keys; anything else is auto."""
    return choice if choice in KEYS else AUTO


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def plan(minute, choice=AUTO):
    """{key: weight} for this minute of the day (0..1439); the weights add up to 1.
    A pinned choice is shown whole. Otherwise the current period is shown, and within BLEND_MINUTES / 2 of a
    boundary the neighbour fades in (smoothly, so it never flashes)."""
    choice = clean(choice)
    if choice != AUTO:
        return {choice: 1.0}
    minute = minute % 1440
    half = BLEND_MINUTES / 2
    n = len(BACKGROUNDS)
    starts = [b["start"] * 60 for b in BACKGROUNDS]
    for i in range(n):                                       # which boundary is near
        nxt = (i + 1) % n
        boundary = starts[nxt]
        delta = (minute - boundary + 720) % 1440 - 720       # minutes after the boundary, -720..720
        if -half <= delta <= half:
            into = _smooth((delta + half) / BLEND_MINUTES)
            return {BACKGROUNDS[i]["key"]: round(1 - into, 4), BACKGROUNDS[nxt]["key"]: round(into, 4)}
    for i in range(n):
        if (minute - starts[i]) % 1440 == min((minute - s) % 1440 for s in starts):
            return {BACKGROUNDS[i]["key"]: 1.0}
    return {BACKGROUNDS[0]["key"]: 1.0}


def minute_of(now_struct):
    """Minute of the local day from a time.struct_time."""
    return now_struct.tm_hour * 60 + now_struct.tm_min


def entry(key):
    return next((b for b in BACKGROUNDS if b["key"] == key), None)


def path(key, root="."):
    b = entry(key)
    return os.path.join(root, ASSET_DIR, b["file"]) if b else None


def choices(current):
    """The rows of the small picker in Settings: Automatic first, then each background."""
    current = clean(current)
    rows = [{"key": AUTO, "label": "Automatic", "on": current == AUTO}]
    rows += [{"key": b["key"], "label": b["label"], "on": current == b["key"]} for b in BACKGROUNDS]
    return rows

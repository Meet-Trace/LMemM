"""LMemM - the time-aware background: which background lies behind the window, and how much of each.

Pure logic, no AppKit (the window draws what this decides). Four backgrounds, one for each part of the
day, each a graphite-dark gradient with its own light, drawn in code. Time never switches
them: the light changes by cross-fading over BLEND_MINUTES around each boundary, so the window is the same
room under slowly different light. A person can pin one instead; "auto" gives the clock back.

Each background is data: where it starts, a base colour, a few soft lights and a grain strength.
"""

AUTO = "auto"
BLEND_MINUTES = 60                 # the cross-fade at each boundary: 30 minutes before to 30 after

# Every background is data. base: the dark ground (r, g, b, 0..1). lights: soft radial fields as
# (x, y, radius, (r, g, b), strength), x and y as fractions of the window and radius as a fraction of its
# longer side. grain: the film grain's strength. They are drawn in code (window.py), so they are sharp at any size.
BACKGROUNDS = (
    {"key": "dawn", "label": "Morning", "start": 5, "grain": 0.040,
     "base": (0.030, 0.030, 0.110), "feel": "Deep cobalt, fresh and clear",
     "lights": ((0.30, 0.24, 0.78, (0.09, 0.10, 0.52), 0.92),
                (0.05, 0.05, 0.60, (0.07, 0.07, 0.38), 0.55))},
    {"key": "day", "label": "Afternoon", "start": 10, "grain": 0.040,
     "base": (0.030, 0.040, 0.120), "feel": "Periwinkle fading to ink, focused",
     "lights": ((0.46, 0.08, 0.90, (0.30, 0.33, 0.88), 0.92),
                (0.08, 0.18, 0.60, (0.20, 0.25, 0.66), 0.50))},
    {"key": "evening", "label": "Evening", "start": 16, "grain": 0.040,
     "base": (0.045, 0.020, 0.110), "feel": "Violet haze, relaxed",
     "lights": ((0.40, 0.42, 0.82, (0.37, 0.14, 0.66), 0.92),
                (0.50, 0.00, 0.62, (0.20, 0.08, 0.40), 0.45))},
    {"key": "night", "label": "Night", "start": 20, "grain": 0.045,
     "base": (0.100, 0.060, 0.050), "feel": "Amber and copper, warm and personal",
     "lights": ((0.21, 0.57, 0.82, (0.54, 0.23, 0.10), 0.92),
                (0.55, 0.88, 0.70, (0.36, 0.17, 0.10), 0.50),
                (0.82, 0.20, 0.62, (0.28, 0.14, 0.09), 0.30))},
)
KEYS = tuple(b["key"] for b in BACKGROUNDS)


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


def choices(current):
    """The rows of the small picker in Settings: Automatic first, then each background."""
    current = clean(current)
    rows = [{"key": AUTO, "label": "Automatic", "on": current == AUTO}]
    rows += [{"key": b["key"], "label": b["label"], "on": current == b["key"]} for b in BACKGROUNDS]
    return rows

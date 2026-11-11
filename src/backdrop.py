"""LMemM - the time-aware background: which background lies behind the window.

Pure logic, no AppKit (the window draws what this decides). Four periods of the day, from config.AMBIENT_SCHEDULE
(morning, afternoon, evening, night). Each has its artwork (ambient.py draws it) and, until the artwork files are
in assets/ambient, a graphite gradient with its own light drawn in code as the stand-in. A person can pin one
period instead; "auto" gives the clock back. plan() says which period is showing; changing period is a slow
cross-fade done by the window, never a jump.
"""

import config

AUTO = "auto"

# The code-drawn stand-in, as data. base: the dark ground (r, g, b, 0..1). lights: soft radial fields as
# (x, y, radius, (r, g, b), strength), x and y as fractions of the window and radius as a fraction of its
# longer side. grain: the film grain's strength.
_LOOKS = {
    "morning": {"label": "Morning", "grain": 0.045, "base": (0.070, 0.066, 0.092), "feel": "First light, soft lavender over indigo",
                "lights": ((0.80, 0.10, 0.62, (0.84, 0.56, 0.36), 0.30),
                           (0.22, 0.80, 0.60, (0.46, 0.34, 0.64), 0.17),
                           (0.05, 0.05, 0.50, (0.30, 0.38, 0.70), 0.10))},
    "afternoon": {"label": "Afternoon", "grain": 0.040, "base": (0.060, 0.072, 0.130), "feel": "In full flow, cobalt and periwinkle",
                  "lights": ((0.60, 0.28, 0.62, (0.34, 0.44, 0.84), 0.30),
                             (0.12, 0.12, 0.55, (0.22, 0.30, 0.62), 0.20),
                             (0.90, 0.95, 0.50, (0.28, 0.34, 0.66), 0.10))},
    "evening": {"label": "Evening", "grain": 0.045, "base": (0.074, 0.062, 0.066), "feel": "Blue hour, sapphire with violet",
                "lights": ((0.82, 0.12, 0.60, (0.88, 0.46, 0.28), 0.26),
                           (0.20, 0.88, 0.62, (0.62, 0.30, 0.26), 0.14),
                           (0.50, 0.40, 0.55, (0.78, 0.56, 0.40), 0.06))},
    "night": {"label": "Night", "grain": 0.040, "base": (0.066, 0.062, 0.112), "feel": "After hours, midnight navy",
              "lights": ((0.70, 0.28, 0.64, (0.46, 0.36, 0.82), 0.28),
                         (0.10, 0.82, 0.60, (0.56, 0.30, 0.48), 0.12),
                         (0.30, 0.00, 0.50, (0.30, 0.42, 0.92), 0.10))},
}
_RENAMED = {"dawn": "morning", "day": "afternoon"}      # choices saved before the periods were renamed
BACKGROUNDS = tuple(dict(_LOOKS[key], key=key, start=hour) for hour, key in config.AMBIENT_SCHEDULE)
KEYS = tuple(b["key"] for b in BACKGROUNDS)


def clean(choice):
    """A stored choice made safe: "auto" or one of the keys; anything else is auto."""
    choice = _RENAMED.get(choice, choice)
    return choice if choice in KEYS else AUTO


def plan(minute, choice=AUTO):
    """{key: 1.0} for this minute of the day (0..1439): the pinned period, else the one the schedule says."""
    choice = clean(choice)
    if choice != AUTO:
        return {choice: 1.0}
    minute = minute % 1440
    # the latest period that has started; before the first start of the day, the last one is still running
    started = [b for b in BACKGROUNDS if b["start"] * 60 <= minute]
    return {(max(started, key=lambda b: b["start"]) if started else max(BACKGROUNDS, key=lambda b: b["start"]))["key"]: 1.0}


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

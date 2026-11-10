"""LMemM - the living ambient canvas, the parts that need no AppKit: where the artwork files are, how a picture
is cropped to the window, and the slow light fields laid over each period's artwork.

The artwork is four supplied pictures (config.AMBIENT_ART_DIR: morning, afternoon, evening, night). They are
never redrawn or recoloured here; the lights below only add a very faint, slow movement on top of them.
"""

import os

import config

PERIODS = ("morning", "afternoon", "evening", "night")
EXTENSIONS = (".webp", ".png", ".jpg", ".jpeg")      # first match wins; webp is the one to ship

# Where each picture's light sits, as a fraction of the picture (x from the left, y from the top). When the
# window's shape is not the picture's, the crop keeps this point as near the middle as the picture allows,
# so the lit region is never the part that gets cut off. Tune per picture once the artwork is in.
# Measured on the supplied pictures (brightness-weighted centre of each).
FOCUS = {"morning": (0.42, 0.41), "afternoon": (0.36, 0.50), "evening": (0.28, 0.46), "night": (0.19, 0.37)}

# A graphite wash over each picture so the light text and cards stay readable: opacity of black, 0..1. The
# morning and evening pictures are pale, so they get the most; night is dark already. A guess to tune by eye.
SCRIM = {"morning": 0.62, "afternoon": 0.50, "evening": 0.58, "night": 0.68}

# Extra darkening that follows the picture's own brightness: the picture is multiplied by itself this many
# times (1.0 = once, fractions blend), so the bright areas darken most and the dark ones hardly change. Tune by eye.
TONE = {"morning": 2.0, "afternoon": 1.0, "evening": 2.0, "night": 1.0}

# The light fields: (x, y, size, (r, g, b), strength, drift_x, drift_y, drift_phase, pulse_phase). x, y are the
# centre as a fraction of the window; size is a fraction of its longer side; strength is the peak opacity of a
# soft radial glow, tinted from that picture's own light so it reads as the same light moving; drift is how far the field wanders, as a
# fraction of the longer side; the phases (0..1) keep the fields out of step. 
LIGHTS = {
    "morning": ((0.55, 0.30, 0.70, (0.86, 0.80, 0.98), 0.22, 0.070, 0.030, 0.00, 0.10),
                (0.15, 0.75, 0.55, (0.70, 0.66, 0.96), 0.16, -0.050, 0.040, 0.45, 0.60)),
    "afternoon": ((0.40, 0.45, 0.70, (0.95, 0.75, 0.98), 0.20, 0.075, 0.045, 0.20, 0.00),
                  (0.70, 0.30, 0.55, (0.85, 0.35, 0.40), 0.16, 0.050, -0.040, 0.70, 0.50),
                  (0.90, 0.85, 0.45, (0.70, 0.30, 0.50), 0.12, -0.055, -0.045, 0.35, 0.25)),
    "evening": ((0.25, 0.45, 0.70, (0.88, 0.92, 0.92), 0.20, 0.070, 0.035, 0.15, 0.30),
                (0.65, 0.75, 0.55, (0.60, 0.72, 0.72), 0.14, -0.060, 0.030, 0.60, 0.80)),
    "night": ((0.20, 0.38, 0.60, (0.98, 0.50, 0.36), 0.20, 0.070, 0.035, 0.05, 0.40),
              (0.55, 0.60, 0.50, (0.55, 0.15, 0.18), 0.16, -0.055, -0.040, 0.55, 0.90)),
}
PULSE_DEPTH = 0.6             # a light swells and settles by this fraction of its strength: slow, never a flash


def art_dir(root=None):
    """The folder the pictures are read from (absolute; config.AMBIENT_ART_DIR is relative to the app's folder)."""
    path = config.AMBIENT_ART_DIR
    if os.path.isabs(path):
        return path
    return os.path.join(root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), path)


def art_path(period, root=None):
    """The picture file for a period, or None."""
    folder = art_dir(root)
    for ext in EXTENSIONS:
        candidate = os.path.join(folder, period + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


def art_files(root=None):
    """{period: path or None} for all four."""
    return {p: art_path(p, root) for p in PERIODS}


def missing(root=None):
    """The periods that have no picture yet."""
    return [p for p, path in art_files(root).items() if path is None]


def crop(view_w, view_h, img_w, img_h, focus=(0.5, 0.5)):
    """The part of the picture to show, as (x, y, w, h) fractions (CALayer.contentsRect) - the picture fills the
    window without stretching, and the crop slides so `focus` stays as central as the picture allows."""
    if min(view_w, view_h, img_w, img_h) <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    view_aspect, img_aspect = view_w / view_h, img_w / img_h
    if view_aspect > img_aspect:                       # the window is wider: keep the full width, trim height
        w, h = 1.0, img_aspect / view_aspect
    else:                                              # taller: keep the full height, trim width
        w, h = view_aspect / img_aspect, 1.0
    x = min(1.0 - w, max(0.0, focus[0] - w / 2))
    y = min(1.0 - h, max(0.0, focus[1] - h / 2))
    return (x, y, w, h)


def seconds_between(lo_hi, phase):
    """A duration inside (lo, hi), spread by a 0..1 phase so no two lights share a rhythm."""
    lo, hi = lo_hi
    return lo + (hi - lo) * phase

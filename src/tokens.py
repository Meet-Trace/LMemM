"""LMemM - the design tokens that are not colours: spacing, radii, type, tracking and motion. Plain numbers, no UI,
so tests/test_tokens.py can hold them on any machine. Colours live in palette.py. Values come from the website
(trace-landing/hero.css): Instrument Serif for display, Figtree for everything else, radii 5 / 8 / 14 / full, one
easing curve. The ui files take their numbers from here and from setup_kit's primitives, not from literals.
"""

SPACE = (4, 8, 12, 16, 24, 32, 48)                 # the spacing scale (points)
RADIUS = {"s": 5, "m": 8, "l": 14, "full": 999}    # tags and keys / fields and buttons / panels and dialogs / pills
SIDEBAR_W = 250
CONTENT_MIN, CONTENT_MAX = 560, 980                # the page's reading column
PAD = 32                                           # the page's side padding

# Type scale (points). Display is Instrument Serif (the page title, the project name, the greeting, an empty state's
# heading); everything else is Figtree. A window heading is never above 34 (desktop density).
DISPLAY = {"xl": 34, "l": 26, "m": 20}
TEXT = {"body": 15, "label": 13, "meta": 11}
# The sizes the UI sets text at (the ui files say SZ["label"], never 13). Display (the serif) is DISPLAY above.
SIZE = {"micro": TEXT["meta"], "small": 12, "label": TEXT["label"], "ui": 14, "body": TEXT["body"], "lead": 16, "title": 17,
        "head": 18, "section": 20, "page": 24, "hero": 25, "welcome": 29}
WINDOW_HEADING_MAX = 34
TRACK = {"display": -0.03, "body": 0.0, "meta": 0.012}       # em
LEAD = {"display": 1.06, "body": 1.42, "meta": 1.3}

# Motion: the site's curve, cubic-bezier(.2, .7, .1, 1); durations in seconds (a window is quicker than a page).
EASE = (0.2, 0.7, 0.1, 1.0)
DURATION = {"press": 0.10, "pop": 0.14, "modal": 0.20, "step": 0.22, "exit": 0.09, "celebrate": 0.25}
POP_SCALE, MODAL_SCALE = 0.97, 0.96

# Surfaces
HAIRLINE = 1                                       # the one border width
SHADOW = {"opacity": 0.12, "radius": 18, "dy": 6}  # the one soft shadow, on floating things only (dialog, toast, pill card)

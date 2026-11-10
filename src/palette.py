"""LMemM - the few hard-coded colours, and the contrast they must keep. Plain numbers, no UI, so
tests/test_palette.py can hold them to WCAG on any machine. The ui files take their colours from here.

    The pill is light in dark mode and dark in light mode, so a mark drawn on it has two colours.
    Marks (a dot, a tick, a waveform) need 3:1; text needs 4.5:1.
"""

PILL_LIGHT = (0.97, 0.97, 0.97)        # the pill itself in dark mode
PILL_DARK = (0.09, 0.09, 0.09)         # ... and in light mode
CARD_LIGHT = (0.98, 0.98, 0.97)        # the window's card colour
CARD_DARK = (0.133, 0.133, 0.145)

ACCENT_ON_DARK_PILL = (0.40, 0.60, 1.0)        # light mode: the pill is dark
ACCENT_ON_LIGHT_PILL = (0.17, 0.40, 0.92)      # dark mode: the pill is light
RED_ON_DARK_PILL = (1.0, 0.54, 0.44)
RED_ON_LIGHT_PILL = (0.74, 0.22, 0.14)

# green, orange and red words on the light card (the system colours are 2.1, 2.1 and 3.4 : 1 there); dark mode keeps the system ones
GREEN_TEXT_ON_LIGHT = (0.10, 0.52, 0.22)
ORANGE_STATUS_ON_LIGHT = (0.75, 0.30, 0.02)
RED_TEXT_ON_LIGHT = (0.80, 0.15, 0.12)
TOAST_LIGHT, TOAST_DARK = 0.12, 0.22                   # the toast's grey in each mode (the dark page is 0.133)

# The main window's colours: a neutral graphite, soft off-white text with a touch of warmth, and Trace's periwinkle as
# the one accent. Not brown, not navy. The window is drawn in the dark appearance (config.WINDOW_APPEARANCE).
# canvas = the page behind everything; sidebar = a step darker; surface = a grouped panel; inset = a field sitting in
# a surface; edge = the one hairline colour. Two treatments of the same layout (config.PAGE_THEME):
#   a  graphite with tonal layering
#   b  deeper graphite, higher contrast, and a faint blue light behind the screenshot (window.py draws the light)
THEMES = {
    "a": {"canvas": (0.098, 0.102, 0.118), "sidebar": (0.082, 0.086, 0.102), "surface": (0.141, 0.145, 0.165),
          "inset": (0.110, 0.114, 0.129), "edge": (0.204, 0.208, 0.231),
          "ink": (0.945, 0.933, 0.910), "mute": (0.647, 0.651, 0.686)},
    "b": {"canvas": (0.078, 0.082, 0.098), "sidebar": (0.063, 0.067, 0.078), "surface": (0.118, 0.122, 0.141),
          "inset": (0.090, 0.094, 0.110), "edge": (0.176, 0.180, 0.204),
          "ink": (0.945, 0.933, 0.910), "mute": (0.647, 0.651, 0.686)},
}
GLOW = {"a": 0.0, "b": 0.11, "header": 0.05, "tone": 0.035}   # opacity of the light: behind the screenshot, behind the header, and the top-lit tone
WARM = dict(THEMES["b"])                          # the colours in use; use() swaps them


def use(theme):
    """Switch the window to a treatment ("a" or "b"). Unknown names keep the current one."""
    if theme in THEMES:
        WARM.clear()
        WARM.update(THEMES[theme])
    return dict(WARM)


ACCENT = (0.208, 0.388, 0.914)                 # #3563E9 buttons: white text on this
ACCENT_TEXT_ON_LIGHT = ACCENT                  # blue words on the light card
ACCENT_TEXT_ON_DARK = (0.467, 0.600, 1.0)      # #7799FF blue words on the dark card (a lighter blue)


def _lin(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colours, 1 to 21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)

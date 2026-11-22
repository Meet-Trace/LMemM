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

# The main window's colours, taken from the website (trace-landing/hero.css): warm ivory paper with ink-blue text and one
# blue accent in light; the same layout in a neutral graphite in dark. The window follows the Mac's appearance
# (config.WINDOW_APPEARANCE): "ivory" when it is light, config.PAGE_THEME when it is dark.
# canvas = the page behind everything; sidebar = a step darker; surface = a grouped panel (paper); inset = a field sitting
# in a surface; edge = the one hairline colour; ink / mute / soft = text, quieter text, quietest text (soft is for
# metadata at 3:1, never for sentences). accent = fills (white words on it); accent_text = blue words; error and
# success_text = words, success = the base of the quiet green tint behind "Decided".
#   ivory  the website: #f4eddf canvas, #191a20 ink, #2856d9 accent (light)
#   a      graphite with tonal layering (dark)
#   b      deeper graphite, higher contrast, and a faint blue light behind the screenshot (window.py draws the light)
ACCENT = (0.157, 0.337, 0.851)                 # #2856d9 the site's blue; buttons: white text on this
ACCENT_TEXT_ON_LIGHT = (0.122, 0.263, 0.690)   # #1f43b0 the site's deep blue: blue words on a light ground
ACCENT_TEXT_ON_DARK = (0.467, 0.600, 1.0)      # #7799FF blue words on a dark ground (a lighter blue)

THEMES = {
    "ivory": {"canvas": (0.957, 0.929, 0.875), "sidebar": (0.925, 0.894, 0.835), "surface": (0.984, 0.980, 0.965),
              "inset": (0.918, 0.906, 0.882), "edge": (0.886, 0.870, 0.835),
              "ink": (0.098, 0.102, 0.125), "mute": (0.290, 0.302, 0.345), "soft": (0.337, 0.349, 0.392),
              "accent": ACCENT, "accent_text": ACCENT_TEXT_ON_LIGHT, "error": (0.702, 0.220, 0.173),
              "success": (0.227, 0.561, 0.353), "success_text": (0.140, 0.400, 0.240)},
    "a": {"canvas": (0.098, 0.102, 0.118), "sidebar": (0.082, 0.086, 0.102), "surface": (0.141, 0.145, 0.165),
          "inset": (0.110, 0.114, 0.129), "edge": (0.204, 0.208, 0.231),
          "ink": (0.945, 0.933, 0.910), "mute": (0.647, 0.651, 0.686), "soft": (0.560, 0.565, 0.600),
          "accent": ACCENT, "accent_text": ACCENT_TEXT_ON_DARK, "error": (1.0, 0.54, 0.44),
          "success": (0.35, 0.78, 0.50), "success_text": (0.45, 0.82, 0.58)},
    "b": {"canvas": (0.078, 0.082, 0.098), "sidebar": (0.063, 0.067, 0.078), "surface": (0.118, 0.122, 0.141),
          "inset": (0.090, 0.094, 0.110), "edge": (0.176, 0.180, 0.204),
          "ink": (0.945, 0.933, 0.910), "mute": (0.647, 0.651, 0.686), "soft": (0.560, 0.565, 0.600),
          "accent": ACCENT, "accent_text": ACCENT_TEXT_ON_DARK, "error": (1.0, 0.54, 0.44),
          "success": (0.35, 0.78, 0.50), "success_text": (0.45, 0.82, 0.58)},
}
# state colours for dots, stripes and icons (not words: words use accent_text / success_text / error and the status_text ones)
STATE_GREEN, STATE_ORANGE, STATE_GRAY, STATE_RED = (0.18, 0.62, 0.36), (0.91, 0.45, 0.16), (0.50, 0.50, 0.55), (0.86, 0.27, 0.25)
# the tint of a thing's kind icon on the pill's card (one quiet set; widget.ICONS maps each kind to one)
KIND_TINT = {"violet": (0.48, 0.35, 0.94), "blue": (0.25, 0.48, 0.88), "green": (0.18, 0.61, 0.38), "teal": (0.09, 0.65, 0.54),
             "red": (0.84, 0.34, 0.24), "grey": (0.45, 0.45, 0.5)}
LIGHT_THEMES = ("ivory",)                         # the treatments with a light ground (the rest are dark)
GLOW = {"a": 0.0, "b": 0.11, "ivory": 0.0, "header": 0.05, "tone": 0.035}   # opacity of the light: behind the screenshot, behind the header, and the top-lit tone
IVORY_WASH = 0.5                                  # opacity of the site's faint warm radial over the ivory canvas
WARM = dict(THEMES["b"])                          # the colours in use; use() swaps them
CURRENT = ["b"]                                   # the name of the treatment in use


def use(theme):
    """Switch the window to a treatment ("ivory", "a" or "b"). Unknown names keep the current one."""
    if theme in THEMES:
        WARM.clear()
        WARM.update(THEMES[theme])
        CURRENT[0] = theme
    return dict(WARM)


def is_light():
    """True when the treatment in use has a light ground."""
    return CURRENT[0] in LIGHT_THEMES


def theme_for(dark, dark_theme="b"):
    """The treatment for the Mac's appearance: ivory in light, `dark_theme` (config.PAGE_THEME) in dark."""
    return dark_theme if dark else "ivory"


def _lin(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colours, 1 to 21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)

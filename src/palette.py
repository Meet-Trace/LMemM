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

# The thing page's warm surfaces: (dark mode, light mode). Near-black with a touch of brown, each surface one step
# lighter than the one under it; in light mode, warm paper. The window's other pages keep the system colours.
WARM = {"canvas": ((0.082, 0.075, 0.071), (0.977, 0.967, 0.949)),
        "surface": ((0.125, 0.114, 0.106), (0.988, 0.982, 0.968)),
        "raised": ((0.168, 0.153, 0.141), (1.0, 1.0, 1.0)),
        "edge": ((0.255, 0.235, 0.218), (0.88, 0.86, 0.83)),
        "ink": ((0.94, 0.92, 0.89), (0.13, 0.12, 0.11)),
        "mute": ((0.69, 0.66, 0.62), (0.38, 0.36, 0.34))}

ACCENT = (0.17, 0.40, 0.92)                    # buttons: white text on this
ACCENT_TEXT_ON_LIGHT = ACCENT                  # blue words on the light card
ACCENT_TEXT_ON_DARK = (0.40, 0.60, 1.0)        # blue words on the dark card (a lighter blue)


def _lin(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colours, 1 to 21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)

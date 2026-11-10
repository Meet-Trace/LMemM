"""LMemM - drawing pieces for the first-run window: the Figtree type, text that is truly centred,
buttons, chips, keys and note boxes. Layout lives in onboarding_ui.py; the words and the rules live
in onboarding.py. This file is only paint, and it is used by the setup window and the main window.

All text is drawn by `_KitText`, not by NSTextField, so alignment and vertical centring are exact
(a label inside a button is centred on both axes, always).
"""

import os

import objc
from AppKit import (NSAttributedString, NSBezierPath, NSColor, NSFont, NSFontAttributeName,
                    NSForegroundColorAttributeName, NSImage, NSImageView, NSKernAttributeName, NSMakeRect, NSMakeSize,
                    NSMutableParagraphStyle, NSParagraphStyleAttributeName)

import palette
from widget import _Control, _Flipped

LEFT, CENTER, RIGHT = 0, 1, 2                         # NSTextAlignment on current macOS (the old 1 = right, 2 = centre is gone)
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")
_FONT_FILE = "Figtree[wght].ttf"
_NAMES = {400: "Figtree-Regular", 500: "Figtree-Medium", 600: "Figtree-SemiBold", 700: "Figtree-Bold"}
_WGHT = 2003265652                                  # the 'wght' variation axis tag
_registered = None
_fonts = {}


# ---------------------------------------------------------------- colour

ACCENT = (0.17, 0.40, 0.92)                          # a calm blue, in place of the pill's orange (= palette.ACCENT)


def accent(alpha=1.0):
    """The blue for fills (a button with white words on it)."""
    return NSColor.colorWithRed_green_blue_alpha_(*ACCENT, alpha)


def accent_text():
    """The blue for blue *words* and thin marks: a lighter blue on the dark card so it can still be read."""
    try:
        def pick(appearance):
            dark = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) \
                == "NSAppearanceNameDarkAqua"
            r, g, b = palette.ACCENT_TEXT_ON_DARK if dark else palette.ACCENT_TEXT_ON_LIGHT
            return NSColor.colorWithRed_green_blue_alpha_(r, g, b, 1.0)
        return NSColor.colorWithName_dynamicProvider_(None, pick)
    except Exception:
        return accent()


def status_text(kind):
    """Green, orange or red *words*: the system colour in dark mode, a deeper one on the light card so it reads."""
    light = {"green": palette.GREEN_TEXT_ON_LIGHT, "orange": palette.ORANGE_STATUS_ON_LIGHT, "red": palette.RED_TEXT_ON_LIGHT}[kind]
    system = {"green": NSColor.systemGreenColor, "orange": NSColor.systemOrangeColor, "red": NSColor.systemRedColor}[kind]
    try:
        def pick(appearance):
            dark = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) \
                == "NSAppearanceNameDarkAqua"
            return system() if dark else NSColor.colorWithRed_green_blue_alpha_(*light, 1.0)
        return NSColor.colorWithName_dynamicProvider_(None, pick)
    except Exception:
        return system()


def ink():
    return NSColor.labelColor()


def mute():
    return NSColor.secondaryLabelColor()


def faint(alpha=0.06):
    return NSColor.labelColor().colorWithAlphaComponent_(alpha)


def line():
    return NSColor.labelColor().colorWithAlphaComponent_(0.16)


def card_background():
    """The card's own colour (the mock-up's #fafaf8 / #222225), following light and dark."""
    try:
        def pick(appearance):
            dark = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) \
                == "NSAppearanceNameDarkAqua"
            return (NSColor.colorWithRed_green_blue_alpha_(0.133, 0.133, 0.145, 1.0) if dark
                    else NSColor.colorWithRed_green_blue_alpha_(0.98, 0.98, 0.97, 1.0))
        return NSColor.colorWithName_dynamicProvider_(None, pick)
    except Exception:
        return NSColor.windowBackgroundColor()


# ---------------------------------------------------------------- type

def register_fonts():
    """Make the bundled Figtree available to this process. True when it is. Never raises; if the
    file is missing or macOS refuses it, the system font is used and nothing else changes."""
    global _registered
    if _registered is None:
        _registered = False
        try:
            from CoreText import CTFontManagerRegisterFontsForURL, kCTFontManagerScopeProcess
            from Foundation import NSURL
            path = os.path.normpath(os.path.join(_FONT_DIR, _FONT_FILE))
            if os.path.exists(path):
                CTFontManagerRegisterFontsForURL(NSURL.fileURLWithPath_(path), kCTFontManagerScopeProcess, None)
                _registered = NSFont.fontWithName_size_("Figtree-Regular", 14) is not None \
                    or NSFont.fontWithName_size_("Figtree", 14) is not None
        except Exception:
            _registered = False
    return _registered


def font(size, weight=400):
    """Figtree at this size and weight (400, 500, 600, 700); the system font if Figtree is missing."""
    key = (size, weight)
    if key not in _fonts:
        found = None
        if register_fonts():
            found = NSFont.fontWithName_size_(_NAMES.get(weight, "Figtree-Regular"), size)
            if found is None:
                try:
                    base = NSFont.fontWithName_size_("Figtree", size)
                    descriptor = base.fontDescriptor().fontDescriptorByAddingAttributes_(
                        {"NSCTFontVariationAttribute": {_WGHT: float(weight)}})
                    found = NSFont.fontWithDescriptor_size_(descriptor, size)
                except Exception:
                    found = None
        if found is None:
            found = NSFont.systemFontOfSize_weight_(size, {400: 0.0, 500: 0.23, 600: 0.3, 700: 0.4}.get(weight, 0.0))
        _fonts[key] = found
    return _fonts[key]


def _attributed(text, size, weight, color, align, wrap):
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(align)
    style.setLineBreakMode_(0 if wrap else 4)
    style.setLineHeightMultiple_(1.18 if wrap else 1.0)
    attrs = {NSFontAttributeName: font(size, weight), NSForegroundColorAttributeName: color,
             NSParagraphStyleAttributeName: style}
    if size >= 24:                                   # large type reads too loose: tighten it as it grows
        attrs[NSKernAttributeName] = -0.02 * size
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def text_width(text, size, weight=400):
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font(size, weight)}).size().width


def text_height(text, size, weight, width, wrap=True):
    """The height this text needs at this width (one line when it does not wrap)."""
    shown = _attributed(text, size, weight, ink(), LEFT, wrap)
    rect = shown.boundingRectWithSize_options_(NSMakeSize(width, 100000), 1)
    return float(int(rect.size.height) + 1)


class _KitText(_Flipped):
    """Text drawn in its own frame: aligned left, centre or right, and optionally centred
    vertically (button labels). Several lines when `wrap`."""

    def initWithText_size_weight_color_align_wrap_middle_(self, text, size, weight, color, align, wrap, middle):
        self = objc.super(_KitText, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.text, self.size, self.weight, self.color = text, size, weight, color
        self.align, self.wrap, self.middle = align, wrap, middle
        self.attributed = None                       # set to draw mixed weights (a bold lead-in)
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        shown = self.attributed if self.attributed is not None else _attributed(self.text, self.size, self.weight, self.color, self.align, self.wrap)
        height = shown.boundingRectWithSize_options_(NSMakeSize(bounds.size.width, 100000), 1).size.height
        y = (bounds.size.height - height) / 2 if self.middle else 0
        shown.drawWithRect_options_(NSMakeRect(0, y, bounds.size.width, height + 2), 1)

    def hitTest_(self, point):
        return None                                  # clicks go to whatever holds the text

    # VoiceOver: this view draws its own words, so it has to say them. Inside a pressable control the
    # control speaks for them (_Control.accessibilityLabel), so they are not read twice.
    def isAccessibilityElement(self):
        view = self.superview()
        while view is not None:
            if isinstance(view, _Control) and view.live():
                return False
            view = view.superview()
        return bool(self.text)

    def accessibilityRole(self):
        return "AXStaticText"

    def accessibilityLabel(self):
        return self.text

    def accessibilityValue(self):
        return self.text

    def setText_(self, text):
        self.text = text
        self.setNeedsDisplay_(True)


def put_text(parent, text, x, y, w, size=15, weight=400, color=None, align=LEFT, wrap=True, height=None, middle=False):
    """Add text to `parent`; returns the height it takes."""
    h = height if height is not None else text_height(text, size, weight, w, wrap)
    view = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(
        text, size, weight, color or ink(), align, wrap, middle)
    view.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(view)
    try:
        if not wrap and text and text_width(text, size, weight) > w + 1 and not parent.toolTip():
            parent.setToolTip_(text)                 # a cut-off name can still be read (the text view itself takes no mouse)
    except Exception:
        pass
    return h


# ---------------------------------------------------------------- pieces

def icon(name, size, color, frame):
    """An SF Symbol, tinted; None when this macOS has no such symbol."""
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None) if name else None
    if image is None:
        return None
    holder = NSImageView.alloc().initWithFrame_(NSMakeRect(*frame))
    holder.setImage_(image)
    holder.setImageScaling_(3)                       # proportionally up or down
    holder.setContentTintColor_(color)
    return holder


class _KitButton(_Control):
    """A rounded button. fill / border are colours or None; the label is centred on both axes.
    Anything else (a chip's icon) is added as a subview by the caller. Hover, press, Tab, the focus ring
    and VoiceOver come from _Control; callback=None makes it inert (a holder, or a disabled button)."""

    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(_KitButton, self).initWithFrame_callback_(frame, callback)
        self.focusable = True
        self.fill = self.border = None
        self.radius = 12
        self.label = None
        return self

    def drawRect_(self, rect):
        b = self.bounds()
        if self.fill is not None:
            self.fill.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, self.radius, self.radius).fill()
        if self.border is not None:
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0.75, 0.75, b.size.width - 1.5, b.size.height - 1.5), self.radius, self.radius)
            path.setLineWidth_(1.5)
            self.border.setStroke()
            path.stroke()
        self.paint_state()


def button(parent, label, x, y, w, h, callback, kind="primary", size=16, weight=600, enabled=True):
    """kind: primary (blue, white text), quiet (soft grey), ghost (just text), outline (bordered), link (blue words)."""
    tap = _KitButton.alloc().initWithFrame_callback_(NSMakeRect(x, y, w, h), callback if enabled else None)
    color = ink()
    if kind == "primary":
        tap.fill, color = accent(), NSColor.whiteColor()
    elif kind == "quiet":
        tap.fill = faint(0.07)
    elif kind == "outline":
        tap.border = line()
    elif kind == "ghost":
        color = mute()
    elif kind == "link":                              # just blue words (the lighter blue on the dark card)
        color = accent_text()
    if label:
        text = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(label, size, weight, color, CENTER, False, True)
        text.setFrame_(NSMakeRect(0, 0, w, h))
        tap.addSubview_(text)
        tap.label = text
    tap.radius = 12 if h >= 40 else 9
    tap.setAlphaValue_(1.0 if enabled else 0.35)
    parent.addSubview_(tap)
    return tap


def chip(parent, name, symbol, x, y, on, callback):
    """A role chip: icon and word, a pill. Unselected is outlined with ink text; selected is blue with
    white text, so it can always be read, on a light card and on a dark one."""
    size, pad, gap, icon_w = 14, 14, 7, 15
    has_icon = symbol is not None and NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None) is not None
    width = pad * 2 + text_width(name, size, 500) + ((icon_w + gap) if has_icon else 0)
    tap = _KitButton.alloc().initWithFrame_callback_(NSMakeRect(x, y, width, 32), callback)
    tap.radius = 16
    color = NSColor.whiteColor() if on else ink()
    if on:
        tap.fill = accent()
    else:
        tap.border = line()
    text_x = pad
    if has_icon:
        holder = icon(symbol, 14, color, (pad, 8, icon_w, 16))
        tap.addSubview_(holder)
        text_x = pad + icon_w + gap
    label = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(name, size, 500, color, LEFT, False, True)
    label.setFrame_(NSMakeRect(text_x, 0, width - text_x - pad, 32))
    tap.addSubview_(label)
    parent.addSubview_(tap)
    return width


def chip_width(name, symbol):
    has_icon = symbol is not None and NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None) is not None
    return 28 + text_width(name, 14, 500) + ((22) if has_icon else 0)


class _KitNote(_Flipped):
    """A soft rounded box behind a note: green for good news, amber for a warning, grey for the rest."""

    def initWithTone_(self, tone):
        self = objc.super(_KitNote, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.tone = tone
        return self

    def drawRect_(self, rect):
        base = {"good": NSColor.systemGreenColor(), "warn": NSColor.systemOrangeColor(),
                "red": NSColor.systemRedColor()}.get(self.tone)
        (base.colorWithAlphaComponent_(0.13) if base is not None else faint(0.06)).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 12, 12).fill()


def note(parent, tone, text, x, y, w, lead=None, symbol=None):
    """A note box. `lead` is a bold first phrase; `symbol` an SF Symbol to lead with (the tone has a
    default). Returns its height."""
    symbol = symbol or {"good": "checkmark.circle.fill", "warn": "exclamationmark.triangle.fill",
                        "red": "exclamationmark.circle.fill"}.get(tone)
    left = 14 + (26 if symbol else 0)
    inner = w - left - 14
    body = (lead + " " if lead else "") + text
    h = max(text_height(body, 14, 400, inner), 18) + 24
    box = _KitNote.alloc().initWithTone_(tone)
    box.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(box)
    if symbol:
        color = {"good": NSColor.systemGreenColor(), "red": NSColor.systemRedColor(),
                 "warn": NSColor.systemOrangeColor()}.get(tone, mute())
        holder = icon(symbol, 18, color, (14, 12, 18, 18))
        if holder is not None:
            box.addSubview_(holder)
    shown = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(body, 14, 400, ink(), LEFT, True, False)
    shown.setFrame_(NSMakeRect(left, 12, inner, h - 24))
    if lead:
        attributed = _attributed(body, 14, 400, ink(), LEFT, True).mutableCopy()
        attributed.addAttribute_value_range_(NSFontAttributeName, font(14, 700), (0, len(lead)))
        shown.attributed = attributed
    box.addSubview_(shown)
    return h


class _KitMark(_Flipped):
    """The app's mark: a dark tile with the pill's shape, a white capsule and a small blue dot."""

    def drawRect_(self, rect):
        NSColor.colorWithRed_green_blue_alpha_(0.11, 0.11, 0.125, 1.0).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 15, 15).fill()
        NSColor.whiteColor().setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(14, 26, 24, 8), 4, 4).fill()
        accent().setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(42, 26, 8, 8)).fill()


class _KitDots(_Flipped):
    """Five little dots; the ones reached are blue."""

    def initWithIndex_(self, index):
        self = objc.super(_KitDots, self).initWithFrame_(NSMakeRect(0, 0, 5 * 13, 8))
        self.index = index
        return self

    def drawRect_(self, rect):
        for i in range(5):
            (accent() if i <= self.index else faint(0.22)).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(i * 13 + 1, 1, 6, 6)).fill()


class _KitKey(_Flipped):
    """A keyboard key: a rounded square with a thicker bottom edge."""

    def initWithGlyph_(self, glyph):
        self = objc.super(_KitKey, self).initWithFrame_(NSMakeRect(0, 0, 56, 58))
        label = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(glyph, 24, 600, ink(), CENTER, False, True)
        label.setFrame_(NSMakeRect(0, 0, 56, 54))
        self.addSubview_(label)
        return self

    def drawRect_(self, rect):
        top = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.75, 0.75, 54.5, 53.5), 13, 13)
        faint(0.04).setFill()
        top.fill()
        top.setLineWidth_(1.5)
        line().setStroke()
        top.stroke()
        edge = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(6, 53, 44, 3.5), 1.7, 1.7)
        faint(0.28).setFill()
        edge.fill()


class _KitBox(_Flipped):
    """A rounded outline: the name field, the search field. Blue while it has the cursor."""

    def initWithFocus_(self, focus):
        self = objc.super(_KitBox, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.focus = focus
        return self

    def drawRect_(self, rect):
        b = self.bounds()
        if self.focus:
            accent(0.14).setStroke()
            glow = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(1, 1, b.size.width - 2, b.size.height - 2), 13, 13)
            glow.setLineWidth_(4)
            glow.stroke()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.75, 0.75, b.size.width - 1.5, b.size.height - 1.5), 12, 12)
        path.setLineWidth_(1.5)
        (accent() if self.focus else line()).setStroke()
        path.stroke()


class _KitTile(_Flipped):
    """A permission's icon: a soft rounded square with an SF Symbol; green once granted, red when off."""

    def initWithSymbol_tone_(self, symbol, tone):
        self = objc.super(_KitTile, self).initWithFrame_(NSMakeRect(0, 0, 36, 36))
        self.tone = tone
        color = NSColor.systemGreenColor() if tone == "good" else NSColor.systemRedColor() if tone == "bad" else ink()
        holder = icon(symbol, 18, color, (9, 9, 18, 18))
        if holder is not None:
            self.addSubview_(holder)
        return self

    def drawRect_(self, rect):
        base = NSColor.systemGreenColor() if self.tone == "good" else NSColor.systemRedColor() if self.tone == "bad" else None
        (base.colorWithAlphaComponent_(0.14) if base is not None else faint(0.07)).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()


class _KitColorTile(_Flipped):
    """A soft rounded square in one colour with an SF Symbol in it: a project's icon."""

    def initWithColor_symbol_size_(self, color, symbol, size):
        self = objc.super(_KitColorTile, self).initWithFrame_(NSMakeRect(0, 0, size, size))
        self.color = color
        self.size = size
        holder = icon(first_symbol(symbol if isinstance(symbol, (list, tuple)) else [symbol]),
                      size * 0.52, color, (size * 0.24, size * 0.24, size * 0.52, size * 0.52))
        if holder is not None:
            self.addSubview_(holder)
        return self

    def drawRect_(self, rect):
        self.color.colorWithAlphaComponent_(0.16).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), self.size * 0.28, self.size * 0.28).fill()


def tile(parent, symbol, color, x, y, size=28):
    """A coloured icon square at (x, y). `symbol` is an SF Symbol name or a list to try in order."""
    view = _KitColorTile.alloc().initWithColor_symbol_size_(color, symbol, size)
    view.setFrame_(NSMakeRect(x, y, size, size))
    parent.addSubview_(view)
    return view


class _KitCanvas(_Flipped):
    """A view that paints the card's colour (the app picker covers the screen with one)."""

    def drawRect_(self, rect):
        card_background().setFill()
        NSBezierPath.fillRect_(self.bounds())


class _KitRule(_Flipped):
    """A hairline between permission rows."""

    def drawRect_(self, rect):
        faint(0.10).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, self.bounds().size.width, 1))


def first_symbol(names):
    """The first SF Symbol this macOS has, from a list of names (older systems lack the newer ones)."""
    for name in names:
        if NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None) is not None:
            return name
    return None

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
import tokens
from widget import _Control, _Flipped

LEFT, CENTER, RIGHT = 0, 1, 2                         # NSTextAlignment on current macOS (the old 1 = right, 2 = centre is gone)
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")
_FONT_FILE = "Figtree[wght].ttf"
_SERIF_FILES = ("InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf")      # the website's display face (SIL OFL, next to its licence)
_NAMES = {400: "Figtree-Regular", 500: "Figtree-Medium", 600: "Figtree-SemiBold", 700: "Figtree-Bold"}
_WGHT = 2003265652                                  # the 'wght' variation axis tag
_registered = None
_fonts = {}


# ---------------------------------------------------------------- colour

ACCENT = palette.ACCENT                              # the website's blue, #2856d9 (one accent for the whole app)


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


def warm(token):
    """A colour of the main window's warm-light scheme (palette.WARM)."""
    return NSColor.colorWithRed_green_blue_alpha_(*palette.WARM[token], 1.0)


def _by_appearance(light, dark):
    """A colour that is `light` (r, g, b) on a light ground and `dark` on a dark one."""
    try:
        def pick(appearance):
            is_dark = appearance.bestMatchFromAppearancesWithNames_(["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) \
                == "NSAppearanceNameDarkAqua"
            return NSColor.colorWithRed_green_blue_alpha_(*(dark if is_dark else light), 1.0)
        return NSColor.colorWithName_dynamicProvider_(None, pick)
    except Exception:
        return NSColor.labelColor()


def ink():
    """Text: the website's ink (#191a20) on a light ground, soft warm white on a dark one."""
    return _by_appearance(palette.THEMES["ivory"]["ink"], palette.THEMES["b"]["ink"])


def mute():
    """Quieter text: the website's muted ink on a light ground."""
    return _by_appearance(palette.THEMES["ivory"]["mute"], palette.THEMES["b"]["mute"])


def soft():
    """The quietest text (metadata, times): never for sentences."""
    return _by_appearance(palette.THEMES["ivory"]["soft"], palette.THEMES["b"]["soft"])


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

def _register_file(name):
    """Register one bundled font file for this process; True when the file is there and macOS took it."""
    try:
        from CoreText import CTFontManagerRegisterFontsForURL, kCTFontManagerScopeProcess
        from Foundation import NSURL
        path = os.path.normpath(os.path.join(_FONT_DIR, name))
        if os.path.exists(path):
            CTFontManagerRegisterFontsForURL(NSURL.fileURLWithPath_(path), kCTFontManagerScopeProcess, None)
            return True
    except Exception:
        pass
    return False


def register_fonts():
    """Make the bundled Figtree and Instrument Serif available to this process. True when Figtree is. Never raises; if a
    file is missing or macOS refuses it, the system font (or the system serif) is used and nothing else changes."""
    global _registered
    if _registered is None:
        _registered = False
        try:
            if _register_file(_FONT_FILE):
                _registered = NSFont.fontWithName_size_("Figtree-Regular", 14) is not None \
                    or NSFont.fontWithName_size_("Figtree", 14) is not None
            for name in _SERIF_FILES:
                _register_file(name)
        except Exception:
            _registered = False
    return _registered


_SERIF_NAMES = (("InstrumentSerif-Regular", "NewYork-Regular", "Georgia"), ("InstrumentSerif-Italic", "NewYork-RegularItalic", "Georgia-Italic"))


def serif(size, italic=False):
    """The display face (Instrument Serif, as on the website); the system serif (New York, then Georgia) when it is
    missing, then the system font. Never raises."""
    key = ("serif", size, italic)
    if key not in _fonts:
        register_fonts()
        found = None
        for name in _SERIF_NAMES[1 if italic else 0]:
            try:
                found = NSFont.fontWithName_size_(name, size)
            except Exception:
                found = None
            if found is not None:
                break
        _fonts[key] = found if found is not None else NSFont.systemFontOfSize_(size)
    return _fonts[key]


def mono(size, weight=400):
    """The monospaced face for file names, addresses and code (SF Mono; Menlo if this macOS lacks it)."""
    key = ("mono", size, weight)
    if key not in _fonts:
        found = None
        try:
            found = NSFont.monospacedSystemFontOfSize_weight_(size, {400: 0.0, 500: 0.23, 600: 0.3, 700: 0.4}.get(weight, 0.0))
        except Exception:
            found = None
        if found is None:
            found = NSFont.fontWithName_size_("Menlo-Regular", size) or NSFont.systemFontOfSize_(size)
        _fonts[key] = found
    return _fonts[key]


def font(size, weight=400, face="sans"):
    """Figtree at this size and weight (400, 500, 600, 700); the system font if Figtree is missing. `face` "serif" or
    "serif-italic" is the display face, "mono" the monospaced one."""
    if face == "serif" or face == "serif-italic":
        return serif(size, face == "serif-italic")
    if face == "mono":
        return mono(size, weight)
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


# Three type roles. Display: the one heading that names the page (tight, heavy). Body: what you read
# (comfortable leading). Meta: when, where, how long (small, quiet, a little open).
DISPLAY = {"size": tokens.DISPLAY["xl"], "weight": 400, "track": tokens.TRACK["display"], "lead": tokens.LEAD["display"], "face": "serif"}
BODY = {"size": 14, "weight": 400, "track": tokens.TRACK["body"], "lead": tokens.LEAD["body"]}
META = {"size": 12, "weight": 400, "track": tokens.TRACK["meta"], "lead": tokens.LEAD["meta"]}


def _attributed(text, size, weight, color, align, wrap, track=None, lead=None, face="sans"):
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(align)
    style.setLineBreakMode_(0 if wrap else 4)
    style.setLineHeightMultiple_((lead or 1.18) if wrap else 1.0)
    attrs = {NSFontAttributeName: font(size, weight, face), NSForegroundColorAttributeName: color,
             NSParagraphStyleAttributeName: style}
    if track is not None:                            # tracking in em, set by the caller (a role)
        attrs[NSKernAttributeName] = track * size
    elif size >= 24:                                 # large type reads too loose: tighten it as it grows
        attrs[NSKernAttributeName] = -0.02 * size
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def text_width(text, size, weight=400, face="sans"):
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: font(size, weight, face)}).size().width


def text_height(text, size, weight, width, wrap=True, track=None, lead=None, face="sans"):
    """The height this text needs at this width (one line when it does not wrap)."""
    shown = _attributed(text, size, weight, ink(), LEFT, wrap, track, lead, face)
    rect = shown.boundingRectWithSize_options_(NSMakeSize(width, 100000), 1)
    return float(int(rect.size.height) + 1)


class _KitText(_Flipped):
    """Text drawn in its own frame: aligned left, centre or right, and optionally centred
    vertically (button labels). Several lines when `wrap`."""

    def initWithText_size_weight_color_align_wrap_middle_(self, text, size, weight, color, align, wrap, middle):
        self = objc.super(_KitText, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.face = "sans"
        self.text, self.size, self.weight, self.color = text, size, weight, color
        self.align, self.wrap, self.middle = align, wrap, middle
        self.attributed = None                       # set to draw mixed weights (a bold lead-in)
        self.track = self.lead = None
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        shown = self.attributed if self.attributed is not None else _attributed(self.text, self.size, self.weight, self.color, self.align, self.wrap, self.track, self.lead, self.face)
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


def _decorated(text, size, weight, color, align, wrap, track, lead, face, accent_word, mark):
    """The attributed string for put_text's accent word and evidence mark."""
    shown = _attributed(text, size, weight, color, align, wrap, track, lead, face).mutableCopy()
    if mark and text:
        whole = (0, len(text))
        shown.addAttribute_value_range_("NSBackgroundColor", accent(0.16), whole)
        shown.addAttribute_value_range_("NSUnderline", 1, whole)
        shown.addAttribute_value_range_("NSUnderlineColor", accent(), whole)
    if accent_word and accent_word in text:
        at = text.index(accent_word)
        span = (at, len(accent_word))
        shown.addAttribute_value_range_(NSFontAttributeName, serif(size, True), span)
        shown.addAttribute_value_range_(NSForegroundColorAttributeName, accent_text(), span)
    return shown


def put_text(parent, text, x, y, w, size=15, weight=400, color=None, align=LEFT, wrap=True, height=None, middle=False, tip=True, track=None, lead=None,
             face="sans", accent_word=None, mark=False):
    """Add text to `parent`; returns the height it takes. `track` (em) and `lead` (line height) come from a type role.
    `face` is "sans", "serif", "serif-italic" or "mono". `accent_word` is set in italic accent blue inside serif text
    (the website's key word); `mark` highlights the whole run as evidence (a soft blue wash and a blue underline)."""
    h = height if height is not None else text_height(text, size, weight, w, wrap, track, lead, face)
    view = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(
        text, size, weight, color or ink(), align, wrap, middle)
    view.track, view.lead, view.face = track, lead, face
    if accent_word or mark:
        view.attributed = _decorated(text, size, weight, color or ink(), align, wrap, track, lead, face, accent_word, mark)
    view.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(view)
    try:
        if tip and not wrap and text and text_width(text, size, weight) > w + 1 and not parent.toolTip():
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
        self.radius = tokens.RADIUS["m"]
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
    """kind: primary (blue, white text), quiet (soft grey), ghost (just text), outline (bordered), danger (red outline), link (blue words)."""
    tap = _KitButton.alloc().initWithFrame_callback_(NSMakeRect(x, y, w, h), callback if enabled else None)
    color = ink()
    if kind == "primary":
        tap.fill, color = accent(), NSColor.whiteColor()
    elif kind == "quiet":
        tap.fill = faint(0.07)
    elif kind == "outline":
        tap.border = line()
        color = ink()
    elif kind == "danger":                            # destructive: a restrained red outline and red words
        color = status_text("red")
        tap.border = color.colorWithAlphaComponent_(0.55)
    elif kind == "ghost":
        color = mute()
    elif kind == "link":                              # just blue words (the lighter blue on the dark card)
        color = accent_text()
    if label:
        text = _KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(label, size, weight, color, CENTER, False, True)
        text.setFrame_(NSMakeRect(0, 0, w, h))
        tap.addSubview_(text)
        tap.label = text
    tap.radius = tokens.RADIUS["m"]
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
        radius = tokens.RADIUS["m"]
        if self.focus:
            accent(0.14).setStroke()
            glow = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(1, 1, b.size.width - 2, b.size.height - 2), radius + 1, radius + 1)
            glow.setLineWidth_(4)
            glow.stroke()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.75, 0.75, b.size.width - 1.5, b.size.height - 1.5), radius, radius)
        path.setLineWidth_(1.5 if self.focus else tokens.HAIRLINE)       # a hairline at rest, the accent ring while focused
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


def glyph(parent, symbols, x, y, size=18, color=None):
    """One family of line icons for navigation and actions: an outline SF Symbol at a fixed light weight,
    centred in a size x size box, no fill. `symbols` is a name or a list to try in order. Returns the view
    (None when this macOS has none of them)."""
    name = first_symbol(symbols if isinstance(symbols, (list, tuple)) else [symbols])
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None) if name else None
    if image is None:
        return None
    try:
        from AppKit import NSImageSymbolConfiguration
        image = image.imageWithSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(size * 0.82, -0.1, 1))   # light-regular, medium scale
    except Exception:
        pass
    holder = NSImageView.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
    holder.setImage_(image)
    holder.setImageScaling_(3)
    holder.setContentTintColor_(color or mute())
    parent.addSubview_(holder)
    return holder


def display(parent, text, x, y, w, color=None, size=None, accent_word=None, **kw):
    """The page's one display heading (role: display): Instrument Serif, tight, with at most one italic accent word
    (`accent_word`). `size` is one of tokens.DISPLAY's values; a window heading is never above 34."""
    r = dict(DISPLAY)
    if size:
        r["size"] = min(size, tokens.WINDOW_HEADING_MAX)
    return put_text(parent, text, x, y, w, r["size"], r["weight"], color, track=r["track"], lead=r["lead"], face=r["face"],
                    accent_word=accent_word, **kw)


def mono_text(parent, text, x, y, w, color=None, size=12, **kw):
    """File names, addresses and code, in the monospaced face."""
    return put_text(parent, text, x, y, w, size, 400, color or mute(), face="mono", wrap=kw.pop("wrap", False), **kw)


def meta(parent, text, x, y, w, color=None, **kw):
    """Small, quiet facts: when, where, how long (role: metadata)."""
    return put_text(parent, text, x, y, w, META["size"], META["weight"], color or mute(), track=META["track"], lead=META["lead"], **kw)


def body(parent, text, x, y, w, color=None):
    """What you read: descriptions and summaries (role: body)."""
    return put_text(parent, text, x, y, w, BODY["size"], BODY["weight"], color, track=BODY["track"], lead=BODY["lead"])


def tag(parent, kind, text, x, y, height=20):
    """A small label: kind "decided" (the quiet green of a decision), "accent" (blue) or "quiet" (grey; a status). One
    shape everywhere. Returns its width."""
    size = tokens.TEXT["meta"]
    width = int(text_width(text, size, 600)) + 14
    if kind == "decided":
        fill, color = warm("success").colorWithAlphaComponent_(0.14), warm("success_text")
    elif kind == "accent":
        fill, color = accent(0.12), warm("accent_text")
    else:
        fill, color = faint(0.06), mute()
    view = _KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, y, width, height), fill, None, tokens.RADIUS["s"])
    parent.addSubview_(view)
    put_text(view, text, 7, 0, width - 14, size, 600, color, wrap=False, height=height, middle=True, tip=False)
    return width


def segmented(parent, x, y, items, size=13, height=28, pad=14):
    """A track with the chosen segment lifted into a filled pill (the website's tab group): one control for tabs and
    periods. `items` are (label, on, callback). The chosen one is always filled. Returns the width."""
    widths = [int(text_width(label, size, 500)) + 2 * pad for label, _on, _call in items]
    parent.addSubview_(_KitSurface.alloc().initWithFrame_fill_edge_radius_(
        NSMakeRect(x, y, sum(widths) + 4, height), faint(0.055), None, height / 2))
    sx = x + 2
    for (label, on, call), width in zip(items, widths):
        seg = _KitButton.alloc().initWithFrame_callback_(NSMakeRect(sx, y + 2, width, height - 4), call)
        seg.radius = (height - 4) / 2
        seg.ax_label = label + (", selected" if on else "")
        if on:
            seg.fill = warm("surface") if palette.is_light() else faint(0.16)
        parent.addSubview_(seg)
        put_text(seg, label, pad, 0, width - 2 * pad, size, 600 if on else 500, ink() if on else mute(), wrap=False,
                 height=height - 4, middle=True, tip=False)
        sx += width
    return sx + 2 - x


def dot(parent, x, y, size=8, color=None):
    """The brand dot: the small accent circle that stands for a project (never a folder)."""
    view = _KitMarkDot.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
    view.color = color or accent()
    parent.addSubview_(view)
    return view


class _KitMarkDot(_Flipped):
    def drawRect_(self, rect):
        (getattr(self, "color", None) or accent()).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(self.bounds()).fill()

    def hitTest_(self, point):
        return None


def empty_state(parent, title, line_text, x, y, w, accent_word=None, action=None):
    """An empty state, always in the same voice: a serif heading that says what will appear, one muted line on how it
    gets there, and the one useful action if there is one (`action` is (label, callback)). No picture, no sample data.
    Returns the height used."""
    width = min(w, 520)
    h = display(parent, title, x, y, width, size=tokens.DISPLAY["m"], accent_word=accent_word)
    h += 8
    h += put_text(parent, line_text, x, y + h, min(w, 460), tokens.TEXT["body"] - 1, 400, mute(), lead=tokens.LEAD["body"])
    if action:
        h += 16
        label, callback = action
        bw = int(text_width(label, 13, 600)) + 32
        button(parent, label, x, y + h, bw, 34, callback, kind="primary", size=13, weight=600)
        h += 34
    return h


def source_line(parent, text, x, y, w, quote=None):
    """A source: a monospaced line (file, address or app) and, under it, the quoted words marked as evidence (a soft
    blue wash and a blue underline). The same on every screen that shows evidence. Returns the height used."""
    h = mono_text(parent, text, x, y, w, size=12, height=16)
    if quote:
        h += 4 + put_text(parent, quote, x, y + h + 4, w, 13, 400, ink(), mark=True, lead=1.35)
    return h


def tile(parent, symbol, color, x, y, size=28):
    """A coloured icon square at (x, y). `symbol` is an SF Symbol name or a list to try in order."""
    view = _KitColorTile.alloc().initWithColor_symbol_size_(color, symbol, size)
    view.setFrame_(NSMakeRect(x, y, size, size))
    parent.addSubview_(view)
    return view


class _KitSurface(_Flipped):
    """A rounded warm surface, optionally with a thin edge. It paints itself and lets clicks through to what is on it."""

    def initWithFrame_fill_edge_radius_(self, frame, fill, edge, radius):
        self = objc.super(_KitSurface, self).initWithFrame_(frame)
        self.fill, self.edge, self.radius = fill, edge, radius
        return self

    def drawRect_(self, rect):
        box = self.bounds()
        shape = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, box.size.width - 1, box.size.height - 1), self.radius, self.radius)
        if self.fill is not None:
            self.fill.setFill()
            shape.fill()
        if self.edge is not None:
            self.edge.setStroke()
            shape.setLineWidth_(1)
            shape.stroke()

    def hitTest_(self, point):
        return None if self.fill is None else objc.super(_KitSurface, self).hitTest_(point)


def surface(parent, x, y, w, h, token="surface", edge=True, radius=tokens.RADIUS["l"], alpha=None):
    """A warm rounded surface at (x, y); anything added to `parent` afterwards sits on it. `alpha` lets the
    background show through a little (a tinted veil, no blur); its edge is then a faint light line."""
    fill = warm(token) if alpha is None else warm(token).colorWithAlphaComponent_(alpha)
    veil = NSColor.blackColor() if palette.is_light() else NSColor.whiteColor()
    line_color = (warm("edge") if alpha is None else veil.colorWithAlphaComponent_(0.08 if palette.is_light() else 0.10)) if edge else None
    view = _KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, y, w, h), fill, line_color, radius)
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


def field(parent, x, y, w, h, focus=False):
    """The outline of a text field or the search field (hairline, the accent ring while it has the cursor). The one
    style for every field; returns the view to put the field's text on."""
    box = _KitBox.alloc().initWithFocus_(focus)
    box.setFrame_(NSMakeRect(x, y, w, h))
    parent.addSubview_(box)
    return box


class _KitProjectMark(_Flipped):
    """A project's mark: a soft accent tile with the accent dot at its centre."""

    def drawRect_(self, rect):
        box = self.bounds()
        w = box.size.width
        accent(0.10).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, w * 0.28, w * 0.28).fill()
        accent().setFill()
        d = w * 0.30
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect((w - d) / 2, (box.size.height - d) / 2, d, d)).fill()

    def hitTest_(self, point):
        return None


def project_mark(parent, x, y, size=26):
    """How a project is shown wherever it is a row or a title: one mark, never a folder."""
    view = _KitProjectMark.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
    parent.addSubview_(view)
    return view

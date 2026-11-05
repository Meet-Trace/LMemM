"""LMemM - the on-screen pill. A tiny bar at the bottom of the screen, like Wispr Flow.

    resting   a thin pill, nearly invisible
    notes     a small pill with a number: edits waiting on the thing in front
    hover     the pill unfolds to show the first one
    speaking  while the ⌃⌥N note window is open: a waveform and the words so far
              (the note card, notecard.py, now shows these itself, so the pill stays quiet)
    saved     right after a note: a check and "Saved to <thing>", then the count rises
    click     a quiet card above it for the thing in front: its open notes, tick one to
              finish it, and an "Add a note…" row. "N more in <project>" goes one level
              deeper to the whole project (grouped by thing, finished notes behind "Done").
              Click the pill again, or anywhere else, to close it.

    suggest   when the model thinks some things belong together and you have no notes waiting
              here, the pill shows a small link mark (the first few times with the words "Group
              these?"). Its card shows the things as icons, a name you can change, "Create project"
              and "Not these". Clicking anywhere else is "not now".
    project   the card's last row says where this thing lives ("Not in a project. Add…").
              It opens a picker: your projects, search, "+ New project", and a tick to file the
              other things open lately along with this one.

    marks     the pill wears an icon when something is wrong (screen access off, a private window,
              the mic off) or, on hover, when there is nothing to show (no notes yet, all done,
              no projects yet). The rules are in rules.py (R8).

    finishing one tap flips a note. A finished note stays 1.2 s crossed out (tap it to take it back),
              then folds away. The pill counts open notes at once. When the last one folds the card
              says "All caught up" once (it still has "Add a note…"); a "Done" row lists this thing's
              finished notes by day, tap a ring to reopen. "Marked done · Undo" reverts the last
              batch for 6 s (rules.py R10).

    pause     a plain "Pause LMemM" row ends the card; it asks for how long (1 hour, until tomorrow,
              until you resume). Any pause, yours or "you stepped away", is one pause mark on the
              pill; its card says which, when it ends, and has Resume if you paused it (rules.py R9).

Everything is a non-activating panel: it never takes focus from the app you're in,
and it shows on every Space and over full-screen apps. It follows the system light/dark
setting. What the card shows is decided in notes.card_view() (plain data, tested);
this file only draws it. Ticking a box calls back into the tracker, which saves under its lock.
"""

import math
import subprocess
import time
from datetime import datetime

import objc
from Foundation import NSObject, NSPointInRect
from AppKit import (NSApplication, NSBackingStoreBuffered, NSBezierPath, NSColor, NSEvent, NSImage,
                    NSAnimationContext, NSImageView, NSFont, NSFontAttributeName, NSForegroundColorAttributeName, NSMakeRect,
                    NSPanel, NSScreen, NSScrollView, NSStrikethroughStyleAttributeName, NSTextField,
                    NSTrackingArea, NSView, NSVisualEffectView, NSAttributedString)

import icon_images
import motion
import notes
import palette
import rules

BORDERLESS, NONACTIVATING = 0, 1 << 7
# all Spaces, stationary, over full-screen apps, not in the window cycle
COLLECTION = 1 | 16 | 256 | 64
FLOATING = 25                       # NSStatusWindowLevel: above normal windows

CARD_W, CARD_MAX_H, PAD = 272, 360, 14
PILL_MARGIN = (10, 6)               # hit area around the drawn capsule
PILL_BOTTOM = 12                    # clear of the Dock edge
TRACK = 1 | 128 | 512               # mouseEnteredAndExited | activeAlways | inVisibleRect
POPOVER_MATERIAL, BEHIND_WINDOW, ACTIVE = 6, 0, 1
CLICK_MASK = (1 << 1) | (1 << 3)    # left and right mouse down, in any app
CLEARED_SECONDS = 1.5               # the check mark stays on the pill this long after a clear
FOLD_ALPHA = 0.45                   # a note that is folding away, and cannot be tapped
ADD_LABEL = "Add a note…"
SEARCH_PLACEHOLDER = "Find or name a project"
ICONS = {"code_file": ("chevron.left.forwardslash.chevron.right", (0.48, 0.35, 0.94)),
         "document": ("doc.text", (0.25, 0.48, 0.88)), "spreadsheet": ("tablecells", (0.18, 0.61, 0.38)),
         "chat": ("bubble.left", (0.09, 0.65, 0.54)), "chat_list": ("bubble.left", (0.09, 0.65, 0.54)),
         "email_draft": ("envelope", (0.84, 0.34, 0.24)), "email": ("envelope", (0.84, 0.34, 0.24)),
         "mailbox": ("envelope", (0.84, 0.34, 0.24))}
DEFAULT_ICON = ("square.on.square", (0.45, 0.45, 0.5))
SUGGEST_ROWS = 4                    # things the suggestion lists before its Show-more row
DONE_SECONDS = 1.5                  # "Pricing created." stays this long, then the card closes
PEEK_MAX = 340                      # the widest the peeking pill gets, whatever the language
LISTEN_W = 240                      # the pill while it shows the words as you say them
SUGGEST_WORDS = "Group these?"
SAVED_SECONDS = 1.8                 # "Saved to Q3 plan" stays on the pill this long
PROJECT_EXPLAINER = ("A project keeps related things and your notes together, so you can pick up "
                     "where you left off. You can change this any time.")
# the marks: an SF Symbol, and whether it is neutral or red (a permission that is off)
MARKS = {"paused": ("pause.fill", False, "Paused"),
         "screen_off": ("eye.slash", True, "Screen access is off"),
         "private": ("lock", False, "Private window"),
         "unwatched": ("circle.slash", False, "Not watching this app"),
         "mic_off": ("mic.slash", True, "Mic is off"),
         "fresh": ("square.and.pencil", False, "No notes yet"),
         "caught": ("checkmark", False, "All caught up"),
         "cleared": ("checkmark", False, "All caught up"),
         "noproj": ("folder.badge.plus", False, "Start a project")}
SCREEN_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
EMPTY_HINT = "Nothing left here. Hold {key} and say what to remember for this page."


def _dark():
    app = NSApplication.sharedApplication()
    match = app.effectiveAppearance().bestMatchFromAppearancesWithNames_(
        ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"])
    return match == "NSAppearanceNameDarkAqua"


def _more_contrast():
    try:
        from AppKit import NSWorkspace
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldIncreaseContrast())
    except Exception:
        return False


def _rgb(rgb, alpha=1.0):
    return NSColor.colorWithRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _accent_mark():
    """The accent blue for marks. The pill is light in dark mode, so the blue is deeper there (palette.py)."""
    return _rgb(palette.ACCENT_ON_LIGHT_PILL if _dark() else palette.ACCENT_ON_DARK_PILL)


def _text_width(text, font):
    return NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font}).size().width


def _fit(text, font, room, tail=False):
    """`text` cut to measured width `room`: at the end with … (or, for tail=True, keep the end and cut the start)."""
    if _text_width(text, font) <= room:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                                     # longest piece that fits
        mid = (lo + hi + 1) // 2
        piece = ("…" + text[-mid:]) if tail else (text[:mid] + "…")
        if _text_width(piece, font) <= room:
            lo = mid
        else:
            hi = mid - 1
    return ("…" + text[-lo:]) if tail else (text[:lo].rstrip() + "…")


def _initial(name):
    """The first whole character of a name (a flag or an accented letter is one character, not half of one)."""
    try:
        from Foundation import NSString
        text = NSString.stringWithString_(name or "")
        if text.length():
            return str(text.substringWithRange_(text.rangeOfComposedCharacterSequenceAtIndex_(0))).upper()
    except Exception:
        pass
    return (name or "")[:1].upper()


class _Veil(NSView):
    """A soft fill under a card's content, so the page behind the blur cannot read through it."""

    def drawRect_(self, rect):
        NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.72).set()
        NSBezierPath.fillRect_(self.bounds())

    def isOpaque(self):
        return False


def _veil(width, height):
    return _Veil.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))


def _font(size, bold=False):
    """Figtree, the window's typeface, so the pill's cards read as the same product; the system font if it is missing."""
    try:
        import setup_kit                              # imported late: setup_kit imports this module
        return setup_kit.font(size, 600 if bold else 400)
    except Exception:
        return NSFont.systemFontOfSize_weight_(size, 0.4) if bold else NSFont.systemFontOfSize_(size)


def _label(text, size=11, bold=False, color=None, frame=(0, 0, 10, 10), wrap=False, strike=False):
    """A text field. wrap=True lets it run to several lines; the frame's height is then
    set from the text. Returns (field, height)."""
    font = _font(size, bold)
    color = color or NSColor.labelColor()
    field = NSTextField.labelWithString_(text)
    if strike:
        field.setAttributedStringValue_(NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color,
                   NSStrikethroughStyleAttributeName: 1}))
    else:
        field.setFont_(font)
        field.setTextColor_(color)
    field.setLineBreakMode_(0 if wrap else 4)         # wrap words / truncate tail
    if wrap:
        field.setMaximumNumberOfLines_(0)             # a label draws one line unless told otherwise
        field.setUsesSingleLineMode_(False)
    x, y, w, h = frame
    if wrap:
        h = max(h, field.cell().cellSizeForBounds_(NSMakeRect(0, 0, w, 10000)).height)
    field.setFrame_(NSMakeRect(x, y, w, h))
    return field, h


class _KeyPanel(NSPanel):
    """A panel that can take the keyboard (for the name and search fields) without
    activating LMemM, so the app you were in stays in front."""

    def canBecomeKeyWindow(self):
        return True


def _panel(width, height, keyable=False):
    panel = (_KeyPanel if keyable else NSPanel).alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), BORDERLESS | NONACTIVATING, NSBackingStoreBuffered, False)
    panel.setLevel_(FLOATING)
    panel.setCollectionBehavior_(COLLECTION)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setReleasedWhenClosed_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    return panel


class _Flipped(NSView):
    def isFlipped(self):
        return True


class _Control(_Flipped):
    """Anything a person can press: a row, a button, a chip. One place for what every control owes them
    (audit: no hover, no pressed state, fired on mouse-down, invisible to VoiceOver):

        hover      a soft wash while the pointer is over it
        press      a deeper wash while it is held; letting go outside cancels, like every Mac button
        commit     the action runs on mouse-up, inside
        keyboard   a `focusable` control takes Tab, shows the focus ring, and Space or Return presses it
        VoiceOver  a button (or a checkbox when `checked` is set) named by `ax_label` or the words inside it

    callback=None makes a control inert: a soft box, a card, a backdrop. It still takes the click, so
    nothing underneath gets it, but it never highlights or announces itself."""

    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(_Control, self).initWithFrame_(frame)
        self.callback = callback
        self.hovered = self.pressed = False
        self.focusable = False
        self.ax_label = None
        self.checked = None                            # None: a button. True/False: a checkbox, ticked or not
        self.radius = 10
        self.washes = True                             # False for a backdrop that must not light up under the pointer
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), TRACK, self, None))
        return self

    @objc.python_method
    def live(self):
        return self.callback is not None

    @objc.python_method
    def paint_state(self):
        """The hover and pressed wash. Called at the end of a control's own drawing."""
        alpha = 0.10 if self.pressed else 0.05 if self.hovered else 0.0
        if alpha and self.live() and self.washes:
            NSColor.labelColor().colorWithAlphaComponent_(alpha).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), self.radius, self.radius).fill()

    @objc.python_method
    def _inside(self, event):
        return NSPointInRect(self.convertPoint_fromView_(event.locationInWindow(), None), self.bounds())

    def mouseEntered_(self, event):
        if self.live() and not self.hovered:
            self.hovered = True
            self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        if self.hovered:
            self.hovered = False
            self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        if not self.live():
            return
        if self.window() is not None:
            self.window().makeFirstResponder_(None)    # a text box lets go, so the arrow keys work on the page
        self.pressed = True
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        if self.live() and self.pressed != self._inside(event):
            self.pressed = not self.pressed
            self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        was, self.pressed = self.pressed, False
        self.setNeedsDisplay_(True)
        if was and self.live() and self._inside(event):
            self.callback()

    def acceptsFirstMouse_(self, event):
        return True

    # keyboard
    def acceptsFirstResponder(self):
        return bool(self.focusable and self.live())

    def keyDown_(self, event):
        if self.focusable and self.live() and int(event.keyCode()) in (49, 36, 76):       # Space, Return, Enter
            self.callback()
            return
        objc.super(_Control, self).keyDown_(event)

    def drawFocusRingMask(self):
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), self.radius, self.radius).fill()

    def focusRingMaskBounds(self):
        return self.bounds()

    # VoiceOver: a control reads as a button named by the words drawn inside it.
    def isAccessibilityElement(self):
        return self.live()

    def accessibilityRole(self):
        return "AXButton" if self.checked is None else "AXCheckBox"

    def accessibilityLabel(self):
        if self.ax_label:
            return self.ax_label
        words = []

        def collect(view):
            for sub in view.subviews():
                text = getattr(sub, "text", None)
                if text is None:
                    try:
                        text = str(sub.stringValue())
                    except Exception:
                        text = None
                if text and str(text).strip() not in ("›", "⌄", "‹", "▾", "▸"):      # decorative glyphs are not words
                    words.append(str(text))
                collect(sub)
        collect(self)
        return ", ".join(words) or "button"

    def accessibilityValue(self):
        return None if self.checked is None else (1 if self.checked else 0)

    def accessibilityPerformPress(self):
        if self.live():
            self.callback()
        return True


class _Tap(_Control):
    """A clickable area (a note row, "more", "Add a note…"). Optionally a soft rounded fill."""

    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(_Tap, self).initWithFrame_callback_(frame, callback)
        self.fill = False
        self.tint = None                               # a solid colour, for a primary button
        return self

    def drawRect_(self, rect):
        if self.tint is not None:
            self.tint.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()
            fraction = 0.28 if self.pressed else 0.14 if self.hovered else 0.0
            if fraction and self.live() and self.washes:            # near-black / near-white fills: a label-coloured wash is invisible, so move toward grey
                self.tint.blendedColorWithFraction_ofColor_(fraction, NSColor.colorWithWhite_alpha_(0.5, 1.0)).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()
            return
        elif self.fill:
            NSColor.labelColor().colorWithAlphaComponent_(0.07).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()
        self.paint_state()


class _Fields(NSObject):
    """Delegate for the card's text fields: report typing, Return and Esc."""

    def initWithChange_submit_cancel_(self, change, submit, cancel):
        self = objc.super(_Fields, self).init()
        self.change, self.submit, self.cancel = change, submit, cancel
        return self

    def controlTextDidChange_(self, note):
        self.change(note.object().stringValue())

    def control_textView_doCommandBySelector_(self, control, view, selector):
        if selector == "insertNewline:":
            self.submit(control.stringValue())
            return True
        if selector == "cancelOperation:":
            self.cancel()
            return True
        arrow = getattr(self, "arrow", None)                  # Up/Down in a field hand the keyboard to the list
        if arrow and selector in ("moveDown:", "moveUp:"):
            arrow(125 if selector == "moveDown:" else 126)
            return True
        return False


def _field(text, placeholder, size, bold, frame, delegate):
    """An editable one-line text field with no box, styled like a label."""
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(*frame))
    field.setStringValue_(text)
    field.setPlaceholderString_(placeholder)
    field.setFont_(_font(size, bold))
    field.setBordered_(False)
    field.setDrawsBackground_(False)
    field.setFocusRingType_(1)                       # none
    field.setDelegate_(delegate)
    return field


class _Tile(_Flipped):
    """An app icon square: a symbol for the kind of thing, white on a colour."""

    def initWithKind_size_(self, kind, size):
        self = objc.super(_Tile, self).initWithFrame_(NSMakeRect(0, 0, size, size))
        self.symbol, self.rgb = ICONS.get(kind, DEFAULT_ICON)
        self.size = size
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(self.symbol, None)
        if image is not None:
            holder = NSImageView.alloc().initWithFrame_(NSMakeRect(size * 0.22, size * 0.22, size * 0.56, size * 0.56))
            holder.setImage_(image)
            holder.setContentTintColor_(NSColor.whiteColor())
            self.addSubview_(holder)
        return self

    def drawRect_(self, rect):
        NSColor.colorWithRed_green_blue_alpha_(*self.rgb, 1.0).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), self.size * .27, self.size * .27).fill()


def _symbol(name):
    return NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)


class _Badge(_Flipped):
    """The big icon on a state card: a soft rounded square with a symbol. tone: soft, accent, bad."""

    def initWithSymbol_tone_(self, symbol, tone):
        self = objc.super(_Badge, self).initWithFrame_(NSMakeRect(0, 0, 46, 46))
        self.tone = tone
        image = _symbol(symbol)
        if image is not None:
            holder = NSImageView.alloc().initWithFrame_(NSMakeRect(11, 11, 24, 24))
            holder.setImage_(image)
            holder.setContentTintColor_(NSColor.whiteColor() if tone == "accent" else
                                        NSColor.systemRedColor() if tone == "bad" else NSColor.secondaryLabelColor())
            self.addSubview_(holder)
        return self

    def drawRect_(self, rect):
        if self.tone == "accent":
            _rgb(palette.ACCENT).setFill()
        elif self.tone == "bad":
            NSColor.systemRedColor().colorWithAlphaComponent_(0.14).setFill()
        else:
            NSColor.labelColor().colorWithAlphaComponent_(0.07).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 14, 14).fill()


class _Dashed(_Flipped):
    """A dashed rounded outline: where a new project will gather its things."""

    def drawRect_(self, rect):
        NSColor.tertiaryLabelColor().setStroke()
        size = self.bounds().size
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.75, 0.75, size.width - 1.5, size.height - 1.5), 14, 14)
        path.setLineWidth_(1.5)
        path.setLineDash_count_phase_([5.0, 4.0], 2, 0)
        path.stroke()


class _Ring(_Flipped):
    """The round tick box: an outline, filled blue with a tick once done."""

    def initWithChecked_(self, checked):
        self = objc.super(_Ring, self).initWithFrame_(NSMakeRect(0, 0, 17, 17))
        self.checked = checked
        return self

    def drawRect_(self, rect):
        circle = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(1, 1, 15, 15))
        if self.checked:
            _rgb(palette.ACCENT).setFill()                                  # white tick on it: 4.9:1
            circle.fill()
            tick = NSBezierPath.bezierPath()
            tick.moveToPoint_((5, 8.8))
            tick.lineToPoint_((7.5, 11.5))
            tick.lineToPoint_((12, 5.5))
            tick.setLineWidth_(1.8)
            tick.setLineCapStyle_(1)
            tick.setLineJoinStyle_(1)
            NSColor.whiteColor().setStroke()
            tick.stroke()
        else:
            NSColor.secondaryLabelColor().setStroke()
            circle.setLineWidth_(1.5)
            circle.stroke()


class _PillView(NSView):
    """The pill. It draws whatever the widget says it is: a thin bar, a count, the first
    note, or a waveform with the words so far. The panel is resized to match (see Widget.layout)."""

    def initWithWidget_(self, widget):
        self = objc.super(_PillView, self).initWithFrame_(NSMakeRect(0, 0, 80, 20))
        self.widget = widget
        self.hover = False
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), TRACK, self, None))
        self.icon = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        self.icon.setHidden_(True)
        self.addSubview_(self.icon)
        return self

    def drawRect_(self, rect):
        w = self.widget
        bw, bh = self.bounds().size.width, self.bounds().size.height
        cw, ch = bw - 2 * PILL_MARGIN[0], bh - 2 * PILL_MARGIN[1]
        capsule = NSMakeRect(PILL_MARGIN[0], PILL_MARGIN[1], cw, ch)
        dark = _dark()
        ink = NSColor.colorWithWhite_alpha_(0.97 if dark else 0.09, 1.0)       # the pill itself
        on_ink = NSColor.colorWithWhite_alpha_(0.09 if dark else 1.0, 1.0)     # text on the pill
        quiet = w.mode_of_pill() == "rest"
        rest = 0.9 if _more_contrast() else 0.45
        ink.colorWithAlphaComponent_(rest if quiet and not self.hover else 0.94).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(capsule, ch / 2, ch / 2).fill()
        state = w.mode_of_pill()
        mid = PILL_MARGIN[1] + ch / 2
        if state == "count":
            self._text(str(w.count), 12, True, on_ink, capsule, centre=True)
        elif state == "suggest":
            if w.sug["first_time"]:
                self._link(PILL_MARGIN[0] + 15, mid, on_ink)
                self._text(SUGGEST_WORDS, 13, False, on_ink, NSMakeRect(PILL_MARGIN[0] + 29, 0, cw - 38, bh))
            else:
                self._link(PILL_MARGIN[0] + cw / 2, mid, on_ink)
        elif state == "peek":
            if w.count or not w.sug:
                self._dot(PILL_MARGIN[0] + 11, mid, lit=bool(w.count))
            else:
                self._link(PILL_MARGIN[0] + 14, mid, on_ink)
            self._text(w.peek_text(), 13, False, on_ink, NSMakeRect(PILL_MARGIN[0] + 22 + (4 if not w.count and w.sug else 0), 0, cw - 32, bh))
        elif state == "saved":
            self._tick(PILL_MARGIN[0] + 15, mid)
            self._text(w.saved, 13, False, on_ink, NSMakeRect(PILL_MARGIN[0] + 29, 0, cw - 38, bh))
        elif state == "listening":
            self._bars(PILL_MARGIN[0] + 12, mid)
            self._text(w.heard_text(), 13, False, on_ink, NSMakeRect(PILL_MARGIN[0] + 26, 0, cw - 36, bh))

    @objc.python_method
    def _dot(self, x, y, lit=True):
        (_accent_mark() if lit else NSColor.colorWithWhite_alpha_(0.5, 0.8)).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 4, y - 4, 8, 8)).fill()

    @objc.python_method
    def _tick(self, x, y):
        """A small blue check."""
        _accent_mark().setStroke()
        path = NSBezierPath.bezierPath()
        path.moveToPoint_((x - 4, y))
        path.lineToPoint_((x - 1, y + 3.2))
        path.lineToPoint_((x + 4.5, y - 3.4))
        path.setLineWidth_(1.8)
        path.stroke()

    @objc.python_method
    def _link(self, x, y, color):
        """The project mark: two rings that overlap."""
        color.setStroke()
        for dx in (-2.6, 2.6):
            ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x + dx - 4.2, y - 4.2, 8.4, 8.4))
            ring.setLineWidth_(1.5)
            ring.stroke()

    @objc.python_method
    def _bars(self, x, y):
        _accent_mark().setFill()
        for i, base in enumerate((5, 12, 8, 13, 6)):
            h = max(3, base * (0.6 + 0.4 * math.sin(time.time() * 7 + i * 1.3)))
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x - 7 + i * 3.6, y - h / 2, 2.5, h), 1.2, 1.2).fill()

    @objc.python_method
    def _text(self, text, size, bold, color, area, centre=False):
        font = _font(size, bold)
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        wide = _text_width(text, font)
        high = NSAttributedString.alloc().initWithString_attributes_(text, attrs).size().height
        x = area.origin.x + ((area.size.width - wide) / 2 if centre else 0)
        text_y = area.origin.y + (area.size.height - high) / 2
        NSAttributedString.alloc().initWithString_attributes_(text, attrs).drawAtPoint_((x, text_y))

    # VoiceOver: the pill is one button; its state was only a tooltip.
    def isAccessibilityElement(self):
        return True

    def accessibilityRole(self):
        return "AXButton"

    def accessibilityLabel(self):
        return self.widget.accessibility_text()

    def accessibilityPerformPress(self):
        self.widget.toggle_card()
        return True

    def viewDidChangeEffectiveAppearance(self):
        self.widget.layout()                    # the mark's tint was set for the old appearance

    def mouseEntered_(self, event):
        if not self.hover:
            self.hover = True
            self.widget.layout()

    def mouseExited_(self, event):
        if self.hover:
            self.hover = False
            self.widget.layout()

    def mouseDown_(self, event):
        try:
            self.widget.toggle_card()
        except Exception:                      # never swallow it silently: say what broke
            import traceback
            traceback.print_exc()

    def acceptsFirstMouse_(self, event):
        return True


class Widget:
    """provider() -> notes.card(...) for the thing in front, or None.
    on_tick(note ids, done) marks notes done / open again.
    on_add() opens the ⌃⌥N note window.
    heard() -> None, or the words so far while the note window is open (the pill shows them).
    flash(text) shows a short confirmation on the pill ("Saved to Q3 plan").
    suggestion() -> notes.suggestion_view(...) or None: a group the model thinks is one project.
    on_project(ids, name) files things in a project; on_decline(forever) answers a suggestion;
    picker(item id, typed text) -> notes.picker_view(...); on_unfile(item id) takes a thing out."""

    def __init__(self, provider, on_tick, on_add=None, heard=None, hotkey="⌃⌥N",
                 suggestion=None, on_project=None, on_decline=None, picker=None, on_unfile=None,
                 busy=None, status=None, on_pause=None, on_resume=None):
        self.on_pause = on_pause or (lambda kind: None)
        self.on_resume = on_resume or (lambda: None)
        self.status_fn = status or (lambda: {})
        self.status = {}                            # what is wrong right now (see rules.pill_mark)
        self.pick_empty = False                     # the picker is showing "Start a project"
        self.busy = busy or (lambda: False)         # True while the note card is up (see rules.py)
        self.provider = provider
        self.on_tick = on_tick
        self.on_add = on_add or (lambda: None)
        self.heard = heard or (lambda: None)
        self.suggestion = suggestion or (lambda: None)
        self.on_project = on_project or (lambda ids, name: None)
        self.on_decline = on_decline or (lambda forever: None)
        self.picker = picker or (lambda item, query: None)
        self.on_unfile = on_unfile or (lambda item: None)
        self.search = self.name = None      # the card's text fields while they exist
        self.sug = None                     # the suggestion in hand (None when there is none)
        self.sug_all, self.kept_name = False, None   # every thing listed; the name typed before the list grew
        self.query, self.also, self.done_text, self.close_at = "", True, "", 0.0
        self.fields = _Fields.alloc().initWithChange_submit_cancel_(self._typed, self._submitted, self._cancelled)
        self.hotkey = hotkey
        self.card_open = False
        self.mode = "here"                  # "here" | "project" | "suggest" | "pick" | "done"
        self.show_done = False
        self.flow = rules.FinishFlow()      # finished notes: hold, fold, clear, undo (rules.py R10)
        self.celebrate_now = False          # play the big check once, on the render after a clear
        self.cleared_until = 0.0            # the pill shows its check mark until then
        self.data = None
        self.count, self.first = 0, ""
        self.listening = None               # None, or the words so far
        self.saved, self.saved_until = None, 0     # "Saved to Q3 plan", shown for a moment after a note
        self.shown = None                   # what the open card currently shows
        self.scroll = None
        self.pill = _panel(80, 20)
        self.pill_view = _PillView.alloc().initWithWidget_(self)
        self.pill.setContentView_(self.pill_view)
        self.card = _panel(CARD_W, 200, keyable=True)
        self.layout()
        self.pill.orderFrontRegardless()
        self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(CLICK_MASK, self._clicked_elsewhere)

    # -- the pill

    def flash(self, text):
        """Confirm something on the pill for a moment ("Saved to Q3 plan"); the count then rises."""
        self.saved, self.saved_until = text, time.time() + SAVED_SECONDS
        self.layout()

    def mode_of_pill(self):
        if self.listening is not None:
            return "listening"
        return rules.pill_state(self.busy(), bool(self.saved), self.pill_view.hover, self.card_open,
                                self.count, bool(self.sug), self._mark(), self._empty_kind(),
                                time.time() < self.cleared_until)

    def _mark(self):
        return rules.pill_mark(self.status, self.count, bool(self.sug))

    def _empty_kind(self):
        if self.card_open and self.mode == "pick":
            return "noproj" if self.pick_empty else None
        if self.count or self.sug or self.data is None:
            return None
        return rules.empty_kind(notes.card_view(self.data, "here", self.show_done, self._holding()))

    def _holding(self):
        """Finished notes still on screen (held or folding)."""
        return self.flow.holding(time.time())

    def _open_left(self):
        return len(self.data["left"]) if self.data else 0

    def accessibility_text(self):
        """What VoiceOver says for the pill: the same words the tooltip and the card use."""
        state = self.mode_of_pill()
        if state in MARKS:
            return "Paused while you’re away" if state == "paused" and self.status.get("paused") == "away" else MARKS[state][2]
        if state == "listening":
            return "Listening. " + self.heard_text()
        if state == "saved":
            return self.saved or "Saved"
        if self.count:
            return f"LMemM, {self.count} open note{'s' * (self.count != 1)}"
        if state == "suggest":
            return "LMemM, group these things into a project?"
        return "LMemM"

    def peek_text(self):
        if not self.count:
            return _fit(self.sug["peek"], _font(13), PEEK_MAX - 44) if self.sug else "No notes here"
        return _fit(self.first.replace("\n", " "), _font(13), PEEK_MAX - 44)

    def heard_text(self):
        text = (self.listening or "").replace("\n", " ").strip()
        if not text:
            return "Listening…"
        return _fit(text, _font(13), LISTEN_W - 48, tail=True)

    def _capsule(self):
        """Size of the drawn capsule for the current state."""
        state = self.mode_of_pill()
        if state == "peek":
            return _text_width(self.peek_text(), _font(13)) + 44 + (4 if not self.count and self.sug else 0), 28
        if state in MARKS:
            return 28, 28
        if state == "saved":
            return _text_width(self.saved, _font(13)) + 48, 28
        if state == "listening":
            return LISTEN_W, 30                          # one width: it does not chase every word
        if state == "count":
            return 44, 22
        if state == "suggest":
            return (_text_width(SUGGEST_WORDS, _font(13)) + 52, 28) if self.sug["first_time"] else (44, 22)
        return 36, 5

    def layout(self, animate=False):
        """Resize and place the pill panel for its state, keeping it bottom-centre.
        Never animated: a panel that is mid-resize under the pointer sends enter/exit events
        and the pill flickers between hovered and not."""
        cw, ch = self._capsule()
        w, h = cw + 2 * PILL_MARGIN[0], ch + 2 * PILL_MARGIN[1]
        scr = NSScreen.mainScreen().visibleFrame()
        frame = NSMakeRect(scr.origin.x + (scr.size.width - w) / 2, scr.origin.y + PILL_BOTTOM - PILL_MARGIN[1], w, h)
        if frame != self.pill.frame():
            self.pill.setFrame_display_(frame, True)
        self.pill_view.setNeedsDisplay_(True)
        self._mark_icon(cw, ch)
        if self.card_open:
            self._place_card()

    def _mark_icon(self, cw, ch):
        """Put the state's symbol on the pill (or hide it)."""
        state = self.mode_of_pill()
        icon = self.pill_view.icon
        if state not in MARKS:
            icon.setHidden_(True)
            self.pill_view.setToolTip_(None)
            return
        symbol, bad, tip = MARKS[state]
        if state == "paused" and self.status.get("paused") == "away":
            tip = "Paused while you’re away"
        image = _symbol(symbol)
        icon.setImage_(image)
        icon.setFrame_(NSMakeRect(PILL_MARGIN[0] + (cw - 16) / 2, PILL_MARGIN[1] + (ch - 16) / 2, 16, 16))
        dark = _dark()                              # the pill is light in dark mode, dark in light mode
        if bad:
            icon.setContentTintColor_(_rgb(palette.RED_ON_LIGHT_PILL if dark else palette.RED_ON_DARK_PILL))
        else:
            icon.setContentTintColor_(NSColor.colorWithWhite_alpha_(0.09 if dark else 1.0, 1.0))
        icon.setHidden_(image is None)
        self.pill_view.setToolTip_(tip if bad else None)    # the system draws a tooltip down-right of the pointer, onto the Dock; the card says the rest
        icon.setAccessibilityElement_(False)            # the pill reads as one button (accessibility_text)

    def pulse(self, heard):
        """Called on every tick of the tracker's loop: moves the waveform and follows the
        note window (open/closed, words so far)."""
        if self.saved and time.time() >= self.saved_until:
            self.saved = None
            self.layout()
        if self.busy() and self.card_open:
            self.toggle_card()              # R1: a card left open must not sit under the note card
        self._flow_step()
        if self.card_open and self._key() != self.shown:
            self.render()                   # a hold became a fold, a fold ended, a toast ran out
        was = self.listening is not None
        self.listening = heard
        if (heard is not None) != was:
            self.layout()
        elif heard is not None:
            self.pill_view.setNeedsDisplay_(True)

    def _flow_step(self):
        """Advance the finished-notes flow (R10): the one "clear" and the pill's check mark."""
        now = time.time()
        if self.flow.update(self._open_left(), now) == "clear":
            self.celebrate_now = self.card_open
            self.cleared_until = now + CLEARED_SECONDS
            self.layout()
        elif self.cleared_until and now >= self.cleared_until:
            self.cleared_until = 0.0
            self.layout()

    # -- state from the tracker

    def refresh(self):
        """Called ~once a second: keep the count (and an open card) current."""
        if self.pill_view.hover and not NSPointInRect(NSEvent.mouseLocation(), self.pill.frame()):
            self.pill_view.hover = False        # the exit event was missed while the panel resized
            self.layout()
        self.data = self.provider()
        count, first = notes.pill_summary(self.data)
        sug = self.suggestion()
        status = self.status_fn()
        if (count, first, sug, status) != (self.count, self.first, self.sug, self.status):
            if sug != self.sug:
                self.sug_all = False
            self.count, self.first, self.sug, self.status = count, first, sug, status
            self.layout()
        if self.mode == "done" and self.card_open and time.time() >= self.close_at:
            self.toggle_card()
        if self.card_open and self._key() != self.shown:
            self.render()                   # only when something changed: keeps your scroll position

    def close_card(self):
        if self.card_open:
            self.toggle_card()

    def toggle_card(self, animate=True):
        """Open or close the card. animate=False when a shortcut opened it: nothing a key does is animated."""
        if rules.pill_click_action(self.busy()) == "ignore" and not self.card_open:
            return                          # R1: the note card owns the screen
        self.card_open = not self.card_open
        if self.card_open:
            self.saved = None               # R5: the card replaces the confirmation
            self.mode, self.show_done, self.scroll = (
                "suggest" if self.sug and not self.count and not self.status.get("paused") else "here"), False, None
            self.query, self.also = "", True
            self.search = None
            self.celebrate_now = False      # opening it later shows the calm state, never the celebration
            self.data = self.provider()
            self.render()
            self.card_leaving = getattr(self, "card_leaving", 0) + 1     # cancels a fade-out still running
            self.card.setAlphaValue_(1.0)
            self.card.orderFrontRegardless()
            if animate:
                motion.pop(self.card.contentView(), origin="bottom")     # grows from the pill, 140 ms
        else:
            self.flow.close_card()          # the Undo window ends with the card
            self.card_leaving = token = getattr(self, "card_leaving", 0) + 1
            def gone():
                if self.card_leaving == token and not self.card_open:      # opened again mid-fade: leave it alone
                    self.card.orderOut_(None)
                    self.card.setAlphaValue_(1.0)
            if animate and not motion.reduced():
                motion.fade_out(self.card, gone)
            else:
                gone()
        self.layout()

    def _clicked_elsewhere(self, event):
        # global monitors only see clicks in *other* apps, so this never fires for our own panels
        if not self.card_open:
            return
        where = NSEvent.mouseLocation()
        if NSPointInRect(where, self.pill.frame()) or NSPointInRect(where, self.card.frame()):
            return                          # a click on the pill or card itself is never "elsewhere"
        self.toggle_card()

    def ticked(self, nid, done):
        """One tap flips a note (R10). A note that is folding away ignores taps."""
        now = time.time()
        phase = self.flow.phase(nid, now)
        if phase == "fold":
            return
        self.on_tick([nid], done)
        self.data = self.provider()
        left = self._open_left()
        if done:
            self.flow.tick(nid, now, left)
        elif phase == "hold":
            self.flow.cancel(nid, left)     # changed your mind: no clear, no toast
        else:
            self.flow.reopened(now)
        self.render()

    def _undo(self):
        ids = self.flow.undo(self._open_left())
        if ids:
            self.on_tick(ids, False)
            self.data = self.provider()
        self.render()

    def _go(self, mode, show_done=None):
        self.mode = mode
        if show_done is not None:
            self.show_done = show_done
        self.scroll = None                  # a different view starts at the top
        if mode != "pick":
            self.search = None
        self.pick_empty = False
        self.render()

    def _add(self):
        self.toggle_card()
        self.on_add()

    # -- the card

    def _key(self):
        if self.mode in ("pick", "suggest"):          # fields in use: don't rebuild under the typing
            return repr((self.mode, self.sug))
        return repr((self.data, self.mode, self.show_done, self.flow.signature(time.time()), self.sug, self.done_text, self.status))

    def render(self):
        self._flow_step()                   # a clear that is due shows in this very render
        self.shown = self._key()
        if self.mode == "suggest" and not self.sug:
            self.mode = "here"
        view = notes.card_view(self.data, self.mode if self.mode == "project" else "here", self.show_done,
                              self._holding())
        kept = self.scroll.contentView().bounds().origin.y if self.scroll is not None else 0
        body = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W, 10))
        y = 14
        if self.status.get("paused"):
            y = self._paused(body, y)
        elif self.mode == "pause":
            y = self._pause_menu(body, y)
        elif self.mode == "suggest":
            y = self._suggest(body, y)
        elif self.mode == "pick":
            y = self._pick(body, y)
        elif self.mode == "done":
            y = self._done(body, y)
        elif self.mode == "finished":
            y = self._finished(body, y)
        elif self.mode == "here" and self._mark() in ("screen_off", "private", "unwatched"):
            y = self._problem(body, y)
        elif view.get("empty"):
            y = self._empty(body, y)
        elif view["mode"] == "project":
            y = self._project(body, view, y)
        else:
            y = self._here(body, view, y)
        y += 8

        height = min(y, CARD_MAX_H)
        body.setFrame_(NSMakeRect(0, 0, CARD_W, y))
        content = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W, height))
        content.setMaterial_(POPOVER_MATERIAL)          # follows light / dark by itself
        content.setBlendingMode_(BEHIND_WINDOW)
        content.setState_(ACTIVE)
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(18)
        content.layer().setMasksToBounds_(True)
        content.addSubview_(_veil(CARD_W, height))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W, height))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(y > CARD_MAX_H)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(body)
        content.addSubview_(scroll)
        self.card.setContentView_(content)
        if kept and y > height:                       # stay where you had scrolled to
            scroll.contentView().scrollToPoint_((0, min(kept, y - height)))
            scroll.reflectScrolledClipView_(scroll.contentView())
        self.scroll = scroll
        self.celebrate_now = False          # played once, never on a re-render
        self.card_height = height
        self._place_card()
        if self.mode == "pick":
            self._focus(self.search)
        self.layout()                           # the pill's mark follows what the card shows

    def _place_card(self):
        pill = self.pill.frame()
        self.card.setFrame_display_(NSMakeRect(pill.origin.x + pill.size.width / 2 - CARD_W / 2,
                                               pill.origin.y + pill.size.height + 4, CARD_W,
                                               getattr(self, "card_height", 100)), True)

    def _header(self, body, y, title, caption, back=None):
        field, h = _label(title, 14, bold=True, frame=(PAD, y, CARD_W - 2 * PAD, 18))
        body.addSubview_(field)
        y += 19
        if back:                                    # "‹ Q3 plan": one step back to this thing
            tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(PAD - 4, y - 4, CARD_W - 2 * PAD + 8, 24), back[1])
            small, _ = _label(f"‹ {back[0]}", 12, color=NSColor.secondaryLabelColor(), frame=(4, 4, CARD_W - 2 * PAD, 16))
            tap.addSubview_(small)
            body.addSubview_(tap)
        else:
            small, _ = _label(caption, 12, color=NSColor.secondaryLabelColor(), frame=(PAD, y, CARD_W - 2 * PAD, 16))
            body.addSubview_(small)
        return y + 24

    def _note(self, body, y, row):
        text_w = CARD_W - 2 * PAD - 28
        color = NSColor.secondaryLabelColor() if row["done"] else NSColor.labelColor()
        field, h = _label(row["text"], 14, color=color, frame=(28 + 8, 0, text_w, 17), wrap=True, strike=row["done"])
        height = h + 14
        folding = self.flow.phase(row["id"], time.time()) == "fold"
        tap = _Tap.alloc().initWithFrame_callback_(
            NSMakeRect(6, y, CARD_W - 12, height),
            None if folding else (lambda nid=row["id"], done=row["done"]: self.ticked(nid, not done)))
        if folding:
            tap.setAlphaValue_(FOLD_ALPHA)
        ring = _Ring.alloc().initWithChecked_(row["done"])
        ring.setFrame_(NSMakeRect(PAD - 6 + 2, 8, 17, 17))
        field.setFrame_(NSMakeRect(PAD - 6 + 2 + 17 + 11, 7, text_w - 4, h))
        tap.checked = bool(row["done"])
        tap.ax_label = row["text"]
        tap.addSubview_(ring)
        tap.addSubview_(field)
        body.addSubview_(tap)
        return y + height

    def _link(self, body, y, left, right, callback):
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y, CARD_W - 12, 28), callback)
        mute = NSColor.secondaryLabelColor()
        tap.addSubview_(_label(left, 12, color=mute, frame=(PAD - 6, 6, CARD_W - 80, 16))[0])
        tap.addSubview_(_label(right, 13, color=mute, frame=(CARD_W - 12 - PAD - 4, 5, 14, 16))[0])
        body.addSubview_(tap)
        return y + 28

    def _add_row(self, body, y):
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(8, y, CARD_W - 16, 34), self._add)
        tap.fill = True
        mute = NSColor.secondaryLabelColor()
        tap.addSubview_(_label(ADD_LABEL, 13, color=mute, frame=(12, 8, 150, 18))[0])
        key = _label(self.hotkey, 11, color=NSColor.secondaryLabelColor(), frame=(CARD_W - 16 - 12 - 50, 9, 50, 16))[0]
        key.setAlignment_(2)                         # right
        tap.addSubview_(key)
        body.addSubview_(tap)
        return y + 42

    def _empty(self, body, y):
        field, h = _label(EMPTY_HINT.format(key=self.hotkey), 13, color=NSColor.secondaryLabelColor(),
                          frame=(PAD, y, CARD_W - 2 * PAD, 18), wrap=True)
        body.addSubview_(field)
        return y + h + 6

    def _state(self, body, y, symbol, tone, title, line, celebrate=False):
        """An empty or error card: a big icon, a title, one line. Returns the next y.
        `celebrate` fades the icon in (only for the render right after a clear)."""
        y += 6
        badge = _Badge.alloc().initWithSymbol_tone_(symbol, tone)
        badge.setFrame_(NSMakeRect((CARD_W - 46) / 2, y, 46, 46))
        body.addSubview_(badge)
        if celebrate:
            badge.setAlphaValue_(0.0)
            NSAnimationContext.beginGrouping()
            motion.eased(motion.CELEBRATE_SECONDS)
            badge.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
        y += 56
        head, _ = _label(title, 16, bold=True, frame=(PAD, y, CARD_W - 2 * PAD, 20))
        head.setAlignment_(1)
        body.addSubview_(head)
        y += 23
        small, h = _label(line, 13, color=NSColor.secondaryLabelColor(), frame=(PAD, y, CARD_W - 2 * PAD, 18), wrap=True)
        small.setAlignment_(1)
        body.addSubview_(small)
        return y + h + 10

    def show_paused(self):
        """⌃⌥N while paused: open the card that says so (rules.py R9). From the keyboard: no animation."""
        if not self.card_open:
            self.toggle_card(animate=False)

    def show_unwatched(self):
        """⌃⌥N in an app you did not choose: open the card that says so (rules.py R11)."""
        self.show_paused()

    def _done_row(self, body, y, count):
        """"Done  3  ›": this thing's finished notes."""
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y + 2, CARD_W - 12, 28), lambda: self._go("finished"))
        mute = NSColor.secondaryLabelColor()
        image = _symbol("checkmark.circle")
        if image is not None:
            holder = NSImageView.alloc().initWithFrame_(NSMakeRect(PAD - 6, 6, 14, 14))
            holder.setImage_(image)
            holder.setContentTintColor_(mute)
            tap.addSubview_(holder)
        tap.addSubview_(_label("Done", 12, color=mute, frame=(PAD - 6 + 22, 6, 100, 16))[0])
        number = _label(str(count), 12, color=NSColor.secondaryLabelColor(), frame=(CARD_W - 12 - PAD - 40, 6, 32, 16))[0]
        number.setAlignment_(2)
        tap.addSubview_(number)
        tap.addSubview_(_label("›", 13, color=mute, frame=(CARD_W - 12 - PAD - 4, 5, 14, 16))[0])
        body.addSubview_(tap)
        return y + 32

    def _toast(self, body, y):
        """"Marked done · Undo" for a few seconds after ticking; "Reopened" has no Undo."""
        shown = self.flow.toast(time.time())
        if not shown:
            return y
        text, undoable = shown
        body.addSubview_(_label(text, 12, color=NSColor.secondaryLabelColor(), frame=(PAD, y + 4, 140, 16))[0])
        if undoable:
            undo = _Tap.alloc().initWithFrame_callback_(NSMakeRect(CARD_W - PAD - 64, y - 2, 64, 28), self._undo)
            word = _label("Undo", 12, bold=True, color=NSColor.controlAccentColor(), frame=(0, 6, 64, 16))[0]
            word.setAlignment_(2)
            undo.addSubview_(word)
            body.addSubview_(undo)
        return y + 28

    def _finished(self, body, y):
        """Done for this thing, newest first, grouped by day. Tapping a ring reopens the note."""
        view = notes.done_view(self.data, datetime.now().isoformat(timespec="seconds"))
        y = self._header(body, y, "Done", "", back=(view["title"], lambda: self._go("here")))
        if not view["groups"]:
            body.addSubview_(_label("Nothing finished yet.", 13, color=NSColor.secondaryLabelColor(),
                                    frame=(PAD, y, CARD_W - 2 * PAD, 18))[0])
            y += 26
        for label, rows in view["groups"]:
            body.addSubview_(_label(label.upper(), 11, bold=True, color=NSColor.secondaryLabelColor(),
                                    frame=(PAD, y + 4, CARD_W - 2 * PAD, 14))[0])
            y += 22
            for row in rows:
                text_w = CARD_W - 2 * PAD - 28
                field, h = _label(row["text"], 14, color=NSColor.secondaryLabelColor(),
                                  frame=(0, 0, text_w - 4, 17), wrap=True, strike=True)
                height = h + 14 + 16
                tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y, CARD_W - 12, height),
                                                           lambda nid=row["id"]: self.ticked(nid, False))
                tap.checked = True
                tap.ax_label = row["text"]
                ring = _Ring.alloc().initWithChecked_(True)
                ring.setFrame_(NSMakeRect(PAD - 6 + 2, 8, 17, 17))
                field.setFrame_(NSMakeRect(PAD - 6 + 2 + 17 + 11, 7, text_w - 4, h))
                when = _label(row["when"], 11, color=NSColor.secondaryLabelColor(),
                              frame=(PAD - 6 + 2 + 17 + 11, 7 + h + 1, text_w, 14))[0]
                for v in (ring, field, when):
                    tap.addSubview_(v)
                body.addSubview_(tap)
                y += height
        return self._toast(body, y + 4)

    def _pause_row(self, body, y):
        """The plain "Pause LMemM" row at the bottom of the card."""
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y + 2, CARD_W - 12, 28), lambda: self._go("pause"))
        mute = NSColor.secondaryLabelColor()
        image = _symbol("pause.fill")
        if image is not None:
            holder = NSImageView.alloc().initWithFrame_(NSMakeRect(PAD - 6, 7, 13, 14))
            holder.setImage_(image)
            holder.setContentTintColor_(mute)
            tap.addSubview_(holder)
        tap.addSubview_(_label("Pause LMemM", 12, color=mute, frame=(PAD - 6 + 22, 6, CARD_W - 80, 16))[0])
        tap.addSubview_(_label("›", 13, color=mute, frame=(CARD_W - 12 - PAD - 4, 5, 14, 16))[0])
        body.addSubview_(tap)
        return y + 32

    def _pause_menu(self, body, y):
        """Pause for: three choices, each showing when it ends."""
        back = _Tap.alloc().initWithFrame_callback_(NSMakeRect(PAD - 4, y - 8, 120, 24), lambda: self._go("here"))
        back.addSubview_(_label("‹ Back", 12, color=NSColor.secondaryLabelColor(), frame=(4, 4, 110, 16))[0])
        body.addSubview_(back)
        y += 22
        body.addSubview_(_label("Pause for", 16, bold=True, frame=(PAD, y, CARD_W - 2 * PAD, 20))[0])
        y += 28
        for row in rules.pause_options(datetime.now()):
            tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y, CARD_W - 12, 46),
                                                       lambda kind=row["kind"]: self._pause(kind))
            tap.addSubview_(_label(row["title"], 14, bold=True, frame=(PAD - 6 + 2, 6, CARD_W - 60, 18))[0])
            tap.addSubview_(_label(row["sub"], 12, color=NSColor.secondaryLabelColor(),
                                   frame=(PAD - 6 + 2, 25, CARD_W - 60, 16))[0])
            body.addSubview_(tap)
            y += 46
        return y

    def _pause(self, kind):
        self.on_pause(kind)
        self._after_pause_change()

    def _resume(self):
        self.on_resume()
        self._after_pause_change()

    def _after_pause_change(self):
        self.status = self.status_fn()               # the pill shows it now, not a second later
        self.close_card()
        self.layout()

    def _paused(self, body, y):
        """The card for any pause: what is true, when it ends, and Resume if you paused it."""
        view = self.status.get("pause_view") or {"title": "Paused", "line": "", "resume": False}
        y = self._state(body, y, "pause.fill", "soft", view["title"], view["line"])
        if view["resume"]:
            self._button(body, (8, y, CARD_W - 16, 33), "Resume", self._resume, primary=True)
            y += 41
        return y

    def _problem(self, body, y):
        """Screen access is off, or this window is private."""
        if self._mark() == "screen_off":
            y = self._state(body, y, "eye.slash", "bad", "Screen access is off",
                            "LMemM can’t see what you’re working on.")
            self._button(body, (8, y, CARD_W - 16, 33), "Open System Settings", self._open_settings, primary=True)
            return y + 41
        if self._mark() == "unwatched":
            return self._state(body, y, "circle.slash", "soft", "Not watching this app",
                               "You chose where LMemM looks, and this isn’t one of those apps.")
        return self._state(body, y, "lock", "soft", "Private window", "Nothing is remembered from here.")

    def _open_settings(self):
        subprocess.Popen(["open", SCREEN_SETTINGS], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _here(self, body, view, y):
        kind = rules.empty_kind(view)
        if kind == "fresh":
            y = self._state(body, y, "square.and.pencil", "soft", "No notes yet",
                            f"Press {self.hotkey} and say what to remember.")
            return self._pause_row(body, y)
        if kind == "caught":
            n = view["done_here"]
            y = self._state(body, y, "checkmark", "accent", "All caught up", f"{n} note{'s' * (n != 1)} done.",
                            celebrate=self.celebrate_now)
            y = self._add_row(body, y - 4)       # caught up with the past, not with the future (F6)
            y = self._done_row(body, y, n)
            y = self._pause_row(body, y)
            return self._toast(body, y)
        y = self._header(body, y, view["title"], view["caption"])
        if view["rows"]:
            for row in view["rows"]:
                y = self._note(body, y, row)
        else:
            y = self._empty(body, y)
        y = self._add_row(body, y + 4)
        if view["more"]:
            y = self._link(body, y, f"{view['more']} more in {view['project']}", "›", lambda: self._go("project"))
        if self.sug and self.count:                  # notes come first; the offer is one quiet line
            y = self._link(body, y, f"Part of a project? {self.sug['peek']}", "›", lambda: self._go("suggest"))
        elif view.get("item"):
            left = f"In {view['filed']}" if view.get("filed") else "Not in a project. Add…"
            y = self._link(body, y, left, "›", lambda: self._go("pick"))
        if view["done_here"]:
            y = self._done_row(body, y, view["done_here"])
        y = self._pause_row(body, y)
        return self._toast(body, y)

    # -- projects: the suggestion, the picker, and the confirmation

    def _focus(self, field):
        if field is None:
            return
        self.card.makeKeyWindow()
        self.card.makeFirstResponder_(field)
        editor = field.currentEditor()
        if editor is not None:
            keep, self.keep_range = getattr(self, "keep_range", None), None
            end = len(field.stringValue())
            if keep is not None:                    # typing in the middle of a word keeps the caret where it was
                editor.setSelectedRange_((min(keep.location, end), min(keep.length, max(0, end - keep.location))))
            else:
                editor.setSelectedRange_((end, 0))

    def _search_field(self, placeholder):
        """The picker's text field. While you type it is the same field object, so its caret and undo survive."""
        old = getattr(self, "reuse_search", False) and self.search
        if old:
            old.setFrame_(NSMakeRect(12, 8, CARD_W - 16 - 24, 18))
            return old
        return _field(self.query, placeholder, 13, False, (12, 8, CARD_W - 16 - 24, 18), self.fields)

    def _item_id(self):
        return (self.data or {}).get("item")

    def _typed(self, text):
        if self.mode == "pick":
            self.query = text
            editor = self.search.currentEditor() if self.search is not None else None
            if editor is not None and editor.hasMarkedText():
                return                              # an input method is mid-word: rebuild when it commits
            self.keep_range = editor.selectedRange() if editor is not None else None
            self.reuse_search = self.search is not None
            try:
                self.render()                       # the rows follow the typing; the field itself is kept
            finally:
                self.reuse_search = False

    def _submitted(self, text):
        if self.mode == "pick" and text.strip():
            self._file(" ".join(text.split()))
        elif self.mode == "suggest":
            self._accept()

    def _cancelled(self):
        if self.card_open:
            self.toggle_card()

    def _finish(self, text):
        self.done_text = text
        self.mode, self.close_at = "done", time.time() + DONE_SECONDS
        self.data = self.provider()
        self.render()

    def _accept(self):
        sug = self.sug
        if not sug:
            return
        name = " ".join(self.name.stringValue().split()) or sug["name"]
        self.on_project([t["id"] for t in sug["things"]], name)
        self._finish(f"{name} created with {len(sug['things'])} things.")

    def _refuse(self):
        self.on_decline(True)
        self.sug = None
        self.toggle_card()

    def _file(self, name):
        view = self.picker(self._item_id(), self.query) or {"also": []}
        ids = [self._item_id()] + ([a["id"] for a in view["also"]] if self.also else [])
        self.on_project(ids, name)
        others = len(ids) - 1
        self._finish(f"Added to {name}" + (f" with {others} other{'s' * (others != 1)}." if others else "."))

    def _button(self, body, frame, text, callback, primary=False):
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(*frame), callback)
        dark = _dark()
        if primary:
            tap.tint = NSColor.colorWithWhite_alpha_(0.97 if dark else 0.09, 1.0)
            color = NSColor.colorWithWhite_alpha_(0.09 if dark else 1.0, 1.0)
        else:
            color = NSColor.secondaryLabelColor()
        label = _label(text, 13, bold=primary, color=color, frame=(0, 8, frame[2], 17))[0]
        label.setAlignment_(1)                      # centre
        tap.addSubview_(label)
        body.addSubview_(tap)
        return tap

    def _suggest(self, body, y):
        sug = self.sug
        body.addSubview_(_label("Looks like one project", 12, color=NSColor.secondaryLabelColor(),
                                frame=(PAD, y, CARD_W - 2 * PAD, 16))[0])
        y += 19
        self.name = _field(self.kept_name or sug["name"], "Project name", 16, True, (PAD - 2, y, CARD_W - 2 * PAD + 4, 22), self.fields)
        body.addSubview_(self.name)
        y += 30
        things = sug["things"]
        shown = things if self.sug_all else things[:SUGGEST_ROWS]
        for thing in shown:
            self._thing_row(body, y, thing)
            y += 38
        if len(things) > len(shown):
            more = _Tap.alloc().initWithFrame_callback_(NSMakeRect(PAD - 4, y, CARD_W - 2 * PAD + 8, 22), self._expand_things)
            more.addSubview_(_label(f"Show {len(things) - len(shown)} more", 12, color=NSColor.secondaryLabelColor(),
                                    frame=(4, 3, CARD_W - 2 * PAD, 16))[0])
            body.addSubview_(more)
            y += 26
        y += 4
        if sug["reason"]:
            body.addSubview_(_label(sug["reason"], 12, color=NSColor.secondaryLabelColor(),
                                    frame=(PAD, y, CARD_W - 2 * PAD, 16))[0])
            y += 22
        if sug["first_time"]:
            field, h = _label(PROJECT_EXPLAINER, 12, color=NSColor.secondaryLabelColor(),
                              frame=(PAD, y + 6, CARD_W - 2 * PAD, 16), wrap=True)
            body.addSubview_(field)
            y += h + 14
        self._button(body, (8, y, 150, 33), "Create project", self._accept, primary=True)
        self._button(body, (164, y, CARD_W - 164 - 8, 33), "Not these", self._refuse)
        return y + 41

    def _thing_row(self, body, y, thing):
        """One thing in the suggestion: its real icon, its name, and the app it lives in; hover for all of it."""
        row = _Flipped.alloc().initWithFrame_(NSMakeRect(PAD - 4, y, CARD_W - 2 * PAD + 8, 34))
        row.setToolTip_(thing["title"] + "\n" + thing["app"])
        image, web = icon_images.best(thing["app"], thing["kind"], thing.get("url"))
        if image is None:
            tile = _Tile.alloc().initWithKind_size_(thing["kind"], 30)
            tile.setFrame_(NSMakeRect(4, 2, 30, 30))
            row.addSubview_(tile)
        else:
            inset = 5 if web else 0             # a favicon fills its square; an app icon has its margin built in
            holder = NSImageView.alloc().initWithFrame_(NSMakeRect(4 + inset, 2 + inset, 30 - 2 * inset, 30 - 2 * inset))
            holder.setImage_(image)
            holder.setImageScaling_(3)
            row.addSubview_(holder)
        width = CARD_W - 2 * PAD - 42
        row.addSubview_(_label(thing["title"], 13, frame=(42, 2, width, 17))[0])
        row.addSubview_(_label(thing["app"], 11, color=NSColor.secondaryLabelColor(), frame=(42, 19, width, 14))[0])
        body.addSubview_(row)

    def _expand_things(self):
        self.sug_all = True
        self.kept_name = self.name.stringValue() if self.name is not None else None
        self.render()
        self.kept_name = None

    def _first_project(self, body, y, view):
        """The picker before you have any project: what one is, and a name to give it."""
        thing = view["title"].replace("Add ", "", 1).replace(" to a project", "")
        back = _Tap.alloc().initWithFrame_callback_(NSMakeRect(PAD - 4, y - 8, 120, 24), lambda: self._go("here"))
        back.addSubview_(_label("‹ Back", 12, color=NSColor.secondaryLabelColor(), frame=(4, 4, 110, 16))[0])
        body.addSubview_(back)
        y += 20
        head, _ = _label("Start a project", 16, bold=True, frame=(PAD, y, CARD_W - 2 * PAD, 20))
        head.setAlignment_(1)
        body.addSubview_(head)
        y += 23
        small, h = _label("Keep related things and their notes together.", 13, color=NSColor.secondaryLabelColor(),
                          frame=(PAD, y, CARD_W - 2 * PAD, 18), wrap=True)
        small.setAlignment_(1)
        body.addSubview_(small)
        y += h + 12
        box = _Dashed.alloc().initWithFrame_(NSMakeRect(PAD, y, CARD_W - 2 * PAD, 62))
        tile = _Tile.alloc().initWithKind_size_(view.get("kind", ""), 34)
        tile.setFrame_(NSMakeRect(14, 14, 34, 34))
        box.addSubview_(tile)
        box.addSubview_(_label(thing, 14, bold=True, frame=(58, 12, CARD_W - 2 * PAD - 70, 18))[0])
        box.addSubview_(_label("will be the first thing in it", 12, color=NSColor.secondaryLabelColor(),
                               frame=(58, 31, CARD_W - 2 * PAD - 70, 16))[0])
        body.addSubview_(box)
        y += 72
        field = _Tap.alloc().initWithFrame_callback_(NSMakeRect(8, y, CARD_W - 16, 34), None)
        field.fill = True
        self.search = self._search_field("Name it, like Pricing")
        field.addSubview_(self.search)
        body.addSubview_(field)
        y += 42
        typed = " ".join(self.query.split())
        button = self._button(body, (8, y, CARD_W - 16, 33), f"Create “{typed}”" if typed else "Create project",
                              (lambda: self._file(typed)) if typed else (lambda: self._focus(self.search)), primary=True)
        button.setAlphaValue_(1.0 if typed else 0.35)
        return y + 41

    def _pick(self, body, y):
        view = self.picker(self._item_id(), self.query)
        if view is None:
            return self._empty(body, y)
        self.pick_empty = view["total"] == 0
        if self.pick_empty:
            return self._first_project(body, y, view)
        y = self._header(body, y, "Add to a project", "", back=(view["title"].replace("Add ", "", 1).replace(" to a project", ""),
                                                                lambda: self._go("here")))
        box = _Tap.alloc().initWithFrame_callback_(NSMakeRect(8, y - 6, CARD_W - 16, 34), None)
        box.fill = True
        self.search = self._search_field(SEARCH_PLACEHOLDER)
        box.addSubview_(self.search)
        body.addSubview_(box)
        y += 36

        def row(y, mark, title, small, callback):
            tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y, CARD_W - 12, 40), callback)
            dot = _Flipped.alloc().initWithFrame_(NSMakeRect(PAD - 6 + 2, 7, 26, 26))
            dot.setWantsLayer_(True)
            dot.layer().setCornerRadius_(8)
            dot.layer().setBackgroundColor_(NSColor.labelColor().colorWithAlphaComponent_(0.12).CGColor())
            dot.addSubview_(_label(mark, 13, bold=True, frame=(0, 4, 26, 17))[0])
            dot.subviews()[0].setAlignment_(1)
            tap.addSubview_(dot)
            tap.addSubview_(_label(title, 13, frame=(PAD + 32, 5, CARD_W - 100, 17))[0])
            tap.addSubview_(_label(small, 11, color=NSColor.secondaryLabelColor(), frame=(PAD + 32, 22, CARD_W - 100, 14))[0])
            body.addSubview_(tap)
            return y + 40

        typed = view["new"]
        y = row(y, "+", f"New project “{typed}”" if typed else "New project",
                "Create and add this" if typed else "Name it above, then press Return",
                (lambda: self._file(typed)) if typed else (lambda: self._focus(self.search)))
        for p in view["rows"]:
            if p["here"]:
                y = row(y, _initial(p["name"]), p["name"], "Here now. Click to take it out.",
                        lambda: (self.on_unfile(self._item_id()), self._finish("Taken out of the project.")))
            else:
                y = row(y, _initial(p["name"]), p["name"], p["meta"], lambda name=p["name"]: self._file(name))
        if view["also"]:
            names = " and ".join(a["title"] for a in view["also"])
            tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(6, y + 4, CARD_W - 12, 36),
                                                       lambda: (setattr(self, "also", not self.also), self.render()))
            ring = _Ring.alloc().initWithChecked_(self.also)
            ring.setFrame_(NSMakeRect(PAD - 6 + 2, 9, 17, 17))
            tap.addSubview_(ring)
            note, h = _label(f"Also add {names}, open now", 12, color=NSColor.secondaryLabelColor(),
                             frame=(PAD + 24, 5, CARD_W - 12 - PAD - 30, 16), wrap=True)
            note.setFrame_(NSMakeRect(PAD + 24, 8, CARD_W - 12 - PAD - 30, h))
            tap.addSubview_(note)
            body.addSubview_(tap)
            y += 44
        return y

    def _done(self, body, y):
        ring = _Ring.alloc().initWithChecked_(True)
        ring.setFrame_(NSMakeRect(PAD, y + 2, 17, 17))
        body.addSubview_(ring)
        body.addSubview_(_label(self.done_text, 14, bold=True, frame=(PAD + 27, y, CARD_W - 2 * PAD - 27, 18))[0])
        body.addSubview_(_label("It will show on the project page.", 12, color=NSColor.secondaryLabelColor(),
                                frame=(PAD + 27, y + 20, CARD_W - 2 * PAD - 27, 16))[0])
        return y + 40

    def _project(self, body, view, y):
        y = self._header(body, y, view["title"], view["caption"], back=(view["back"], lambda: self._go("here")))
        for thing, rows in view["groups"]:
            field, _ = _label(thing.upper(), 11, bold=True, color=NSColor.secondaryLabelColor(),
                              frame=(PAD, y + 4, CARD_W - 2 * PAD, 14))
            body.addSubview_(field)
            y += 22
            for row in rows:
                y = self._note(body, y, row)
        if view["done_count"]:
            label = f"Done · {view['done_count']}"
            y = self._link(body, y + 4, label, "⌄" if view["done"] else "›",
                           lambda: self._go("project", show_done=not self.show_done))
            for row in view["done"]:
                y = self._note(body, y, row)
        return y

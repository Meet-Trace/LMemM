"""LMemM - the note card. ⌃⌥N opens it just above the pill and it is already listening:

    Note for  Pricing › Q3 plan                     ▍▍▍ Listening
    the words you say, as you say them (click in to edit)
    Return saves · Esc cancels

Like the pill's cards it is a translucent panel that never activates LMemM, so the app you
were in stays in front. It can still take the keyboard (to type or fix a word). This file only
draws; dictation.NotePanel owns the speech and decides what Return and Esc do.
"""

import math
import time

import objc
from Foundation import NSObject
from AppKit import (NSBezierPath, NSColor, NSFont, NSMakeRect, NSScreen, NSScrollView, NSTextView,
                    NSVisualEffectView)

import palette
import tokens
import widget
from widget import _Flipped, _font, _label, _panel, _veil, PAD, PILL_BOTTOM, POPOVER_MATERIAL, BEHIND_WINDOW, ACTIVE
SZ = tokens.SIZE                                   # the type scale (tokens.py)

CARD_W, CARD_H = 320, 150
GAP = 46                                    # from the screen's bottom edge to the card: clear of the pill
PLACEHOLDER = "Say what to remember…"
HINT = "Return saves  ·  Esc cancels"


def _accent(alpha=1.0):
    """The accent blue, lighter on the dark card so the words in it can be read (palette.py)."""
    r, g, b = palette.ACCENT_TEXT_ON_DARK if widget._dark() else palette.ACCENT_TEXT_ON_LIGHT
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, alpha)


class _Bars(_Flipped):
    """Five little bars that move while the mic is listening and sit still otherwise."""

    def initWithFrame_(self, frame):
        self = objc.super(_Bars, self).initWithFrame_(frame)
        self.live = False
        return self

    def drawRect_(self, rect):
        if self.live:
            _accent().setFill()
        else:
            NSColor.tertiaryLabelColor().setFill()
        mid = self.bounds().size.height / 2
        for i, base in enumerate((5, 12, 8, 13, 6)):
            h = max(3, base * (0.6 + 0.4 * math.sin(time.time() * 7 + i * 1.3))) if self.live else 3
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(i * 3.6, mid - h / 2, 2.5, h), 1.2, 1.2).fill()


class _Typing(NSObject):
    """The text view's delegate: Return saves, Esc cancels, Shift-Return is a new line."""

    def initWithCard_(self, card):
        self = objc.super(_Typing, self).init()
        self.card = card
        return self

    def textView_doCommandBySelector_(self, view, selector):
        selector = selector.decode() if isinstance(selector, bytes) else str(selector)
        if selector == "insertNewline:":
            self.card.on_save()
            return True
        if selector == "cancelOperation:":
            self.card.on_cancel()
            return True
        return False

    def textDidChange_(self, note):
        self.card.typed()


class NoteCard:
    def __init__(self, on_save, on_cancel):
        self.on_save, self.on_cancel = on_save, on_cancel
        self.panel = _panel(CARD_W, CARD_H, keyable=True)
        self.panel.setBecomesKeyOnlyIfNeeded_(False)        # Return and Esc must reach the text at once
        content = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W, CARD_H))
        content.setMaterial_(POPOVER_MATERIAL)
        content.setBlendingMode_(BEHIND_WINDOW)
        content.setState_(ACTIVE)
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(tokens.RADIUS["l"])
        content.layer().setMasksToBounds_(True)
        content.addSubview_(_veil(CARD_W, CARD_H))
        body = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W, CARD_H))
        mute = NSColor.secondaryLabelColor()

        self.crumb, _ = _label("", SZ["micro"], color=mute, frame=(PAD, 15, CARD_W - 2 * PAD - 100, 16))
        body.addSubview_(self.crumb)
        self.bars = _Bars.alloc().initWithFrame_(NSMakeRect(CARD_W - PAD - 92, 15, 16, 16))
        body.addSubview_(self.bars)
        self.status, _ = _label("", SZ["micro"], bold=True, frame=(CARD_W - PAD - 72, 15, 72, 16))
        self.status.setAlignment_(0)
        body.addSubview_(self.status)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, 40, CARD_W - 2 * PAD, 68))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(False)
        self.text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, CARD_W - 2 * PAD, 68))
        self.text.setDrawsBackground_(False)
        self.text.setFont_(_font(SZ["lead"]))
        self.text.setRichText_(False)
        self.text.setTextContainerInset_((0, 2))
        self.text.textContainer().setLineFragmentPadding_(0)
        self.text.setVerticallyResizable_(True)
        self.text.setHorizontallyResizable_(False)
        self.text.textContainer().setWidthTracksTextView_(True)
        self.text.setInsertionPointColor_(_accent())
        self.typing = _Typing.alloc().initWithCard_(self)
        self.text.setDelegate_(self.typing)
        scroll.setDocumentView_(self.text)
        body.addSubview_(scroll)
        self.placeholder, _ = _label(PLACEHOLDER, SZ["lead"], color=NSColor.tertiaryLabelColor(),
                                     frame=(PAD, 42, CARD_W - 2 * PAD, 20))
        body.addSubview_(self.placeholder)

        self.hint, _ = _label(HINT, SZ["micro"], color=NSColor.secondaryLabelColor(), frame=(PAD, CARD_H - 28, CARD_W - 2 * PAD, 14))
        body.addSubview_(self.hint)
        content.addSubview_(body)
        self.panel.setContentView_(content)

    # -- what the note panel drives

    def show(self, crumb, listening):
        self.crumb.setStringValue_(f"Note for  {crumb}"[:60])
        self.set_text("")
        self.hint.setStringValue_(HINT)
        self.status_is(listening)
        scr = NSScreen.mainScreen().visibleFrame()
        self.panel.setFrame_display_(NSMakeRect(scr.origin.x + (scr.size.width - CARD_W) / 2,
                                                scr.origin.y + GAP, CARD_W, CARD_H), True)
        self.panel.setAlphaValue_(1.0)                       # ⌃⌥N is a keyboard shortcut used all day: it appears at once
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.text)

    def hide(self):
        self.bars.live = False
        self.panel.orderOut_(None)

    @property
    def visible(self):
        return self.panel.isVisible()

    def status_is(self, listening, problem=None):
        """Listening (blue, moving), or off: the mic is unavailable and you type instead."""
        self.bars.live = bool(listening)
        self.bars.setNeedsDisplay_(True)
        if listening:
            self.status.setStringValue_("Listening")
            self.status.setTextColor_(_accent())
            self.hint.setStringValue_(HINT)
        else:
            self.status.setStringValue_("Mic is off")
            self.status.setTextColor_(NSColor.systemRedColor())
            self.hint.setStringValue_((problem or "Type your note instead.") + "  ·  Return saves")

    def animate(self):
        if self.bars.live:
            self.bars.setNeedsDisplay_(True)

    def string(self):
        return self.text.string()

    def set_text(self, text):
        self.text.setString_(text)
        self.text.scrollRangeToVisible_((len(text), 0))
        self.typed()

    def typed(self):
        self.placeholder.setHidden_(bool(self.text.string()))

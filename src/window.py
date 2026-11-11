"""LMemM - the main window. It draws window_model.view() (the state banner) and
page_model.view() (the project page), and forwards presses; it decides nothing (S8, W1-W6 in
window_model.py, P1-P8 in page_model.py). Only the drawing is here, so a Mac is needed to look at it.

Layout: the banner across the top when there is one; under it the sidebar (search, Needs you, the
project tree) and the page. The search field is built once and never rebuilt, so typing is never
interrupted by a redraw; the two scrolling areas keep their place when the data underneath changes.
"""

import os
import re
import sys
import time
import traceback

import objc
from Foundation import NSData, NSObject
from AppKit import (NSApplication, NSBezierPath, NSColor, NSImage, NSImageView, NSMakePoint, NSMakeRect, NSMenu, NSMenuItem, NSScrollView, NSTextView,
                    NSTextField, NSStrikethroughStyleAttributeName, NSWindow, NSWorkspace)

import ambient
import apps
import backdrop
import config
import favicons
import icon_images
import menu_model
import motion
import notes
import keynav
import page_model
import palette
import project_actions
import rules
import setup_kit as kit
import thing_actions
import window_model
from setup_kit import LEFT, RIGHT
from widget import _Fields, _Flipped, _Ring, _Tap

W, H = 1080, 720                                       # the size it first opens at; after that the window remembers its own
MIN_W, MIN_H = 880, 560                                # the smallest it can be dragged to
RADIUS_PANEL, RADIUS_FIELD = 14, 10
CARD_ALPHA = 0.60                                       # the cards let the background show through a little (a tint, never a blur)
DETAIL_ICONS = {"Last seen": ("waveform.path.ecg", "waveform"), "First seen": ("calendar",), "Time spent": ("clock",),
                "Visits": ("chart.bar", "chart.bar.fill"), "Address": ("link",)}                # one family of corners for the thing page
BUTTON_H, FIELD_H = 34, 40
SIDE_W = 250
FAVICON_INSET = 0.17                                   # of the icon square, each side: a favicon is drawn at 66% inside its soft tile
PAD = 32
MIN_MAIN, MAX_MAIN = 560, 980 
                         # the page's reading column: it grows with the window, up to this
TOP = 32                                               # below the title bar
TONES = {"red": "red", "grey": None, "calm": None}      # the banner box's tone (red is only for what is off)


def _is_dark():
    try:
        match = NSApplication.sharedApplication().effectiveAppearance().bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"])
        return match == "NSAppearanceNameDarkAqua"
    except Exception:
        return False


def hue_color(hue):
    """The eight calm colours projects wear. Never red: red is only for a permission that is off."""
    names = ("systemBlue", "systemPurple", "systemPink", "systemOrange", "systemTeal", "systemGreen",
             "systemIndigo", "systemBrown")
    return getattr(NSColor, names[hue % len(names)] + "Color")()


def _local_minute():
    """Minute of the day on the Mac's clock right now. Asks the system each time: Python's own time.localtime() keeps the
    time zone the process started in, so changing the Mac's time zone would never reach the background."""
    try:
        from Foundation import NSCalendar, NSDate, NSTimeZone
        NSTimeZone.resetSystemTimeZone()
        parts = NSCalendar.autoupdatingCurrentCalendar().components_fromDate_(32 | 64, NSDate.date())    # hour | minute
        return int(parts.hour()) * 60 + int(parts.minute())
    except Exception:
        return backdrop.minute_of(time.localtime())


def _short_ago(text):
    """"12 min ago" becomes "12m", "3 h ago" "3h", "5 days ago" "5d"; dates and "Yesterday" stay as they are."""
    return re.sub(r"^(\d+) (?:min|h|days?) ago$", lambda m: m.group(1) + {"m": "m", "h": "h", "d": "d"}[text.split()[1][0]], text)


class _SourcePick(NSObject):
    """Receives a menu pick and runs the callable kept for that item's tag."""

    def pick_(self, sender):
        call = self.calls.get(sender.tag())
        if call:
            call()


class _Row(_Tap):
    """A timeline row. The source (where the thing lives) stays out of sight until the pointer, the keys or a tick
    are on the row; the title then gives up that much width."""

    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(_Row, self).initWithFrame_callback_(frame, callback)
        self.reveal, self.title_view, self.title_frames, self.pinned = None, None, None, False
        return self

    @objc.python_method
    def show(self, on):
        if self.reveal is None or self.title_view is None:
            return
        on = on or self.pinned
        self.reveal.setHidden_(not on)
        self.title_view.setFrame_(self.title_frames[1 if on else 0])

    def mouseEntered_(self, event):
        objc.super(_Row, self).mouseEntered_(event)
        self.show(True)

    def mouseExited_(self, event):
        objc.super(_Row, self).mouseExited_(event)
        self.show(False)


class _Bar(_Flipped):
    """A short rounded bar in one colour that lets clicks through: the keyboard-focus mark at the left of a row."""

    def initWithFrame_color_(self, frame, color):
        self = objc.super(_Bar, self).initWithFrame_(frame)
        self.color = color
        return self

    def drawRect_(self, rect):
        self.color.setFill()
        box = self.bounds()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, box.size.width / 2, box.size.width / 2).fill()

    def hitTest_(self, point):
        return None


class _Rail(_Flipped):
    """The thin vertical line that ties a session's Things together on the Overview timeline."""

    def drawRect_(self, rect):
        kit.faint(0.14).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, 1, self.bounds().size.height))


class _MainWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def keyDown_(self, event):
        """Arrow keys and Return walk the lists (when no text field has the keyboard)."""
        handler = getattr(self, "on_key", None)
        if handler and handler(int(event.keyCode())):
            return
        objc.super(_MainWindow, self).keyDown_(event)

    def cancelOperation_(self, sender):
        """Esc: close what is open on top (a card), else step back, else clear the search."""
        if getattr(self, "on_escape", None):
            self.on_escape()


class _MainClosing(NSObject):
    """A closed window is only hidden; it opens again from the menu. A resized one is laid out again."""

    def windowShouldClose_(self, window):
        window.orderOut_(None)
        if getattr(self, "on_close", None):
            self.on_close()
        return False

    def windowDidResize_(self, note):
        if getattr(self, "on_resize", None):
            self.on_resize()


class _WindowMenuTarget(NSObject):
    """Where the menu bar's own items (Search, Settings…, Back, Quit) land. The standard ones (Close, Minimize,
    Zoom, Copy, Paste…) go down the responder chain to the window and the text fields."""

    def initWithActions_(self, actions):
        self = objc.super(_WindowMenuTarget, self).init()
        self.actions = actions
        return self

    def focusSearch_(self, sender):
        self.actions["search"]()

    def openSettings_(self, sender):
        self.actions["settings"]()

    def goBack_(self, sender):
        self.actions["back"]()

    def quitLMemM_(self, sender):
        self.actions["quit"]()


class _SideBackdrop(_Flipped):
    """The sidebar: a dark veil over the background, edge to edge (nothing of the window shows round it), and a
    hairline on its right. No blur: the room shows faintly through."""

    def drawRect_(self, rect):
        box = self.bounds()
        if config.WINDOW_APPEARANCE != "system":
            kit.warm("sidebar").colorWithAlphaComponent_(0.80).setFill()
            NSBezierPath.fillRect_(box)
            kit.warm("edge").setFill()
        else:
            kit.faint(0.035).setFill()
            NSBezierPath.fillRect_(box)
            kit.faint(0.09).setFill()
        NSBezierPath.fillRect_(NSMakeRect(box.size.width - 1, 0, 1, box.size.height))


class _Backdrop(_Flipped):
    """The background behind the whole window (sidebar and page), drawn in code: a dark ground, a few soft
    radial lights, a faint top light, a vignette and film grain, for each time-of-day background at its weight
    (backdrop.plan). Redrawn only when the mix changes. Passes clicks through."""

    def initWithFrame_(self, frame):
        self = objc.super(_Backdrop, self).initWithFrame_(frame)
        self.mix = {}
        return self

    def drawRect_(self, rect):
        try:
            self._paint()
        except Exception:
            traceback.print_exc()
            kit.warm("canvas").setFill()                                   # at worst: the plain graphite ground
            NSBezierPath.fillRect_(self.bounds())

    def _paint(self):
        from AppKit import NSGradient
        box = self.bounds()
        w, h = box.size.width, box.size.height
        weights = {k: v for k, v in self.mix.items() if v > 0.004 and backdrop.entry(k)}
        if not weights:
            weights = {"night": 1.0}
        total = sum(weights.values())
        base = [sum(backdrop.entry(k)["base"][i] * v for k, v in weights.items()) / total for i in range(3)]
        NSColor.colorWithSRGBRed_green_blue_alpha_(base[0], base[1], base[2], 1.0).setFill()
        NSBezierPath.fillRect_(box)
        for key, weight in weights.items():
            for fx, fy, fr, (r, g, b), strength in backdrop.entry(key)["lights"]:
                light = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, strength * weight / total)
                centre = NSMakePoint(fx * w, fy * h)
                NSGradient.alloc().initWithColors_([light, light.colorWithAlphaComponent_(light.alphaComponent() * 0.4),
                                                    light.colorWithAlphaComponent_(0.0)]) \
                    .drawFromCenter_radius_toCenter_radius_options_(centre, 0, centre, fr * max(w, h), 0)
        top = NSColor.whiteColor()                                         # a faint light from above, fading out
        NSGradient.alloc().initWithStartingColor_endingColor_(top.colorWithAlphaComponent_(0.025), top.colorWithAlphaComponent_(0.0)) \
            .drawInRect_angle_(NSMakeRect(0, 0, w, min(h, 320)), 90)
        dark = NSColor.blackColor()                                        # a soft vignette: the edges settle into the ground
        centre = NSMakePoint(w * 0.58, h * 0.36)
        NSGradient.alloc().initWithStartingColor_endingColor_(dark.colorWithAlphaComponent_(0.0), dark.colorWithAlphaComponent_(0.34)) \
            .drawFromCenter_radius_toCenter_radius_options_(centre, max(w, h) * 0.5, centre, max(w, h) * 0.95, 3)
        grain = sum(backdrop.entry(k)["grain"] * v for k, v in weights.items()) / total
        ambient.grain_color(grain).setFill()
        NSBezierPath.fillRect_(box)

    def hitTest_(self, point):
        return None


def _scroll(parent):
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    scroll.setDrawsBackground_(False)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setScrollerStyle_(1)                                   # overlay: thin, over the content's empty edge, never beside it
    scroll.setBorderType_(0)
    parent.addSubview_(scroll)
    return scroll


class MainWindow:
    """on_press(row id) is called when the banner's button is pressed (ids the menu already knows).
    data() returns (project registry, things by id) for the page."""

    def __init__(self, on_press, data, on_tick=None, on_add_note=None, on_project=None, on_project_undo=None, suggest=None, prefs=None, on_memory=None, memory_view=None):
        self.on_memory=on_memory or (lambda action,payload:None)
        self.memory_view=memory_view
        self.memory_pane=None
        self.memory_snapshot=None
        self.on_press = on_press
        self.data = data
        self.on_tick = on_tick or (lambda ids, done: None)               # tick or reopen notes (the pill's own function)
        self.on_add_note = on_add_note or (lambda item_id, text: None)
        self.on_project = on_project                                   # (action, args) -> {"message", "go", "undo"}; raises ValueError
        self.on_project_undo = on_project_undo or (lambda undo: None)
        self.suggest = suggest or (lambda: None)                        # the proposals waiting (suggestions.view)
        self.prefs = prefs or (lambda section: None)                    # the Settings page (settings_model.view)
        self.installed = None                                          # the apps on this Mac, found once (the picker and the icons)
        self.dialog = None                                             # the question or form on screen, if any
        self.done = None                                               # {"message", "undo", "until"}: the last project change
        self.sel = None                                                # None, or the ids of the things ticked in Select mode
        self.flow = rules.FinishFlow()                                 # hold, fold, Undo: the pill's rules (R10)
        self.flow_sig = None
        self.item_here = None
        self.banner = None                                         # the last window_model.view
        self.nav = page_model.new_state()
        self.shown = None
        self.cur = {"zone": "main", "id": None}                    # the row the arrow keys are on
        self.krows = {"side": [], "main": []}                      # what the keys can walk: [(id, open it, its view)]
        self.where = None                                          # (view, project, search) the page was last showing
        self.app_icons = {}
        self.app_paths = None
        self.W, self.H = W, H                                      # the content's size now; _measure() keeps them true
        self.pane_key = None                                       # which pane the main scroll area shows (None: a page of its own)
        self.main_w, self.col_x = MIN_MAIN, PAD                    # the page's column
        self.stamp, self.page_cache = None, None                   # what the last draw was for (so a resize does not recompute pages)
        style = 1 | 2 | 4 | 8 | (1 << 15)                          # titled, closable, minimise, resize, content under the title bar
        self.window = _MainWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, 2, False)
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(1)
        self.window.setReleasedWhenClosed_(False)
        self.window.setHidesOnDeactivate_(False)                   # it stays where it is when you go to another app
        self.window.setCollectionBehavior_(1 << 7)                 # the green button makes it full screen
        self.window.setContentMinSize_((MIN_W, MIN_H))
        palette.use(config.PAGE_THEME)
        if config.WINDOW_APPEARANCE in ("dark", "light"):          # one application: sidebar and pages together
            from AppKit import NSAppearance
            self.window.setAppearance_(NSAppearance.appearanceNamed_(
                "NSAppearanceNameDarkAqua" if config.WINDOW_APPEARANCE == "dark" else "NSAppearanceNameAqua"))
            self.window.setBackgroundColor_(kit.warm("canvas"))
        else:
            self.window.setBackgroundColor_(kit.card_background())
        self.closing = _MainClosing.alloc().init()
        self.closing.on_resize = self._resized
        self.closing.on_close = lambda: self._regular(False)
        self.window.setDelegate_(self.closing)
        self.window.on_escape = self.escape
        self.window.on_key = self.key
        self.root = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.window.setContentView_(self.root)
        self.wall = _Backdrop.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))      # the time-of-day picture, behind everything
        self.root.addSubview_(self.wall)
        self.ambient = ambient.AmbientCanvas(NSMakeRect(0, 0, W, H))   # the supplied artwork over it, once all four pictures are in
        self.root.addSubview_(self.ambient.view)
        try:
            self.ambient.attach(self.window)
        except Exception:
            traceback.print_exc()
        self.wall_tween = self.wall_clock = self.wall_choice = None
        self.backdrop = _SideBackdrop.alloc().initWithFrame_(NSMakeRect(0, 0, SIDE_W, H))
        self.root.addSubview_(self.backdrop)
        self.banner_host = _Flipped.alloc().initWithFrame_(NSMakeRect(SIDE_W, TOP, W - SIDE_W, 0))
        self.root.addSubview_(self.banner_host)
        self.head_host = _Flipped.alloc().initWithFrame_(NSMakeRect(SIDE_W, TOP, W - SIDE_W, 0))
        self.root.addSubview_(self.head_host)
        self.typing = _Fields.alloc().initWithChange_submit_cancel_(
            lambda text: self.press("search", text), lambda text: self.press("search", text), self._cancel_search)
        self.search = NSTextField.alloc().initWithFrame_(NSMakeRect(16, 0, SIDE_W - 32, 28))
        self.search.setPlaceholderString_("Search")
        self.search.setBezeled_(True)
        self.search.setBezelStyle_(1)
        self.search.setDelegate_(self.typing)
        self.typing.arrow = self._arrow_from_field
        self.root.addSubview_(self.search)
        self.side_scroll = _scroll(self.root)
        self.main_scroll = _scroll(self.root)
        self.keep_focus, self.note_surface, self.shot_at = False, None, None
        self.composing = False                                         # there are words in the note field that are not saved yet
        self.note_typing = _Fields.alloc().initWithChange_submit_cancel_(
            self._note_typed, self._submit_note, self._cancel_note)
        self.note_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 22))   # drawn inside the thing page (_composer)
        self.note_field.setPlaceholderString_("Add a note, then press Return")
        self.note_field.setBordered_(False)
        self.note_field.setBezeled_(False)
        self.note_field.setDrawsBackground_(False)
        self.note_field.setFocusRingType_(1)                           # none: the surface around it shows focus
        self.note_field.setFont_(kit.font(14, 400))
        self.note_field.setDelegate_(self.note_typing)
        self.note_field.setHidden_(True)
        self.toast_host = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.toast_host.setHidden_(True)
        self.root.addSubview_(self.toast_host)
        self.side_foot = _Flipped.alloc().initWithFrame_(NSMakeRect(0, H - 48, SIDE_W, 48))
        self.root.addSubview_(self.side_foot)
        self.overlay = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.overlay.setHidden_(True)
        self.root.addSubview_(self.overlay)
        self.dlg_typing = _Fields.alloc().initWithChange_submit_cancel_(
            self._dialog_typed, lambda text: self._dialog_submit(), self.close_dialog)
        self.dlg_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.dlg_field.setBezeled_(True)
        self.dlg_field.setBezelStyle_(1)
        self.dlg_field.setDelegate_(self.dlg_typing)
        self.dlg_field.setHidden_(True)
        self.overlay.addSubview_(self.dlg_field)
        if not self.window.setFrameUsingName_("LMemMMain"):         # last time's size and place, if there was one
            self.window.center()
        self.window.setFrameAutosaveName_("LMemMMain")
        self.menu_target = _WindowMenuTarget.alloc().initWithActions_(
            {"search": self.focus_search, "settings": self.open_settings, "back": self.go_back,
             "quit": lambda: self.on_press("quit")})
        self._install_menu()
        self._measure()
        self._layout()
        self._sync_wall(animate=False)
        self._start_wall_clock()

    # ------------------------------------------------------------ size, menu bar, Dock

    def _measure(self):
        """Read the window's size and work out the page's column. The column grows with the window up to
        MAX_MAIN, then stays centred, so a huge screen does not stretch lines across it."""
        size = self.root.frame().size
        self.W, self.H = max(MIN_W, size.width), max(MIN_H, size.height)
        room = self.W - SIDE_W - 2 * PAD
        self.main_w = max(MIN_MAIN, min(MAX_MAIN, room))
        self.col_x = PAD + max(0, (room - self.main_w) / 2)

    def _layout(self):
        """Put the parts that do not scroll where the window's size says."""
        self.backdrop.setFrame_(NSMakeRect(0, 0, SIDE_W, self.H))
        self.wall.setFrame_(NSMakeRect(0, 0, self.W, self.H))
        self.ambient.view.setFrame_(NSMakeRect(0, 0, self.W, self.H))
        self.side_foot.setFrame_(NSMakeRect(0, self.H - 48, SIDE_W, 48))
        self.overlay.setFrame_(NSMakeRect(0, 0, self.W, self.H))

    # ------------------------------------------------------------ the time-of-day background

    def _wall_target(self):
        return backdrop.plan(_local_minute(), config.BACKDROP)

    def _sync_ambient(self, target, animate):
        """Show the supplied artwork for the period the clock (or the pinned choice) says, if it is in place. The clock
        changes period over config.AMBIENT_FADE_SECONDS; a person's own pick in Settings over about a second.
        False means there is no artwork: the code-drawn stand-in below takes over."""
        if not self.ambient.usable:
            return False
        picked = config.BACKDROP != self.wall_choice
        self.wall_choice = config.BACKDROP
        try:
            shown = self.ambient.show(next(iter(target)), animate=animate, seconds=1.2 if picked else None)
        except Exception:
            traceback.print_exc()
            shown = False
        self.wall.setHidden_(shown)
        return shown

    def _sync_wall(self, animate=True):
        """Show what the clock (or the chosen background) says. Time itself only ever nudges the weights by a
        little; a manual change cross-fades over about a second (reduced motion: it just changes)."""
        target = self._wall_target()
        if self._sync_ambient(target, animate):
            return
        now = dict(self.wall.mix)
        keys = set(now) | set(target)
        if all(abs(now.get(k, 0) - target.get(k, 0)) < 0.004 for k in keys):
            return
        big = max(abs(now.get(k, 0) - target.get(k, 0)) for k in keys) > 0.2
        if not animate or not big or not now or motion.reduced():
            self.wall.mix = dict(target)
            self.wall.setNeedsDisplay_(True)
            return
        from Foundation import NSTimer
        if self.wall_tween is not None:
            self.wall_tween.invalidate()
        began, start, span = time.time(), now, 0.9 if big and config.BACKDROP != "auto" else config.AMBIENT_FADE_SECONDS

        def step(timer):
            try:
                self._wall_step(timer, began, span, start, target, keys)
            except Exception:
                traceback.print_exc()
                timer.invalidate()
                self.wall_tween = None
        self.wall_tween = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 30, True, step)

    def _wall_step(self, timer, began, span, start, target, keys):
        if True:
            t = min(1.0, (time.time() - began) / span)
            t = t * t * (3 - 2 * t)
            self.wall.mix = {k: start.get(k, 0) * (1 - t) + target.get(k, 0) * t for k in keys}
            self.wall.setNeedsDisplay_(True)
            if t >= 1.0:
                timer.invalidate()
                self.wall_tween = None

    def _clock_tick(self, timer):
        try:
            self._sync_wall()
        except Exception:
            traceback.print_exc()

    def _start_wall_clock(self):
        """Look at the clock once a minute (nothing else runs for the background)."""
        try:
            from Foundation import NSTimer
            self.wall_clock = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(60.0, True, self._clock_tick)
            self.wall_clock.setTolerance_(10.0)
            from Foundation import NSNotificationCenter
            self.wall_watch = [NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                name, None, None, lambda note: self._clock_tick(None))                         # a changed time zone or clock: look now
                for name in ("NSSystemTimeZoneDidChangeNotification", "NSSystemClockDidChangeNotification")]
        except Exception:
            self.wall_clock = None

    def _resized(self):
        """The window was dragged, zoomed or made full screen: lay it out again and redraw (the pages stay computed)."""
        self._measure()
        self._layout()
        self.shown = self.stamp = None
        self.render()
        if self.dialog:
            self._dialog()

    def _regular(self, on):
        """While the window is open LMemM is a normal app (in the Dock and ⌘-Tab, so you can come back to it from
        any other app); when it is closed it goes back to living in the menu bar."""
        try:
            NSApplication.sharedApplication().setActivationPolicy_(0 if on else 1)
        except Exception:
            pass

    def _install_menu(self):
        """The Mac menu bar while the window is up: Close, Minimize, Zoom, the editing keys text fields need
        (⌘C ⌘V ⌘A), ⌘K for search, ⌘, for Settings, ⌘[ for Back, ⌘Q to quit LMemM."""
        def item(menu, title, action, key="", target=None, mask=None):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            if target is not None:
                it.setTarget_(target)
            if mask is not None:
                it.setKeyEquivalentModifierMask_(mask)
            menu.addItem_(it)
            return it

        def submenu(bar, title):
            holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            sub = NSMenu.alloc().initWithTitle_(title)
            holder.setSubmenu_(sub)
            bar.addItem_(holder)
            return sub
        bar = NSMenu.alloc().initWithTitle_("")
        app = submenu(bar, "LMemM")
        item(app, "Settings…", "openSettings:", ",", self.menu_target)
        app.addItem_(NSMenuItem.separatorItem())
        item(app, "Quit LMemM", "quitLMemM:", "q", self.menu_target)
        edit = submenu(bar, "Edit")
        for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None), ("Cut", "cut:", "x"),
                                   ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
            if title is None:
                edit.addItem_(NSMenuItem.separatorItem())
            else:
                item(edit, title, action, key)
        window = submenu(bar, "Window")
        item(window, "Search", "focusSearch:", "k", self.menu_target)
        item(window, "Back", "goBack:", "[", self.menu_target)
        window.addItem_(NSMenuItem.separatorItem())
        item(window, "Close Window", "performClose:", "w")
        item(window, "Minimize", "performMiniaturize:", "m")
        item(window, "Zoom", "performZoom:")
        self.menu_bar = bar
        NSApplication.sharedApplication().setMainMenu_(bar)

    def _arrow_from_field(self, code):
        self.window.makeFirstResponder_(None)
        self.key(code)

    def key(self, code):
        """An arrow or Return. True when it was used."""
        if self.dialog:
            d = self.dialog
            if d["kind"] == "viewer" and code in (keynav.LEFT, keynav.RIGHT):      # step through the dated versions
                k = d["v"] + (1 if code == keynav.RIGHT else -1)
                if 0 <= k < len(d["screen"]["versions"]):
                    self._viewer_to(k)
                return True
            return False
        rows = {z: [(i, cb) for i, cb, _v in r] for z, r in self.krows.items()}
        state, call = keynav.press(self.cur, rows, code)
        if state == self.cur and call is None:
            return code in (keynav.UP, keynav.DOWN, keynav.LEFT, keynav.RIGHT)
        self.cur = state
        if call:
            call()
            return True
        self.render()
        view = dict((i, v) for i, _cb, v in self.krows[state["zone"]]).get(state["id"])
        if view is not None:
            view.scrollRectToVisible_(view.bounds())
        return True

    def _krow(self, zone, rid, call, view):
        """Register a row the keys can reach; True when the keys are on it (so it is drawn lit)."""
        self.krows[zone].append((rid, call, view))
        return self.cur["zone"] == zone and self.cur["id"] == rid

    def escape(self):
        if self.dialog:
            self.close_dialog()
        elif self.sel is not None:
            self.stop_select()
        elif self.nav["q"] and self.nav["view"] == "search":
            self._cancel_search()
        else:
            self.go_back()

    def focus_search(self):
        self.window.makeFirstResponder_(self.search)

    def go_back(self):
        kind = self.nav["view"]
        if kind in ("thing", "settings", "search"):
            self.back(kind)
        elif kind in ("needs", "archived", "project"):
            self.press("home")

    # ------------------------------------------------------------ the loop calls these

    def visible(self):
        return bool(self.window.isVisible())

    def show(self, banner):
        self._regular(True)
        if self.window.isMiniaturized():
            self.window.deminiaturize_(None)
        self.update(banner)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(None)                      # not in the search box: the arrow keys work at once
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def update(self, banner):
        """Redraw only when something a person would see has changed."""
        self.banner = banner
        self.render()

    def press(self, action, arg=None):
        reg, items = self.data()
        if action in {"go", "home", "needs"}:
            self.search.setStringValue_("")                        # leaving a search clears its words
        if action not in {"more", "more_subs", "more_side", "toggle"}:
            self.sel = None                                        # a selection belongs to the page it was made on
        self.nav = page_model.press(reg, self.nav, action, arg, items)
        self.render()

    def open_url(self, url):
        """Open a remembered web address in the default browser (only http/https ever reaches here)."""
        from Foundation import NSURL
        target = NSURL.URLWithString_(url) if favicons.host_of(url) else None
        if not (target and NSWorkspace.sharedWorkspace().openURL_(target)):
            self.done = {"message": "Couldn't open that.", "undo": None, "until": time.time() + 4}
            self.render_now()

    def copy_text(self, text, message):
        from AppKit import NSPasteboard
        paste = NSPasteboard.generalPasteboard()
        paste.clearContents()
        paste.setString_forType_(text, "public.utf8-plain-text")
        self.done = {"message": message, "undo": None, "until": time.time() + 3}
        self.render_now()

    def back(self, kind):
        if kind in ("thing", "settings"):
            self.press("back")
        elif kind == "search":
            self.press("clear")
        else:
            self.press("home")

    def tick(self):
        """Called a few times a second by the loop: a ticked note's hold and fold, and the Undo line,
        change with the clock, not with a press."""
        if self.visible():
            if self.flow_signature() != self.flow_sig:
                self.render()

    def flow_signature(self):
        """Everything that changes with the clock: a ticked note's hold, and the line at the bottom."""
        now = time.time()
        return (self.flow.signature(now), bool(self.done and now < self.done["until"]))

    def tick_note(self, nid, done):
        """One tap flips a note (R10). A note that is folding away ignores taps."""
        now = time.time()
        phase = self.flow.phase(nid, now)
        if phase == "fold":
            return
        self.on_tick([nid], done)
        left = self._open_left()
        if done:
            self.flow.tick(nid, now, left)
        elif phase == "hold":
            self.flow.cancel(nid, left)                            # changed your mind: no toast
        else:
            self.flow.reopened(now)
        self.render()

    def undo(self):
        ids = self.flow.undo(self._open_left())
        if ids:
            self.on_tick(ids, False)
        self.render()

    def _open_left(self):
        _reg, items = self.data()
        return sum(len(notes.open_notes(i)) for i in items.values())

    def _note_typed(self, text):
        """The surface around the field takes the blue edge while it holds words. No redraw of the page: that would
        interrupt an input method mid-word."""
        self.composing = bool((text or "").strip())
        self._note_edge()

    def _note_edge(self):
        surface = getattr(self, "note_surface", None)
        if surface is not None:
            surface.edge = kit.accent_text() if self.composing else kit.warm("edge")
            surface.setNeedsDisplay_(True)

    def _cancel_note(self):
        self.note_field.setStringValue_("")
        self.composing = False
        self._note_edge()

    def _submit_note(self, text):
        text = " ".join((text or "").split())
        if text and self.item_here:
            self.on_add_note(self.item_here, text)
            self.note_field.setStringValue_("")
            self.composing, self.keep_focus = False, True              # the cursor stays, ready for the next note
            self.render()

    def _cancel_search(self):
        self.search.setStringValue_("")
        self.press("clear")

    # ------------------------------------------------------------ drawing

    def render(self):
        """Draw the window. A failure here is printed and skipped: the window is never allowed to
        stop LMemM from remembering."""
        try:
            self._render()
        except Exception:
            self.shown = None
            print("window: could not draw the page:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    def _render(self):
        reg, items = self.data()
        now = time.time()
        page = page_model.view(reg, items, self.nav, fading=self.flow.holding(now), sugg=self.suggest(),
                               prefs=self.prefs(self.nav["sec"]) if self.nav["view"] == "settings" else None)
        self.memory_snapshot=self.memory_view(page['main'].get('pid')) if self.memory_view and page['main']['kind']=='project' else None
        self.flow_sig = self.flow_signature()
        sig = repr((window_model.signature(self.banner), page, self.flow_sig, self.sel, self.cur,self.memory_snapshot))
        if sig == self.shown:
            return
        self.shown = sig
        self.krows = {"side": [], "main": []}
        top = self._banner(self.banner and self.banner["banner"])
        main = page["main"]
        split = main["kind"] == "project" and (not main.get("empty") or self.memory_snapshot is not None)      # a project: its top stays put, Things scrolls
        head = self._head(main) if split else self._no_head()
        thing = main["kind"] == "thing"
        self.item_here = main.get("item") if thing else None
        self.note_field.setHidden_(not thing)
        bar = 0                                                    # the note field is part of the page
        self._toast(now, bar)
        self.search.setFrame_(NSMakeRect(16, TOP + 10, SIDE_W - 32, 28))
        self.side_scroll.setFrame_(NSMakeRect(0, TOP + 52, SIDE_W, self.H - TOP - 52 - 48))
        self._side_foot(page["side"])
        self.head_host.setFrame_(NSMakeRect(SIDE_W, top, self.W - SIDE_W, head))
        self.main_scroll.setFrame_(NSMakeRect(SIDE_W, top + head, self.W - SIDE_W, self.H - top - head - bar))
        here = (self.nav["view"], self.nav["pid"], self.nav["q"], self.nav["tid"])
        moved, self.where = here != self.where, here
        if moved:
            self.cur = {"zone": self.cur["zone"], "id": None}
        self._fill(self.side_scroll, SIDE_W, lambda doc: self._side(doc, page["side"]))
        if self.memory_snapshot and self.memory_snapshot['tab']=='Memory':
            from memory_pane import MemoryPane
            if self.memory_pane is None:self.memory_pane=MemoryPane(self._memory_action)
            self.memory_pane.render(self.memory_snapshot,self.W-SIDE_W,self.col_x,self.main_w)
            retained = self.memory_snapshot.get("retained_binding") or self.memory_snapshot.get("binding") or {}
            self._place_pane(self.memory_pane.host, ("memory", bool(self.memory_pane.settings), retained.get("project_id")))
        else:
            leaving_pane, self.pane_key = self.pane_key is not None, None
            self._fill(self.main_scroll, self.W - SIDE_W, lambda doc: self._main(doc, main, split), top=moved or leaving_pane)

    def _place_pane(self, host, key):
        """Show a pane's view in the scroll area without losing the place the person scrolled to. Setting the same
        document view again, or one the scroll area has not measured, snaps it back to the top: so the view is set only when
        it changes, and the old position is put back, kept inside the new height. A different pane or project starts at the top."""
        scroll = self.main_scroll
        clip = scroll.contentView()
        keep = 0 if key != self.pane_key else clip.bounds().origin.y
        self.pane_key = key
        if scroll.documentView() is not host:
            scroll.setDocumentView_(host)
        room = max(0, host.frame().size.height - scroll.frame().size.height)
        clip.scrollToPoint_(NSMakePoint(0, max(0, min(keep, room))))
        scroll.reflectScrolledClipView_(clip)

    def _fill(self, scroll, width, build, top=False):
        """Swap the scrolling area's contents, keeping the place the person had scrolled to (or
        going to the top when they have moved to another page)."""
        clip = scroll.contentView()
        keep = 0 if top else clip.bounds().origin.y
        doc = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, width, 10))
        height = build(doc)
        view_h = scroll.frame().size.height
        doc.setFrame_(NSMakeRect(0, 0, width, max(height + 24, view_h)))
        scroll.setDocumentView_(doc)
        clip.scrollToPoint_(NSMakePoint(0, max(0, min(keep, height + 24 - view_h))))
        scroll.reflectScrolledClipView_(clip)

    def _toast(self, now, bar):
        """"Marked done · Undo" while R10 says so: a small dark line at the bottom of the page."""
        for sub in list(self.toast_host.subviews()):
            sub.removeFromSuperview()
        toast = self.flow.toast(now)
        undo = self.undo
        if self.done and now < self.done["until"]:
            toast, undo = (self.done["message"], bool(self.done.get("undo"))), self.undo_project
        arriving = toast is not None and self.toast_host.isHidden()
        self.toast_host.setHidden_(toast is None)
        if toast is None:
            return
        text, undoable = toast
        width = min(kit.text_width(text, 13, 500) + (74 if undoable else 36), self.main_w)
        self.toast_host.setFrame_(NSMakeRect(SIDE_W + (self.W - SIDE_W - width) / 2, self.H - bar - 54, width, 36))
        pill = self._tap(self.toast_host, 0, 0, width, 36, None)
        pill.tint = NSColor.colorWithWhite_alpha_(palette.TOAST_DARK if _is_dark() else palette.TOAST_LIGHT, 0.96)       # dark page is 0.133: lift the toast so it has an edge
        kit.put_text(pill, text, 18, 0, width - 36, 13, 500, NSColor.whiteColor(), wrap=False, height=36, middle=True)
        if undoable:
            link = self._tap(pill, width - 62, 0, 56, 36, undo)
            kit.put_text(link, "Undo", 0, 0, 56, 13, 600, NSColor.colorWithRed_green_blue_alpha_(*palette.ACCENT_TEXT_ON_DARK, 1.0),
                         align=kit.CENTER, wrap=False, height=36, middle=True)
        if arriving:
            motion.pop(self.toast_host, origin="center")

    BANNER_SYMBOLS = {"paused:manual": "pause.circle.fill", "paused:away": "moon.zzz.fill", "screen_off": "exclamationmark.circle.fill",
                      "mic_off": "mic.slash.fill", "restart": "arrow.clockwise.circle.fill", "private": "lock.fill",
                      "unwatched": "eye.slash.fill", "first_run": "sparkles", "loading": "hourglass",
                      "damaged": "exclamationmark.triangle.fill"}

    def _banner(self, banner):
        """The one state banner: a soft box in the page's own column, its words on the left and
        its one button on the right. Returns where the page starts."""
        for sub in list(self.banner_host.subviews()):
            sub.removeFromSuperview()
        if not banner:
            self.banner_host.setFrame_(NSMakeRect(SIDE_W, TOP, self.W - SIDE_W, 0))
            return TOP
        button = banner["button"]
        room = self.main_w - (176 if button else 0)
        symbol = self.BANNER_SYMBOLS.get(banner["kind"], "info.circle.fill")
        height = kit.note(self.banner_host, TONES[banner["tone"]], banner["line"], self.col_x, 0, room,
                          lead=banner["title"], symbol=symbol)
        if button:
            row = button["id"]
            kit.button(self.banner_host, button["title"], self.col_x + room + 12, (height - 36) / 2, 164, 36,
                       lambda: self.on_press(row), kind="outline", size=13, weight=600)
        self.banner_host.setFrame_(NSMakeRect(SIDE_W, TOP + 6, self.W - SIDE_W, height))
        return TOP + 6 + height + 6

    # ------------------------------------------------------------ changing projects

    def project_action(self, action, **args):
        """Do a project change now. Returns True when it worked; on a ValueError the sentence is shown."""
        if self.on_project is None:
            return False
        try:
            result = self.on_project(action, args)
        except ValueError as error:
            if self.dialog:
                self.dialog["error"] = str(error)
                self._dialog()
            else:
                self.done = {"message": str(error), "undo": None, "until": time.time() + 5}
                self.render_now()
            return False
        if self.dialog and self.dialog.get("stay"):                   # the app picker stays open for the next app
            self._dialog()
        else:
            self.close_dialog()
        self.done = {"message": result["message"], "undo": result["undo"], "until": time.time() + 8}
        if result.get("stay"):                                     # a change to things: the page stays where it is
            self.sel = None
            self.render_now()
            return True
        self.press("go", result["go"]) if result["go"] else self.press("home")
        self.render_now()
        return True

    def render_now(self):
        self.shown = None
        self._sync_wall()
        self.render()

    def undo_project(self):
        if self.done and self.done.get("undo"):
            self.on_project_undo(self.done["undo"])
        self.done = None
        self.render_now()

    def _reg(self):
        return self.data()[0]

    def open_name(self, action, pid=None, parent=None):
        reg = self._reg()
        title = {"new": "New project" if parent is None else f"New sub-project in “{reg['projects'][parent]['name']}”",
                 "rename": "Rename project", "me": "Your first name"}[action]
        value = (reg["projects"][pid]["name"] if action == "rename"
                 else (self.prefs("you") or {}).get("you", {}).get("name", "") if action == "me" else "")
        self.dialog = {"kind": "name", "action": action, "pid": pid, "parent": parent, "title": title, "error": "", "value": value}
        self._dialog(focus=True)

    def setting(self, action, value=None):
        """Change a setting now (settings_model.py). Returns True when it worked."""
        return self.project_action(action, value=value)

    def open_settings(self, section=None):
        self.press("settings", section)

    def open_apps(self, purpose):
        """The app picker for "watch only these" or "never remember these"."""
        if self.installed is None:
            self.installed = apps.installed()
            self.app_paths = {a["name"]: a["path"] for a in self.installed}
        self.dialog = {"kind": "appick", "purpose": purpose, "query": "", "error": "", "stay": True}
        self._dialog(focus=True)

    def open_menu(self, pid):
        self.dialog = {"kind": "menu", "pid": pid}
        self._dialog()

    def open_pick(self, action, pid):
        self.dialog = {"kind": "pick", "action": action, "pid": pid, "query": "", "error": ""}
        self._dialog(focus=True)

    def open_ask(self, action, pid, target=None):
        reg, items = self.data()
        try:
            asking = project_actions.plan(reg, items, action, pid, target)
        except ValueError as error:
            self.dialog = {"kind": "ask", "action": action, "pid": pid, "target": target, "error": str(error),
                           "plan": {"title": "That can't be done", "text": str(error), "button": None}}
        else:
            self.dialog = {"kind": "ask", "action": action, "pid": pid, "target": target, "error": "", "plan": asking}
        self._dialog()

    def thing_action(self, action, ids, pid=None):
        """Do something to things (thing_actions.py); the page they are on stays."""
        return self.project_action(action, ids=list(ids), pid=pid, here=self.nav["pid"])

    def open_thing_menu(self, ids):
        self.dialog = {"kind": "tmenu", "ids": list(ids)}
        self._dialog()

    def open_thing_pick(self, action, ids):
        self.dialog = {"kind": "tpick", "action": action, "ids": list(ids), "query": "", "error": ""}
        self._dialog(focus=True)

    def open_thing_forget(self, ids):
        reg, items = self.data()
        try:
            asking = thing_actions.plan_forget(items, ids)
        except ValueError as error:
            asking = {"title": "That can't be done", "text": str(error), "button": None}
        self.dialog = {"kind": "task", "ids": list(ids), "plan": asking}
        self._dialog()

    def answer(self, action, sid, ids=(), name=None):
        """Answer a suggestion (suggestions.py): Create, Dismiss, Add, Not here."""
        return self.project_action(action, sid=sid, ids=list(ids), name=name)

    def start_select(self):
        self.sel = []
        self.render_now()

    def stop_select(self):
        self.sel = None
        self.render_now()

    def clear_select(self):
        """Deselect everything and stay in selection mode (Done is the way out)."""
        self.sel = []
        self.render_now()

    def pick_thing(self, tid):
        if self.sel is None:
            return
        self.sel = [i for i in self.sel if i != tid] if tid in self.sel else self.sel + [tid]
        self.render_now()

    def close_dialog(self):
        self.dialog = None
        self._dialog()

    def _dialog_typed(self, text):
        if self.dialog and self.dialog["kind"] in ("pick", "tpick", "appick"):
            self.dialog["query"] = text
            self._dialog()

    def _dialog_submit(self):
        d = self.dialog
        if not d or d["kind"] != "name":
            return
        name = self.dlg_field.stringValue()
        if d["action"] == "me":
            self.setting("set_name", name)
        elif d["action"] == "new":
            self.project_action("new", name=name, parent=d["parent"])
        else:
            self.project_action("rename", pid=d["pid"], name=name)

    def _dialog(self, focus=False):
        """Draw the question or form over the page (or take it away). The text field is built once
        and only moved, so typing is never interrupted."""
        for sub in list(self.overlay.subviews()):
            if sub is not self.dlg_field:
                sub.removeFromSuperview()
        d = self.dialog
        arriving = d is not None and self.overlay.isHidden()           # a dialog that was not on screen a moment ago
        self.overlay.setHidden_(d is None)
        self.dlg_field.setHidden_(True)
        if d is None:
            return
        forms = d["kind"] in ("name", "pick", "tpick", "appick")      # a click outside must not throw away what was typed
        scrim = self._tap(self.overlay, 0, 0, self.W, self.H, None if forms else self.close_dialog)
        scrim.tint = NSColor.colorWithWhite_alpha_(0.0, 0.32)
        scrim.washes = False                                         # a backdrop does not light up under the pointer
        cw, cx = 460, (self.W - 460) / 2
        cy = max(16, min(120, (self.H - 520) // 2))                  # a tall picker still ends inside the smallest window
        if d["kind"] == "viewer":
            cw = min(720, self.W - 64)
            cx, cy = (self.W - cw) / 2, 32
        card = self._tap(self.overlay, cx, cy, cw, 100, None)
        card.tint = kit.card_background()
        reg = self._reg()
        y = 24
        if d["kind"] == "name":
            kit.put_text(card, d["title"], 24, y, cw - 48, 18, 700, wrap=False, height=26)
            y += 42
            self._field_at(cx + 24, cy + y, cw - 48, d["value"] if focus else None, "Name")
            y += 44
            if d["error"]:
                y += kit.put_text(card, d["error"], 24, y, cw - 48, 13, 500) + 12
            y = self._dialog_buttons(card, cw, y + 6, "Cancel", "Make project" if d["action"] == "new" else "Save", self._dialog_submit)
        elif d["kind"] == "menu":
            name = reg["projects"][d["pid"]]["name"]
            kit.put_text(card, name, 24, y, cw - 48, 18, 700, wrap=False, height=26)
            y += 40
            for label, symbol, call in (
                    ("Rename…", "pencil", lambda: self.open_name("rename", d["pid"])),
                    ("Move to…", "arrow.turn.down.right", lambda: self.open_pick("move", d["pid"])),
                    ("Merge into…", "arrow.triangle.merge", lambda: self.open_pick("merge", d["pid"])),
                    ("Archive", "archivebox", lambda: (self.close_dialog(), self.project_action("archive", pid=d["pid"]))),
                    ("Delete…", "trash", lambda: self.open_ask("delete", d["pid"]))):
                row = self._tap(card, 12, y, cw - 24, 40, call)
                kit.glyph(row, (symbol, "circle"), 11, 11, 18, kit.ink())
                kit.put_text(row, label, 46, 0, cw - 24 - 60, 14, 500, wrap=False, height=40, middle=True)
                y += 42
            y += 12
        elif d["kind"] == "pick":
            name = reg["projects"][d["pid"]]["name"]
            kit.put_text(card, (f"Move “{name}” to…" if d["action"] == "move" else f"Merge “{name}” into…"), 24, y, cw - 48, 18, 700, wrap=False, height=26)
            y += 42
            self._field_at(cx + 24, cy + y, cw - 48, "" if focus else None, "Search projects")
            y += 44
            found = project_actions.targets(reg, d["pid"], d["action"], d["query"])
            y = self._place_rows(card, cw, y, found, self._picked)
            y = self._dialog_buttons(card, cw, y + 10, "Cancel", None, None)
        elif d["kind"] == "tmenu":
            reg, items = self.data()
            found = [items[i] for i in d["ids"] if i in items]
            one = found[0] if len(found) == 1 else None
            title = f"“{page_model.title_of(one)}”" if one else page_model.plural(len(found), "thing")
            kit.put_text(card, title, 24, y, cw - 48, 18, 700, wrap=False, height=26)
            y += 40
            rows = [("Move to…", "arrow.turn.down.right", lambda: self.open_thing_pick("assign", d["ids"])),
                    ("Also in…", "plus.circle", lambda: self.open_thing_pick("also", d["ids"]))]
            if one:
                for p in thing_actions.homes(reg, one)[:4]:
                    rows.append((f"Not in “{p['name']}”", "minus.circle",
                                 (lambda i=p["id"]: (self.close_dialog(), self.thing_action("not_this", d["ids"], i)))))
            rows.append(("Forget this…", "trash", lambda: self.open_thing_forget(d["ids"])))
            for label, symbol, call in rows:
                row = self._tap(card, 12, y, cw - 24, 40, call)
                kit.glyph(row, (symbol, "circle"), 11, 11, 18, kit.ink())
                kit.put_text(row, label, 46, 0, cw - 24 - 60, 14, 500, wrap=False, height=40, middle=True)
                y += 42
            y += 12
        elif d["kind"] == "tpick":
            reg, items = self.data()
            many = len(d["ids"]) > 1
            what = page_model.plural(len(d["ids"]), "thing") if many else "this"
            kit.put_text(card, (f"Move {what} to…" if d["action"] == "assign" else f"Also put {what} in…"), 24, y, cw - 48, 18, 700,
                         wrap=False, height=26)
            y += 42
            self._field_at(cx + 24, cy + y, cw - 48, "" if focus else None, "Search projects")
            y += 44
            found = thing_actions.places(reg, items, d["ids"], d["action"], d["query"])
            y = self._place_rows(card, cw, y, found, (lambda t: self.thing_action(d["action"], d["ids"], t)))
            y = self._dialog_buttons(card, cw, y + 10, "Cancel", None, None)
        elif d["kind"] == "appick":
            watch = d["purpose"] == "watch"
            kit.put_text(card, "Watch only these apps" if watch else "Never remember these apps", 24, y, cw - 48, 18, 700, wrap=False, height=26)
            y += 42
            self._field_at(cx + 24, cy + y, cw - 48, "" if focus else None, "Search apps")
            y += 44
            user = (self.prefs("apps" if watch else "privacy") or {})
            listed = {e["id"] for e in (user.get("apps", {}).get("chosen", []) if watch else user.get("privacy", {}).get("never", []))}
            found = [a for a in apps.search(self.installed or [], d["query"]) if a["id"] not in listed]
            for a in found[:7]:
                row = self._tap(card, 12, y, cw - 24, 40,
                                (lambda a=a: self.setting("watch_add" if watch else "skip_add", {"id": a["id"], "name": a["name"]})))
                self._app_icon(row, a["name"], 8, 5, 30)
                kit.put_text(row, a["name"], 48, 0, cw - 24 - 60, 14, 500, wrap=False, height=40, middle=True)
                y += 42
            if len(found) > 7:
                kit.put_text(card, f"{len(found) - 7} more. Keep typing to narrow it.", 24, y + 2, cw - 48, 12, 400, kit.mute(), wrap=False, height=18)
                y += 26
            if not found:
                kit.put_text(card, "No app matches.", 24, y + 2, cw - 48, 13, 400, kit.mute(), wrap=False, height=20)
                y += 28
            y = self._dialog_buttons(card, cw, y + 10, "Done", None, None)
        elif d["kind"] == "viewer":
            ch = max(320, self.H - 64)
            card.setFrame_(NSMakeRect(cx, cy, cw, ch))
            self._viewer(card, cw, ch, d)
            if arriving:
                motion.fade_in(scrim)
                motion.pop(card, origin="center", seconds=motion.MODAL_SECONDS, scale=motion.MODAL_SCALE)
            return
        elif d["kind"] == "task":
            plan = d["plan"]
            y += kit.put_text(card, plan["title"], 24, y, cw - 48, 17, 700) + 10
            y += kit.put_text(card, plan["text"], 24, y, cw - 48, 13, 400, kit.mute()) + 18
            action = lambda: self.thing_action("forget", d["ids"])
            y = self._dialog_buttons(card, cw, y, "Cancel", plan["button"], action if plan["button"] else None)
        elif d["kind"] == "ask":
            plan = d["plan"]
            y += kit.put_text(card, plan["title"], 24, y, cw - 48, 17, 700) + 10
            y += kit.put_text(card, plan["text"], 24, y, cw - 48, 13, 400, kit.mute()) + 18
            action = lambda: self.project_action(d["action"], pid=d["pid"], target=d["target"])
            y = self._dialog_buttons(card, cw, y, "Cancel", plan["button"], action if plan["button"] else None)
        card.setFrame_(NSMakeRect(cx, cy, cw, y + 22))
        if arriving:                                                 # occasional, so it earns a short arrival: fade + 0.96 scale
            motion.fade_in(scrim)
            motion.pop(card, origin="center", seconds=motion.MODAL_SECONDS, scale=motion.MODAL_SCALE)
        if not self.dlg_field.isHidden():                          # the field was added first: bring it above the backdrop and card
            self.dlg_field.removeFromSuperview()
            self.overlay.addSubview_(self.dlg_field)
        if focus:
            self.window.makeFirstResponder_(self.dlg_field)

    def _place_rows(self, card, cw, y, found, call):
        """The list of places in a picker (rows of {"id", "name", "path"}), then "n more". Returns y."""
        for r in found["rows"][:7]:
            row = self._tap(card, 12, y, cw - 24, 44, (lambda t=r["id"]: call(t)))
            if r["id"] is None:
                kit.tile(row, ("arrow.up.to.line", "tray.fill"), kit.ink(), 8, 7, 30)
            else:
                self._project_icon(row, page_model.hue(r["id"]), 8, 7, 30)
            kit.put_text(row, r["name"], 48, 4, cw - 24 - 60, 14, 600, wrap=False, height=20)
            kit.put_text(row, r["path"], 48, 23, cw - 24 - 60, 12, 400, kit.mute(), wrap=False, height=16)
            y += 46
        left = found["more"] + max(0, len(found["rows"]) - 7)
        if left:
            kit.put_text(card, f"{left} more. Keep typing to narrow it.", 24, y + 2, cw - 48, 12, 400, kit.mute(), wrap=False, height=18)
            y += 26
        if not found["rows"]:
            kit.put_text(card, "No place to put it there.", 24, y + 2, cw - 48, 13, 400, kit.mute(), wrap=False, height=20)
            y += 28
        return y

    def _field_at(self, x, y, w, value, placeholder):
        """Move the one text field into the dialog. `value` replaces its text; None leaves what was typed."""
        self.dlg_field.setFrame_(NSMakeRect(x, y, w, 30))
        self.dlg_field.setPlaceholderString_(placeholder)
        if value is not None:
            self.dlg_field.setStringValue_(value)
        self.dlg_field.setHidden_(False)

    def _dialog_buttons(self, card, cw, y, cancel, go, call):
        kit.button(card, cancel, cw - 24 - 110 - (8 + 140 if go else 0), y, 110, 36, self.close_dialog, kind="quiet", size=13, weight=600)
        if go:
            kit.button(card, go, cw - 24 - 140, y, 140, 36, call, kind="primary", size=13, weight=600)
        return y + 36

    def _picked(self, target):
        d = self.dialog
        if d["action"] == "move":
            self.project_action("move", pid=d["pid"], parent=target)
        else:
            self.open_ask("merge", d["pid"], target)

    # ------------------------------------------------------------ icons

    def _project_icon(self, parent, hue, x, y, size=26):
        kit.tile(parent, ("folder", "folder.fill"), hue_color(hue), x, y, size)

    def _app_icon(self, parent, app, x, y, size=30, kind="", url=None):
        """The best real icon there is: the installed app's, else the web page's favicon from the
        browser (`url`, `kind`), else a dimmed LMemM mark as a placeholder."""
        url = favicons.address(app, kind, url)
        image = self._app_image(app)                      # an installed app (WhatsApp.app) has its own icon
        if image is not None:
            self._icon_view(parent, image, x, y, size)
            return
        image = self._favicon(url) if url else None       # else the tab's icon from the browser
        if image is None:
            self._placeholder(parent, x, y, size)         # nothing to be had: LMemM's own mark, dimmed
            return
        # A favicon is a small bitmap that fills its whole square, unlike an app icon, which has its
        # margin built in. Sit it in a soft rounded tile at the same visual size, so every row matches.
        tile = kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x, y, size, size), None)
        tile.fill = kit.faint(0.10)
        tile.radius = size * 0.28
        parent.addSubview_(tile)
        inset = size * FAVICON_INSET
        self._icon_view(parent, image, x + inset, y + inset, size - 2 * inset)

    def _icon_view(self, parent, image, x, y, size):
        holder = NSImageView.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
        holder.setImage_(image)
        holder.setImageScaling_(3)
        holder.setAccessibilityElement_(False)
        parent.addSubview_(holder)

    def _placeholder(self, parent, x, y, size):
        """For a thing whose own icon we cannot find: the LMemM icon at half strength."""
        mark = NSApplication.sharedApplication().applicationIconImage()
        if mark is None:
            kit.tile(parent, ("circle.dashed", "app.fill"), kit.mute(), x, y, size)
            return
        holder = NSImageView.alloc().initWithFrame_(NSMakeRect(x, y, size, size))
        holder.setImage_(mark)
        holder.setImageScaling_(3)
        holder.setAlphaValue_(0.5)
        holder.setAccessibilityElement_(False)
        parent.addSubview_(holder)

    def _favicon(self, url):
        return icon_images.favicon(url)

    def _app_image(self, app):
        return icon_images.app_image(app)

    # ------------------------------------------------------------ small pieces

    def _tap(self, parent, x, y, w, h, callback, fill=False):
        tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(x, y, w, h), callback)
        tap.fill = fill
        parent.addSubview_(tap)
        return tap

    def _label(self, parent, text, x, y, w, size=12, weight=600, color=None, align=LEFT):
        kit.put_text(parent, text, x, y, w, size, weight, color or kit.mute(), align=align, wrap=False, height=size + 6)

    def _link(self, parent, text, x, y, callback, size=13, color=None, tip=None):
        """Quiet clickable text. Returns its width. `tip` is a short hover hint."""
        width = kit.text_width(text, size, 500) + 12
        tap = self._tap(parent, x, y, width, size + 12, callback)
        if tip:
            tap.setToolTip_(tip)
        kit.put_text(tap, text, 6, 0, width - 12, size, 500, color or kit.mute(), wrap=False, height=size + 12, middle=True)
        return width

    def _chips(self, parent, x, y, items, w=None):
        """Small buttons side by side, wrapping onto a new row when the column is narrow; items are
        (label, on, callback). Returns the height."""
        left, row = x, 0
        for label, on, callback in items:
            width = kit.text_width(label, 13, 500) + 26
            if w is not None and x > left and x + width > left + w:
                x, row = left, row + 1
            kit.button(parent, label, x, y + row * 34, width, 28, callback, kind="primary" if on else "quiet", size=13, weight=500)
            x += width + 6
        return 28 + row * 34

    # ------------------------------------------------------------ the sidebar

    def _side(self, doc, side):
        y = 6
        needs = side["needs"]
        self._label(doc, "NEEDS YOU", 16, y, 130)
        if needs["count"]:
            self._link(doc, f"See all {needs['count']}", SIDE_W - 96, y - 6, lambda: self.press("needs"), size=12)
        y += 24
        for row in needs["rows"]:
            call = lambda r=row: self.press("thing", r["id"])
            tap = self._tap(doc, 8, y, SIDE_W - 16, 34, call)
            tap.fill = self._krow("side", "n" + row["id"], call, tap)
            self._app_icon(tap, row["app"], 8, 5, 24, row.get("kind", ""), row.get("url"))
            kit.put_text(tap, row["title"], 40, 0, SIDE_W - 16 - 40 - 40, 13, 500, wrap=False, height=34, middle=True)
            kit.put_text(tap, str(row["open"]), SIDE_W - 16 - 34, 0, 26, 12, 500, kit.mute(), align=RIGHT, wrap=False, height=34, middle=True)
            y += 34
        if not needs["count"]:
            self._label(doc, "Nothing waiting.", 16, y, SIDE_W - 32, size=13, weight=400)
            y += 26
        y += 14
        self._label(doc, "PROJECTS", 16, y, 130)
        y += 24
        tree = side["tree"]
        for row in tree:
            x = 8 + min(row["level"], 9) * 14
            if row["id"] is None:                                  # "Show 8 more of 22"
                more = row["more"]
                self._link(doc, row["name"], x + 18, y, (lambda m=more: self.press("more_side", m)), size=12)
                y += 26
                continue
            call = (lambda: self.press("archived")) if row.get("folder") else (lambda r=row: self.press("go", r["id"]))
            tap = self._tap(doc, 8, y, SIDE_W - 16, 28, call, fill=row["selected"])
            if self._krow("side", "p" + row["id"], call, tap):
                tap.fill = True
            if row["expandable"]:
                caret = self._tap(tap, x - 8, 0, 22, 28, (lambda r=row: self.press("toggle", r["id"])))
                kit.put_text(caret, "▾" if row["expanded"] else "▸", 0, 0, 22, 11, 600, kit.mute(), align=kit.CENTER,
                             wrap=False, height=28, middle=True)
            if row.get("folder"):
                kit.glyph(tap, ("archivebox", "tray"), x + 18, 6, 16, kit.mute())
            else:
                self._project_icon(tap, row["hue"], x + 16, 4, 20)
            kit.put_text(tap, row["name"], x + 42, 0, SIDE_W - 16 - x - 42 - 40, 13, 500,
                         kit.mute() if row.get("archived") else None, wrap=False, height=28, middle=True)
            if row["count"]:
                kit.put_text(tap, str(row["count"]), SIDE_W - 16 - 38, 0, 30, 12, 400, kit.mute(), align=RIGHT,
                             wrap=False, height=28, middle=True)
            y += 28
        if not tree:
            self._label(doc, "None yet. They appear as you work.", 16, y, SIDE_W - 32, size=13, weight=400)
            y += 26
        return y

    # ------------------------------------------------------------ the page

    def _pane(self):
        if self.memory_pane is None:
            from memory_pane import MemoryPane
            self.memory_pane=MemoryPane(self._memory_action)
        return self.memory_pane

    def _memory_action(self,action,payload=None):
        payload=payload or {}
        try:
            if action=='tab' and self.memory_pane is not None:self.memory_pane.settings=False        # choosing a tab always leaves Settings
            if action in {'toggle_details','toggle_composer'}:
                pane=self._pane();attr='details' if action=='toggle_details' else 'composer'
                setattr(pane,attr,not getattr(pane,attr));self.shown=None;self.render();return
            if action=='repaint':self.shown=None;self.render();return
            if action=='view_source':            # Source from the Overview strip opens the same provenance view as the Memory page
                self._memory_action('tab',{'tab':'Memory'});self._memory_action('source',payload);return
            if action=='add_note':               # Add note from the Overview strip opens the Memory page's composer
                self._memory_action('tab',{'tab':'Memory'});self._pane().composer=True;self.shown=None;self.render();return
            if action in {'toggle_status','open_status'}:
                self._pane().settings=True            # the details live in Settings, never on the Memory page
                self.shown=None;self.render();return
            if action in {'open_settings','close_settings'}:
                self._pane().settings=action=='open_settings'
                if action=='open_settings':self.on_memory('tab',{'tab':'Memory'})
                self.shown=None;self.render();return
            if action=='choose_root':
                from AppKit import NSOpenPanel
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)     # bring the chooser in front
                panel=NSOpenPanel.openPanel();panel.setCanChooseDirectories_(True);panel.setCanChooseFiles_(False);panel.setAllowsMultipleSelection_(False)
                panel.setMessage_('Choose the folder for this project. LMemM will keep only new text you view there and the notes you add.')
                panel.setPrompt_('Enable Memory')
                def chosen(result):                          # a sheet on this window: it cannot open out of sight, and the app never blocks
                    if result==1:self._memory_dispatch('enroll',{'root':str(panel.URL().path())})
                self.window.makeKeyAndOrderFront_(None)
                panel.beginSheetModalForWindow_completionHandler_(self.window,chosen);return
            if action=='show_excerpt_form':
                from AppKit import NSAlert,NSView,NSTextField,NSMakeRect
                import secrets
                alert=NSAlert.alloc().init();alert.setMessageText_('Save project context')
                alert.setInformativeText_('Paste an excerpt with an optional source URL. This is saved as user-supplied text.')
                accessory=NSView.alloc().initWithFrame_(NSMakeRect(0,0,420,180))
                fields=[]
                for placeholder,y,h in [('Title',146,24),('Source URL (optional)',110,24),('Text to remember',0,98)]:
                    field=NSTextField.alloc().initWithFrame_(NSMakeRect(0,y,420,h));field.setPlaceholderString_(placeholder);field.setAccessibilityLabel_(placeholder);accessory.addSubview_(field);fields.append(field)
                alert.setAccessoryView_(accessory);alert.addButtonWithTitle_('Save excerpt');alert.addButtonWithTitle_('Save note');alert.addButtonWithTitle_('Cancel')
                def saved(response):
                    if response==1000:
                        title,url,text=[str(f.stringValue()) for f in fields]
                        self._memory_dispatch('save_excerpt',{'payload':dict(request_id=secrets.token_hex(16),kind='pasted_excerpt',title=title,url=url or None,text=text)})
                    elif response==1001:
                        self._memory_dispatch('note',{'text':str(fields[2].stringValue())})
                alert.beginSheetModalForWindow_completionHandler_(self.window,saved);return
            if action in {'forget_source','confirm_delete_memory'}:
                from AppKit import NSAlert
                alert=NSAlert.alloc().init()
                alert.setMessageText_('Delete this saved snapshot?' if action=='forget_source' else 'Delete this project’s retained memory?')
                alert.setInformativeText_('Removes retained evidence and its search/graph connections. Original notes, files and pages remain. New edits or saves may create new snapshots. This cannot be undone in LMemM.')
                alert.addButtonWithTitle_('Delete');alert.addButtonWithTitle_('Cancel')
                def forgotten(response):
                    if response==1000:self._memory_dispatch('delete_saved_source',{'source_id':payload['source_id']}) if action=='forget_source' else self._memory_dispatch('delete_project_memory',{})
                alert.beginSheetModalForWindow_completionHandler_(self.window,forgotten);return
            elif action in {'confirm_model','confirm_agent','confirm_disconnect'}:
                self._pane().confirm=action;self.shown=None;self.render();return        # asked in the window itself: a pop-up could open out of sight
            elif action=='confirm_no':
                self._pane().confirm=None;self.shown=None;self.render();return
            elif action=='confirm_yes':
                pane=self._pane();chosen=pane.confirm;pane.confirm=None
                if not chosen:return
                action={'confirm_model':'install_model','confirm_agent':'connect_agent','confirm_disconnect':'disconnect'}[chosen];payload={'approved':True}
            elif action=='allow_accessibility':
                import permissions
                permissions.MacSystem().open_settings('ax')        # lists LMemM there, then opens the page
                return
            elif action=='copy_codex_prompt':
                from AppKit import NSPasteboard
                from datetime import datetime,timedelta,timezone
                project=(self.memory_snapshot.get('binding') or {}).get('project_id')
                if project:
                    now=datetime.now(timezone.utc);fmt='%Y-%m-%dT%H:%M:%SZ'
                    prompt=('Use only the lmemm_memory tools. Call get_project_context for project_id %s with start %s and end %s. '
                            'List my open tasks and decisions, with their citations.')%(project,(now-timedelta(days=30)).strftime(fmt),(now+timedelta(days=1)).strftime(fmt))
                    paste=NSPasteboard.generalPasteboard();paste.clearContents();paste.setString_forType_(prompt,'public.utf8-plain-text')
                    self.done={"message":"Copied. Paste it into Codex.","undo":None,"until":time.time()+3};self.render_now()
                return
            elif action=='copy_codex_command':
                from AppKit import NSPasteboard
                command=((self.memory_snapshot.get('preview') or {}).get('connection') or {}).get('command')
                if command:
                    paste=NSPasteboard.generalPasteboard();paste.clearContents();paste.setString_forType_(command,'public.utf8-plain-text')
                    self.done={"message":"Copied. Paste it in Terminal to finish.","undo":None,"until":time.time()+3};self.render_now()
                return
            elif action=='copy_preview':
                from AppKit import NSPasteboard
                import json
                preview=self.memory_snapshot.get('preview')
                if preview:
                    paste=NSPasteboard.generalPasteboard();paste.clearContents();paste.setString_forType_(json.dumps(preview,ensure_ascii=False,indent=2),'public.utf8-plain-text')
                    self.done={"message":"Copied the context preview","undo":None,"until":time.time()+3};self.render_now()
                return
        except (ValueError,OSError) as error:
            if self.memory_snapshot:self.memory_snapshot['error']=str(error)
            self.shown=None;self.render();return
        self._memory_dispatch(action,payload)

    def _memory_dispatch(self,action,payload):
        try:self.on_memory(action,payload)
        except (ValueError,OSError) as error:
            if self.memory_snapshot:self.memory_snapshot['error']=str(error)
        self.shown=None;self.render()

    def _main(self, doc, page, split=False):
        x, w = self.col_x, self.main_w
        if split:
            if page.get("empty"):return self._empty(doc,page["empty"],x,20,w)
            return self._things(doc, page, x, 6, w)
        y = 30
        kind = page["kind"]
        if kind in ("search", "needs", "thing", "archived", "settings"):
            self._link(doc, "‹ Back", x - 6, y - 8, lambda: self.back(kind))
            y += 24
        if kind == "project":
            y = self._crumbs(doc, page["crumbs"], x, y)
        tx = x
        if kind == "project":
            self._project_icon(doc, page["hue"], x, y - 2, 40)
            tx = x + 54
        elif kind in ("home", "needs", "archived", "settings"):
            kit.glyph(doc, {"home": ("square.stack.3d.up", "folder"), "needs": ("bell", "bell.badge"),
                           "archived": ("archivebox", "tray"), "settings": ("gearshape", "gear")}[kind], x + 4, y + 2, 28, kit.ink())
            tx = x + 54
        if kind == "thing":                                       # the identity lives in the page's hero, below
            y += 8
        else:
            kit.put_text(doc, page["title"], tx, y, w - (tx - x) - (170 if kind == "project" else 0), 26, 700, wrap=False, height=34)
            y += 36
            if page.get("meta"):
                self._label(doc, page["meta"], tx, y, w - (tx - x), size=13, weight=400)
                y += 34
        if kind == "home":
            kit.button(doc, "+ New project", x + w - 150, 30, 150, 34, lambda: self.open_name("new"), kind="quiet", size=13, weight=600)
        if kind == "project":
            self._project_actions(doc, x + w, 28, page["pid"])
        if kind == "thing":
            self._thing_buttons(doc, x + w, 18, page["item"])
        build = {"home": self._home, "project": self._project, "needs": self._needs, "search": self._search,
                 "thing": self._thing_page, "archived": self._archived, "settings": self._settings}[kind]
        return build(doc, page, x, y + 6, w)

    def _crumbs(self, doc, chain, x, y, quiet=False):
        cx = x - 6
        cx += self._link(doc, "All projects", cx, y - 12, lambda: self.press("home"), size=12)
        for i, c in enumerate(chain):
            self._label(doc, "›", cx, y - 8, 12, size=12, weight=400)
            cx += 12
            last = i == len(chain) - 1
            if c["id"] and not last:
                cx += self._link(doc, c["name"], cx, y - 12, (lambda p=c["id"]: self.press("go", p)), size=12)
            else:
                cx += self._link(doc, c["name"], cx, y - 12, None, size=12, color=kit.ink() if last and not quiet else kit.mute())
        return y + 18

    def _section(self, doc, text, x, y, w):
        kit.put_text(doc, text, x, y, w, 15, 700, wrap=False, height=22)
        return y + 30

    def _empty(self, doc, empty, x, y, w, symbol=("tray.fill", "folder.fill")):
        kit.tile(doc, symbol, kit.mute(), x, y + 10, 56)
        kit.put_text(doc, empty["title"], x, y + 80, w, 18, 600, wrap=False, height=26)
        h = kit.put_text(doc, empty["line"], x, y + 112, min(w, 460), 14, 400, kit.mute())
        return y + 112 + h

    def _thing(self, doc, t, x, y, w, notes=False, select=False, compact=False):
        """A thing: its title, where it is, when. With notes: the open ones underneath. While selecting,
        a tap ticks it instead of opening it. Returns the new y."""
        call = (lambda i=t["id"]: self.pick_thing(i)) if select else (lambda i=t["id"]: self.press("thing", i))
        box = (_Row if compact else _Tap).alloc().initWithFrame_callback_(NSMakeRect(x, y, w, 10), call)
        if not notes:
            box.fill = self._krow("main", "t" + t["id"], call, box)
        if compact:
            return self._thing_line(doc, box, t, x, y, w, select)
        lead = 0
        if select:
            on = t["id"] in self.sel
            kit.tile(box, ("checkmark.circle.fill", "checkmark.circle") if on else ("circle", "circle"),
                     kit.ink() if on else kit.mute(), 0, 12, 22)
            lead = 32
        self._app_icon(box, t["app"], lead, 8, 34, t.get("kind", ""), t.get("url"))
        tx = 46 + lead
        kit.put_text(box, t["title"], tx, 6, w - tx - 130, 14, 600, wrap=False, height=20)
        if t["ago"]:
            kit.put_text(box, t["ago"], w - 120, 6, 120, 12, 400, kit.mute(), align=RIGHT, wrap=False, height=20)
        kit.put_text(box, t["sub"], tx, 27, w - tx - (96 if t["open"] else 0), 12, 400, kit.mute(), wrap=False, height=18)
        if t["open"]:
            kit.put_text(box, f"{t['open']} open", w - 90, 27, 90, 12, 600, kit.ink(), align=RIGHT, wrap=False, height=18)
        h = 52
        if notes:
            for text in t.get("notes", []):
                h += kit.put_text(box, "○  " + text, tx, h - 2, w - tx, 13, 400, height=None) + 4
            if t.get("more_notes"):
                self._label(box, f"+{t['more_notes']} more", tx, h, w - tx, size=12, weight=400)
                h += 20
            h += 6
        box.setFrame_(NSMakeRect(x, y, w, h))
        doc.addSubview_(box)
        return y + h

    def _thing_line(self, doc, box, t, x, y, w, select):
        """The timeline row. Resting: a source plate, the title, the time. Pointer, keys or a tick on it: the source
        appears too. The open-notes count stays, since it asks something of you."""
        h, lead = 48, 0
        ticked = False
        if select:
            ticked = t["id"] in self.sel
            kit.tile(box, ("checkmark.circle.fill", "checkmark.circle") if ticked else ("circle", "circle"),
                     kit.accent_text() if ticked else kit.mute(), 2, 13, 22)
            lead = 30
            box.fill = box.fill or ticked                                # a ticked row stays softly lit
        elif box.fill:                                                   # the keys are on this row: the lit wash and a short blue mark
            box.addSubview_(_Bar.alloc().initWithFrame_color_(NSMakeRect(0, 12, 3, 24), kit.accent_text()))
        plate = kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(lead + 8, 8, 32, 32), kit.faint(0.07), None, 9)
        box.addSubview_(plate)
        self._app_icon(box, t["app"], lead + 12, 12, 24, t.get("kind", ""), t.get("url"))
        tx = lead + 52
        right = w - 14
        if t["ago"]:
            kit.put_text(box, _short_ago(t["ago"]), right - 70, 0, 70, 13, 400, kit.mute(), align=RIGHT, wrap=False, height=h, middle=True, track=0.01)
            right -= 78
        if t["open"]:
            kit.put_text(box, f"{t['open']} open", right - 70, 0, 70, 13, 600, kit.accent_text(), align=RIGHT, wrap=False, height=h, middle=True)
            right -= 78
        src_w = min(220, max(int((right - tx) * 0.34), 0)) if t["sub"] else 0
        wide = max(right - 12 - tx, 40)
        before = len(box.subviews())
        kit.put_text(box, t["title"], tx, 0, wide, 15, 600 if ticked else 500, wrap=False, height=h, middle=True, track=-0.006)
        title_view = box.subviews()[before]
        if src_w:
            kit.put_text(box, t["sub"], right - src_w, 0, src_w, 13, 400, kit.mute(), align=RIGHT, wrap=False, height=h, middle=True, track=0.01)
            reveal = box.subviews()[before + 1]
            box.reveal, box.title_view = reveal, title_view
            box.title_frames = (NSMakeRect(tx, 0, wide, h), NSMakeRect(tx, 0, max(wide - src_w - 12, 40), h))
            box.pinned = bool(box.fill) or ticked
            box.show(False)
        box.setFrame_(NSMakeRect(x, y, w, h))
        doc.addSubview_(box)
        return y + h

    def _card(self, doc, t, x, y, w):
        """A pick-up card: a soft box with the thing and its open notes."""
        tap = self._tap(doc, x, y, w, 10, (lambda i=t["id"]: self.press("thing", i)), fill=True)
        end = self._thing(tap, t, 12, 4, w - 24, notes=True)
        tap.setFrame_(NSMakeRect(x, y, w, end + 6))
        return y + end + 6 + 10

    def _rows_of_projects(self, doc, rows, x, y, w):
        for r in rows:
            call = lambda p=r["id"]: self.press("go", p)
            tap = self._tap(doc, x, y, w, 56, call, fill=True)
            if self._krow("main", "p" + r["id"], call, tap):
                tap.tint = NSColor.labelColor().colorWithAlphaComponent_(0.14)
            self._project_icon(tap, r["hue"], 12, 12, 32)
            kit.put_text(tap, r["name"], 56, 9, w - 70, 14, 600, wrap=False, height=20)
            kit.put_text(tap, r["line"], 56, 30, w - 70, 12, 400, kit.mute(), wrap=False, height=16)
            y += 64
        return y

    def _home(self, doc, page, x, y, w):
        if page.get("suggest"):
            y = self._suggest_home(doc, page["suggest"], x, y, w)
        if page["empty"]:
            return self._empty(doc, page["empty"], x, y, w)
        needs = page["needs"]
        if needs["count"]:
            y = self._section(doc, "Needs you", x, y, w)
            for row in needs["rows"]:
                y = self._card(doc, row, x, y, w)
            if needs["count"] > len(needs["rows"]):
                self._link(doc, f"See all {needs['count']}", x - 6, y - 4, lambda: self.press("needs"))
                y += 30
        y = self._section(doc, "Projects", x + 0, y + 6, w)
        y = self._rows_of_projects(doc, page["cards"], x, y, w)
        if page["unplaced"]:
            y += 6
            self._label(doc, f"{page['unplaced']:,} thing{'s' * (page['unplaced'] != 1)} not in a project yet.",
                        x, y, w, size=13, weight=400)
            y += 26
        return y

    def _suggest_home(self, doc, sug, x, y, w):
        """Proposals on the first page: projects to make (Create / Dismiss), and projects with things waiting."""
        y = self._section(doc, f"Suggestions · {sug['count']}", x, y, w)
        for e in sug["projects"]:
            y = self._card_box(doc, x, y, w, lambda box, inner, e=e: self._suggest_project(box, inner, e)) + 8
        for e in sug["items"]:
            tap = self._tap(doc, x, y, w, 56, (lambda p=e["pid"]: self.press("go", p)), fill=True)
            self._project_icon(tap, e["hue"], 12, 12, 32)
            n = e["count"]
            kit.put_text(tap, f"{n} thing{'s' * (n != 1)} may belong in “{e['name']}”", 56, 9, w - 56 - 110, 14, 600, wrap=False, height=20)
            kit.put_text(tap, e["line"], 56, 30, w - 56 - 110, 12, 400, kit.mute(), wrap=False, height=16)
            kit.button(tap, "Review", w - 94, 11, 82, 34, (lambda p=e["pid"]: self.press("go", p)), kind="outline", size=12, weight=600)
            y += 64
        return y + 14

    def _suggest_project(self, box, inner, e):
        """"Make “Trip” a project?" with the things that would go in it. Returns the inner height."""
        w = inner + 28
        kit.tile(box, ("sparkles", "wand.and.stars", "star.fill"), kit.ink(), 14, 12, 32)
        kit.put_text(box, f"Make “{e['name']}” a project?", 58, 12, w - 58 - 190, 14, 600, wrap=False, height=20)
        n = len(e["things"])
        kit.put_text(box, f"{n} things · {e['reason']}", 58, 32, w - 58 - 190, 12, 400, kit.mute(), wrap=False, height=16)
        kit.button(box, "Dismiss", w - 14 - 84 - 8 - 84, 14, 84, 32, (lambda: self.answer("dismiss_project", e["id"])), kind="quiet", size=13, weight=600)
        kit.button(box, "Create", w - 14 - 84, 14, 84, 32, (lambda: self.answer("accept_project", e["id"])), kind="primary", size=13, weight=600)
        top = 56
        for t in e["things"][:3]:
            kit.put_text(box, t["title"], 58, top, w - 58 - 14, 12, 500, wrap=False, height=18)
            top += 20
        if n > 3:
            kit.put_text(box, f"+{n - 3} more", 58, top, w - 58 - 14, 12, 400, kit.mute(), wrap=False, height=18)
            top += 20
        kit.put_text(box, "Dismiss it and LMemM will not suggest this again.", 58, top + 2, w - 58 - 14, 12, 400, kit.mute(), wrap=False, height=18)
        return top + 20 - 12

    def _suggested(self, doc, e, x, y, w):
        """On a project: things that may belong here, each with Add and Not here. Returns the new y."""
        n = len(e["things"])
        kit.put_text(doc, f"Suggested for this project · {n}", x, y, w, 15, 700, wrap=False, height=22)
        if n > 1:
            label = f"Add all {n}"
            self._link(doc, label, x + w - kit.text_width(label, 13, 500) - 12, y - 4, lambda: self.answer("accept_items", e["id"]))
        y += 30

        def rows(box, inner):
            top = 12
            for t in e["things"][:5]:
                call = (lambda i=t["id"]: self.press("thing", i))
                self._app_icon(box, t["app"], 14, top + 6, 30, t.get("kind", ""), t.get("url"))
                open_it = self._tap(box, 54, top, inner + 28 - 54 - 190, 44, call)
                kit.put_text(open_it, t["title"], 0, 3, inner + 28 - 54 - 190, 14, 600, wrap=False, height=20)
                kit.put_text(open_it, t["sub"], 0, 23, inner + 28 - 54 - 190, 12, 400, kit.mute(), wrap=False, height=16)
                kit.button(box, "Not here", inner + 14 - 84, top + 6, 84, 32, (lambda i=t["id"]: self.answer("reject_items", e["id"], [i])), kind="quiet", size=13, weight=600)
                kit.button(box, "Add", inner + 14 - 84 - 6 - 64, top + 6, 64, 32, (lambda i=t["id"]: self.answer("accept_items", e["id"], [i])), kind="primary", size=13, weight=600)
                top += 44
            if n > 5:
                kit.put_text(box, f"+{n - 5} more after these", 14, top + 2, inner, 12, 400, kit.mute(), wrap=False, height=18)
                top += 22
            kit.put_text(box, "“Not here” keeps a thing out of this project for good. LMemM will not suggest it again.",
                         14, top + 2, inner, 12, 400, kit.mute(), wrap=False, height=18)
            return top + 22 - 12
        return self._card_box(doc, x, y, w, rows) + 20

    def _needs(self, doc, page, x, y, w):
        for t in page["things"]:
            y = self._card(doc, t, x, y, w)
        if not page["things"]:
            y = self._empty(doc, {"title": "All caught up", "line": "No open notes anywhere."}, x, y, w,
                                 symbol=("checkmark.circle.fill", "checkmark.circle"))
        return y

    def _project(self, doc, page, x, y, w):
        """A project with nothing in it yet (one with things is split: _head above, _things below)."""
        if page.get("suggested"):
            y = self._suggested(doc, page["suggested"], x, y, w)
        return self._empty(doc, page["empty"], x, y, w)

    def _project_actions(self, parent, right, y, pid):
        """"+ Sub-project" and the "…" menu, at the right of a project's title."""
        kit.button(parent, "⋯", right - 40, y, 40, 32, lambda: self.open_menu(pid), kind="quiet", size=16, weight=700)
        kit.button(parent, "+ Sub-project", right - 40 - 8 - 120, y, 120, 32, lambda: self.open_name("new", parent=pid),
                   kind="quiet", size=13, weight=600)

    def _thing_buttons(self, parent, right, y, tid):
        """"Move to…" and "⋯" at the right of a thing's title."""
        kit.button(parent, "⋯", right - 40, y, 40, 32, lambda: self.open_thing_menu([tid]), kind="quiet", size=16, weight=700)
        kit.button(parent, "Move to…", right - 40 - 8 - 100, y, 100, 32, lambda: self.open_thing_pick("assign", [tid]),
                   kind="quiet", size=13, weight=600)

    # ------------------------------------------------------------ settings

    def _settings(self, doc, page, x, y, w):
        """Settings: six sections as tabs, then the one you are on. Every control changes the setting at once."""
        y += self._chips(doc, x, y, [(t["title"], t["on"], (lambda i=t["id"]: self.press("settings", i))) for t in page["tabs"]], w) + 24
        return getattr(self, "_set_" + page["section"])(doc, page[page["section"]], x, y, w)

    def _row(self, doc, x, y, w, title, line, build=None, height=60):
        """A soft row: a title, a quiet line under it, and whatever `build(row, width)` puts at the right."""
        row = self._tap(doc, x, y, w, height, None, fill=True)
        kit.put_text(row, title, 16, 10, w - 200, 14, 600, wrap=False, height=20)
        if line:
            kit.put_text(row, line, 16, 32, w - 200, 12, 400, kit.mute(), wrap=False, height=16)
        if build:
            build(row, w)
        return y + height + 8

    def _set_you(self, doc, you, x, y, w):
        y = self._row(doc, x, y, w, "First name", you["name"] or "Not set yet",
                      lambda row, rw: kit.button(row, "Change…", rw - 106, 14, 90, 32, lambda: self.open_name("me"), kind="outline", size=13, weight=600))
        self._label(doc, you["line"], x, y - 2, w, size=12, weight=400)
        y = self._section(doc, "What you mostly work on", x, y + 30, w)
        x2 = x
        for r in you["roles"]:
            width = kit.text_width(r["role"], 13, 500) + 30
            kit.button(doc, r["role"], x2, y, width, 32, (lambda v=r["role"]: self.setting("toggle_role", v)),
                       kind="primary" if r["on"] else "quiet", size=13, weight=500)
            x2 += width + 8
        return y + 48

    def _app_list(self, doc, chosen, x, y, w, remove):
        for e in chosen:
            row = self._tap(doc, x, y, w, 48, None, fill=True)
            self._app_icon(row, e["name"], 14, 9, 30)
            kit.put_text(row, e["name"], 56, 0, w - 56 - 110, 14, 500, wrap=False, height=48, middle=True)
            kit.button(row, "Remove", w - 14 - 84, 8, 84, 32, (lambda i=e["id"]: self.setting(remove, i)), kind="quiet", size=13, weight=600)
            y += 56
        return y

    def _set_apps(self, doc, apps_, x, y, w):
        y = self._row(doc, x, y, w, "Every app" if apps_["mode"] == "all" else "Only some apps", apps_["line"])
        y = self._app_list(doc, apps_["chosen"], x, y, w, "watch_remove")
        kit.button(doc, "Add apps…", x, y + 4, 120, 34, lambda: self.open_apps("watch"), kind="primary", size=13, weight=600)
        if apps_["chosen"]:
            kit.button(doc, "Watch every app", x + 128, y + 4, 150, 34, lambda: self.setting("watch_all"), kind="quiet", size=13, weight=600)
        return y + 50

    def _set_access(self, doc, access, x, y, w):
        for r in access["rows"]:
            off = r["state"] != "granted"
            red = off and r["key"] != "ax"                        # red is for a permission that is off (accessibility is optional)
            row = self._tap(doc, x, y, w, 64, None, fill=True)
            kit.tile(row, ("exclamationmark.circle.fill", "circle") if off else ("checkmark.circle.fill", "checkmark.circle"),
                     NSColor.systemRedColor() if red else kit.mute(), 14, 17, 30)
            kit.put_text(row, r["title"] + (f"  ·  {r['tag']}" if r["tag"] else ""), 58, 11, w - 58 - 230, 14, 600, wrap=False, height=20)
            kit.put_text(row, r["line"], 58, 33, w - 58 - 230, 12, 400, kit.mute(), wrap=False, height=16)
            if r["button"]:
                call = "restart" if r["action"] == "restart" else "access_ax" if r["key"] == "ax" else "access"
                kit.button(row, r["button"], w - 14 - 180, 16, 180, 32, (lambda c=call: self.on_press(c)), kind="outline", size=13, weight=600)
            y += 72
        self._label(doc, "Read from macOS every time. Nothing here is remembered.", x, y + 2, w, size=12, weight=400)
        return y + 30

    def _set_privacy(self, doc, privacy, x, y, w):
        y = self._section(doc, "LMemM never reads", x, y, w)
        for text in privacy["always"]:
            row = self._tap(doc, x, y, w, 40, None, fill=True)
            kit.tile(row, ("lock.fill", "lock"), kit.mute(), 12, 7, 26)
            kit.put_text(row, text, 52, 0, w - 64, 13, 500, wrap=False, height=40, middle=True)
            y += 46
        y = self._section(doc, "Never remember these apps", x, y + 20, w)
        y = self._app_list(doc, privacy["never"], x, y, w, "skip_remove")
        if not privacy["never"]:
            self._label(doc, "None yet.", x, y, w, size=13, weight=400)
            y += 28
        self._label(doc, privacy["line"], x, y, w, size=12, weight=400)
        kit.button(doc, "Add apps…", x, y + 26, 120, 34, lambda: self.open_apps("skip"), kind="primary", size=13, weight=600)
        return y + 74

    def _set_general(self, doc, general, x, y, w):
        y = self._section(doc, "Keep screenshots for", x, y, w)
        x2 = x
        for k in general["keep"]:
            width = kit.text_width(k["label"], 13, 500) + 30
            kit.button(doc, k["label"], x2, y, width, 32, (lambda d=k["days"]: self.setting("keep_days", d)),
                       kind="primary" if k["on"] else "quiet", size=13, weight=500)
            x2 += width + 8
        y += 44
        y += kit.put_text(doc, general["keep_line"], x, y, w, 12, 400, kit.mute()) + 24
        y = self._section(doc, "Background", x, y, w)
        x2 = x
        for k in general["backdrop"]["rows"]:
            width = kit.text_width(k["label"], 13, 500) + 30
            kit.button(doc, k["label"], x2, y, width, 32, (lambda key=k["key"]: self.setting("backdrop", key)),
                       kind="primary" if k["on"] else "quiet", size=13, weight=500)
            x2 += width + 8
        y += 44
        y += kit.put_text(doc, general["backdrop"]["line"], x, y, w, 12, 400, kit.mute()) + 24
        y = self._row(doc, x, y, w, "Add a note", "Press this anywhere to write or dictate a note.",
                      lambda row, rw: kit.put_text(row, general["hotkey"], rw - 116, 0, 100, 15, 600, align=RIGHT, wrap=False, height=60, middle=True))
        if self.memory_view:
            y=self._section(doc,general['memory']['title'],x,y+16,w)
            y+=kit.put_text(doc,general['memory']['line'],x,y,w,13,400,kit.mute())+12
            kit.button(doc,'Choose project…',x,y,160,32,lambda:self.press('home'),kind='outline',size=13,weight=600)
            y+=52
        return self._row(doc, x, y, w, "Setup", "Go through the first-run questions again. Your memory and notes stay.",
                         lambda row, rw: kit.button(row, "Reopen setup…", rw - 14 - 130, 14, 130, 32, lambda: self.on_press("setup"), kind="outline", size=13, weight=600))

    def _set_data(self, doc, data, x, y, w):
        line = (f"{data['files']:,} file{'s' * (data['files'] != 1)} · {menu_model.size_text(data['bytes'])}" if data["known"]
                else "LMemM cannot read its data folder.")
        y = self._row(doc, x, y, w, "Everything stays on this Mac", (data["where"] + "  ·  " if data["known"] else "") + line, height=60)
        y = self._row(doc, x, y, w, "Memory file", "Everything LMemM remembers, in one readable file.",
                      lambda row, rw: kit.button(row, "Show in Finder", rw - 14 - 130, 14, 130, 32, lambda: self.on_press("show_file"), kind="outline", size=13, weight=600))
        return self._row(doc, x, y, w, "Delete all my data", "Asks first, says what goes, then restarts LMemM.",
                         lambda row, rw: kit.button(row, "Delete…", rw - 14 - 130, 14, 130, 32, lambda: self.on_press("delete"), kind="outline", size=13, weight=600))

    def _side_foot(self, side):
        """Pinned under the tree: make a project."""
        for sub in list(self.side_foot.subviews()):
            sub.removeFromSuperview()
        self.side_foot.setFrame_(NSMakeRect(0, self.H - 48, SIDE_W, 48))
        tap = self._tap(self.side_foot, 8, 8, SIDE_W - 16 - 40, 34, lambda: self.open_name("new"))
        kit.glyph(tap, ("plus.circle", "plus"), 9, 8, 18, kit.ink())
        kit.put_text(tap, "New project", 40, 0, 110, 13, 500, wrap=False, height=34, middle=True)
        gear = self._tap(self.side_foot, SIDE_W - 8 - 34, 8, 34, 34, lambda: self.open_settings(), fill=self.nav["view"] == "settings")
        kit.glyph(gear, ("gearshape", "gear"), 8, 8, 18, kit.mute())

    def _archived(self, doc, page, x, y, w):
        if page["empty"]:
            return self._empty(doc, page["empty"], x, y, w, symbol=("archivebox.fill", "tray.fill"))
        for r in page["rows"]:
            tap = self._tap(doc, x, y, w, 56, (lambda i=r["id"]: self.press("go", i)), fill=True)
            self._project_icon(tap, r["hue"], 12, 12, 32)
            kit.put_text(tap, r["name"], 56, 9, w - 56 - 110, 14, 600, wrap=False, height=20)
            kit.put_text(tap, (r["where"] + " · " if r["where"] else "") + r["line"], 56, 30, w - 56 - 110, 12, 400, kit.mute(), wrap=False, height=16)
            kit.button(tap, "Bring back", w - 100, 11, 88, 34, (lambda i=r["id"]: self.project_action("restore", pid=i)),
                       kind="outline", size=12, weight=600)
            y += 64
        return y

    # The top of a project page stays where it is; only the list of things under it scrolls.

    def _no_head(self):
        for sub in list(self.head_host.subviews()):
            sub.removeFromSuperview()
        return 0

    def _head(self, page):
        """Breadcrumb, title, Pick up, sub-projects and the Things controls. Returns its height."""
        host = self.head_host
        for sub in list(host.subviews()):
            sub.removeFromSuperview()
        x, w = self.col_x, self.main_w
        y = self._crumbs(host, page["crumbs"], x, 26, quiet=True)                      # identity: where you are, quietly
        self._project_icon(host, page["hue"], x, y + 2, 36)
        if page["archived"]:
            kit.button(host, "Bring back", x + w - 110, y, 110, 32, lambda: self.project_action("restore", pid=page["pid"]),
                       kind="outline", size=13, weight=600)
        else:
            self._grouped_actions(host, x + w, y + 2, page["pid"])
        title_w = min(kit.text_width(page["title"], 26, 700), w - 52 - 190)
        kit.put_text(host, page["title"], x + 50, y - 2, title_w + 8, 26, 700, wrap=False, height=34, track=-0.026)
        kit.put_text(host, page["meta"], x + 50, y + 33, max(w - 50 - 190, 60), 13, 400, kit.mute(), wrap=False, height=20)
        y += 66
        if self.memory_snapshot:
            in_settings = bool(self.memory_pane and self.memory_pane.settings and (self.memory_snapshot.get('binding') or self.memory_snapshot.get('retained_binding')))
            tabs = self.memory_snapshot['tabs']
            self._segmented(host, x, y, [(tab, self.memory_snapshot['tab'] == tab and not in_settings,
                                          (lambda t=tab: self._memory_action('tab', {'tab': t}))) for tab in tabs], size=14, height=40, pad=26)
            if self.memory_snapshot.get('binding') or self.memory_snapshot.get('retained_binding'):
                gx = x + w - 32                                                        # Settings: a quiet gear at the far edge, not a tab
                gear = self._tap(host, gx, y + 4, 32, 32, lambda: self._memory_action('open_settings'), fill=in_settings)
                gear.focusable, gear.ax_label = True, "Settings"
                gear.setToolTip_("Settings")
                kit.glyph(gear, ("gearshape", "gear"), 8, 8, 16, kit.ink() if in_settings else kit.mute())
            y += 40 + (16 if self.memory_snapshot['tab'] == 'Memory' else 30)
            if self.memory_snapshot['tab'] == 'Memory' or page.get('empty'):return y
        y = self._status(host, page, x, y, w)
        host.addSubview_(kit._KitRule.alloc().initWithFrame_(NSMakeRect(x, y + 8, w, 1)))   # the one separator: identity above, activity below
        y += 32
        y = self._toolbar(host, page, x, y, w)
        return y

    def _grouped_actions(self, parent, right, y, pid):
        """"+ Sub-project" and "…" as one soft pair, not two buttons."""
        add_w, more_w = int(kit.text_width("+ Sub-project", 13, 600)) + 28, 36
        total = add_w + more_w + 1
        x = right - total
        parent.addSubview_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, y, total, 32), kit.faint(0.07), None, 10))
        add = self._tap(parent, x, y, add_w, 32, lambda: self.open_name("new", parent=pid))
        kit.put_text(add, "+ Sub-project", 14, 0, add_w - 28, 13, 600, wrap=False, height=32, middle=True)
        add.radius = 10
        parent.addSubview_(_Rail.alloc().initWithFrame_(NSMakeRect(x + add_w, y + 8, 1, 16)))
        more = self._tap(parent, x + add_w + 1, y, more_w, 32, lambda: self.open_menu(pid))
        kit.put_text(more, "⋯", 0, 0, more_w, 16, 700, align=kit.CENTER, wrap=False, height=32, middle=True)
        more.setToolTip_("More")
        more.radius = 10

    def _status(self, host, page, x, y, w):
        """Where you left off, in one quiet strip above the history. With Memory on it leads with the one thing to pick
        up (an unfinished next action, else a decision, else a note) and the two quick actions; below it, the
        things with open notes as small links (their notes show on hover). With nothing at all it is one muted line."""
        snap = self.memory_snapshot if (self.memory_snapshot and self.memory_snapshot.get("binding") and self.memory_snapshot.get("tab") == "Overview") else None
        from memory_view import friendly, resume_item
        result = (snap or {}).get("result") or {}
        item = resume_item(result) if snap else None
        chips = page["pick_up"]
        has_memory = bool(result) and any(result.get(k) for k in ("open_tasks", "decisions", "saved_activity"))
        empty = bool(snap) and not item and not chips and bool(result) and not has_memory and not snap.get("loading")
        if not snap:
            if not chips:
                caught = page["caught_up"]
                kit.glyph(host, ("checkmark.circle", "checkmark.circle.fill"), x, y + 3, 14, kit.mute())
                tw = kit.text_width(caught["title"], 12, 600)
                kit.put_text(host, caught["title"], x + 20, y, tw + 6, 12, 600, kit.mute(), wrap=False, height=20, middle=True)
                kit.put_text(host, caught["line"], x + 20 + tw + 10, y, max(w - 20 - tw - 10, 40), 12, 400, kit.mute(), wrap=False, height=20, middle=True)
                return y + 28
        if not snap:                                                                  # Memory is off: only the quiet activity links
            cx = x
            cw = min(220, (w - 8 * (len(chips) - 1)) / len(chips))
            for t in chips:
                tap = self._tap(host, cx, y, cw, 26, (lambda i=t["id"]: self.press("thing", i)), fill=True)
                tap.setToolTip_("\n".join("\u25cb  " + n for n in t.get("notes", [])[:3]) or t["title"])
                self._app_icon(tap, t["app"], 6, 4, 18, t.get("kind", ""), t.get("url"))
                kit.put_text(tap, t["title"] + (f"  {t['open']}" if t["open"] else ""), 30, 0, cw - 38, 12, 500, wrap=False, height=26, middle=True)
                cx += cw + 8
            return y + 38
        gap = 8
        kit.put_text(host, "Resume", x, y - 2, 120, 16, 700, wrap=False, height=24, middle=True, track=-0.012)        # the section, named
        y += 28
        top = y
        aw = int(kit.text_width("Add note", 13, 600)) + 28
        kit.button(host, "Add note", x + w - aw, top + 7, aw, 28, lambda: self._memory_action("add_note"), kind="primary", size=13, weight=600)
        right = x + w - aw - gap
        if not empty:
            cw_ = int(kit.text_width("Copy context", 13, 600)) + 28
            kit.button(host, "Copy context", right - cw_, top + 7, cw_, 28, lambda: self._memory_action("copy_context"), kind="outline", size=13, weight=600)
            right -= cw_ + gap
        if item:                                                                      # one unit: accent edge, title first, then type, time, Source
            host.addSubview_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, top + 1, 3, 40), kit.accent(0.85), None, 1.5))
            left = x + 16
            stamp = friendly(item["at"]) if item["at"] else ""
            kit.put_text(host, item["title"], left, top, max(right - left - 16, 80), 15, 600, wrap=False, height=22, middle=True, track=-0.004)
            cx = left
            kw = int(kit.text_width(item["kind"], 12, 600)) + 2
            kit.put_text(host, item["kind"], cx, top + 24, kw, 12, 600, kit.accent_text(), wrap=False, height=18, middle=True)
            cx += kw + 8
            if stamp:
                tw_ = int(kit.text_width(stamp, 12, 400)) + 2
                kit.put_text(host, "\u00b7  " + stamp, cx, top + 24, tw_ + 16, 12, 400, kit.mute(), wrap=False, height=18, middle=True)
                cx += tw_ + 22
            if item["citation_id"]:
                sw_ = int(kit.text_width("Source", 12, 600)) + 30
                pill = kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(cx, top + 21, sw_, 24), lambda c=item["citation_id"]: self._memory_action("view_source", {"citation_id": c}))
                pill.fill, pill.radius, pill.ax_label = kit.accent(0.10), 8, "Source for: " + item["title"][:60]
                host.addSubview_(pill)
                kit.put_text(pill, "Source  \u203a", 0, 0, sw_, 12, 600, kit.accent_text(), align=kit.CENTER, wrap=False, height=24, middle=True)
                cx += sw_ + gap
            if item["done"]:
                dw_ = int(kit.text_width("Mark done", 12, 600)) + 22
                kit.button(host, "Mark done", cx, top + 21, dw_, 24, lambda d=item["done"]: self._memory_action(d[0], d[1]), kind="outline", size=12, weight=600)
            y = top + 46
            undo = snap.get("last_completed")
            if undo:
                kit.put_text(host, "Marked done", left, y, 90, 12, 500, kit.mute(), wrap=False, height=24, middle=True)
                uw = int(kit.text_width("Undo", 12, 600)) + 22
                kit.button(host, "Undo", left + 88, y, uw, 24, lambda: self._memory_action(undo[0], undo[1]), kind="outline", size=12, weight=600)
                y += 30
        else:
            msg = "Save a note to start" if empty else "" if snap.get("loading") else page["caught_up"]["title"] if not chips else ""
            if msg:kit.put_text(host, msg, x, top, max(right - x - 16, 80), 14, 500, kit.mute(), wrap=False, height=42, middle=True)
            y = top + 46
        if chips:                                                                     # open-note links: present, but quieter than Resume
            cx = x + 16
            cw = min(200, (w - 16 - gap * (len(chips) - 1)) / len(chips))
            for t in chips:
                tap = self._tap(host, cx, y, cw, 24, (lambda i=t["id"]: self.press("thing", i)))
                tap.setToolTip_("\n".join("\u25cb  " + n for n in t.get("notes", [])[:3]) or t["title"])
                self._app_icon(tap, t["app"], 4, 4, 16, t.get("kind", ""), t.get("url"))
                kit.put_text(tap, t["title"] + (f"  {t['open']}" if t["open"] else ""), 26, 0, cw - 30, 11, 500, kit.mute(), wrap=False, height=24, middle=True)
                cx += cw + gap
            y += 30
        return y

    def _toolbar(self, host, page, x, y, w):
        """The Things heading on its own line (count and Select beside it), then the controls on a line of their
        own: where to look (a segmented Only here / With sub-projects) at the left, which source (one menu) at the right."""
        kit.put_text(host, "Things", x, y, w, 20, 700, wrap=False, height=30, middle=True, track=-0.018)
        cx = x + kit.text_width("Things", 20, 700) + 10
        kit.put_text(host, str(page["total"]), cx, y + 2, 60, 13, 400, kit.mute(), wrap=False, height=30, middle=True)
        if self.sel is None and page["total"]:                                        # a contextual action of browsing, at the heading's edge
            sw = int(kit.text_width("Select", 13, 600)) + 28
            kit.button(host, "Select", x + w - sw, y + 1, sw, 28, self.start_select, kind="quiet", size=13, weight=600)
        y += 42
        if page["deep"]["show"]:
            self._segmented(host, x, y, [("Only here", not page["deep"]["on"], lambda: self.press("deep", False)),
                                         ("With sub-projects", page["deep"]["on"], lambda: self.press("deep", True))])
        if len(page["filters"]) > 1 or self.nav["app"] is not None:
            self._source_menu(host, page, x + w, y)
        y += 28 + 16
        if self.sel is not None:
            y += self._select_bar(host, page, x, y, w) + 10
        return y

    def _segmented(self, parent, x, y, items, size=12, height=28, pad=24):
        """A compact two-or-more way toggle: a soft track with the chosen segment lifted. `size`, `height` and `pad`
        make the same control larger for primary navigation."""
        widths = [kit.text_width(label, size, 500) + 2 * pad for label, _on, _call in items]
        track = kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, y, sum(widths) + 4, height), kit.faint(0.07), None, 9 + (height - 28) / 4)
        parent.addSubview_(track)
        sx = x + 2
        for (label, on, call), width in zip(items, widths):
            seg = self._tap(parent, sx, y + 2, width, height - 4, call)
            seg.focusable, seg.ax_label = True, label
            if on:
                seg.tint = kit.faint(0.16)
            kit.put_text(seg, label, pad, 0, width - 2 * pad, size, 600 if on else 500, None if on else kit.mute(), wrap=False, height=height - 4, middle=True)
            sx += width
        return sx + 2 - x

    def _source_menu(self, parent, page, right, y):
        """One control for the source filter. Closed, it names what is chosen; open, it is a native menu."""
        chosen = self.nav["app"]
        label = chosen or "All sources"
        width = min(kit.text_width(label, 12, 600) + 44, 240)
        tap = self._tap(parent, right - width, y, width, 28, None, fill=True)
        kit.put_text(tap, label, 12, 0, width - 36, 12, 600 if chosen else 500, None if chosen else kit.mute(), wrap=False, height=28, middle=True)
        kit.glyph(tap, ("chevron.down",), width - 22, 8, 12, kit.mute())
        tap.callback = lambda: self._popup_sources(tap, page)

    def _popup_sources(self, anchor, page):
        menu = NSMenu.alloc().initWithTitle_("")
        helper = _SourcePick.alloc().init()
        helper.calls = {}
        entries = [("All sources", None, self.nav["app"] is None)]
        entries += [(f"{f['app']}  ·  {f['count']}", f["app"], self.nav["app"] == f["app"]) for f in page["filters"]]
        for n, (title, app, on) in enumerate(entries):
            if n == 1:
                menu.addItem_(NSMenuItem.separatorItem())
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "pick:", "")
            item.setTarget_(helper)
            item.setTag_(n)
            item.setState_(1 if on else 0)
            helper.calls[n] = (lambda a=app: self.press("app", a))
            menu.addItem_(item)
        self._source_helper = helper                                   # AppKit keeps only a weak reference to a menu's target
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, anchor.bounds().size.height + 4), anchor)

    def _select_bar(self, parent, page, x, y, w):
        """While selecting: how many, and what to do with them. Returns the height."""
        n = len(self.sel)
        tap = self._tap(parent, x, y, w, 44, None, fill=True)
        text = f"{n} selected" if n else "Tap things to select them"
        tw = int(kit.text_width(text, 13, 600))
        kit.put_text(tap, text, 14, 0, tw + 8, 13, 600, None if n else kit.mute(), wrap=False, height=44, middle=True)
        if n:                                                            # Clear deselects but stays here; Done, at the right, leaves
            kit.button(tap, "Clear", 14 + tw + 12, 6, 64, 32, self.clear_select, kind="quiet", size=13, weight=600)
        right = w - 8
        for label, kind, call, width in (
                ("Done", "primary", self.stop_select, 70),
                ("Forget…", "quiet", lambda: self.open_thing_forget(self.sel), 84),
                ("Not in here", "quiet", lambda: self.thing_action("not_this", self.sel, page["pid"]), 104),
                ("Also in…", "quiet", lambda: self.open_thing_pick("also", self.sel), 90),
                ("Move to…", "quiet", lambda: self.open_thing_pick("assign", self.sel), 96)):
            if label != "Done" and not n:
                continue
            right -= width
            kit.button(tap, label, right, 6, width, 32, call, kind=kind, size=13, weight=600)
            right -= 6
        return 44

    def _mini_card(self, parent, t, x, y, w, h):
        """A small Pick up card: the app, the thing, and its first open notes. Opens the thing."""
        tap = self._tap(parent, x, y, w, h, (lambda i=t["id"]: self.press("thing", i)), fill=True)
        self._app_icon(tap, t["app"], 12, 11, 24, t.get("kind", ""), t.get("url"))
        kit.put_text(tap, t["title"], 44, 11, w - 56, 13, 600, wrap=False, height=24, middle=True)
        shown = t.get("notes", [])[:2]
        more = t["open"] - len(shown)
        for i, text in enumerate(shown):
            tail = f"   +{more}" if more > 0 and i == len(shown) - 1 else ""
            kit.put_text(tap, "○  " + text + tail, 12, 44 + i * 18, w - 24, 12, 400, kit.mute(), wrap=False, height=18)
        return tap

    def _things(self, doc, page, x, y, w):
        """The scrolling list: things grouped by when, newest first."""
        if page.get("suggested"):
            y = self._suggested(doc, page["suggested"], x, y, w)
        first = y
        for index, group in enumerate(page["groups"]):
            recent = index == 0
            n = len(group["things"])
            tw = kit.text_width(group["title"], 17, 700)
            kit.put_text(doc, group["title"], x + 26, y, tw + 8, 17, 700, None if recent else kit.mute(), wrap=False, height=26, track=-0.014)
            label = f"{n} thing{'s' * (n != 1)}"
            kit.put_text(doc, label, x + 26 + tw + 10, y + 1, 100, 13, 400, kit.mute(), wrap=False, height=26, track=0.01)
            self._filmstrip(doc, group["things"], x + w - 8, y - 1, recent)
            if recent:                                                   # the newest chapter is a filled blue mark, the older ones hollow rings
                marker = kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x + 3, y + 8, 11, 11), kit.accent_text(), None, 5.5)
            else:
                marker = kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x + 3, y + 8, 11, 11), None, kit.faint(0.38), 5.5)
            doc.addSubview_(marker)
            top = y + 34
            y = top
            for t in group["things"]:
                y = self._thing(doc, t, x + 20, y, w - 20, select=self.sel is not None, compact=True)
            rail = _Rail.alloc().initWithFrame_(NSMakeRect(x + 8, top - 10, 1, y - top + 8))
            doc.addSubview_(rail)
            y += 26
        if page["groups"]:                                               # one quiet tonal ground under the whole history, so it reads against the canvas
            ground = kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(
                NSMakeRect(x - 12, first - 10, w + 24, y - first - 10 + 14), kit.faint(0.035), kit.faint(0.06), 16)
            doc.addSubview_positioned_relativeTo_(ground, -1, None)
        if not page["groups"]:
            self._label(doc, "Nothing matches this filter.", x, y + 8, w, size=13, weight=400)
            y += 34
        if page["things_more"]:
            kit.button(doc, f"Show {min(page['things_more'], page_model.THINGS_SHOWN)} more of {page['things_more']}",
                       x, y + 4, 240, 36, lambda: self.press("more"), kind="quiet", size=13, weight=500)
            y += 48
        return y

    def _filmstrip(self, doc, things, right, y, recent):
        """Up to four of the screenshots already kept for this session's things, small, at the right of its heading:
        a glance at what the session looked like. Older sessions are dimmer. Sessions with none show nothing."""
        shots = [t["shot"] for t in things if t.get("shot")][:4]
        cx = right
        for path in shots:
            image = self._thumb(path)
            if image is None:
                continue
            cx -= 50
            tile = _Flipped.alloc().initWithFrame_(NSMakeRect(cx, y, 44, 28))
            tile.setWantsLayer_(True)
            layer = tile.layer()
            try:
                cg = image.CGImageForProposedRect_context_hints_(None, None, None)
                cg = cg[0] if isinstance(cg, tuple) else cg                  # pyobjc hands back (image, rect) for the in/out rect
                layer.setContents_(cg)
                layer.setContentsGravity_("resizeAspectFill")
                layer.setBorderWidth_(1)
                layer.setBorderColor_(kit.faint(0.12).CGColor())
            except Exception:
                continue                                                     # no picture, no tile: never an empty box
            layer.setCornerRadius_(6)
            layer.setMasksToBounds_(True)
            tile.setAlphaValue_(1.0 if recent else 0.62)
            tile.setAccessibilityElement_(False)
            doc.addSubview_(tile)

    def _thumb(self, path):
        cache = self.__dict__.setdefault("_thumbs", {})
        if path not in cache:
            cache[path] = NSImage.alloc().initWithContentsOfFile_(path)
        return cache[path]

    def _card_box(self, parent, x, y, w, build, tap=None):
        """A soft box. `build(box, inner_width)` draws inside it from (14, 12) and returns the inner height."""
        box = self._tap(parent, x, y, w, 10, tap, fill=True)
        inner = build(box, w - 28)
        box.setFrame_(NSMakeRect(x, y, w, inner + 24))
        return y + inner + 24

    def _thing_page(self, doc, page, x, y, w):
        """The activity sits on the page itself: identity, headline, summary and Reopen beside the screenshot, then your
        notes in the one white surface that groups them, and the quiet facts beside it on the page."""
        self.shot_at = None
        pad = 24
        hero_card = kit.surface(doc, x, y, w, 10, "surface", radius=RADIUS_PANEL + 2, alpha=CARD_ALPHA)          # the one big box: what it was
        hero_bottom = self._hero(doc, page, x + pad + 4, y + pad, w - 2 * pad - 8) - 28
        hero_card.setFrame_(NSMakeRect(x, y, w, hero_bottom + pad - y))
        top = hero_bottom + pad + 16
        wide = w >= 640
        gap = 16
        nw = int(w * 0.57) if wide else w
        notes_card = kit.surface(doc, x, top, nw, 10, "surface", radius=RADIUS_PANEL, alpha=CARD_ALPHA)
        notes_bottom = self._notes(doc, page, x + pad, top + pad - 2, nw - 2 * pad) + pad - 2
        if wide:
            facts_card = kit.surface(doc, x + nw + gap, top, w - nw - gap, 10, "surface", radius=RADIUS_PANEL, alpha=CARD_ALPHA)
            facts_bottom = self._facts(doc, page, x + nw + gap + pad, top + pad - 2, w - nw - gap - 2 * pad) + pad - 2
            bottom = max(notes_bottom, facts_bottom)                                          # the two cards end together
            notes_card.setFrame_(NSMakeRect(x, top, nw, bottom - top))
            facts_card.setFrame_(NSMakeRect(x + nw + gap, top, w - nw - gap, bottom - top))
            return bottom + 16
        notes_card.setFrame_(NSMakeRect(x, top, nw, notes_bottom - top))
        facts_top = notes_bottom + gap
        facts_card = kit.surface(doc, x, facts_top, w, 10, "surface", radius=RADIUS_PANEL, alpha=CARD_ALPHA)
        facts_bottom = self._facts(doc, page, x + pad, facts_top + pad - 2, w - 2 * pad) + pad - 2
        facts_card.setFrame_(NSMakeRect(x, facts_top, w, facts_bottom - facts_top))
        return facts_bottom + 16

    def _hero(self, doc, page, x, y, w):
        """The top of the page. Left: where it was, a short headline, the summary, Reopen. Right: the screenshot
        and its caption (or the app's own icon when none was kept). Returns where the notes begin."""
        gap = 44
        shot = page["shot"] and NSImage.alloc().initWithContentsOfFile_(page["shot"])
        wide = w >= 640
        tw = int(w * 0.40) if (shot and wide) else w
        sw = min(w - tw - gap, 580) if (shot and wide) else min(w, 580)
        px, top = x, y
        self._app_icon(doc, page["app"], px, top - 2, 26, page.get("thing_kind", ""), page.get("url"))
        main = next((p["name"] for p in page["places"] if p["main"]), "")
        where = " · ".join(b for b in (page.get("meta"), "in " + main if main else "") if b)
        kit.meta(doc, where, px + 36, top + 3, tw - 36, kit.warm("mute"), wrap=False, height=18, tip=False)
        cy = top + 46
        cy += kit.display(doc, page["headline"], px, cy, tw, kit.warm("ink")) + 12
        if page["ai_summary"]:                                                # written by the model from the window and your notes: labelled
            cy += kit.body(doc, page["ai_summary"], px, cy, tw, kit.warm("ink")) + 8
            kit.put_text(doc, "Summary by Trace", px, cy, tw, 11, 500, kit.accent_text(), wrap=False, height=16, tip=False)
            cy += 26
        elif page["observed"]:                                                # no AI line yet: only stored facts
            cy += kit.body(doc, page["observed"], px, cy, tw, kit.warm("ink")) + 14
        if page["reopen"]:                                                    # the one dominant action, right under what you were doing
            kit.button(doc, "      Reopen", px, cy + 6, 128, BUTTON_H, lambda u=page["reopen"]: self.open_url(u), kind="primary", size=13, weight=600)
            kit.glyph(doc, ("play", "arrow.up.right.square"), px + 16, cy + 6 + (BUTTON_H - 14) / 2, 14, NSColor.whiteColor())
            kit.glyph(doc, ("link",), px + 148, cy + 6 + (BUTTON_H - 16) / 2, 16, kit.warm("mute"))
            self._link(doc, "Copy link", px + 168, cy + 6 + (BUTTON_H - 24) / 2, lambda u=page["reopen"]: self.copy_text(u, "Link copied."), size=13,
                       tip="Copy original link")
            cy += BUTTON_H + 12
        bottom = cy
        if shot is not None and wide:
            bottom = max(bottom, self._moment(doc, page, shot, px + tw + gap, top, sw))
        elif shot is not None:
            bottom = self._moment(doc, page, shot, px, cy + 12, sw)
        elif wide:                                                            # no screenshot kept: the app's own icon, large, as the anchor
            self._app_icon(doc, page["app"], x + w - 96, top, 96, page.get("thing_kind", ""), page.get("url"))
            bottom = max(bottom, top + 96)
        return bottom + 28

    def _moment(self, doc, page, image, x, y, w):
        """The screenshot LMemM kept: a thin edge, a caption, and Full size set right after the caption."""
        size = image.size()
        h = min(w * size.height / max(size.width, 1), 340)
        w = h * size.width / max(size.height, 1)
        self.shot_at = (x, y, w, h)
        lift = _Flipped.alloc().initWithFrame_(NSMakeRect(x, y, w, h))            # depth under the screenshot, nothing more
        lift.setWantsLayer_(True)
        lift.layer().setShadowOpacity_(0.5 if config.PAGE_THEME == "b" else 0.35)
        lift.layer().setShadowRadius_(22)
        lift.layer().setShadowOffset_((0, 0))
        doc.addSubview_(lift)
        holder = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        holder.setImage_(image)
        holder.setImageScaling_(3)
        holder.setWantsLayer_(True)
        holder.layer().setCornerRadius_(RADIUS_PANEL)
        holder.layer().setMasksToBounds_(True)
        lift.addSubview_(holder)
        doc.addSubview_(kit._KitSurface.alloc().initWithFrame_fill_edge_radius_(NSMakeRect(x, y, w, h), None, kit.warm("edge"), RADIUS_PANEL))
        tap = self._tap(doc, x, y, w, h, (lambda p=page["shot"]: self.open_file(p)))
        tap.ax_label = "Screenshot of " + page["title"] + ". Opens full size."
        caption = "As it looked, " + page["shot_when"].lower() + "."
        kit.meta(doc, caption, x, y + h + 12, max(w - 110, 40), kit.warm("mute"), wrap=False, height=16, tip=False)
        width = kit.text_width("Full size", 12, 500) + 12
        kit.glyph(doc, ("arrow.up.left.and.arrow.down.right", "arrow.up.left.and.arrow.down.right.circle"), x + w - width - 18, y + h + 12, 13, kit.accent_text())
        self._link(doc, "Full size", x + w - width, y + h + 8, (lambda p=page["shot"]: self.open_file(p)), size=12, color=kit.accent_text())
        return y + h + 32

    def _notes(self, doc, page, x, y, w):
        """Your notes, then the one place to write another: the composer sits at the end of the list, in this panel."""
        count = page["open_count"]
        kit.glyph(doc, ("waveform", "list.bullet"), x, y + 1, 18, kit.warm("ink"))
        kit.put_text(doc, "Your notes", x + 28, y, w - 98, 15, 600, kit.warm("ink"), wrap=False, height=20, tip=False)
        kit.put_text(doc, f"{count} open" if count else "all done", x + w - 70, y + 1, 70, 12, 400, kit.warm("mute"), align=RIGHT, wrap=False, height=18, tip=False)
        y += 36
        for n in page["open"] + page["done"]:
            y = self._note_row(doc, n, x, y, w)
        if not page["open"] and not page["done"]:
            kit.put_text(doc, "Nothing written yet.", x, y, w, 13, 400, kit.warm("mute"), wrap=False, height=20, tip=False)
            y += 28
        return self._composer(doc, x, y + 6, w)

    def _composer(self, doc, x, y, w):
        """The note field, drawn into the page: an inset that brightens its edge while you are writing. Return saves;
        Esc clears. It is the only way to add a note, so there is one interaction. Returns its bottom edge."""
        field = self.note_surface = kit.surface(doc, x, y, w, FIELD_H, "inset", radius=RADIUS_FIELD)
        self._note_edge()
        self.note_field.setFrame_(NSMakeRect(x + 14, y + (FIELD_H - 20) / 2, w - 28, 20))
        self.note_field.setTextColor_(kit.warm("ink"))
        self.note_field.setHidden_(False)
        doc.addSubview_(self.note_field)                              # moves it here from the last page it was on
        if self.composing or getattr(self, "keep_focus", False):
            self.window.makeFirstResponder_(self.note_field)          # a redraw must not take the cursor away mid-sentence
            self.keep_focus = False
        return y + FIELD_H

    def _note_row(self, doc, n, x, y, w):
        """One note: a tick (a tap flips it), the words, when. A finished one is grey and crossed out."""
        words = w - 34 - 74
        h = max(kit.text_height(n["text"], 14, 400, words), 20)
        row = self._tap(doc, x, y, w, h + 14, (lambda i=n["id"], d=n["done"]: self.tick_note(i, not d)))
        row.checked, row.ax_label = bool(n["done"]), n["text"]
        ring = _Ring.alloc().initWithChecked_(n["done"])
        ring.blue = True
        ring.setFrame_(NSMakeRect(4, 6, 17, 17))
        row.addSubview_(ring)
        if n["done"]:
            self._struck(row, n["text"], 34, 6, words, 14)
        else:
            kit.put_text(row, n["text"], 34, 6, words, 14, 400, kit.warm("ink"))
        kit.put_text(row, n["when"], w - 72, 8, 72, 12, 400, kit.warm("mute"), align=RIGHT, wrap=False, height=18, tip=False)
        return y + h + 14

    def _facts(self, doc, page, x, y, w):
        """The quiet part, always in view but small: when, how long, the address, the captured text and where it is kept."""
        kit.glyph(doc, ("info.circle",), x, y + 1, 18, kit.warm("ink"))
        kit.put_text(doc, "Details", x + 28, y, w - 28, 15, 600, kit.warm("ink"), wrap=False, height=20, tip=False)
        y += 36
        rows = list(page["details"])
        if page["address"]:
            rows.append(("Address", page["address"]))
        rows += list(page["latest"])
        for k, v in rows:
            kit.glyph(doc, DETAIL_ICONS.get(k, ("circle",)), x, y, 15, kit.warm("mute"))
            kit.meta(doc, k, x + 28, y, 84, kit.warm("mute"), height=18, tip=False)   # wraps like the value so both lines share a baseline
            h = max(kit.meta(doc, v, x + 118, y, w - 118, kit.warm("ink"), wrap=(k != "Address"), tip=False), 18)
            if k == "Address":                                        # the full address on hover of this row only, never over the buttons
                hold = _Flipped.alloc().initWithFrame_(NSMakeRect(x + 118, y, w - 118, h))
                hold.setToolTip_(v)
                doc.addSubview_(hold)
            y += h + 10
        screen = page["screen"]
        if screen:                                                # the raw captured text is one tap away, never on the page
            y += 8
            kit.glyph(doc, ("doc.text",), x, y + 5, 15, kit.accent_text())
            self._link(doc, "View captured text" + (f" · {screen['count']} versions" if screen["count"] > 1 else ""),
                       x + 22, y, (lambda e=screen: self.open_viewer(e)), size=12, color=kit.accent_text())
            y += 28
        reg, items = self.data()
        if page["item"] in items:                                 # where it is kept: only what is stored
            for text in thing_actions.why(reg, items[page["item"]]):
                y += kit.meta(doc, text, x, y, w, kit.warm("mute")) + 6
        px = x
        for p in page["places"]:
            label = p["name"] + (" · main" if p["main"] else "")
            width = kit.text_width(label, 13, 500) + 52
            if px + width > x + w:
                px, y = x, y + 40
            tap = self._tap(doc, px, y, width, 32, (lambda i=p["id"]: self.press("go", i)), fill=True)
            self._project_icon(tap, p["hue"], 6, 5, 22)
            kit.put_text(tap, label, 34, 0, width - 40, 13, 500, wrap=False, height=32, middle=True, tip=False)
            px += width + 8
        return y + (40 if page["places"] else 0)

    def open_file(self, path):
        """A kept screenshot in the default viewer (Preview)."""
        from Foundation import NSURL
        if not NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(path)):
            self.done = {"message": "Couldn't open that.", "undo": None, "until": time.time() + 4}
            self.render_now()

    def _struck(self, parent, text, x, y, w, size):
        """Muted text with a line through it: a note you just finished."""
        height = kit.text_height(text, size, 500, w)
        view = kit._KitText.alloc().initWithText_size_weight_color_align_wrap_middle_(text, size, 500, kit.mute(), LEFT, True, False)
        view.setFrame_(NSMakeRect(x, y, w, height))
        marked = kit._attributed(text, size, 500, kit.mute(), LEFT, True).mutableCopy()
        marked.addAttribute_value_range_(NSStrikethroughStyleAttributeName, 1, (0, len(text)))
        view.attributed = marked
        parent.addSubview_(view)
        return height

    def open_viewer(self, screen):
        """The raw screen text in a viewer of its own: a fixed header, one tab per dated version, and a
        scroll area of bounded height, so Close and Copy never scroll away."""
        self.dialog = {"kind": "viewer", "screen": screen, "v": 0}
        self._dialog()

    def _viewer(self, card, cw, ch, d):
        screen, i = d["screen"], d["v"]
        v = screen["versions"][i]
        kit.put_text(card, screen["place"], 24, 20, cw - 48 - 90, 15, 700, wrap=False, height=24)
        kit.button(card, "Close", cw - 24 - 76, 16, 76, 32, self.close_dialog, kind="quiet", size=13, weight=600)
        x = 24
        for n, ver in enumerate(screen["versions"]):                # one tab per dated version, newest first
            width = kit.text_width(ver["label"], 13, 600) + 28
            kit.button(card, ver["label"], x, 60, width, 30, (lambda k=n: self._viewer_to(k)),
                       kind="primary" if n == i else "quiet", size=13, weight=600)
            x += width + 8
        top = 104
        if v["place"]:
            kit.put_text(card, v["place"], 24, top, cw - 48, 12, 400, kit.mute(), wrap=False, height=16)
            top += 22
        box = self._tap(card, 24, top, cw - 48, ch - top - 76, None, fill=True)       # the text sits in a box of its own
        box.washes = False                                          # a text box does not light up under the pointer
        scroll = _scroll(box)
        scroll.setFrame_(NSMakeRect(12, 10, cw - 48 - 24, ch - top - 76 - 20))
        view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, cw - 48 - 24, 10))
        view.setEditable_(False)
        view.setSelectable_(True)                                   # drag to select, Cmd-C copies, Cmd-A selects all
        view.setDrawsBackground_(False)
        view.setRichText_(False)
        view.setFont_(kit.font(13))
        view.setTextColor_(kit.ink())
        view.setTextContainerInset_((0, 4))
        view.textContainer().setLineFragmentPadding_(0)
        view.setVerticallyResizable_(True)
        view.setHorizontallyResizable_(False)
        view.setAutoresizingMask_(2)
        view.textContainer().setWidthTracksTextView_(True)
        view.setString_("\n\n".join(v["lines"]))
        scroll.setDocumentView_(view)
        kit.button(card, "Copy text", 24, ch - 52, 110, 36, (lambda t="\n".join(v["lines"]): self.copy_text(t, "Copied the text.")),
                   kind="primary", size=13, weight=600)
        link = v["url"] or screen["url"]
        if link:
            kit.button(card, "Copy link", 24 + 118, ch - 52, 110, 36, (lambda u=link: self.copy_text(u, "Copied the link.")),
                       kind="quiet", size=13, weight=600)

    def _viewer_to(self, k):
        self.dialog["v"] = k
        self._dialog()

    def _search(self, doc, page, x, y, w):
        if page["none"]:
            return self._empty(doc, {"title": "Nothing matches", "line": "Try part of a name, a note or a project."}, x, y, w,
                                symbol=("magnifyingglass",))
        if page["projects"]:
            y = self._section(doc, "Projects", x, y, w)
            y = self._rows_of_projects(doc, [{"id": p["id"], "name": p["name"], "line": p["path"]} for p in page["projects"]], x, y, w)
        if page["notes"]:
            y = self._section(doc, "Notes", x, y + 8, w)
            for n in page["notes"]:
                row = self._tap(doc, x, y, w, 42, (lambda i=n["id"]: self.press("thing", i)))
                self._app_icon(row, n.get("app", ""), 0, 6, 30, n.get("kind", ""), n.get("url"))
                kit.put_text(row, n["text"], 42, 4, w - 42, 14, 500, wrap=False, height=20)
                kit.put_text(row, n["thing"], 42, 22, w - 42, 12, 400, kit.mute(), wrap=False, height=16)
                y += 46
        if page["things"]:
            y = self._section(doc, "Things", x, y + 8, w)
            for t in page["things"]:
                y = self._thing(doc, t, x, y, w)
            if page["things_more"]:
                self._label(doc, f"{page['things_more']} more. Keep typing to narrow it.", x, y + 6, w, size=13, weight=400)
                y += 30
        return y

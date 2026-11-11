"""The handoff panel (AppKit) and a small runner for it.  UNVERIFIED ON A MAC.

All behaviour lives in `panel_model.PanelModel` (tested). This file only draws its `view()` and
forwards keys, so a drawing bug cannot change what is transferred. Nothing here sends a prompt.

Run it:  python3 lmemm.py handoff panel     (then press the hotkey from Claude, ChatGPT...)
"""

import threading

import objc
from AppKit import (NSBackingStoreBuffered, NSButton, NSColor, NSFont, NSMakeRect, NSPanel, NSScreen,
                    NSScrollView, NSTextField, NSTextView, NSView)

import palette
from . import coordinator as co
from . import hotkey, mac
from .adapters import Clipboard
from .panel_model import PanelModel

W, PAD = 380, 16
NONACTIVATING, BORDERLESS_TITLED = 1 << 7, 1 << 0 | 1 << 3 | 1 << 15      # titled, closable, full-size content
FLOATING = 3


class _Panel(NSPanel):
    """Takes the keyboard without activating LMemM, so the source app stays in front."""
    def initWithModel_(self, model):
        self = objc.super(_Panel, self).initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, 220), NONACTIVATING | 1 | 8, NSBackingStoreBuffered, False)
        self.model, self.on_change, self.sync = model, None, None
        self.setLevel_(FLOATING)
        self.setTitle_("LMemM")
        self.setHidesOnDeactivate_(False)
        self.setReleasedWhenClosed_(False)
        return self

    def canBecomeKeyWindow(self):
        return True

    def keyDown_(self, event):
        if self.sync:
            self.sync()
        if not self.model.press(event.keyCode()) and self.model.view()["kind"] != "review":
            return
        if self.on_change:
            self.on_change()

    def cancelOperation_(self, _sender):
        self.model.press(53)
        if self.on_change:
            self.on_change()


class _Actions(objc.lookUpClass("NSObject")):
    """One target for every button. Buttons carry a tag; the tag picks the action. The target is
    held by the HandoffPanel for its whole life (a button's target is a weak reference)."""

    def init(self):
        self = objc.super(_Actions, self).init()
        self.table = {}
        self.after = None
        return self

    @objc.python_method
    def reset(self):
        self.table = {}

    @objc.python_method
    def add(self, fn):
        tag = len(self.table) + 1
        self.table[tag] = fn
        return tag

    @objc.IBAction
    def pressed_(self, sender):
        fn = self.table.get(int(sender.tag()))
        try:
            if fn:
                fn()
        except Exception as e:                    # a bad action must be visible, not silent
            print(f"  ! handoff button: {type(e).__name__}: {e}", flush=True)
        if self.after:
            self.after()


class HandoffPanel:
    def __init__(self, coordinator):
        self.co, self.model = coordinator, PanelModel(coordinator)
        self.panel = _Panel.alloc().initWithModel_(self.model)
        self.panel.on_change = self.draw
        self.panel.sync = self._sync
        self._dirty = threading.Event()
        self._actions = _Actions.alloc().init()
        self._actions.after = self._dirty.set          # redraw on the next tick, never inside the click

    # ---------------------------------------------------------------- lifecycle

    def show(self):
        """Hotkey pressed. The front app is read first (synchronously), then the panel opens and
        the slow read happens off the main thread."""
        s = self.co.invoke()
        self.model.selected, self.model.reviewing = 0, False
        self.draw()
        self.panel.makeKeyAndOrderFront_(None)
        if s.state == co.CAPTURING:
            def work():
                self.co.capture(s)
                self._dirty.set()
            threading.Thread(target=work, daemon=True).start()

    def tick(self):
        """Call from the main loop: redraw after the worker finished, close after cancel."""
        if self._dirty.is_set():
            self._dirty.clear()
            self.draw()

    # ---------------------------------------------------------------- drawing

    def _label(self, parent, text, y, size=13, bold=False, secondary=False, h=18, w=W - 2 * PAD):
        f = NSTextField.labelWithString_(text)
        f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        if secondary:
            f.setTextColor_(NSColor.secondaryLabelColor())
        f.setLineBreakMode_(4)                                          # truncate tail
        f.setFrame_(NSMakeRect(PAD, y, w, h))
        parent.addSubview_(f)
        return f

    def _button(self, parent, title, x, y, w, fn, primary=False):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 28))
        b.setTitle_(title)
        b.setBezelStyle_(1)
        if primary:
            b.setBezelColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*palette.ACCENT, 1.0))
            b.setKeyEquivalent_("\r")
        b.setTag_(self._actions.add(lambda: (self._sync(), fn())))
        b.setTarget_(self._actions)
        b.setAction_("pressed:")
        parent.addSubview_(b)

    def _sync(self):
        """Edits in the review box change the outgoing text only."""
        if self.model.reviewing and getattr(self, "_edit", None) is not None:
            self.co.edit(str(self._edit.string()))

    def draw(self):
        v = self.model.view()
        if v["kind"] == "closed":
            self.panel.orderOut_(None)
            return
        self._actions.reset()
        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 10))
        rows = []          # (kind, payload) laid out top to bottom, then flipped to AppKit coordinates
        k = v["kind"]
        if k == "loading":
            rows += [("title", "LMemM"), ("text", v["title"] + "…"), ("sub", v.get("source", ""))]
            rows += [("button", "Cancel", False, self.co.cancel)]
        elif k == "source":
            rows += [("title", "LMemM · " + (v["app"] or "Unknown app"))]
            if v.get("readable"):
                rows += [("text", "Conversation: " + (v["conversation"] or "untitled")),
                         ("sub", "Context: " + v["context_status"])]
            else:
                rows += [("sub", v["fallback"] or "Nothing to hand off from here."),
                         ("button", v["manual"], False, self.model.use_clipboard)]
            for i, d in enumerate(v["destinations"]):
                tag = "▸ " if i == v["selected"] else "   "
                label = tag + d["name"] + ("" if d["available"] else "  (unavailable)")
                rows += [("button", label, False, (lambda i=i: self.model.choose_row(i)))]
        elif k == "confirm":
            rows += [("title", "Hand off to " + v["destination"]), ("text", v["task"] or "Continue this work"),
                     ("sub", f'{v["messages"]} message(s) used' + ("  ·  " + "  ·  ".join(v["notes"]) if v["notes"] else "")),
                     ("sub", "Inserts into the composer" if v["inserts"] else "Copies to the clipboard; you paste and send"),
                     ("button", v["primary"], True, self.co.transfer),
                     ("button", v["secondary"], False, self.model.review),
                     ("button", "Cancel", False, self.co.cancel)]
        elif k == "review":
            rows += [("title", "Review context"), ("editor", v["text"]),
                     ("button", "Copy", False, self.co.copy), ("button", "Back", False, self.model.back)]
        elif k == "result":
            rows += [("title", "Done" if v["ok"] else "Not transferred"), ("text", v["message"])]
            if v["can_copy"] and not v["ok"]:
                rows += [("button", "Copy handoff", False, self.co.copy)]
            rows += [("button", "Close", False, self.co.clear)]
        y = PAD
        flipped = []
        for r in rows:
            kind = r[0]
            h = {"title": 22, "text": 20, "sub": 18, "button": 32, "editor": 240}[kind]
            flipped.append((r, y, h))
            y += h + 4
        total = y + PAD
        for r, ty, h in flipped:
            fy = total - ty - h
            kind = r[0]
            if kind == "title":
                self._label(root, r[1], fy, 15, True)
            elif kind == "text":
                self._label(root, r[1], fy)
            elif kind == "sub":
                self._label(root, r[1], fy, 11, secondary=True)
            elif kind == "button":
                self._button(root, r[1], PAD, fy, W - 2 * PAD, r[3], r[2])
            elif kind == "editor":
                sv = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, fy, W - 2 * PAD, h))
                tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 2 * PAD, h))
                tv.setString_(r[1])
                tv.setFont_(NSFont.systemFontOfSize_(12))
                tv.setEditable_(True)
                self._edit = tv
                sv.setDocumentView_(tv)
                sv.setHasVerticalScroller_(True)
                root.addSubview_(sv)
        root.setFrame_(NSMakeRect(0, 0, W, total))
        self.panel.setContentView_(root)
        self.panel.setContentSize_((W, total))
        if not self.panel.isVisible():
            scr = NSScreen.mainScreen().frame()
            self.panel.setFrameTopLeftPoint_((scr.origin.x + scr.size.width / 2 - W / 2,
                                              scr.origin.y + scr.size.height * 0.72))


def run(spec=hotkey.DEFAULT):
    """Developer runner: register the hotkey and serve the panel until Ctrl-C."""
    import dictation
    import macos
    app = dictation.start_app()
    coordinator = co.Coordinator(mac.default_sources(), mac.default_destinations(), mac.PasteboardClipboard(),
                                 mac.front_info, is_private=mac.is_private)
    panel = HandoffPanel(coordinator)
    hk = hotkey.Hotkey(lambda: panel.show(), spec)
    status = hk.register()
    print(f"Handoff hotkey {hotkey.describe(spec)}: {status}" + (f" ({hk.error})" if hk.error else ""), flush=True)
    if status != "ok":
        return 1
    try:
        while True:
            macos.next_event(app, 0.1)
            panel.tick()
    except KeyboardInterrupt:
        return 0

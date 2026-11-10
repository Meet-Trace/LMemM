"""LMemM - the first-run window. It draws onboarding.Setup.view() and forwards presses; it decides
nothing (rule S8). Every word, every enabled button and every permission state comes from the
model, which is tested without a Mac. This file is only layout; the paint is in setup_kit.py.

    welcome → you → access → try → done          (docs/specs/2026-10-09-first-run-onboarding.md)

The type is Figtree (assets/fonts), the highlight is blue, and the card matches the mock-up. The
"try" screen uses the real ⌃⌥N hotkey and the real note card, as a rehearsal: what you say is not
stored. Closing the window leaves setup where it was; the next start resumes there.
"""

import objc
from Foundation import NSObject
from AppKit import NSApplication, NSColor, NSImageView, NSMakeRect, NSScrollView, NSWindow, NSWorkspace

import apps
import config
import dictation
import macos
import motion
import onboarding
import permissions
import setup_kit as kit
from setup_kit import CENTER, LEFT
from widget import _Fields, _field, _Flipped, _Ring, _Tap

W, H, X = 480, 600, 36                                         # H is the smallest height; the window can grow
CW = W - 2 * X
TOP = 60                                                        # below the dots
ROLE_SYMBOLS = {"Code": "chevron.left.forwardslash.chevron.right", "Writing": "pencil", "Research": "magnifyingglass",
                "Design": "paintpalette", "Studying": "book", "Business": "briefcase"}
PERM_SYMBOLS = {"voice": ("mic",), "ax": ("figure.stand", "accessibility", "person.crop.circle"),
                "screen": ("display", "desktopcomputer")}


class _SetupWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True


class _SetupClosing(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_SetupClosing, self).init()
        self.owner = owner
        return self

    def windowShouldClose_(self, window):
        self.owner.closed = True
        return True

    def windowDidResize_(self, note):
        self.owner.resized()


class SetupWindow:
    def __init__(self, setup):
        self.setup = setup
        self.closed = False
        self.show_saved = False
        self.picker = None
        self.hotkey_flag = False
        self.signature = None
        self.panel = dictation.NotePanel()
        self.icons = {}
        self.name_field = self.primary_tap = self.hint = None
        self.last_index = None                                     # the screen last drawn (for the push between screens)

        self.h = H
        style = 1 | 2 | 8 | (1 << 15)                              # titled, closable, resizable, content under the title bar
        self.window = _SetupWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, 2, False)
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(1)
        self.window.standardWindowButton_(1).setHidden_(True)      # no minimise; zoom stays so it can grow
        self.window.setContentMinSize_((W, H))
        self.window.setReleasedWhenClosed_(False)
        self.window.setBackgroundColor_(kit.card_background())
        self.closing = _SetupClosing.alloc().initWithOwner_(self)
        self.window.setDelegate_(self.closing)
        self.root = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        self.root.setAutoresizingMask_(18)                           # follows the window
        self.window.setContentView_(self.root)
        self.stage = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))   # the 480-wide column, centred when the window is wider
        self.stage.setAutoresizingMask_(1 | 4)                       # stays centred horizontally
        self.root.addSubview_(self.stage)
        self.window.center()
        self.fields = _Fields.alloc().initWithChange_submit_cancel_(self._typed, lambda _t: self._primary(), lambda: None)

    # ------------------------------------------------------------ the loop calls these

    def show(self):
        self.render()
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def close(self):
        if self.panel.open:
            self.panel.close(save=False)
        self.window.orderOut_(None)

    def hotkey(self):
        self.hotkey_flag = True

    def tick(self):
        """Called about ten times a second."""
        if self.panel.open:
            self.panel.poll()
        if self.hotkey_flag:
            self.hotkey_flag = False
            if self.setup.step == "try":
                self.open_rehearsal()
        changed = self.setup.refresh()
        if changed or self._signature() != self.signature:
            self.render()

    def _signature(self):
        v = self.setup.view()
        if v["step"] == "you":                                     # typing must not rebuild the field under your fingers
            v = {**v, "name": None, "primary": None}
        return repr(v) + repr(self.picker is not None)

    # ------------------------------------------------------------ presses

    def _primary(self):
        button = self.setup.view()["primary"]
        if not button or not button["enabled"]:
            return
        if button["action"] == "show_note":
            self.open_rehearsal()
            return
        self.setup.press_primary()
        self.render()

    def _secondary(self):
        secondary = self.setup.view()["secondary"]
        if not secondary:
            return
        {"next": self.setup.next, "finish": self.setup.finish}.get(secondary["action"], lambda: None)()
        self.render()

    def _do(self, fn, *args):
        fn(*args)
        self.render()

    def _typed(self, text):
        self.setup.set_name(text)
        view = self.setup.view()
        if self.primary_tap is not None:
            self.primary_tap.setAlphaValue_(1.0 if view["primary"]["enabled"] else 0.35)
            self.primary_tap.callback = self._primary if view["primary"]["enabled"] else None

    def open_rehearsal(self):
        if self.panel.open or self.setup.step != "try":
            return
        self.panel.show("LMemM setup", self._rehearsed)

    def _rehearsed(self, text):
        """The rehearsal card closed. A note that was actually written counts; nothing is stored."""
        if text:
            self.setup.rehearsed()
        self.render()

    # ------------------------------------------------------------ drawing

    def resized(self):
        """The window was dragged to a new size: re-lay the column out for it (the same screen, no push)."""
        if getattr(self, "stage", None) is None:
            return
        size = self.window.contentView().frame().size
        self.h = max(H, size.height)
        self.stage.setFrame_(NSMakeRect((size.width - W) / 2, 0, W, self.h))
        if self.setup.view():
            self.render()

    def render(self):
        view = self.setup.view()
        if self.last_index is not None and view["index"] != self.last_index:
            motion.step(self.stage, forward=view["index"] > self.last_index)     # a new screen arrives from the side it belongs to
        self.last_index = view["index"]
        for sub in list(self.stage.subviews()):
            sub.removeFromSuperview()
        self.name_field = self.primary_tap = self.hint = None
        self.signature = self._signature()
        root = self.stage
        dots = kit._KitDots.alloc().initWithIndex_(view["index"])
        dots.setFrame_(NSMakeRect((W - 65) / 2, 28, 65, 8))
        root.addSubview_(dots)
        if view["back"]:
            self._link(root, "‹ Back", 20, 20, lambda: self._do(self.setup.back), size=14)
        step = view["step"]
        foot_top = self._foot(root, view)
        body = _Flipped.alloc().initWithFrame_(NSMakeRect(X, TOP, CW, 10))
        height = {"welcome": self._intro, "done": self._intro, "you": self._you, "access": self._access,
                  "try": self._try}[step](body, view)
        room = foot_top - 14 - TOP
        top = TOP if step == "access" or height >= room else TOP + (room - height) / 2      # centred, as in the mock-up
        if height > room and room > 0:                              # taller than the space above the button: scroll, never overlap it
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(X, TOP, CW, room))
            scroll.setDrawsBackground_(False)
            scroll.setHasVerticalScroller_(True)
            scroll.setAutohidesScrollers_(True)
            body.setFrame_(NSMakeRect(0, 0, CW, height))
            scroll.setDocumentView_(body)
            root.addSubview_(scroll)
            if self.show_saved and step == "you":                   # the list just opened: bring it into view
                scroll.contentView().scrollToPoint_((0, max(0, height - room)))
                scroll.reflectScrolledClipView_(scroll.contentView())
        else:
            body.setFrame_(NSMakeRect(X, top, CW, height))
            root.addSubview_(body)
        if self.picker is not None:
            self._picker_view()
        elif step == "you":
            self.window.makeFirstResponder_(self.name_field)

    def _link(self, parent, text, x, y, callback, size=14, color=None, w=None, align=LEFT):
        width = w or kit.text_width(text, size, 500) + 16
        tap = kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(x, y, width, size + 12), callback)
        kit.put_text(tap, text, 8 if align == LEFT else 0, 0, width - (8 if align == LEFT else 0), size, 500,
                     color or kit.mute(), align, wrap=False, height=size + 12, middle=True)
        parent.addSubview_(tap)
        return tap

    # -- the foot: the main button, the way out, and the trust line, pinned to the bottom

    def _foot(self, root, view):
        """Lays the foot out from the bottom edge up and returns the y where it starts."""
        items = []                                                  # (kind, height) from top to bottom
        trust = view["trust"]
        primary, secondary = view["primary"], view["secondary"]
        if view["step"] == "access":
            order = ["trust", "primary"]
        else:
            order = ["primary", "secondary", "trust"]
        for kind in order:
            if kind == "trust" and trust:
                items.append((kind, 22))
            elif kind == "primary" and primary:
                items.append((kind, 48))
            elif kind == "secondary" and secondary:
                items.append((kind, 28))
        if view["error"]:
            items.insert(0, ("error", kit.text_height(view["error"], 13, 500, CW) + 2))
        gap = 12
        total = sum(h for _k, h in items) + gap * max(0, len(items) - 1)
        y = self.h - 30 - total
        start = y
        for kind, h in items:
            if kind == "error":
                kit.put_text(root, view["error"], X, y, CW, 13, 500, kit.status_text("red"), CENTER)
            elif kind == "trust":
                self._trust(root, trust, y)
            elif kind == "primary":
                label = primary["label"]
                width = min(CW, max(220, kit.text_width(label, 16, 600) + 56))
                self.primary_tap = kit.button(root, label, (W - width) / 2, y, width, 48, self._primary,
                                              kind="quiet" if primary["quiet"] else "primary",
                                              enabled=primary["enabled"])
            else:
                self._link(root, secondary["label"], (W - 160) / 2, y, self._secondary, w=160, align=CENTER)
            y += h + gap
        return start

    def _trust(self, root, text, y):
        width = kit.text_width(text, 13, 400)
        if width > CW - 24:
            kit.put_text(root, text, X, y, CW, 13, 400, kit.mute(), CENTER)
            return
        left = (W - (width + 20)) / 2
        lock = kit.icon("lock.fill", 12, kit.status_text("green"), (left, y + 4, 12, 14))
        if lock is not None:
            root.addSubview_(lock)
        kit.put_text(root, text, left + 20, y, width + 4, 13, 400, kit.mute(), LEFT, wrap=False, height=22, middle=True)

    # -- the five screens. Each fills `body` (CW wide) from y=0 and returns its height.

    def _notes(self, body, notes, y):
        for item in notes:
            y += self._note(body, item, y) + 10
        return y

    def _note(self, body, item, y):
        tone = {"good": "good", "warn": "warn"}.get(item["tone"], "info")
        return kit.note(body, tone, item["text"], 0, y, CW, lead=item.get("lead"))

    def _intro(self, body, view):
        mark = kit._KitMark.alloc().initWithFrame_(NSMakeRect((CW - 60) / 2, 0, 60, 60))
        body.addSubview_(mark)
        y = 60 + 24
        y += kit.put_text(body, view["title"], 0, y, CW, 29, 700, align=CENTER) + 12
        y += kit.put_text(body, view["sub"], 20, y, CW - 40, 16, 400, kit.mute(), CENTER) + 18
        return self._notes(body, view["notes"], y) - (10 if view["notes"] else 0)

    def _you(self, body, view):
        y = kit.put_text(body, view["title"], 0, 0, CW, 29, 700, align=CENTER) + 22
        y += kit.put_text(body, "First name", 0, y, CW, 13, 600, align=CENTER) + 8
        box = kit._KitBox.alloc().initWithFocus_(True)
        box.setFrame_(NSMakeRect(0, y, CW, 52))
        body.addSubview_(box)
        self.name_field = _field(view["name"]["value"], view["name"]["placeholder"], 20, False,
                                 (16, y + 12, CW - 32, 28), self.fields)
        self.name_field.setFont_(kit.font(20, 500))
        self.name_field.setAlignment_(CENTER)
        body.addSubview_(self.name_field)
        y += 52 + 8
        y += kit.put_text(body, view["name"]["hint"], 0, y, CW, 13, 400, kit.mute(), CENTER) + 22
        optional = "Optional"
        label = "What do you mostly work on?"
        lw, ow = kit.text_width(label, 13, 600), kit.text_width(optional, 13, 500)
        left = (CW - (lw + 8 + ow)) / 2
        kit.put_text(body, label, left, y, lw + 4, 13, 600, wrap=False, height=18)
        kit.put_text(body, optional, left + lw + 8, y, ow + 4, 13, 500, kit.mute(), wrap=False, height=18)
        y += 18 + 10
        rows, row, row_w = [], [], 0
        for role in view["roles"]:
            w = kit.chip_width(role["name"], ROLE_SYMBOLS.get(role["name"]))
            if row and row_w + 8 + w > CW:
                rows.append((row, row_w))
                row, row_w = [], 0
            row_w += (8 if row else 0) + w
            row.append(role)
        rows.append((row, row_w))
        for chips, total in rows:                                    # each row is centred
            x = (CW - total) / 2
            for role in chips:
                name = role["name"]
                x += kit.chip(body, name, ROLE_SYMBOLS.get(name), x, y, role["on"],
                              (lambda r=name: self._do(self.setup.toggle_role, r))) + 8
            y += 32 + 8
        y += 6
        opened = self.show_saved
        link_text = "What gets saved ▴" if opened else "What gets saved ▾"
        self._link(body, link_text, (CW - 150) / 2, y, lambda: (setattr(self, "show_saved", not self.show_saved), self.render()),
                   size=13, w=150, align=CENTER)
        y += 30
        if opened:
            y += kit.note(body, "info", "\n".join("•  " + line for line in view["saved"]), 0, y, CW) + 4
        return y

    def _access(self, body, view):
        y = kit.put_text(body, view["title"], 0, 0, CW, 29, 700, align=CENTER) + 16
        for i, row in enumerate(view["rows"]):
            if i:
                rule = kit._KitRule.alloc().initWithFrame_(NSMakeRect(0, y, CW, 1))
                body.addSubview_(rule)
            y = self._permission(body, row, view, y + (0 if i == 0 else 1))
        y += 8
        kit.button(body, "Checking…" if view["checking"] else "Refresh status", 0, y, 142, 30,
                   lambda: self._do(self.setup.press, "refresh"), kind="ghost", size=13, weight=600)
        if view["can_retry_restart"]:
            kit.button(body, "Restart LMemM", CW - 142, y, 142, 30,
                       lambda: self._do(self.setup.press, "retry_restart"), kind="ghost", size=13, weight=600)
        y += 38
        y += kit.put_text(body, view["permission_help"], 0, y, CW, 12, 400, kit.mute(), CENTER) + 8
        return y

    def _permission(self, body, row, view, y):
        state = row["state"]
        tone = "good" if state == onboarding.GRANTED else "bad" if state == onboarding.DENIED else "soft"
        pad = 14
        y += pad
        tile = kit._KitTile.alloc().initWithSymbol_tone_(kit.first_symbol(PERM_SYMBOLS[row["key"]]), tone)
        tile.setFrame_(NSMakeRect(0, y, 36, 36))
        body.addSubview_(tile)
        text_x = 48
        action = row["action"]
        button_w = max(76, kit.text_width(row["button"], 13, 600) + 28) if action else 66
        text_w = CW - text_x - button_w - 12
        ty = y
        head = row["title"]
        hw = kit.text_width(head, 15, 600)
        kit.put_text(body, head, text_x, ty, hw + 4, 15, 600, wrap=False, height=20)
        if row["tag"]:
            kit.put_text(body, row["tag"].upper(), text_x + hw + 8, ty + 3, 80, 11, 600, kit.mute(), wrap=False, height=16)
        ty += 21
        color = (kit.status_text("red") if state == onboarding.DENIED else
                 kit.status_text("orange") if state == onboarding.RESTART else kit.mute())
        ty += kit.put_text(body, row["line"], text_x, ty, text_w, 13, 400, color) + 2
        if row["key"] == "screen":
            some = bool(view["watch_apps"])
            ty += 8
            holder = kit._KitButton.alloc().initWithFrame_callback_(NSMakeRect(text_x, ty, 188, 28), None)
            holder.fill, holder.radius = kit.faint(0.07), 9
            body.addSubview_(holder)
            for label, active, cb, x, w in (("All apps", not some, lambda: self._do(self.setup.set_apps, []), 2, 78),
                                            ("Only some apps", some, self._open_picker, 82, 104)):
                kit.button(holder, label, x, 2, w, 24, cb, kind="primary" if active else "ghost", size=12, weight=600)
            ty += 28
        if action:
            kit.button(body, row["button"], CW - button_w, y + 3, button_w, 30,
                       (lambda a=action, k=row["key"]: self._do(self.setup.press, a, k)), kind="primary", size=13, weight=600)
        else:
            check = kit.icon("checkmark.circle.fill", 18, kit.status_text("green"), (CW - 62, y + 8, 18, 18))
            if check is not None:
                body.addSubview_(check)
            kit.put_text(body, "On", CW - 40, y + 5, 40, 13, 600, kit.status_text("green"), LEFT, wrap=False, height=24, middle=True)
        return max(ty, y + 38) + pad

    def _try(self, body, view):
        y = kit.put_text(body, view["title"], 20, 0, CW - 40, 29, 700, align=CENTER) + 24
        keys = view["keys"]
        total = len(keys) * 56 + (len(keys) - 1) * 10
        for i, glyph in enumerate(keys):
            key = kit._KitKey.alloc().initWithGlyph_(glyph)
            key.setFrame_(NSMakeRect((CW - total) / 2 + i * 66, y, 56, 58))
            body.addSubview_(key)
        y += 58 + 22
        y += kit.put_text(body, view["sub"], 20, y, CW - 40, 16, 400, kit.mute(), CENTER) + 16
        return self._notes(body, view["notes"], y) - (10 if view["notes"] else 0)

    # -- the app picker (Screen Recording, only some apps)

    def _open_picker(self):
        listing = apps.installed()
        chosen = [a["id"] for a in self.setup.state["watch_apps"]]
        self.picker = {"all": listing, "chosen": chosen, "query": "", "scroll": None, "count": None, "done": None}
        self.render()

    def _picker_view(self):
        state = self.picker
        sheet = kit._KitCanvas.alloc().initWithFrame_(NSMakeRect(0, 0, W, self.h))
        y = 52
        y += kit.put_text(sheet, "Where should LMemM look?", X, y, CW, 25, 700, align=CENTER) + 8
        y += kit.put_text(sheet, "Apps installed on this Mac. LMemM looks only at the ones you pick and stays off in every other app.",
                          X + 10, y, CW - 20, 14, 400, kit.mute(), CENTER) + 16
        box = kit._KitBox.alloc().initWithFocus_(True)               # the search field has the cursor from the start
        box.setFrame_(NSMakeRect(X, y, CW, 38))
        sheet.addSubview_(box)
        search = _field(state["query"], "Search apps", 14, False, (X + 14, y + 9, CW - 28, 20),
                        _Fields.alloc().initWithChange_submit_cancel_(self._query, lambda _t: None, lambda: None))
        search.setFont_(kit.font(14, 400))
        sheet.addSubview_(search)
        y += 38 + 10
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(X, y, CW, self.h - y - 132))
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        state["scroll"] = scroll
        sheet.addSubview_(scroll)
        self._fill_picker()
        state["done"] = kit.button(sheet, "", (W - 220) / 2, self.h - 108, 220, 48, self._picker_done)
        label = kit._KitText.alloc().initWithText_size_weight_color_align_wrap_middle_("", 16, 600, NSColor.whiteColor(), CENTER, False, True)
        label.setFrame_(NSMakeRect(0, 0, 220, 48))
        state["done"].addSubview_(label)
        state["done"].label = label
        self._link(sheet, "Allow all apps instead", (W - 200) / 2, self.h - 52, self._picker_all, w=200, align=CENTER)
        self._update_picker_button()
        self.stage.addSubview_(sheet)
        self.window.makeFirstResponder_(search)                      # the ring is drawn around it, so the cursor really is there

    def _query(self, text):
        self.picker["query"] = text
        self._fill_picker()

    def _fill_picker(self):
        state = self.picker
        rows = apps.search(state["all"], state["query"])
        doc = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, CW, max(1, len(rows)) * 44))
        if not rows:
            kit.put_text(doc, f"No app named “{state['query']}”.", 8, 12, CW - 16, 14, 400, kit.mute())
        for i, app in enumerate(rows):
            tap = _Tap.alloc().initWithFrame_callback_(NSMakeRect(0, i * 44, CW, 44), (lambda a=app: self._toggle_app(a)))
            image = self._icon(app)
            if image is not None:
                holder = NSImageView.alloc().initWithFrame_(NSMakeRect(6, 7, 30, 30))
                holder.setImage_(image)
                tap.addSubview_(holder)
            kit.put_text(tap, app["name"], 48, 0, CW - 48 - 44, 14, 500, wrap=False, height=44, middle=True)
            ring = _Ring.alloc().initWithChecked_(app["id"] in state["chosen"])
            ring.blue = True
            ring.setFrame_(NSMakeRect(CW - 32, 13, 18, 18))
            tap.addSubview_(ring)
            doc.addSubview_(tap)
        state["scroll"].setDocumentView_(doc)

    def _icon(self, app):
        if app["id"] not in self.icons:
            try:
                image = NSWorkspace.sharedWorkspace().iconForFile_(app["path"])
                image.setSize_((30, 30))
            except Exception:
                image = None
            self.icons[app["id"]] = image
        return self.icons[app["id"]]

    def _toggle_app(self, app):
        chosen = self.picker["chosen"]
        if app["id"] in chosen:
            chosen.remove(app["id"])
        else:
            chosen.append(app["id"])
        self._fill_picker()
        self._update_picker_button()

    def _update_picker_button(self):
        tap, n = self.picker["done"], len(self.picker["chosen"])
        tap.label.setText_(f"Use {n} app{'s' * (n != 1)}" if n else "Pick at least one app")
        tap.fill = kit.accent()
        tap.setNeedsDisplay_(True)
        tap.setAlphaValue_(1.0 if n else 0.35)

    def _picker_done(self):
        state = self.picker
        if not state["chosen"]:
            return
        known = {a["id"]: {"id": a["id"], "name": a["name"]} for a in state["all"]}
        for kept in self.setup.state["watch_apps"]:                      # an app no longer installed stays chosen
            known.setdefault(kept["id"], kept)
        self.setup.set_apps([known[i] for i in state["chosen"] if i in known])
        self.picker = None
        self.render()

    def _picker_all(self):
        self.setup.set_apps([])
        self.picker = None
        self.render()


def run():
    """Show first-run setup until it is finished or the window is closed. True when finished.
    A restart (Screen Recording) replaces this process; the next one resumes on the same screen."""
    p = config.paths()
    system = permissions.MacSystem()
    setup = onboarding.Setup(system, p.onboarding_file)
    if setup.completed:
        return True
    app = dictation.start_app()
    dictation.ensure_listener()                       # build the speech helper now, so the voice row can ask
    system.start()
    window = SetupWindow(setup)
    dictation.register_hotkey(window.hotkey)
    window.show()
    try:
        while not window.closed and not setup.completed:
            macos.next_event(app, 0.1)
            window.tick()
    finally:
        dictation.release_hotkey()
        window.close()
        system.stop()
    return setup.completed

"""LMemM - the event trail on macOS: the operating system tells us when something changed.

Everything that touches macOS for the trail lives here. The decisions (when to look, what the
user is on, what to log) are in trail_engine.py / trail_place.py and are tested on any OS; this
file only supplies facts and notifications:

  front()        the app in front (NSWorkspace; needs no permission, not even for its name)
  AXReader       window title, URL, focused box, tree text (Accessibility permission)
  Observers      AXObserver notifications for the front app: focus/window/title/selection/layout
  Tap            listen-only event tap: key counts, clicks, scrolls, a whitelist of shortcuts
                 (Input Monitoring permission); characters are never read
  vision()       screenshot + Apple Vision OCR, only when an app exposes too little text and
                 Screen Recording is already granted

No Screen Recording permission is needed unless vision() is used; without it the trail still runs.

THIS FILE HAS NOT BEEN RUN ON A MAC. See docs/specs/2026-10-09-event-trail.md for what to check.
"""

import collections
import json
import os
import signal
import threading
import time

import config
import macos
import trail_ax
import trail_engine
from trail_store import TrailStore

BROWSER_IDS = ("com.google.Chrome", "com.brave.Browser", "com.microsoft.edgemac", "company.thebrowser.Browser",
               "org.chromium.Chromium", "com.apple.Safari")
ELECTRON_IDS = ("com.microsoft.VSCode", "com.tinyspeck.slackmacgap", "com.hnc.Discord", "com.microsoft.teams2",
                "com.anthropic.claudefordesktop", "com.openai.chat", "com.todesktop.230313mzl4w4u92",
                "notion.id", "md.obsidian", "com.figma.Desktop")
AX_NOTIFICATIONS = {                                  # notification -> what the scheduler should do
    "AXFocusedWindowChanged": "window", "AXFocusedUIElementChanged": "focus", "AXTitleChanged": "title",
    "AXSelectedRowsChanged": "selection", "AXSelectedChildrenChanged": "selection", "AXLayoutChanged": "layout",
}
# keycode -> name, for the shortcut whitelist. Anything else with Cmd/Ctrl held is ignored, not logged.
SHORTCUT_KEYS = {0: "a", 1: "s", 3: "f", 6: "z", 7: "x", 8: "c", 9: "v", 13: "w", 15: "r", 17: "t", 37: "l", 45: "n",
                 48: "tab", 49: "space", 50: "`", 12: "q", 4: "h", 40: "k", 5: "g", 35: "p", 31: "o"}
CTRL, SHIFT, OPT, CMD = 1 << 18, 1 << 17, 1 << 19, 1 << 20


def front():
    app = macos.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None or app.processIdentifier() == os.getpid():
        return None                         # never inspect our own UI from a worker
    return {"app": app.localizedName(), "bundle_id": app.bundleIdentifier(), "pid": app.processIdentifier()}


AX_SYMBOLS = ("AXIsProcessTrusted", "AXUIElementCreateApplication", "AXUIElementCreateSystemWide", "AXUIElementSetMessagingTimeout",
              "AXUIElementSetAttributeValue", "AXUIElementCopyAttributeValue", "AXUIElementCopyMultipleAttributeValues",
              "AXUIElementCopyElementAtPosition", "AXValueGetType", "AXValueGetValue", "kAXValueAXErrorType", "kAXValueCGPointType",
              "AXObserverCreate", "AXObserverAddNotification", "AXObserverGetRunLoopSource", "CFRunLoopAddSource",
              "CFRunLoopRemoveSource", "CFRunLoopGetMain", "kCFRunLoopDefaultMode")
QUARTZ_SYMBOLS = ("CGPreflightListenEventAccess", "CGRequestListenEventAccess", "CGEventTapCreate", "CGEventTapEnable",
                  "CGEventGetFlags", "CGEventGetIntegerValueField", "CGEventGetLocation", "kCGKeyboardEventKeycode",
                  "kCGEventKeyDown", "kCGEventLeftMouseDown", "kCGEventRightMouseDown", "kCGEventScrollWheel",
                  "kCGEventTapDisabledByTimeout", "kCGEventTapDisabledByUserInput", "kCGAnnotatedSessionEventTap",
                  "kCGHeadInsertEventTap", "kCGEventTapOptionListenOnly", "CFMachPortCreateRunLoopSource",
                  "CFRunLoopAddSource", "CFRunLoopRemoveSource", "CFRunLoopGetMain", "kCFRunLoopCommonModes", "CFMachPortInvalidate")


class AXReader:
    """Reads the front app. Nothing here polls: the engine calls it when told something changed."""

    def __init__(self):
        import ApplicationServices as AS
        self.AS = AS
        trail_ax.warm(AS, AX_SYMBOLS)                 # resolve PyObjC's lazy names here, on the main thread (see warm())
        self.apps = {}                       # pid -> app element
        self.enabled = {}                    # pid -> element whose AXManualAccessibility we switched on
        self.enabled_at = {}                 # pid -> when; the app builds its tree for a few seconds after
        self.url_cache = {}                  # (pid, title) -> url found by a tree search

    def trusted(self):
        return bool(self.AS.AXIsProcessTrusted())

    def app_element(self, pid, bundle_id):
        el = self.apps.get(pid)
        if el is None:
            el = self.apps[pid] = self.AS.AXUIElementCreateApplication(pid)
            self.AS.AXUIElementSetMessagingTimeout(el, 0.2)              # a hung app cannot stall us
            if bundle_id in BROWSER_IDS[:-1] or bundle_id in ELECTRON_IDS:   # Chromium builds its tree on request
                if self.AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True) == 0:
                    self.enabled[pid] = el
                    self.enabled_at[pid] = time.monotonic()
        return el

    def warming(self, pid, seconds=6.0):
        """True while an app we just switched accessibility on for is still building its tree."""
        return time.monotonic() - self.enabled_at.get(pid, -1e9) < seconds

    def read(self, front, full):
        if front["pid"] == os.getpid():
            return None                     # own-process AX can invoke AppKit off the main thread
        if not self.trusted():
            return None
        el = self.app_element(front["pid"], front["bundle_id"])
        win = trail_ax.element_attr(el, "AXFocusedWindow") or trail_ax.element_attr(el, "AXMainWindow")
        if win is None:
            return trail_ax.Snapshot()
        focus = trail_ax.element_attr(el, "AXFocusedUIElement")
        snap = trail_ax.collect(trail_ax.LiveNode(win), trail_ax.LiveNode(focus) if focus is not None else None,
                                max_nodes=500 if full else 0, max_ms=config.TRAIL_FULL_MS)
        if not snap.url and front["bundle_id"] in BROWSER_IDS:           # the address is on the web area, not the window
            key = (front["pid"], snap.title)
            if key not in self.url_cache:
                found = trail_ax.collect(trail_ax.LiveNode(win), None, max_nodes=150, max_ms=25)
                self.url_cache[key] = found.url
                if len(self.url_cache) > 64:
                    self.url_cache.pop(next(iter(self.url_cache)))
            snap.url = self.url_cache[key]
        return snap

    def restore(self):
        """Switch back every accessibility flag we turned on."""
        for el in self.enabled.values():
            try:
                self.AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", False)
            except Exception:
                pass
        self.enabled.clear()


class Observers:
    """AXObserver for the app in front only (one observer, so the cost does not grow with open apps)."""

    def __init__(self, reader, poke):
        self.reader, self.poke = reader, poke
        self.pid = self.observer = None
        self.active = 0                         # notifications registered for the current app
        self.failed = set()                     # pids we could not observe: don't retry every loop

    def attach(self, pid, bundle_id):
        AS = self.reader.AS
        if pid == self.pid or pid in self.failed:
            return
        self.detach()
        el = self.reader.app_element(pid, bundle_id)

        import objc

        @objc.callbackFor(AS.AXObserverCreate)
        def callback(observer, element, notification, refcon):
            self.poke(AX_NOTIFICATIONS.get(str(notification), "layout"))

        self.callback = callback                 # keep it alive
        err, observer = AS.AXObserverCreate(pid, callback, None)
        if err != 0 or observer is None:
            return
        for name in AX_NOTIFICATIONS:
            if AS.AXObserverAddNotification(observer, el, name, None) == 0:
                self.active += 1
        AS.CFRunLoopAddSource(AS.CFRunLoopGetMain(), AS.AXObserverGetRunLoopSource(observer), AS.kCFRunLoopDefaultMode)
        self.pid, self.observer = pid, observer

    def detach(self):
        if self.observer is not None:
            AS = self.reader.AS
            try:
                AS.CFRunLoopRemoveSource(AS.CFRunLoopGetMain(), AS.AXObserverGetRunLoopSource(self.observer),
                                         AS.kCFRunLoopDefaultMode)
            except Exception:
                pass
        self.pid = self.observer = None
        self.active = 0


class Tap:
    """Listen-only input tap. The callback only queues a tiny tuple; all work happens on the worker."""

    def __init__(self, queue):
        self.queue, self.tap, self.source, self.state = queue, None, None, "off"

    def start(self):
        import Quartz as q
        trail_ax.warm(q, QUARTZ_SYMBOLS)
        if not q.CGPreflightListenEventAccess():
            q.CGRequestListenEventAccess()
            self.state = "no_input_monitoring"
            return False
        kinds = (q.kCGEventKeyDown, q.kCGEventLeftMouseDown, q.kCGEventRightMouseDown, q.kCGEventScrollWheel)

        def callback(proxy, kind, event, refcon):
            try:
                if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                    q.CGEventTapEnable(self.tap, True)
                    return event
                now = time.monotonic()
                if kind == q.kCGEventKeyDown:
                    flags = int(q.CGEventGetFlags(event))
                    if flags & (CMD | CTRL):
                        code = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
                        name = SHORTCUT_KEYS.get(int(code))
                        if name:
                            mods = "+".join(m for m, f in (("ctrl", CTRL), ("opt", OPT), ("shift", SHIFT), ("cmd", CMD)) if flags & f)
                            self.queue.append(("shortcut", now, None, mods + "+" + name))
                    else:
                        self.queue.append(("key", now, None, None))         # a count; the key itself is not read
                elif kind == q.kCGEventScrollWheel:
                    self.queue.append(("scroll", now, None, None))
                else:
                    p = q.CGEventGetLocation(event)
                    self.queue.append(("click", now, (p.x, p.y), "right" if kind == q.kCGEventRightMouseDown else "left"))
            except Exception:
                pass
            return event

        self.callback = callback
        self.tap = q.CGEventTapCreate(q.kCGAnnotatedSessionEventTap, q.kCGHeadInsertEventTap, q.kCGEventTapOptionListenOnly,
                                      sum(1 << k for k in kinds), callback, None)
        if self.tap is None:
            self.state = "tap_unavailable"
            return False
        self.source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetMain(), self.source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(self.tap, True)
        self.state = "recording"
        return True

    def stop(self):
        if self.tap is not None:
            import Quartz as q
            q.CGEventTapEnable(self.tap, False)
            q.CFRunLoopRemoveSource(q.CFRunLoopGetMain(), self.source, q.kCFRunLoopCommonModes)
            q.CFMachPortInvalidate(self.tap)
        self.tap = self.source = None
        self.state = "off"


def click_target(reader, x, y):
    """What was clicked: its role and, for buttons/links/tabs/rows, its name. Never a text field's contents."""
    if front() is None:
        return None, None                   # own window/menu: no background AX hit-testing
    AS = reader.AS
    err, el = AS.AXUIElementCopyElementAtPosition(AS.AXUIElementCreateSystemWide(), x, y, None)
    if err != 0 or el is None:
        return None, None
    role = None
    for hop in range(5):                 # an unlabeled group/static text: the nearest labeled parent names it
        node = trail_ax.LiveNode(el).attrs()
        if hop == 0:
            role = node.get("AXRole")
        if node.get("AXSubrole") == "AXSecureTextField" or node.get("AXRole") in trail_ax.TEXT_FIELDS:
            return role, None
        label = node.get("AXTitle") or node.get("AXDescription") or (node.get("AXValue") if hop == 0 else None)
        if isinstance(label, str) and label.strip():
            return role, " ".join(label.split())[:80]
        err, el = AS.AXUIElementCopyAttributeValue(el, "AXParent", None)
        if err != 0 or el is None:
            break
    return role, None


def vision(front_info, snap):
    """Read the screen with Apple Vision. None when Screen Recording is not granted."""
    if not macos.screen_recording_allowed(request=False):
        return None
    import resolver
    f = macos.front()
    display, _frame = macos.display_for(f["bounds"] if f else None)
    frame = macos.grab(display)
    if frame is None:
        return None
    try:
        lines, _rects, _ = resolver.run_vision(frame.cg, frame.width, frame.height, True)
    finally:
        frame.release()
    lines.sort(key=lambda l: (round(l["box"][1] / 8), l["box"][0]))
    out = trail_ax.Snapshot(title=snap.title, url=snap.url)
    out.texts = [" ".join(l["text"].split())[:trail_ax.MAX_TEXT] for l in lines][:trail_ax.MAX_TEXTS]
    return out


class Trail:
    """The running tracker: a worker thread does the reading, the main thread runs the run loop."""

    def __init__(self, watch_apps=None, paused=None):
        self.external_pause = paused or (lambda: False)     # the screenshot tracker's pause, when run inside it
        self.changes = 0                                    # counts notifications; wait_change() sleeps on it
        self.worker = None
        self.paths = config.paths()
        os.makedirs(self.paths.trail_dir, mode=0o700, exist_ok=True)
        self.store = TrailStore(session=time.strftime("%Y%m%d-%H%M%S"))
        self.reader = AXReader()
        self.cond = threading.Condition()
        self.sched = trail_engine.Scheduler()
        self.queue = collections.deque(maxlen=2000)
        self.tap = Tap(self.queue)
        self.observers = Observers(self.reader, self.poke)
        self.engine = trail_engine.Engine(self.store, front, self.reader, vision=vision,
                                          gate=trail_engine.Gate(watch_apps), paused=self.paused)
        self.running = False
        self.flags = set()
        self.last_status = 0.0
        self.last_sweep = 0.0
        self.said = {}

    def set_watch(self, watch_apps):
        self.engine.gate.set_watch(watch_apps)

    # ---- notifications -> the scheduler (called on the main thread; they only record and wake)

    def poke(self, kind):
        with self.cond:
            self.sched.poke(kind, time.monotonic())
            if kind in self.sched.PLACE:             # layout/scroll chatter does not count as "you moved"
                self.changes += 1
            self.cond.notify_all()

    def wait_change(self, seen, timeout):
        """Sleep until something changed since `seen` (a value of .changes) or `timeout`; returns .changes.
        Lets another thread follow the trail without polling."""
        with self.cond:
            if self.changes == seen:
                self.cond.wait(timeout)
            return self.changes

    trigger = lambda self, why: self.poke("app")                # macos.subscribe() handler protocol

    def set_flag(self, name, on):
        (self.flags.add if on else self.flags.discard)(name)
        self.poke("app")

    def chat_place(self, bundle_id, min_conf=0.7):
        """The chat the user is in right now, as the event trail read it from the app, or None
        when it is not sure (low confidence, conflicting signals, a different app in front)."""
        cur = self.engine.tracker.current
        if (cur and cur.get("bundle_id") == bundle_id and cur.get("kind") == "chat"
                and cur.get("confidence", 0) >= min_conf and not cur.get("conflict")
                and not cur["key"].endswith(":?") and cur.get("name")):
            return dict(cur)
        return None

    def read_now(self, front):
        """A full accessibility read of the window in front, for the screenshot tracker to use instead of
        OCR. None when it cannot be trusted: paused, a page the privacy gate refuses, an app whose tree
        is still filling in, or nothing readable."""
        if self.paused() or self.reader.warming(front["pid"]) or self.engine.gate.app(front):
            return None
        try:
            snap = self.reader.read(front, True)
        except Exception:
            return None
        if snap is None or snap.focus_secure or self.engine.gate.page(snap.url, snap.title, snap.focus_secure):
            return None
        return snap

    def paused(self):
        return bool(self.flags & {"asleep", "display_off", "locked"}) or bool(self.external_pause()) or os.path.exists(
            os.path.join(self.paths.trail_dir, ".paused"))

    # ---- the worker

    def work(self):
        while self.running:
            with self.cond:
                self.cond.wait(timeout=max(0.02, min(self.sched.wait(time.monotonic()), 1.0)))
            now = time.monotonic()
            try:
                self.sched.set_idle(macos.idle_seconds() > config.TRAIL_IDLE)
                info = front()
                if info and info["pid"] != self.observers.pid and not self.engine.gate.app(info):
                    try:
                        self.observers.attach(info["pid"], info["bundle_id"])
                    except Exception as e:          # notifications are an accelerator; the 1 s check still runs
                        self.observers.failed.add(info["pid"])
                        self.observers.detach()
                        self.say(f"no change notifications for {info['app']} ({type(e).__name__}: {e}); checking every second")
                if info and self.reader.warming(info["pid"]):      # its tree is still filling in: look again
                    self.engine.last_sig = None
                    self.poke("window")
                    self.poke("layout")
                self.drain(now)
                jobs = self.sched.due(now)
                if jobs:
                    self.engine.step(jobs, now)
                    wait = self.engine.recheck_in(now)
                    if wait is not None:
                        threading.Timer(wait, self.poke, ("focus",)).start()
                self.engine.flush_bursts(now)
                self.housekeeping(now)
            except Exception as e:
                self.engine.gap_once("internal_error")
                self.say(f"{type(e).__name__}: {e}")

    def say(self, msg):
        """Print a message the 1st, 10th, 100th... time it happens, not every time."""
        n = self.said[msg] = self.said.get(msg, 0) + 1
        if n in (1, 10, 100, 1000):
            print("trail: " + msg + (f" (x{n})" if n > 1 else ""))

    def drain(self, now):
        while self.queue:
            kind, t, point, extra = self.queue.popleft()
            if kind == "click":
                role, label = click_target(self.reader, *point)
                self.engine.on_input("click", t, button=extra, role=role, target=label)
                self.poke("selection")                      # a click often changes the chat or tab
            elif kind == "shortcut":
                self.engine.on_input("shortcut", t, combo=extra)
                self.poke("title")
            else:
                self.engine.on_input(kind, t)
                self.poke("layout")

    def housekeeping(self, now):
        if now - self.last_sweep > config.RETENTION_SWEEP:
            self.last_sweep = now
            self.store.sweep()
        if now - self.last_status > 10:
            self.last_status = now
            self.write_status()

    def write_status(self):
        ms = sorted(self.engine.cost["ms"][-200:])
        cur = self.engine.tracker.current
        doc = {"pid": os.getpid(), "updated": time.time(), "accessibility": self.reader.trusted(),
               "input_tap": self.tap.state, "observer_notifications": self.observers.active,
               "place": cur and {k: cur.get(k) for k in ("key", "kind", "name", "confidence")},
               "gap": self.engine.gap, "paused": self.paused(),
               "reads": self.engine.cost["reads"], "full_reads": self.engine.cost["full"],
               "ocr_reads": self.engine.cost["vision"], "events": self.engine.cost["events"],
               "full_read_nodes_p50": sorted(self.engine.cost["nodes"][-200:])[len(self.engine.cost["nodes"][-200:]) // 2]
               if self.engine.cost["nodes"] else None, "truncated_reads": self.engine.cost["truncated"],
               "full_read_ms": {"p50": ms[len(ms) // 2] if ms else None, "p95": ms[int(len(ms) * .95)] if ms else None}}
        tmp = os.path.join(self.paths.trail_dir, ".status.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, os.path.join(self.paths.trail_dir, ".status.json"))

    # ---- lifecycle

    def start(self):
        """Begin tracking and return at once (observers and the event tap live on the main run loop,
        which the caller keeps spinning). Safe to call when Accessibility is off: it says so and idles."""
        if not self.reader.trusted():
            print("trail: Accessibility is off for this app. System Settings > Privacy & Security > Accessibility, then restart.")
        self.running = True
        self.events = macos.subscribe(self)
        if not self.tap.start():
            print(f"trail: input events off ({self.tap.state}); app, window and text tracking still run")
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        self.poke("app")

    def stop(self):
        self.running = False
        with self.cond:
            self.cond.notify_all()
        if self.worker:
            self.worker.join(2)
        self.tap.stop()
        self.observers.detach()
        self.reader.restore()
        for b in self.engine.bursts.all():
            self.engine.emit_burst(b)
        self.store.flush()

    def run(self):
        """Stand-alone: `lmemm.py trail start`."""
        self.start()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        print("trail running. Ctrl-C to stop.")
        try:
            while self.running:
                macos.Foundation.NSRunLoop.currentRunLoop().runMode_beforeDate_(
                    macos.Foundation.NSDefaultRunLoopMode, macos.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.25))
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

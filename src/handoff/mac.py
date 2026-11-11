"""macOS adapters for the handoff workflow.  UNVERIFIED ON A MAC: written in a cloud session with
no Mac. Every integration below must pass the smoke procedure in docs/handoff-smoke-tests.md
before it is described as supported. Nothing here sends a prompt.

Honest capabilities:
  * destinations: open or focus the app, put the package on the clipboard. No composer insertion
    (`can_insert=False`): browsers and Electron composers do not reliably accept AXValue writes.
  * source: reads the *visible* text of the window through Accessibility. It cannot tell who
    said what, so the result is always `partial` with speaker `unknown`.
"""

import os
import time
from urllib.parse import urlparse

import config
from . import adapters as ad
from .model import COMPLETE, DENIED, EMPTY, FAILED, PARTIAL, UNKNOWN, Conversation, Message

# app id -> (display name, bundle ids, site hosts)
ASSISTANTS = {
    "claude": ("Claude", {"com.anthropic.claudefordesktop"}, {"claude.ai"}),
    "chatgpt": ("ChatGPT", {"com.openai.chat"}, {"chatgpt.com", "chat.openai.com"}),
    "gemini": ("Gemini", set(), {"gemini.google.com"}),
    "cursor": ("Cursor", {"com.todesktop.230313mzl4w4u92"}, set()),
}
# where to open an assistant that has no app: (bundle ids to try first, URL)
WEB_HOME = {"chatgpt": "https://chatgpt.com/", "claude": "https://claude.ai/new",
            "gemini": "https://gemini.google.com/app"}


def front_info():
    """SourceInfo of the app in front, with the browser tab's URL when it is a browser."""
    import macos
    f = macos.front()
    if not f:
        return ad.SourceInfo()
    url = ""
    if f["app"] in config.BROWSERS:
        try:
            url = macos.browser_info(f["app"])[0] or ""
        except Exception:
            url = ""
    return ad.SourceInfo(app=f["app"] or "", bundle_id=f["bundle_id"] or "", window=f["window"] or "", url=url)


def is_private(info):
    """The same never-read rules the memory tracker uses: password managers, banking sites,
    private windows. Checked before any extraction."""
    if info.bundle_id in config.SKIP_APPS:
        return True
    if info.url and config.SKIP_SITES.search(info.url):
        return True
    return bool(info.window and config.SKIP_TITLES.search(info.window))


class PasteboardClipboard(ad.Clipboard):
    def write(self, text):
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        return bool(pb.setString_forType_(text, NSPasteboardTypeString))

    def read(self):
        from AppKit import NSPasteboard, NSPasteboardTypeString
        return str(NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString) or "")


class AssistantSource(ad.SourceAdapter):
    """The visible text of an assistant's window (app or a browser tab on its site)."""
    capabilities = ad.Capabilities(can_open=False,
                                   notes="Visible text only; speakers are not identified; partial by design.")

    def __init__(self, key):
        self.id = key
        self.name, self.bundle_ids, self.hosts = ASSISTANTS[key]

    def matches(self, info):
        if info.bundle_id in self.bundle_ids:
            return True
        host = urlparse(info.url).hostname or "" if info.url else ""
        return host in self.hosts

    def extract(self, info, cancelled=lambda: False):
        import ApplicationServices as AS
        import macos
        if not AS.AXIsProcessTrusted():
            return Conversation(self.id, self.name, status=DENIED, reason="Accessibility access is off for LMemM.")
        f = macos.front()
        if not f:
            return Conversation(self.id, self.name, status=FAILED, reason="No window in front.")
        pid = f["pid"]
        if pid == os.getpid() and info.bundle_id:         # our own panel is in front: read the app we were called from
            from AppKit import NSRunningApplication
            apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(info.bundle_id)
            pid = apps[0].processIdentifier() if apps else pid
        snap, diag = read_visible(pid, cancelled)
        if cancelled():
            return Conversation(self.id, self.name, status=FAILED, reason="Cancelled.")
        if snap is None or not snap.texts:
            return Conversation(self.id, self.name, status=EMPTY,
                                reason=f"The app exposed no readable text ({diag}).")
        msg = Message(UNKNOWN, "\n".join(snap.texts), time.strftime("%Y-%m-%dT%H:%M:%S"))
        return Conversation(self.id, self.name, title=snap.page_title or snap.title or info.window,
                            status=PARTIAL, messages=[msg],
                            reason="only the visible part was read and speakers aren't identified")


def read_visible(pid, cancelled=lambda: False, wait=2.5):
    """(Snapshot or None, one-line diagnostic). Chromium and Electron apps (Claude, ChatGPT, Cursor,
    every browser) build their accessibility tree only when asked, and take a moment to do it, so
    switch it on and poll until text appears or `wait` seconds pass. The flag is put back after."""
    import ApplicationServices as AS
    import trail_ax
    el = AS.AXUIElementCreateApplication(pid)

    def get(name):
        err, v = AS.AXUIElementCopyAttributeValue(el, name, None)
        return v if err == 0 else None
    was = get("AXManualAccessibility")
    turned_on = was is not True and AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True) == 0
    snap, tries, note = None, 0, ""
    try:
        deadline = time.monotonic() + wait
        while True:
            tries += 1
            win = get("AXFocusedWindow") or get("AXMainWindow")
            if win is None:
                wins = get("AXWindows")
                win = wins[0] if wins else None
            if win is None:
                note = "no window in the accessibility tree"
            else:
                snap = trail_ax.collect(trail_ax.LiveNode(win), None, None, max_nodes=2500, max_ms=900)
                note = f"{snap.nodes} elements read" + (", cut short" if snap.truncated else "")
                if snap.texts:
                    break
            if cancelled() or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    finally:
        if turned_on:
            AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", False)
    return snap, f"{note}; {tries} tries" + ("; accessibility had to be switched on" if turned_on else "")


def dump_tree(pid, limit=400, wait=2.0):
    """Debug: every node's role and text-ish attributes, breadth first, for `handoff probe`.
    Returns (role histogram, lines). Reads names and values only on the app in front; prints to the
    developer's own terminal and stores nothing."""
    import ApplicationServices as AS
    import trail_ax
    el = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    time.sleep(wait)
    err, win = AS.AXUIElementCopyAttributeValue(el, "AXFocusedWindow", None)
    if err or win is None:
        err, win = AS.AXUIElementCopyAttributeValue(el, "AXMainWindow", None)
    if err or win is None:
        return {}, ["no window"]
    hist, lines, queue = {}, [], [(trail_ax.LiveNode(win), 0)]
    while queue and len(lines) < limit:
        node, depth = queue.pop(0)
        a = node.attrs()
        role = str(a.get("AXRole"))
        hist[role] = hist.get(role, 0) + 1
        bits = [f"{k[2:]}={str(a[k])[:60]!r}" for k in ("AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXURL") if a.get(k)]
        if bits or role in ("AXStaticText", "AXWebArea", "AXTextArea"):
            lines.append("  " * min(depth, 8) + role + " " + " ".join(bits))
        for kid in node.children()[:80]:
            queue.append((kid, depth + 1))
    AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", False)
    return hist, lines


class AppDestination(ad.DestinationAdapter):
    """Open or focus an assistant (its app if installed, else its website), then the package is
    on the clipboard for the user to paste."""
    capabilities = ad.Capabilities(can_open=True, can_insert=False, can_new_conversation=False,
                                   notes="Clipboard handoff; the user pastes and sends.")

    def __init__(self, key):
        self.id = f"mac-{key}"
        self.key = key
        self.name, self.bundle_ids, _ = ASSISTANTS[key]

    def _app_url(self):
        from AppKit import NSWorkspace
        for b in self.bundle_ids:
            u = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(b)
            if u is not None:
                return u
        return None

    def available(self):
        if self._app_url() is not None or self.key in WEB_HOME:
            return True, ""
        return False, f"{self.name} is not installed."

    def open(self):
        from AppKit import NSWorkspace
        from Foundation import NSURL
        ws = NSWorkspace.sharedWorkspace()
        u = self._app_url()
        if u is not None:
            return bool(ws.openURL_(u))
        if self.key in WEB_HOME:
            return bool(ws.openURL_(NSURL.URLWithString_(WEB_HOME[self.key])))
        return False

    def insert(self, text):
        return False                      # clipboard-only until composer insertion is verified on a Mac


def default_sources():
    return [AssistantSource(k) for k in ("claude", "chatgpt", "gemini", "cursor")]


def default_destinations():
    return [AppDestination(k) for k in ("claude", "chatgpt", "gemini", "cursor")]

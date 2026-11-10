"""LMemM - where you are, read from the app itself (macOS Accessibility), not from pixels.

Every app tells the system what its focused control is called: WhatsApp's message box says
"Type a message to <chat>", Slack's "Message #<channel>", a browser has the page's URL.
That label is exact (no OCR noise) and reads in about a millisecond, so it is the pill's
way of knowing what you are on the instant you switch. Nothing is read from the
control's contents, only its name; the text you type is never touched.
"""

import os
import macos
import time

import ApplicationServices as AS

TEXT_ROLES = {"AXTextArea", "AXTextField", "AXComboBox"}
BROWSERS = {"Brave Browser", "Google Chrome", "Chromium", "Microsoft Edge", "Arc"}
_enabled = {}                        # pid -> when Chromium's accessibility tree was switched on


def _get(el, attr):
    err, val = AS.AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def read(pid, app):
    """The place signature of the app in front: {"doc": url or "", "label": name of the
    focused text control or ""}, or None when the app exposes nothing."""
    # Own-process AX calls enter AppKit synchronously on this worker thread.
    if pid == os.getpid():
        return None
    if macos.privacy_reason({"app": app}):
        return None
    if not AS.AXIsProcessTrusted():
        return None
    el = AS.AXUIElementCreateApplication(pid)
    if app in BROWSERS and pid not in _enabled:         # Chromium builds its tree only on request
        AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        _enabled[pid] = time.time()
    win = _get(el, "AXFocusedWindow") or _get(el, "AXMainWindow")
    doc = str(_get(win, "AXDocument") or "") if win is not None else ""
    title = str(_get(win, "AXTitle") or "") if win is not None else ""
    focus = _get(el, "AXFocusedUIElement")
    label = ""
    if focus is not None and _get(focus, "AXRole") in TEXT_ROLES:
        for attr in ("AXPlaceholderValue", "AXTitle", "AXDescription"):
            value = _get(focus, attr)
            if value and str(value).strip():
                label = str(value).strip()[:160]
                break
    if macos.privacy_reason({"app": app}):
        return None
    if not doc and not label:
        return None
    return {"doc": doc.split("#")[0], "label": label, "title": title}


def key(app, sig):
    """A stable string for a signature, or None when it can't tell places apart."""
    if not sig or not sig["label"]:
        return None                                      # a URL alone is already handled elsewhere
    return f'{app}|{sig["doc"]}|{sig["label"]}'

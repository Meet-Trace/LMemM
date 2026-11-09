"""LMemM - read what an app exposes through accessibility, fast and bounded.

`collect(root)` walks an app window's accessibility tree and returns a Snapshot: the window
title, the page URL, what is focused, the headings, the selected sidebar rows and the visible
text. It never throws, never reads a password field and never reads what is typed into a
text box (only that one exists). A hung app cannot stall it: the walk stops at a node count
or a time budget, whichever comes first.

The walk is written against a tiny node protocol (`attrs()` and `children()`), so it runs on
plain test data on any OS. `LiveNode` (bottom of the file) is the macOS implementation: one
`AXUIElementCopyMultipleAttributeValues` call per node instead of one call per attribute,
which is what makes a read ~10-40 ms instead of hundreds.
"""

import re
import time
from dataclasses import dataclass, field

MAX_NODES = 350            # nodes visited per read
MAX_DEPTH = 16             # web content nests deeply
MAX_MS = 45                # stop reading after this long, return what we have
MAX_TEXT = 300             # characters kept per text
MAX_TEXTS = 120            # texts kept per snapshot
MAX_CHARS = 6000           # characters kept per snapshot
MAX_CHILDREN = 80          # children looked at per node (long lists: the visible part only)

SKIP_ROLES = {"AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem", "AXScrollBar", "AXValueIndicator",
              "AXSlider", "AXImage", "AXBusyIndicator", "AXProgressIndicator", "AXSplitter",
              "AXToolbar", "AXTabGroup", "AXPopUpButton", "AXMenuButton"}      # a browser's tabs and bookmarks are not the page
TEXT_FIELDS = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}
CONTROL_ROLES = {"AXButton", "AXCheckBox", "AXRadioButton", "AXTab", "AXTabButton", "AXDisclosureTriangle",
                 "AXIncrementor", "AXColorWell", "AXLink"}     # drawn UI: ink on screen, but not content to read
BIG_FIELD = 0.25           # a text box this much of the window is content we withhold (a terminal), not a small input box
MAX_REGIONS = 700          # screen rectangles kept per snapshot
ROW_ROLES = {"AXRow", "AXCell", "AXOutlineRow", "AXListItem", "AXGroup", "AXStaticText_row"}
TAB_ROLES = {"AXTab", "AXRadioButton", "AXTabButton"}
SECURE = "AXSecureTextField"

# the attributes a node is asked for, in one call
ATTRS = ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue",
         "AXSelected", "AXURL", "AXDocument", "AXPosition", "AXSize", "AXFocused",
         "AXChildren", "AXVisibleRows", "AXVisibleChildren")      # children ride the same call: half the round trips
LIST_ROLES = {"AXList", "AXTable", "AXOutline", "AXScrollArea"}


@dataclass
class Snapshot:
    title: str = ""                  # window title
    url: str = ""                    # page URL / document, when the app has one
    page_title: str = ""             # title of the web page inside the window (an app wrapping a web page)
    focus_role: str = ""
    focus_label: str = ""            # name of the focused text control ("Type a message to Mum")
    focus_secure: bool = False
    headings: list = field(default_factory=list)
    selected: list = field(default_factory=list)   # [[texts of one selected row], ...]
    tabs: list = field(default_factory=list)       # titles of selected tabs
    texts: list = field(default_factory=list)      # visible text in reading order
    nodes: int = 0
    ms: float = 0.0
    truncated: bool = False
    window: tuple = None                           # (x, y, w, h) of the window, in screen points
    items: list = field(default_factory=list)      # [(text, (x, y, w, h)), ...] where each text is drawn
    regions: list = field(default_factory=list)    # [((x, y, w, h), kind)]: what the tree accounts for on screen
                                                   #   kind: text | ui | media | skipped | withheld | unread

    def thin(self, min_texts=3):
        """Too little to tell what this is: the cue to fall back on the screen."""
        return not (self.url or self.focus_label or self.headings or self.selected) and len(self.texts) < min_texts


def _s(v, n=MAX_TEXT):
    if isinstance(v, str):
        v = " ".join(v.split())
        return v[:n]
    return ""


def _pos(v):
    try:
        return float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        pass
    try:
        return float(v["x"]), float(v["y"])
    except (TypeError, ValueError, KeyError):
        return None


def _rect(a):
    """(x, y, w, h) in screen points from a node's position and size, or None."""
    p, z = _pos(a.get("AXPosition")), _pos(a.get("AXSize"))
    if p is None or z is None or z[0] <= 0 or z[1] <= 0:
        return None
    return (p[0], p[1], z[0], z[1])


def _row_texts(node, limit=24):
    """The first few texts inside one row (a chat's name, its last message, the time)."""
    out, stack, seen = [], [node], 0
    while stack and seen < limit and len(out) < 4:
        n = stack.pop(0)
        seen += 1
        a = n.attrs()
        if a.get("AXRole") == "AXStaticText":
            t = _s(a.get("AXValue") or a.get("AXTitle") or a.get("AXDescription"))
            if t:
                out.append(t)
        stack.extend(n.children()[:12])
    if not out:                                   # a list cell that carries its text as one description
        t = _s(node.attrs().get("AXDescription") or node.attrs().get("AXTitle"))
        if t:
            out.append(t)
    return out


def collect(window, focus=None, app=None, clock=time.monotonic, max_nodes=MAX_NODES, max_ms=MAX_MS):
    """Snapshot of `window` (and of the focused control, when given). Never raises."""
    snap = Snapshot()
    t0 = clock()
    texts = []                                    # (y, x, text)
    try:
        wa = window.attrs() if window is not None else {}
        snap.title = _s(wa.get("AXTitle"))
        snap.window = _rect(wa)
        win_area = (snap.window[2] * snap.window[3]) if snap.window else 0
        snap.url = _s(wa.get("AXDocument"), 500)
        if app is not None:
            ap = app.attrs()
            snap.url = snap.url or _s(ap.get("AXURL"), 500)
        if focus is not None:
            fa = focus.attrs()
            snap.focus_role = _s(fa.get("AXRole"))
            if fa.get("AXSubrole") == SECURE:
                snap.focus_secure = True
            elif snap.focus_role in TEXT_FIELDS:
                for key in ("AXPlaceholderValue", "AXTitle", "AXDescription"):
                    if _s(fa.get(key)):
                        snap.focus_label = _s(fa.get(key), 160)
                        break
        queue, total, web = ([(window, 0)] if window is not None and max_nodes > 0 else []), 0, False
        while queue:
            node, depth = queue.pop(0)
            if snap.nodes >= max_nodes or (clock() - t0) * 1000 > max_ms:
                snap.truncated = True
                break
            snap.nodes += 1
            a = node.attrs()
            role, sub = a.get("AXRole"), a.get("AXSubrole")
            rect = _rect(a)
            if role in SKIP_ROLES or role == SECURE or sub == SECURE:
                if rect and len(snap.regions) < MAX_REGIONS:
                    snap.regions.append((rect, "skipped"))      # deliberately not read: not a gap in what we know
                continue
            if role == "AXWebArea":
                u = _s(a.get("AXURL"), 500)
                real = not u.startswith("file:")         # an app's own shell page is not "the page"
                if u and real and (not snap.url or snap.url.startswith("file:")):
                    snap.url = u
                if real and not snap.page_title:
                    snap.page_title = _s(a.get("AXTitle"), 200)
                if not snap.url:
                    snap.url = u
                if real and not web:              # the page itself: stop spending the budget on the browser around it
                    web, queue = True, []
            y, x = (_pos(a.get("AXPosition")) or (0.0, 0.0))[::-1]
            if role == "AXStaticText":
                t = _s(a.get("AXValue") or a.get("AXTitle") or a.get("AXDescription"))
                if t and total + len(t) <= MAX_CHARS and len(texts) < MAX_TEXTS:
                    texts.append((y, x, t))
                    total += len(t)
                    if rect:
                        snap.items.append((t, rect))
                if rect and len(snap.regions) < MAX_REGIONS:
                    snap.regions.append((rect, "text" if t else "ui"))
            elif role == "AXHeading":
                t = _s(a.get("AXTitle") or a.get("AXDescription") or a.get("AXValue"))
                if not t:
                    inner = _row_texts(node, 6)
                    t = inner[0] if inner else ""
                if t and t not in snap.headings and len(snap.headings) < 8:
                    snap.headings.append(t)
                if t and rect and len(snap.items) < MAX_TEXTS:
                    snap.items.append((t, rect))
                if rect and len(snap.regions) < MAX_REGIONS:
                    snap.regions.append((rect, "text"))
            elif role in TAB_ROLES and a.get("AXValue") in (1, True) and _s(a.get("AXTitle")):
                if len(snap.tabs) < 4:
                    snap.tabs.append(_s(a.get("AXTitle"), 120))
            if role in CONTROL_ROLES and rect and len(snap.regions) < MAX_REGIONS:
                snap.regions.append((rect, "ui"))
            elif role in TEXT_FIELDS and rect and len(snap.regions) < MAX_REGIONS:
                big = win_area and rect[2] * rect[3] > BIG_FIELD * win_area
                snap.regions.append((rect, "unread" if big else "withheld"))    # a terminal is content we did not read
            if a.get("AXSelected") is True and role in ROW_ROLES and len(snap.selected) < 3:
                row = _row_texts(node)
                if row:
                    snap.selected.append(row)
            if depth < MAX_DEPTH:
                for child in node.children()[:MAX_CHILDREN]:
                    queue.append((child, depth + 1))
    except Exception:
        snap.truncated = True
    texts.sort(key=lambda r: (round(r[0] / 6), r[1]))
    seen, ordered = set(), []
    for _y, _x, t in texts:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    snap.texts = ordered
    snap.ms = round((clock() - t0) * 1000, 1)
    return snap


# ---------------------------------------------------------------- macOS (not testable off a Mac)

def warm(module, names):
    """Touch each name once on the calling thread. PyObjC resolves framework names lazily and that is
    not thread-safe: two threads using a name for the first time at once can raise KeyError. The
    tracker has several threads reading accessibility, so everything the trail uses is resolved up front."""
    for name in names:
        try:
            getattr(module, name)
        except AttributeError:
            pass


class LiveNode:
    """An AXUIElement behind the node protocol. Reads attributes in one call."""

    def __init__(self, element):
        self.el = element
        self._attrs = None

    def attrs(self):
        if self._attrs is None:
            import ApplicationServices as AS
            out = {}
            try:
                err, values = AS.AXUIElementCopyMultipleAttributeValues(self.el, ATTRS, 0, None)
                if err == 0 and values is not None:
                    for name, v in zip(ATTRS, values):
                        v = _plain(AS, v)
                        if v is not None:
                            out[name] = v
            except Exception:
                pass
            self._attrs = out
        return self._attrs

    def children(self):
        a = self.attrs()
        names = ("AXVisibleRows", "AXVisibleChildren", "AXChildren") if a.get("AXRole") in LIST_ROLES else ("AXChildren",)
        for name in names:
            kids = a.get(name)
            if kids:
                return [LiveNode(k) for k in kids[:MAX_CHILDREN]]
        return []


_NUM = r"(-?\d+(?:\.\d+)?)"
_XY = re.compile(r"x\s*:\s*" + _NUM + r"\s+y\s*:\s*" + _NUM)
_WH = re.compile(r"w(?:idth)?\s*:\s*" + _NUM + r"\s+h(?:eight)?\s*:\s*" + _NUM)


def _from_text(v):
    """(a, b) read out of an AXValue's printed form ("{value = x:12 y:34 type = kAXValueCGPointType}").
    The fallback for when AXValueGetValue will not give the struct back."""
    text = str(v)
    for rx in (_XY, _WH):
        m = rx.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def _plain(AS, v):
    """An attribute value as str / number / bool / (x, y), or None when it is an error or an
    object we don't read."""
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, (list, tuple)) or type(v).__name__ in {"NSArray", "__NSArrayI", "__NSArrayM", "__NSCFArray"}:
        return list(v)                          # child lists
    try:
        kind = AS.AXValueGetType(v)
        if kind == AS.kAXValueAXErrorType:
            return None
        if kind == AS.kAXValueCGPointType:
            ok, point = AS.AXValueGetValue(v, kind, None)
            return (point.x, point.y) if ok else None
        if kind == AS.kAXValueCGSizeType:
            ok, size = AS.AXValueGetValue(v, kind, None)
            return (size.width, size.height) if ok else None
    except Exception:
        pass
    if type(v).__name__ == "AXValueRef":
        return _from_text(v)
    return str(v) if type(v).__name__ in {"NSURL", "__NSCFString"} else None


def element_attr(element, name):
    import ApplicationServices as AS
    err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None

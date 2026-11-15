"""Accessibility (AX) tree reader for trace_v2.observations.pipeline (`sources.accessibility`), reader `ax-tree/1`.

read(context) -> (raw, text) for the front application's window:
  1. AXIsProcessTrusted() is checked first, never prompting; untrusted -> ReaderUnavailable("accessibility not trusted").
  2. window: the app's AXFocusedWindow; else the AXWindows entry whose AXTitle equals context.window["title"]; else
     AXMainWindow; none of them -> NoWindow (a failed result).
  3. breadth-first walk of the window under hard budgets, so a huge or hung app cannot stall a capture:
     max_nodes (3000), max_depth (40), max_value_chars (2000) and a wall-clock budget (1.5 s); each AX call also has a
     messaging timeout (0.25 s) set on the application element. Hitting a budget stops the walk cleanly and is recorded.

raw = {"window_source", "app": {bundle_id, pid}, "document"?, "root": node, "stats": {nodes, max_depth, timeouts,
       elapsed_ms}, "truncated": {nodes, depth, time}, "limits"}
node = {role, subrole?, title?, value?, description?, identifier?, frame? [x, y, w, h] global points, focused?,
        selected_text?, url?, value_truncated? (original length), value_omitted? ("secure"), children}
Strings are kept as strings and numbers as numbers. The value (and selected text) of an AXSecureTextField, by role or
subrole, is never read: it is omitted at read time, before the pipeline's redaction ever sees it.
text = title/value strings in tree order, consecutive duplicates removed.

Error mapping: AX API disabled or process not trusted -> ReaderUnavailable; any other failure to reach the window
(app not responding, invalid element, no window) -> an exception, recorded as a failed result. Attribute errors on
individual nodes are tolerated (the attribute is simply absent); many consecutive timeouts end the walk as `time`.

Browsers: a web view's AXURL and the window's AXDocument are recorded when present. The excluded_site gate already ran
on the context URL; no extra gate is applied here. Electron/Chromium apps expose a rich tree only after the app turns
accessibility on (AXManualAccessibility); this reader never sets that, because it would change another app's behaviour.
"""

import math
import time

from trace_v2.observations.pipeline import ReaderUnavailable

READER_VERSION = "ax-tree/1"
DEFAULT_LIMITS = {"max_nodes": 3000, "max_depth": 40, "max_value_chars": 2000, "time_budget_s": 1.5,
                  "messaging_timeout_s": 0.25}
SECURE = "AXSecureTextField"
MAX_TIMEOUTS = 8


class AXError(RuntimeError):
    """An AX call failed in a way that makes the whole read fail."""


class NoWindow(AXError):
    """The application exposes no focused, matching or main window."""


class Unresponsive(AXError):
    """The application did not answer within the messaging timeout."""


# ---- the AX API seam: the real one wraps ApplicationServices, tests pass a fake with the same four methods ----

class SystemAX:
    """Statuses: ok | none (no such attribute/value) | disabled | timeout | invalid | error."""

    def __init__(self):
        import ApplicationServices as AS
        self._as = AS
        self._status = {AS.kAXErrorSuccess: "ok", AS.kAXErrorNoValue: "none", AS.kAXErrorAttributeUnsupported: "none",
                        AS.kAXErrorNotImplemented: "none", AS.kAXErrorAPIDisabled: "disabled",
                        AS.kAXErrorCannotComplete: "timeout", AS.kAXErrorInvalidUIElement: "invalid"}

    def trusted(self):
        return bool(self._as.AXIsProcessTrusted())          # never AXIsProcessTrustedWithOptions: no prompt

    def application(self, pid):
        return self._as.AXUIElementCreateApplication(pid)

    def set_timeout(self, element, seconds):
        self._as.AXUIElementSetMessagingTimeout(element, seconds)

    def attribute(self, element, name):
        error, value = self._as.AXUIElementCopyAttributeValue(element, name, None)
        status = self._status.get(error, "error")
        return (status, self._convert(name, value)) if status == "ok" else (status, None)

    def frame(self, element):
        (ps, pos), (ss, size) = self._raw(element, "AXPosition"), self._raw(element, "AXSize")
        if ps != "ok" or ss != "ok" or pos is None or size is None:
            return None
        ok_p, point = self._as.AXValueGetValue(pos, self._as.kAXValueCGPointType, None)
        ok_s, dims = self._as.AXValueGetValue(size, self._as.kAXValueCGSizeType, None)
        return [point.x, point.y, dims.width, dims.height] if ok_p and ok_s else None

    def _raw(self, element, name):
        error, value = self._as.AXUIElementCopyAttributeValue(element, name, None)
        return self._status.get(error, "error"), value

    @staticmethod
    def _convert(name, value):
        if name in ("AXChildren", "AXWindows"):
            return list(value or ())
        if name in ("AXFocusedWindow", "AXMainWindow"):
            return value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return str(value)
        absolute = getattr(value, "absoluteString", None)
        return str(absolute()) if absolute else None


# ---- the reader ----

def _clean_number(value):
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _is_secure(role, subrole):
    return role == SECURE or subrole == SECURE


class _Walk:
    def __init__(self, api, limits, clock):
        self.api, self.limits, self.clock = api, limits, clock
        self.deadline = clock() + limits["time_budget_s"]
        self.nodes = self.max_depth = self.timeouts = self.timeouts_total = 0
        self.truncated = {"nodes": False, "depth": False, "time": False}

    def attr(self, element, name):
        status, value = self.api.attribute(element, name)
        if status == "disabled":
            raise ReaderUnavailable("accessibility API disabled")
        if status == "timeout":
            self.timeouts += 1                      # consecutive: a run of them ends the walk
            self.timeouts_total += 1
        else:
            self.timeouts = 0
        return value if status == "ok" else None

    def out_of_time(self):
        if self.clock() >= self.deadline or self.timeouts >= MAX_TIMEOUTS:
            self.truncated["time"] = True
        return self.truncated["time"]

    def string(self, element, name, node, key):
        value = self.attr(element, name)
        if isinstance(value, str) and value:
            limit = self.limits["max_value_chars"]
            if len(value) > limit:
                node[key + "_truncated"] = len(value)
                value = value[:limit]
            node[key] = value

    def describe(self, element):
        node = {}
        role, subrole = self.attr(element, "AXRole"), self.attr(element, "AXSubrole")
        node["role"] = role if isinstance(role, str) else None
        if isinstance(subrole, str):
            node["subrole"] = subrole
        self.string(element, "AXTitle", node, "title")
        if _is_secure(node["role"], node.get("subrole")):
            node["value_omitted"] = "secure"
        else:
            value = self.attr(element, "AXValue")
            if isinstance(value, str) and value:
                self.string_value(value, node)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                if _clean_number(value) is not None:
                    node["value"] = value
        self.string(element, "AXDescription", node, "description")
        self.string(element, "AXIdentifier", node, "identifier")
        frame = self.api.frame(element)
        if frame and all(math.isfinite(v) for v in frame):
            node["frame"] = frame
        if self.attr(element, "AXFocused") is True:
            node["focused"] = True
            if "value_omitted" not in node:
                self.string(element, "AXSelectedText", node, "selected_text")
        if node["role"] == "AXWebArea":
            self.string(element, "AXURL", node, "url")
        return node

    def string_value(self, value, node):
        limit = self.limits["max_value_chars"]
        if len(value) > limit:
            node["value_truncated"] = len(value)
            value = value[:limit]
        node["value"] = value

    def tree(self, window):
        root = self.describe(window)
        root["children"] = []
        self.nodes = 1
        queue = [(window, root, 0)]
        index = 0
        while index < len(queue):
            element, node, depth = queue[index]
            index += 1
            self.max_depth = max(self.max_depth, depth)
            if self.out_of_time():
                break
            children = self.attr(element, "AXChildren") or []
            if not children:
                continue
            if depth >= self.limits["max_depth"]:
                self.truncated["depth"] = True
                continue
            for child in children:
                if self.nodes >= self.limits["max_nodes"]:
                    self.truncated["nodes"] = True
                    break
                if self.out_of_time():
                    break
                child_node = self.describe(child)
                child_node["children"] = []
                node["children"].append(child_node)
                self.nodes += 1
                queue.append((child, child_node, depth + 1))
        return root


def tree_text(root):
    """Titles and string values in tree order, consecutive duplicates removed."""
    lines, stack = [], [root]
    while stack:
        node = stack.pop()
        for key in ("title", "value"):
            value = node.get(key)
            if isinstance(value, str) and value.strip() and (not lines or lines[-1] != value):
                lines.append(value)
        stack.extend(reversed(node.get("children", ())))
    return "\n".join(lines)


class AccessibilityReader:
    version = READER_VERSION

    def __init__(self, api=None, clock=time.monotonic, **limits):
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise TypeError(f"unknown limits {sorted(unknown)}")
        self.limits = {**DEFAULT_LIMITS, **limits}
        for name, value in self.limits.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self._api, self._clock = api, clock

    @property
    def api(self):
        if self._api is None:
            self._api = SystemAX()
        return self._api

    def read(self, context):
        api = self.api
        if not api.trusted():
            raise ReaderUnavailable("accessibility not trusted")
        pid = context.application.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise AXError("the context has no application pid")
        app = api.application(pid)
        api.set_timeout(app, self.limits["messaging_timeout_s"])
        walk = _Walk(api, self.limits, self._clock)
        started = self._clock()
        window, source = self._window(walk, app, context)
        root = walk.tree(window)
        raw = {"window_source": source,
               "app": {"bundle_id": context.application.get("bundle_id"), "pid": pid},
               "root": root,
               "stats": {"nodes": walk.nodes, "max_depth": walk.max_depth, "timeouts": walk.timeouts_total,
                         "elapsed_ms": round((self._clock() - started) * 1000, 1)},
               "truncated": walk.truncated, "limits": dict(self.limits)}
        document = walk.attr(window, "AXDocument")
        if isinstance(document, str) and document:
            raw["document"] = document[:self.limits["max_value_chars"]]
        return raw, tree_text(root)

    def _window(self, walk, app, context):
        window = walk.attr(app, "AXFocusedWindow")
        if window is not None:
            return window, "focused"
        title = (context.window or {}).get("title")
        windows = walk.attr(app, "AXWindows") or []
        if title:
            for candidate in windows:
                if walk.attr(candidate, "AXTitle") == title:
                    return candidate, "title"
        window = walk.attr(app, "AXMainWindow")
        if window is not None:
            return window, "main"
        if walk.timeouts_total:
            raise Unresponsive("the application did not answer the accessibility request")
        raise NoWindow("the application has no focused, matching or main window")

"""AX reader with fake elements (no pyobjc calls): budgets, secure fields, truncation, text, error mapping."""

import json
import sys
import unittest

from trace_v2.observations import Context
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import ReaderUnavailable, build_draft, run_reader
from v2_observation_helpers import PNG, T0

try:
    from trace_v2.macos import AccessibilityReader, MacSources
    from trace_v2.macos.accessibility import AXError, NoWindow, Unresponsive
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False


class El:
    def __init__(self, role, children=(), errors=None, frame=None, **attrs):
        self.attrs = {"AXRole": role, **{k if k.startswith("AX") else "AX" + k[0].upper() + k[1:]: v
                                         for k, v in attrs.items()}}
        self.children, self.errors, self._frame = list(children), errors or {}, frame
        self.reads = []


class FakeAX:
    def __init__(self, app, trusted=True, tick=None):
        self.app, self._trusted, self.timeout, self.tick = app, trusted, None, tick

    def trusted(self):
        return self._trusted

    def application(self, pid):
        self.pid = pid
        return self.app

    def set_timeout(self, element, seconds):
        self.timeout = seconds

    def attribute(self, element, name):
        if self.tick:
            self.tick()
        element.reads.append(name)
        if name in element.errors:
            return element.errors[name], None
        if name == "AXChildren":
            return ("ok", element.children) if element.children else ("none", None)
        if name in element.attrs:
            return "ok", element.attrs[name]
        return "none", None

    def frame(self, element):
        return element._frame


def app_with(window, **attrs):
    return El("AXApplication", focusedWindow=window, **attrs)


def context(title="notes.txt"):
    return Context({"bundle_id": "com.example.editor", "name": "Editor", "pid": 42}, {"title": title, "id": 7},
                   {"id": 1, "bounds": [0, 0, 100, 100], "scale": 1.0})


def read(app, trusted=True, ctx=None, clock=None, **limits):
    api = FakeAX(app, trusted)
    reader = AccessibilityReader(api=api, **({"clock": clock} if clock else {}), **limits)
    return reader.read(ctx or context()), api


def wide(n, depth_each=0):
    return El("AXWindow", [El("AXStaticText", value=f"line {i}") for i in range(n)])


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class Tree(unittest.TestCase):
    def test_node_fields_frame_and_json(self):
        window = El("AXWindow", [El("AXTextField", frame=[1.0, 2.0, 30.0, 4.0], value="hello", description="name",
                                    identifier="field", focused=True, selectedText="hel", subrole="AXSearchField")],
                    title="Doc")
        (raw, text), api = read(app_with(window))
        json.dumps(raw, allow_nan=False)
        field = raw["root"]["children"][0]
        self.assertEqual((field["role"], field["subrole"], field["value"], field["description"], field["identifier"]),
                         ("AXTextField", "AXSearchField", "hello", "name", "field"))
        self.assertEqual((field["frame"], field["focused"], field["selected_text"]), ([1.0, 2.0, 30.0, 4.0], True, "hel"))
        self.assertEqual((raw["window_source"], raw["app"], text), ("focused", {"bundle_id": "com.example.editor", "pid": 42},
                                                                  "Doc\nhello"))
        self.assertEqual((api.pid, api.timeout), (42, 0.25))
        self.assertEqual(AccessibilityReader.version, "ax-tree/1")

    def test_numbers_stay_numbers_and_are_not_text(self):
        (raw, text), _ = read(app_with(El("AXWindow", [El("AXSlider", value=0.5), El("AXCheckBox", value=1)])))
        self.assertEqual([c["value"] for c in raw["root"]["children"]], [0.5, 1])
        self.assertEqual(text, "")

    def test_text_is_in_tree_order_and_drops_consecutive_duplicates(self):
        window = El("AXWindow", [El("AXGroup", [El("AXStaticText", value="a"), El("AXStaticText", value="a")]),
                                 El("AXButton", title="b")], title="w")
        self.assertEqual(read(app_with(window))[0][1], "w\na\nb")

    def test_url_and_document_are_recorded(self):
        window = El("AXWindow", [El("AXWebArea", AXURL="https://example.com/x")], document="file:///tmp/d.txt")
        (raw, _), _ = read(app_with(window))
        self.assertEqual(raw["root"]["children"][0]["url"], "https://example.com/x")
        self.assertEqual(raw["document"], "file:///tmp/d.txt")


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class SecureFields(unittest.TestCase):
    def secure_window(self, **node):
        return El("AXWindow", [El("AXTextField", value="visible"), El("AXSecureTextField", value="hunter2", **node),
                               El("AXTextField", subrole="AXSecureTextField", value="hunter3", focused=True,
                                  selectedText="hunter3", description="Password")])

    def test_secure_values_are_never_read_or_returned(self):
        window = self.secure_window()
        (raw, text), _ = read(app_with(window))
        blob = json.dumps(raw) + text
        self.assertNotIn("hunter", blob)
        self.assertIn("visible", text)
        secure_nodes = raw["root"]["children"][1:]
        self.assertTrue(all(n["value_omitted"] == "secure" and "value" not in n for n in secure_nodes))
        for el in window.children[1:]:
            self.assertNotIn("AXValue", el.reads)                       # not even requested
            self.assertNotIn("AXSelectedText", el.reads)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class Budgets(unittest.TestCase):
    def test_max_nodes(self):
        (raw, _), _ = read(app_with(wide(50)), max_nodes=10)
        self.assertEqual((raw["stats"]["nodes"], raw["truncated"]), (10, {"nodes": True, "depth": False, "time": False}))
        self.assertEqual(len(raw["root"]["children"]), 9)

    def test_max_depth(self):
        leaf = El("AXStaticText", value="deep")
        node = leaf
        for _ in range(6):
            node = El("AXGroup", [node])
        (raw, text), _ = read(app_with(El("AXWindow", [node])), max_depth=3)
        self.assertTrue(raw["truncated"]["depth"])
        self.assertNotIn("deep", text)
        self.assertEqual(raw["stats"]["max_depth"], 3)

    def test_complete_walk_is_not_truncated(self):
        (raw, _), _ = read(app_with(wide(5)))
        self.assertEqual(raw["truncated"], {"nodes": False, "depth": False, "time": False})
        self.assertEqual(raw["stats"]["nodes"], 6)

    def test_time_budget_stops_the_walk(self):
        now = [0.0]
        api = FakeAX(app_with(wide(100)), tick=lambda: now.__setitem__(0, now[0] + 0.1))
        reader = AccessibilityReader(api=api, clock=lambda: now[0], time_budget_s=1.0)
        raw, _ = reader.read(context())
        self.assertTrue(raw["truncated"]["time"])
        self.assertLess(raw["stats"]["nodes"], 101)

    def test_long_values_are_truncated_and_flagged(self):
        window = El("AXWindow", [El("AXTextArea", value="x" * 50)], title="t" * 30)
        (raw, text), _ = read(app_with(window), max_value_chars=20)
        area = raw["root"]["children"][0]
        self.assertEqual((len(area["value"]), area["value_truncated"]), (20, 50))
        self.assertEqual((len(raw["root"]["title"]), raw["root"]["title_truncated"]), (20, 30))
        self.assertLessEqual(max(len(line) for line in text.split("\n")), 20)

    def test_consecutive_timeouts_end_the_walk(self):
        window = El("AXWindow", [El("AXGroup", errors={name: "timeout" for name in (
            "AXRole", "AXSubrole", "AXTitle", "AXValue", "AXDescription", "AXIdentifier", "AXFocused", "AXChildren")})
                                 for _ in range(10)])
        (raw, _), _ = read(app_with(window))
        self.assertTrue(raw["truncated"]["time"])
        self.assertGreater(raw["stats"]["timeouts"], 0)

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError):
            AccessibilityReader(max_nodes=0)
        with self.assertRaises(TypeError):
            AccessibilityReader(max_nodez=5)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class WindowChoice(unittest.TestCase):
    def test_falls_back_to_the_window_with_the_context_title_then_main(self):
        other, wanted = El("AXWindow", title="other"), El("AXWindow", [El("AXStaticText", value="mine")], title="notes.txt")
        app = El("AXApplication", windows=[other, wanted], mainWindow=other)
        (raw, text), _ = read(app)
        self.assertEqual((raw["window_source"], text), ("title", "notes.txt\nmine"))
        (raw, _), _ = read(app, ctx=context(title="nothing"))
        self.assertEqual(raw["window_source"], "main")

    def test_no_window_is_a_failure(self):
        with self.assertRaises(NoWindow):
            read(El("AXApplication"))


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class ErrorMapping(unittest.TestCase):
    def run_it(self, app, trusted=True):
        reader = AccessibilityReader(api=FakeAX(app, trusted))
        return run_reader(reader, iter(range(10, 1000, 10)).__next__, context())

    def test_not_trusted_is_unavailable(self):
        result = self.run_it(app_with(wide(1)), trusted=False)
        self.assertEqual((result.status, result.error_code), ("unavailable", "ReaderUnavailable"))
        self.assertIn("not trusted", result.error_message)

    def test_api_disabled_is_unavailable(self):
        result = self.run_it(El("AXApplication", errors={"AXFocusedWindow": "disabled"}))
        self.assertEqual(result.status, "unavailable")

    def test_unresponsive_app_is_failed(self):
        result = self.run_it(El("AXApplication", errors={"AXFocusedWindow": "timeout", "AXWindows": "timeout",
                                                         "AXMainWindow": "timeout"}))
        self.assertEqual((result.status, result.error_code), ("failed", Unresponsive.__name__))

    def test_no_window_and_bad_pid_are_failed(self):
        self.assertEqual(self.run_it(El("AXApplication")).error_code, "NoWindow")
        reader = AccessibilityReader(api=FakeAX(El("AXApplication")))
        bad = Context({"bundle_id": "x", "name": "x", "pid": "no"}, {}, {"id": 1, "bounds": [0, 0, 1, 1]})
        result = run_reader(reader, iter(range(10, 100, 10)).__next__, bad)
        self.assertEqual((result.status, result.error_code), ("failed", AXError.__name__))

    def test_success_is_recorded(self):
        result = self.run_it(app_with(El("AXWindow", [El("AXStaticText", value="hi")])))
        self.assertEqual((result.status, result.reader_version, result.text), ("succeeded", "ax-tree/1", "hi"))


class _Source:
    def context(self):
        return context()

    def secure_input(self):
        return False

    def screenshot(self, display_id):
        from trace_v2.observations import ScreenshotCapture
        return ScreenshotCapture(PNG, "png", 32, 32, T0)


class _Ocr:
    version = "ocr-fake/1"

    def read(self, screenshot, context):
        return {"lines": []}, ""


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RedactionThroughThePipeline(unittest.TestCase):
    def draft(self, window):
        sources = MacSources(context_source=_Source(), screenshot_source=_Source(), ocr=_Ocr(),
                             accessibility=AccessibilityReader(api=FakeAX(app_with(window))))
        return build_draft(sources, EventBuffer(start_ns=T0 - 10), "ax:0", clock_ns=lambda: T0)[0]

    def test_a_secret_in_ax_text_is_redacted_and_withholds_the_screenshot(self):
        draft = self.draft(El("AXWindow", [El("AXTextField", value="key AKIAIOSFODNN7EXAMPLE")]))
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", json.dumps(draft.accessibility.raw) + draft.accessibility.text)
        self.assertIn("[REDACTED:aws_access_key]", draft.accessibility.text)
        self.assertEqual(draft.screenshot.reason, "secret_on_screen")

    def test_a_secure_field_alone_does_not_withhold(self):
        draft = self.draft(El("AXWindow", [El("AXSecureTextField", value="hunter2")]))
        self.assertEqual(draft.accessibility.status, "succeeded")
        self.assertNotIn("hunter2", json.dumps(draft.accessibility.raw))
        self.assertTrue(hasattr(draft.screenshot, "data"))

    def test_default_accessibility_reader_is_ax_tree_1(self):
        self.assertEqual(MacSources(context_source=_Source(), screenshot_source=_Source()).accessibility.version, "ax-tree/1")
        with self.assertRaises(ReaderUnavailable):
            AccessibilityReader(api=FakeAX(wide(1), trusted=False)).read(context())


if __name__ == "__main__":
    unittest.main()

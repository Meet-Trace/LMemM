"""Screenshot scope on this Mac. macOS only.

Fakes first (no permission needed): MacSources hands the scope and the front app that context() reported to the
screenshot source. Then real captures, skipped with the reason unless Screen Recording is granted to the process running
the tests: two solid-colour windows owned by two other processes are put on screen, the "front app" one (magenta) and
one that floats over it (cyan, standing in for a notification banner or another app's window). `display` scope must
contain both, `front_app` scope only the magenta one. The windows are on screen for a few seconds at the top-left of the
main display."""

import subprocess
import sys
import unittest
from pathlib import Path

from trace_v2.observations import Context, ScopeUnavailable, ScreenshotCapture
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import observe_once
from v2_observation_helpers import SEC, TempStore, context

try:
    import Quartz
    from trace_v2.macos import MacSources, ScreenshotSource
    ON_MAC = sys.platform == "darwin"
    SCREEN = bool(Quartz.CGPreflightScreenCaptureAccess())
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = SCREEN = False

HELPER = Path(__file__).with_name("v2_macos_color_window.py")
MAGENTA, CYAN = (255, 0, 255), (0, 255, 255)


class RecordingScreenshots:
    def __init__(self):
        self.calls = []

    def screenshot(self, display_id, **kwargs):
        self.calls.append((display_id, kwargs))
        return ScreenshotCapture(b"\x89PNG\r\n\x1a\n" + b"0" * 32, "png", 32, 32, 1, kwargs.get("scope"))


class FrontContext:
    def context(self):
        return Context({"bundle_id": "com.example.front", "name": "Front", "pid": 4242}, {"title": "w", "id": 1},
                       {"id": 7, "bounds": [0, 0, 10, 10], "scale": 2.0})

    def secure_input(self):
        return False


class _Ocr:
    version = "ocr-fake/1"

    def read(self, screenshot, context):
        return {"lines": []}, ""


class _Ax:
    version = "ax-fake/1"

    def read(self, context):
        return {"role": "AXWindow", "children": []}, ""


@unittest.skipUnless(ON_MAC, "macOS only")
class ScopeIsPassedThrough(unittest.TestCase):
    def sources(self, **kwargs):
        self.shots = RecordingScreenshots()
        return MacSources(context_source=FrontContext(), screenshot_source=self.shots, ocr=_Ocr(), accessibility=_Ax(),
                          **kwargs)

    def test_default_scope_is_front_app_with_the_app_the_context_reported(self):
        sources = self.sources()
        sources.context()
        sources.screenshot()
        self.assertEqual(self.shots.calls, [(7, {"scope": "front_app", "pid": 4242, "bundle_id": "com.example.front"})])

    def test_display_scope_and_live_changes(self):
        sources = self.sources(screenshot_scope="display")
        sources.context()
        sources.screenshot()
        sources.screenshot_scope = "front_app"
        sources.screenshot()
        self.assertEqual([c[1]["scope"] for c in self.shots.calls], ["display", "front_app"])

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(ValueError):
            self.sources(screenshot_scope="window")
        with self.assertRaises(ValueError):
            self.sources().screenshot_scope = "everything"

    def test_the_observation_records_the_scope(self):
        sources = self.sources()
        with TempStore() as t:
            oid = observe_once(t.store, sources, EventBuffer(0), "k:0").observation_id
            self.assertEqual(t.store.get(oid)["metadata"]["privacy"]["screenshot_scope"], "front_app")


class Window:
    def __init__(self, rgb, x, y, w, h, level="normal"):
        self.args = [sys.executable, str(HELPER), *map(str, rgb), *map(str, (x, y, w, h)), level]

    def __enter__(self):
        self.proc = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        ready = self.proc.stdout.readline().split()
        if len(ready) != 2 or ready[0] != "READY":
            self.proc.kill()
            raise RuntimeError("the colour window did not start")
        self.pid = int(ready[1])
        return self

    def __exit__(self, *exc):
        self.proc.stdin.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()


def count_colour(png, name):
    """Sampled pixels that are clearly magenta or cyan. The display's colour management shifts the exact values
    (255,0,255 comes back as about 242,73,248), so classify by channel instead of matching an RGB triple."""
    import AppKit
    rep = AppKit.NSBitmapImageRep.imageRepWithData_(png)
    width, height, n = rep.pixelsWide(), rep.pixelsHigh(), 0
    for yy in range(0, height, 8):
        for xx in range(0, width, 8):
            c = rep.colorAtX_y_(xx, yy)
            if c.alphaComponent() < 0.9:
                continue
            r, g, b = c.redComponent(), c.greenComponent(), c.blueComponent()
            if (name == "magenta" and r > 0.8 and b > 0.8 and g < 0.5) or (name == "cyan" and g > 0.8 and b > 0.8 and r < 0.6):
                n += 1
    return n


def transparent_share(png):
    import AppKit
    rep = AppKit.NSBitmapImageRep.imageRepWithData_(png)
    width, height, clear, total = rep.pixelsWide(), rep.pixelsHigh(), 0, 0
    for yy in range(0, height, 16):
        for xx in range(0, width, 16):
            clear += rep.colorAtX_y_(xx, yy).alphaComponent() == 0
            total += 1
    return clear / total


@unittest.skipUnless(ON_MAC, "macOS only")
class RealScope(unittest.TestCase):
    def setUp(self):
        if not SCREEN:
            self.skipTest("Screen Recording is not granted to the process running the tests")
        session = Quartz.CGSessionCopyCurrentDictionary()
        if session is None or session.get("CGSSessionScreenIsLocked"):
            self.skipTest("the screen is locked")
        self.source = ScreenshotSource()
        if not self.source._sck:
            self.skipTest("ScreenCaptureKit is not available")
        self.display = int(Quartz.CGMainDisplayID())

    def shoot(self, scope, pid, bundle_id=None):
        return self.source.screenshot(self.display, scope=scope, pid=pid, bundle_id=bundle_id)

    def test_front_app_scope_leaves_out_another_apps_window_that_floats_over_it(self):
        with Window(MAGENTA, 60, 60, 500, 400) as front, Window(CYAN, 260, 160, 500, 400, "floating") as banner:
            wide = self.shoot("display", front.pid)
            narrow = self.shoot("front_app", front.pid)
        self.assertEqual((wide.scope, narrow.scope), ("display", "front_app"))
        self.assertEqual((narrow.width, narrow.height), (wide.width, wide.height))              # full size, no downscale
        self.assertGreater(count_colour(wide.data, "cyan"), 100)                                    # the banner is on screen
        self.assertEqual(count_colour(narrow.data, "cyan"), 0)                                      # ... and not in front_app
        self.assertGreater(count_colour(narrow.data, "magenta"), 100)                               # the front app is there
        self.assertGreater(count_colour(narrow.data, "magenta"), count_colour(wide.data, "magenta"))   # incl. under the banner
        self.assertGreater(transparent_share(narrow.data), 0.5)             # desktop, menu bar and everything else: clear
        self.assertEqual(transparent_share(wide.data), 0)                   # display scope: every pixel is opaque

    def test_front_app_scope_matches_by_bundle_id_when_the_pid_is_unknown_and_never_widens_when_nothing_matches(self):
        with Window(MAGENTA, 60, 60, 300, 300) as front:
            with self.assertRaises(ScopeUnavailable):
                self.shoot("front_app", 2_000_000, "com.example.not-running")
            with self.assertRaises(ScopeUnavailable):
                self.shoot("front_app", None, None)
            self.assertGreater(count_colour(self.shoot("front_app", front.pid).data, "magenta"), 50)

    def test_through_the_pipeline_an_unmatched_app_is_a_withheld_screenshot_not_a_display_capture(self):
        class Ghost(FrontContext):
            def context(self):
                return Context({"bundle_id": "com.example.ghost", "name": "Ghost", "pid": 2_000_000}, {"title": "w", "id": 1},
                               {"id": self_display, "bounds": [0, 0, 10, 10], "scale": 2.0})
        self_display = self.display
        sources = MacSources(context_source=Ghost(), screenshot_source=self.source, ocr=_Ocr(), accessibility=_Ax())
        with TempStore() as t:
            o = t.store.get(observe_once(t.store, sources, EventBuffer(0), "k:0").observation_id)
        self.assertEqual((o["evidence"]["screenshot"]["status"], o["evidence"]["screenshot"]["error_code"]),
                         ("withheld", "scope_unavailable"))


if __name__ == "__main__":
    unittest.main()

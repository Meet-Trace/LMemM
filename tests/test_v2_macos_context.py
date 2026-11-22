"""macOS context adapter: real frontmost-app metadata, and the AppleScript browser parsing (injected, no prompts)."""

import json
import subprocess
import sys
import unittest
from unittest import mock

try:
    from trace_v2.macos import ContextSource, browser, session, windows
    ON_MAC = sys.platform == "darwin"
except ImportError:                                  # not macOS, or pyobjc is not installed in this interpreter
    ON_MAC = False


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class RealContext(unittest.TestCase):
    def test_context_describes_the_frontmost_app_and_display(self):
        source = ContextSource(browser_state=lambda bundle_id: {})
        ctx = source.context()
        ctx.validate()
        json.dumps([ctx.application, ctx.window, ctx.display])
        self.assertTrue(ctx.application["bundle_id"])
        self.assertGreater(ctx.application["pid"], 0)
        d = ctx.display
        self.assertGreater(d["pixel_size"][0], 0)
        self.assertGreaterEqual(d["scale"], 1.0)
        self.assertEqual(len(d["bounds"]), 4)
        if ctx.window and not ctx.window.get("locked"):
            self.assertEqual(len(ctx.window["bounds"]), 4)
            self.assertIsInstance(ctx.window["title"], str)

    def test_secure_input_is_a_bool(self):
        self.assertIsInstance(ContextSource().secure_input(), bool)


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class LockedContext(unittest.TestCase):
    def test_locked_screen_reads_nothing_about_the_window(self):
        front_window = mock.Mock(return_value={"id": 1, "title": "secret.txt", "bounds": [0, 0, 10, 10]})
        browser_state = mock.Mock(return_value={"url": "https://a.example"})
        with mock.patch.object(session, "screen_locked", return_value=True), \
                mock.patch.object(windows, "front_window", front_window):
            ctx = ContextSource(browser_state=browser_state).context()
        ctx.validate()
        self.assertEqual(ctx.window, {"locked": True})
        front_window.assert_not_called()
        browser_state.assert_not_called()
        self.assertGreater(ctx.display["pixel_size"][0], 0)


def _run(stdout="", stderr="", code=0, timeout=False):
    def run(cmd, **kw):
        if timeout:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    return run


@unittest.skipUnless(ON_MAC, "macOS with pyobjc only")
class BrowserState(unittest.TestCase):
    def test_non_browser_is_untouched(self):
        self.assertEqual(browser.browser_state("com.apple.finder", run=_run(code=1)), {})

    def test_chrome_normal_and_incognito(self):
        normal = browser.browser_state("com.google.Chrome", run=_run("https://a.example/x\nnormal\n"))
        self.assertEqual(normal, {"url": "https://a.example/x", "private": False})
        private = browser.browser_state("com.google.Chrome", run=_run("https://a.example/x\nincognito\n"))
        self.assertEqual(private, {"url": "https://a.example/x", "private": True})

    def test_chrome_unknown_mode_leaves_private_absent(self):
        state = browser.browser_state("com.brave.Browser", run=_run("https://a.example\nweird\n"))
        self.assertEqual(state, {"url": "https://a.example", "private_error": "unknown_mode"})

    def test_safari_reports_private_as_unsupported_never_guessed(self):
        state = browser.browser_state("com.apple.Safari", run=_run("https://a.example\n"))
        self.assertEqual(state, {"url": "https://a.example", "private_error": "unsupported_safari"})

    def test_failures_never_raise_and_say_why(self):
        denied = _run(stderr="execution error: Not authorized to send Apple events (-1743)", code=1)
        self.assertEqual(browser.browser_state("com.google.Chrome", run=denied)["url_error"], "automation_denied")
        self.assertEqual(browser.browser_state("com.google.Chrome", run=_run(timeout=True))["url_error"],
                         "automation_timeout")
        self.assertEqual(browser.browser_state("com.google.Chrome", run=_run(stderr="boom", code=1))["url_error"],
                         "applescript_error")
        self.assertEqual(browser.browser_state("com.google.Chrome", run=_run(""))["url_error"], "no_window")

    def test_budget_is_passed_as_the_timeout(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "https://a.example\nnormal", "")
        browser.browser_state("com.google.Chrome", run=run)
        self.assertLessEqual(seen["timeout"], 0.3)


if __name__ == "__main__":
    unittest.main()

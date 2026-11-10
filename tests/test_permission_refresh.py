"""Permission recovery uses real polling with only macOS boundaries substituted."""
import threading
import tempfile
from pathlib import Path
import onboarding
import unittest
from unittest.mock import patch
import permissions


class PermissionRefreshTests(unittest.TestCase):
    def test_failed_screen_check_does_not_hide_accessibility_grant(self):
        system = permissions.MacSystem()
        with patch("permissions.screen_allowed_here", side_effect=RuntimeError("screen unavailable")), \
             patch("permissions.screen_allowed_fresh", return_value=False), \
             patch("permissions.accessibility_trusted", return_value=True), \
             patch("permissions.voice_helper", side_effect=lambda *a, **kw: system.stop()):
            system._loop()
        self.assertTrue(system.facts().get("ax"))
        self.assertIn("screen", system.facts().get("errors", {}))

    def test_setup_refresh_reports_only_observed_grants_and_keeps_progress(self):
        system = permissions.MacSystem()
        with tempfile.TemporaryDirectory() as directory:
            setup = onboarding.Setup(system, str(Path(directory) / "onboarding.json"))
            setup.next()
            setup.set_name("Beta")
            setup.next()
            before = dict(setup.state)
            self.assertTrue(setup.press("refresh"))
            setup.refresh()
            self.assertTrue(setup.view()["checking"])
            self.assertEqual(setup.perm("screen"), onboarding.OFF)
            with patch("permissions.screen_allowed_here", return_value=True), \
                 patch("permissions.accessibility_trusted", return_value=True):
                system._read_facts()
            setup.refresh()
            self.assertEqual(setup.perm("screen"), onboarding.GRANTED)
            self.assertEqual(setup.perm("ax"), onboarding.GRANTED)
            self.assertFalse(setup.view()["checking"])
            self.assertFalse(setup.view()["can_retry_restart"])
            self.assertEqual(setup.state, before)

    def test_manual_refresh_updates_facts_without_requesting_access(self):
        system = permissions.MacSystem()
        allowed = threading.Event()
        checked = threading.Event()
        def screen():
            if allowed.is_set():
                checked.set()
            return allowed.is_set()
        with patch("permissions.screen_allowed_here", side_effect=screen), \
             patch("permissions.screen_allowed_fresh", return_value=False), \
             patch("permissions.accessibility_trusted", return_value=True), \
             patch("permissions.voice_helper", return_value=None):
            system.start()
            try:
                allowed.set()
                refresh = getattr(system, "request_refresh", None)
                self.assertTrue(callable(refresh), "manual permission refresh is unavailable")
                refresh()
                self.assertTrue(checked.wait(2), "refresh never reached the permission check")
                system.stop()
                system._thread.join(2)
                self.assertTrue(system.facts()["screen"])
                self.assertFalse(system.facts()["checking"])
            finally:
                system.stop()
                system._thread.join(2)

if __name__ == "__main__":
    unittest.main()

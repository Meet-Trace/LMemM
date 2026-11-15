"""Capture interval setting and the screen_locked privacy gate (platform independent)."""

import math
import unittest

from trace_v2.observations import Context, privacy, settings
from trace_v2.observations.events import EventBuffer
from trace_v2.observations.pipeline import ContinuousObserver, observe_once
from v2_observation_helpers import SEC, T0, TempStore, context
from test_v2_observation_pipeline import Sources


class CaptureIntervalSetting(unittest.TestCase):
    def test_default_is_three_seconds(self):
        with TempStore() as t:
            self.assertEqual(settings.get_capture_interval(t.store), 3.0)

    def test_set_get_and_history(self):
        with TempStore() as t:
            self.assertEqual(settings.set_capture_interval(t.store, 5), 5.0)
            self.assertEqual(settings.get_capture_interval(t.store), 5.0)
            self.assertEqual(settings.get_capture_interval(t.reopen()), 5.0)
            rows = t.store.db.execute("SELECT value_json FROM settings_history WHERE key='capture_interval_s'").fetchall()
            self.assertEqual(len(rows), 1)

    def test_bounds_are_inclusive(self):
        with TempStore() as t:
            self.assertEqual(settings.set_capture_interval(t.store, 0.5), 0.5)
            self.assertEqual(settings.set_capture_interval(t.store, 3600), 3600.0)

    def test_invalid_values_are_refused_and_not_stored(self):
        with TempStore() as t:
            for bad in (0.49, 0, -1, 3600.1, math.nan, math.inf, True, "3", None):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    settings.set_capture_interval(t.store, bad)
            self.assertEqual(settings.get_capture_interval(t.store), 3.0)

    def test_observer_default_interval_is_the_setting_default(self):
        with TempStore() as t:
            observer = ContinuousObserver(t.store, Sources(), EventBuffer(start_ns=T0))
            self.assertEqual(observer.interval_s, settings.DEFAULT_CAPTURE_INTERVAL_S)
            self.assertEqual(observer.interval_s, 3.0)


class ScreenLockedGate(unittest.TestCase):
    def locked(self, **extra):
        return Context({"bundle_id": "com.apple.loginwindow", "name": "loginwindow", "pid": 1},
                       {"locked": True, **extra}, {"id": 1})

    def test_locked_window_is_withheld(self):
        decision = privacy.gate(self.locked(), privacy.PrivacyPolicy())
        self.assertEqual((decision.withheld, decision.reason), (True, "screen_locked"))

    def test_locked_false_or_absent_is_not_withheld(self):
        ctx = context()
        self.assertFalse(privacy.gate(ctx, privacy.PrivacyPolicy()).withheld)
        ctx = Context(ctx.application, {**ctx.window, "locked": False}, ctx.display)
        self.assertFalse(privacy.gate(ctx, privacy.PrivacyPolicy()).withheld)

    def test_existing_gates_keep_their_reasons(self):
        policy = privacy.PrivacyPolicy()
        self.assertEqual(privacy.gate(context("com.1password.1password"), policy).reason, "excluded_app")
        self.assertEqual(privacy.gate(context(), policy, secure_input=True).reason, "secure_input")

    def test_locked_observation_never_captures_pixels(self):
        sources = Sources(ctx=self.locked())
        with TempStore() as t:
            result = observe_once(t.store, sources, EventBuffer(start_ns=T0 - 10 * SEC), "s:0")
            self.assertEqual(sources.screenshots, 0)
            obs = t.store.get(result.observation_id)
            self.assertEqual(obs["metadata"]["privacy"]["reason"], "screen_locked")


class ContextUnavailableGate(unittest.TestCase):
    def test_unknown_context_is_withheld_fail_closed(self):
        decision = privacy.gate(Context({}, {}, {}, error_code="KeyError"), privacy.PrivacyPolicy())
        self.assertEqual((decision.withheld, decision.reason), (True, "context_unavailable"))

    def test_failed_context_never_captures_pixels(self):
        sources = Sources(context_error=KeyError("bounds"))
        with TempStore() as t:
            result = observe_once(t.store, sources, EventBuffer(start_ns=T0 - 10 * SEC), "s:0")
            self.assertEqual(sources.screenshots, 0)
            obs = t.store.get(result.observation_id)
            self.assertEqual(obs["metadata"]["privacy"]["reason"], "context_unavailable")
            self.assertEqual(obs["metadata"]["extraction_status"]["context"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

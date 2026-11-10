"""The Settings window as data (settings_model.py): every setting, its words, and that bad values change nothing."""

import copy
import unittest

import onboarding
import settings_model as sm

USER = {"name": "Aditya", "works_on": ["code"], "watch_apps": [{"id": "com.a", "name": "A"}], "set_up": "2026-10-09"}


class ApplyTests(unittest.TestCase):
    def test_a_name_is_cleaned_and_kept_and_a_bad_one_changes_nothing(self):
        r = sm.apply(USER, "set_name", "  Ada   Lovelace ")
        self.assertEqual((r["user"]["name"], r["message"]), ("Ada Lovelace", "Saved “Ada Lovelace”"))
        self.assertEqual(USER["name"], "Aditya")                                 # the old block is not touched
        for bad in ("", "...", None, 7):
            with self.assertRaises(ValueError):
                sm.apply(USER, "set_name", bad)

    def test_roles_toggle_in_setup_order(self):
        r = sm.apply(USER, "toggle_role", "Design")
        self.assertEqual((r["user"]["works_on"], r["message"]), (["code", "design"], "Design added"))
        r = sm.apply(r["user"], "toggle_role", "Code")
        self.assertEqual((r["user"]["works_on"], r["message"]), (["design"], "Code removed"))
        self.assertNotIn("works_on", sm.apply(r["user"], "toggle_role", "Design")["user"])
        with self.assertRaises(ValueError):
            sm.apply(USER, "toggle_role", "Gardening")

    def test_the_last_watched_app_off_the_list_means_every_app(self):
        r = sm.apply(USER, "watch_add", {"id": "com.b", "name": "B"})
        self.assertEqual((r["message"], [e["id"] for e in r["user"]["watch_apps"]]), ("Watching only A, B", ["com.a", "com.b"]))
        r = sm.apply(r["user"], "watch_remove", "com.a")
        self.assertEqual(r["message"], "Watching only B")
        r = sm.apply(r["user"], "watch_remove", {"id": "com.b"})
        self.assertEqual(r["message"], "Watching every app")
        self.assertNotIn("watch_apps", r["user"])
        self.assertNotIn("watch_apps", sm.apply(USER, "watch_all")["user"])

    def test_lists_refuse_duplicates_unknowns_and_junk(self):
        for action, value in (("watch_add", "com.a"), ("watch_add", {"id": ""}), ("watch_remove", "nope"), ("skip_remove", "nope"), ("skip_add", 5)):
            with self.assertRaises(ValueError, msg=(action, value)):
                sm.apply(USER, action, value)

    def test_never_remember_is_its_own_list(self):
        r = sm.apply(USER, "skip_add", {"id": "com.x", "name": "Notes X"})
        self.assertEqual((r["message"], r["user"]["skip_apps"], r["user"]["watch_apps"]), ("Never remembering Notes X", [{"id": "com.x", "name": "Notes X"}], USER["watch_apps"]))
        back = sm.apply(r["user"], "skip_remove", "com.x")
        self.assertEqual(back["message"], "Notes X can be remembered again")
        self.assertNotIn("skip_apps", back["user"])

    def test_screenshots_are_kept_for_one_of_four_choices(self):
        self.assertEqual(sm.apply(USER, "keep_days", 30)["user"]["screenshot_days"], 30)
        self.assertEqual(sm.apply(USER, "keep_days", 1)["message"], "Screenshots are kept 1 day")
        for bad in (0, 5, "7", None):
            with self.assertRaises(ValueError):
                sm.apply(USER, "keep_days", bad)
        self.assertEqual((sm.keep_days({}), sm.keep_days({"screenshot_days": 3}), sm.keep_days({"screenshot_days": 9})), (7, 3, 7))

    def test_unknown_actions_and_missing_blocks_are_safe(self):
        with self.assertRaises(ValueError):
            sm.apply(USER, "explode")
        self.assertEqual(sm.apply(None, "set_name", "Ada")["user"], {"name": "Ada"})
        before = copy.deepcopy(USER)
        sm.apply(USER, "watch_all"); sm.apply(USER, "keep_days", 3)
        self.assertEqual(USER, before)


class ViewTests(unittest.TestCase):
    def test_tabs_are_the_six_sections_and_a_wrong_one_falls_back_to_you(self):
        page = sm.view(USER, "nonsense")
        self.assertEqual(([t["id"] for t in page["tabs"]], page["section"]), (list(sm.SECTIONS), "you"))
        self.assertEqual([t["on"] for t in page["tabs"]], [True] + [False] * 5)

    def test_you_apps_privacy_and_general_say_what_is_set(self):
        you = sm.view(USER, "you")["you"]
        self.assertEqual((you["name"], [r["role"] for r in you["roles"] if r["on"]]), ("Aditya", ["Code"]))
        some = sm.view(USER, "apps")["apps"]
        self.assertEqual((some["mode"], some["line"]), ("some", "Only A. Every other app is skipped before anything is read."))
        self.assertEqual(sm.view({}, "apps")["apps"]["mode"], "all")
        priv = sm.view({"skip_apps": [{"id": "com.x", "name": "X"}]}, "privacy")["privacy"]
        self.assertEqual((len(priv["always"]), [e["name"] for e in priv["never"]]), (4, ["X"]))
        gen = sm.view({"screenshot_days": 30}, "general")["general"]
        self.assertEqual([k["label"] for k in gen["keep"] if k["on"]], ["30 days"])
        self.assertIn("30 days", gen["keep_line"])
        self.assertIn("your notes stay", gen["keep_line"])

    def test_access_uses_setups_own_words_and_states(self):
        facts = {"screen": False, "screen_fresh": True, "ax": True, "mic": "granted", "speech": "denied"}
        rows = {r["key"]: r for r in sm.view(USER, "access", facts)["access"]["rows"]}
        self.assertEqual((rows["screen"]["state"], rows["screen"]["button"]), (onboarding.RESTART, "Restart LMemM"))
        self.assertEqual((rows["ax"]["state"], rows["ax"]["button"]), (onboarding.GRANTED, None))
        self.assertEqual((rows["voice"]["state"], rows["voice"]["button"]), (onboarding.DENIED, "Open System Settings"))
        self.assertIn("Only A", {r["key"]: r for r in sm.view(USER, "access", {})["access"]["rows"]}["screen"]["line"])

    def test_data_reports_what_is_kept_or_that_it_cannot_be_read(self):
        page = sm.view(USER, "data", data={"data_dir": "/x/data", "files": 3, "bytes": 1200})["data"]
        self.assertEqual((page["where"], page["files"], page["known"]), ("/x/data", 3, True))
        self.assertFalse(sm.view(USER, "data", data=None)["data"]["known"])


class LiveTests(unittest.TestCase):
    """A setting must change what LMemM does while it runs, not only the file."""

    def test_never_remember_changes_the_set_every_gate_holds(self):
        import config
        import trail_engine
        held = config.SKIP_APPS                                   # what a Gate keeps
        gate = trail_engine.Gate()
        try:
            config.set_skip_apps(["com.x"])
            self.assertIs(config.SKIP_APPS, held)
            self.assertEqual(gate.app({"bundle_id": "com.x", "app": "X"}), "excluded_app")
            self.assertEqual(gate.app({"bundle_id": "com.1password.1password", "app": "1P"}), "excluded_app")
            config.set_skip_apps([])
            self.assertIsNone(gate.app({"bundle_id": "com.x", "app": "X"}))
            self.assertEqual(gate.app({"bundle_id": "com.1password.1password", "app": "1P"}), "excluded_app")   # built-in stays
        finally:
            config.set_skip_apps([])

    def test_changing_the_watched_apps_changes_what_the_gate_lets_through(self):
        import trail_engine
        gate = trail_engine.Gate()
        self.assertIsNone(gate.app({"bundle_id": "com.b", "app": "B"}))
        gate.set_watch([{"id": "com.a", "name": "A"}])
        self.assertEqual(gate.app({"bundle_id": "com.b", "app": "B"}), "not_in_watch_list")
        gate.set_watch([])
        self.assertIsNone(gate.app({"bundle_id": "com.b", "app": "B"}))

    def test_the_pages_state_goes_to_settings_and_back_to_where_you_were(self):
        import page_model
        import projects
        reg = projects.empty()
        pid = projects.create(reg, "Work")
        state = page_model.press(reg, page_model.new_state(), "go", pid)
        state = page_model.press(reg, state, "settings", "apps")
        self.assertEqual((state["view"], state["sec"]), ("settings", "apps"))
        state = page_model.press(reg, state, "settings", "data")                 # switching tabs keeps where Back goes
        self.assertEqual(state["sec"], "data")
        state = page_model.press(reg, state, "back")
        self.assertEqual((state["view"], state["pid"]), ("project", pid))
        self.assertEqual(page_model.press(reg, page_model.new_state(), "settings", "nonsense")["sec"], "you")
        page = settings = sm.view({}, "you")
        self.assertEqual(page_model.view(reg, {}, page_model.press(reg, page_model.new_state(), "settings"), prefs=settings)["main"]["kind"], "settings")
        self.assertEqual(page_model.view(reg, {}, page_model.press(reg, page_model.new_state(), "settings"))["main"]["kind"], "home")   # no data: home, never a crash


class WiringTests(unittest.TestCase):
    """window.py and the tracker need a Mac to import, so the hand-offs are checked from the source."""

    def read(self, name):
        import os
        with open(os.path.join(os.path.dirname(__file__), "..", "src", name)) as fh:
            return fh.read()

    def test_every_setting_the_window_changes_is_a_real_one_and_each_is_offered(self):
        import re
        window = self.read("window.py")
        sent = set(re.findall(r'self\.setting\("([a-z_]+)"', window))
        self.assertLessEqual(sent, set(sm.ACTIONS))
        for action in sm.ACTIONS:                                # each one is offered somewhere (some are passed as a variable)
            self.assertIn(f'"{action}"', window, action)
        for section in sm.SECTIONS:
            self.assertIn(f"def _set_{section}(", window)

    def test_the_tracker_saves_the_change_and_makes_it_true_now(self):
        import ast
        tree = ast.parse(self.read("tracker.py"))
        fns = {n.name: ast.unparse(n) for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n, ast.FunctionDef)}
        change = fns["window_settings"]
        for needle in ("with self.lock", "settings_model.apply(", "store.save_user(", "self.apply_user("):
            self.assertIn(needle, change)
        self.assertIn("'settings': before", change)
        self.assertIn("settings_model.ACTIONS", fns["window_project"])
        self.assertIn("store.save_user(undo['settings'])", fns["window_project_undo"])
        live = fns["apply_user"]
        for needle in ("self.watch_apps[:]", "self.trail.set_watch(", "config.set_skip_apps(", "config.SCREENSHOT_DAYS"):
            self.assertIn(needle, live)
        self.assertIn("settings_model.keep_days(user)", fns["__init__"])           # and again at start-up
        self.assertIn("config.set_skip_apps(", fns["__init__"])


class BackdropSetting(unittest.TestCase):
    def test_pin_and_return_to_automatic(self):
        pinned = sm.apply({}, "backdrop", "night")["user"]
        self.assertEqual(pinned["backdrop"], "night")
        self.assertEqual([r["key"] for r in sm.view(pinned, "general")["general"]["backdrop"]["rows"] if r["on"]], ["night"])
        auto = sm.apply(pinned, "backdrop", "auto")["user"]
        self.assertNotIn("backdrop", auto)

    def test_nonsense_changes_nothing(self):
        with self.assertRaises(ValueError):
            sm.apply({}, "backdrop", "neon")



if __name__ == "__main__":
    unittest.main()

class MemoryEntryTests(unittest.TestCase):
    def test_general_explains_project_enrollment_and_agent_connection(self):
        view=sm.view({},'general')
        self.assertIn('Memory',view['general']['memory']['title'])
        self.assertIn('agent',view['general']['memory']['line'])

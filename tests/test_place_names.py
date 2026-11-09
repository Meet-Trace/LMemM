"""Every thing gets a real name from its own window (step 0 of the grouping plan)."""

import unittest
from datetime import datetime

import project_finder
import understand as u

RES = {"objects": []}


class CaptureNamesTest(unittest.TestCase):
    def test_terminal_is_named_by_its_folder_not_its_size(self):
        a = u.describe(RES, {"app": "Terminal", "window": "LMemM — -zsh — 80×24"})
        b = u.describe(RES, {"app": "Terminal", "window": "LMemM — -zsh — 202×60"})
        self.assertEqual((a["title"], a["target"]), ("LMemM", "LMemM"))
        self.assertEqual(u.ref(a), u.ref(b))

    def test_finder_and_other_apps_take_the_window_name(self):
        self.assertEqual(u.describe(RES, {"app": "Finder", "window": "Downloads"})["title"], "Downloads")
        self.assertEqual(u.describe(RES, {"app": "Preview", "window": "GroundEval.pdf"})["title"], "GroundEval.pdf")
        self.assertIsNone(u.describe(RES, {"app": "FaceTime", "window": "FaceTime"})["title"])

    def test_editor_panel_named_by_an_id_is_not_a_file(self):
        st = u.describe(RES, {"app": "Code", "bundle_id": "com.microsoft.VSCode",
                              "window": "LMemM f2f909638e2b13b9ec60422f8fec01a3"})
        self.assertIsNone(st["details"].get("file"))
        self.assertEqual(st["target"], "LMemM")
        st = u.describe(RES, {"app": "Code", "bundle_id": "com.microsoft.VSCode", "window": "activity.py — LMemM"})
        self.assertEqual((st["title"], st["target"]), ("activity.py", "LMemM/activity.py"))


class OldThingsTest(unittest.TestCase):
    def test_saved_things_get_their_window_name(self):
        cases = {"Terminal|terminal|LMemM — -zsh — 202×60": ("terminal", "LMemM"),
                 "VS Code|code_file|LMemM/LMemM f2f909638e2b13b9ec60422f8fec01a3": ("code_file", "LMemM"),
                 "Preview|app_window|GroundEval.pdf": ("app_window", "GroundEval.pdf"),
                 "FaceTime|app_window|FaceTime": ("app_window", None),
                 "ChatGPT|ai_conversation|6aec78ba-2a04-83e8-ac7b-a69820ea7c91": ("ai_conversation", None)}
        for ref, (kind, want) in cases.items():
            self.assertEqual(u.place_name({"ref": ref, "kind": kind, "app": ref.split("|")[0]}), want, ref)
        self.assertEqual(u.place_name({"title": "Kept", "ref": "a|b|c"}), "Kept")

    def test_project_finder_sees_the_names(self):
        items = {"t": {"app": "Terminal", "kind": "terminal", "title": None, "ref": "Terminal|terminal|LMemM — -zsh — 80×24"}}
        visits = [{"item": "t", "from": "2026-11-05T10:00:00", "to": "2026-11-05T10:05:00"}]
        things, _ = project_finder.recent(items, visits, datetime(2026, 11, 5, 12))
        self.assertEqual(things["t"]["title"], "LMemM")
        self.assertIsNone(items["t"]["title"])            # the memory itself is not changed


if __name__ == "__main__":
    unittest.main()

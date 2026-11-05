"""project_shadow.py: shadow mode notices projects in what you did and only ever suggests them."""

import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime

import config
import project_shadow as sh
import projects
import suggestions as sg
try:
    import tracker
except ImportError:                 # tracker needs PyObjC: macOS only
    tracker = None

HERE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping")
NOW = datetime(2026, 11, 4, 9, 0, 0)


def day():
    with open(os.path.join(HERE, "sample_day.json")) as fh:
        doc = json.load(fh)
    items = {i: {k: v for k, v in item.items() if k not in {"label"}} for i, item in doc["items"].items()}
    return items, doc["timeline"]


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.use_paths(self.tmp.name)
        self.paths.__enter__()
        self.items, self.visits = day()
        self.reg = projects.empty()

    def tearDown(self):
        self.paths.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_recurring_groups_are_offered_as_projects_and_nothing_is_filed(self):
        before = copy.deepcopy(self.items)
        sug = sg.empty()
        added = sh.propose(self.reg, self.items, self.visits, sug, now=NOW)
        self.assertTrue(added)
        self.assertEqual(self.items, before)                       # H1: offered, never filed
        northwind = next(e for e in sug["projects"] if "Northwind" in e["name"])
        self.assertIn("180DC", northwind["name"])                  # spelled as you write it
        self.assertIn("separate sittings", northwind["reason"])
        self.assertNotIn("gmail-inbox", northwind["ids"])

    def test_at_most_three_wait(self):
        sug = sg.empty()
        sh.MAX_WAITING, old = 1, sh.MAX_WAITING
        try:
            self.assertEqual(len(sh.propose(self.reg, self.items, self.visits, sug, now=NOW)), 1)
            self.assertEqual(sh.propose(self.reg, self.items, self.visits, sug, now=NOW), [])
        finally:
            sh.MAX_WAITING = old

    def test_old_work_is_not_looked_at(self):
        later = datetime(2026, 12, 1)
        self.assertEqual(sh.propose(self.reg, self.items, self.visits, sg.empty(), now=later), [])

    def test_your_projects_steer_the_rest(self):
        pid = projects.create(self.reg, "Client: Northwind pricing")
        projects.move_to(self.reg, self.items, ["word-proposal", "excel-model"], pid)
        sug = sg.empty()
        sh.propose(self.reg, self.items, self.visits, sug, now=NOW)
        offered = [e for e in sug["items"] if e["pid"] == pid]
        self.assertTrue(offered)
        self.assertIn("slack-pricing", offered[0]["ids"])
        self.assertNotIn("word-proposal", offered[0]["ids"])
        self.assertFalse(any("Northwind" in e["name"] for e in sug["projects"]))

    def test_a_dismissed_project_is_not_offered_again_and_answers_are_logged(self):
        sug = sg.empty()
        sh.propose(self.reg, self.items, self.visits, sug, now=NOW)
        entry = next(e for e in sug["projects"] if "Northwind" in e["name"])
        sg.run(self.reg, self.items, sug, "dismiss_project", entry["id"])
        sh.propose(self.reg, self.items, self.visits, sug, now=NOW)
        self.assertFalse(any("Northwind" in e["name"] for e in sug["projects"]))
        log = sh.read_log()
        self.assertTrue(any(e["event"] == "offer" and "Northwind" in e["target"] for e in log))

    @unittest.skipIf(tracker is None, "tracker needs macOS")
    def test_the_tracker_looks_on_a_copy_and_offers_under_its_lock(self):
        class Fake:
            pass
        t = Fake()
        t.lock, t.items, t.sug, t.shadow_busy = threading.Lock(), self.items, sg.empty(), True
        t.saved = 0
        t.save_sug = lambda: setattr(t, "saved", t.saved + 1)
        t.sync_pill = lambda: None
        sh_find = sh.find
        sh.find = lambda reg, items, visits: sh_find(reg, items, self.visits, now=NOW)
        try:
            tracker.Tracker.shadow_pass(t)
        finally:
            sh.find = sh_find
        self.assertTrue(t.sug["projects"])
        self.assertEqual(t.saved, 1)
        self.assertFalse(t.shadow_busy)

    @unittest.skipIf(tracker is None, "tracker needs macOS")
    def test_a_failed_look_never_raises(self):
        class Fake:
            pass
        t = Fake()
        t.lock, t.items, t.sug, t.shadow_busy = None, {}, sg.empty(), True       # no lock: it fails inside
        tracker.Tracker.shadow_pass(t)
        self.assertFalse(t.shadow_busy)


if __name__ == "__main__":
    unittest.main()

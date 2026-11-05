"""project_shadow.py: shadow mode notices projects in what you did and only ever suggests them."""

import copy
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

import config
import project_shadow as sh
import projects
import suggestions as sg
import synthetic_days as sd

try:
    import tracker
except ImportError:                 # tracker needs PyObjC: macOS only
    tracker = None

DOC = sd.generate(12, seed=4)
NOW = datetime.fromisoformat(max(v["to"] for v in DOC["timeline"])) + timedelta(hours=1)
DAYS = 30


def stream():
    items = {i: {k: v for k, v in item.items() if k != "label"} for i, item in copy.deepcopy(DOC["items"]).items()}
    return items, DOC["timeline"]


def truth(ids):
    return {DOC["items"][i]["label"][0].split("/")[0] for i in ids if DOC["items"][i]["label"]}


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.use_paths(self.tmp.name)
        self.paths.__enter__()
        self.items, self.visits = stream()
        self.reg = projects.empty()

    def tearDown(self):
        self.paths.__exit__(None, None, None)
        self.tmp.cleanup()

    def propose(self, sug, now=NOW):
        return sh.propose(self.reg, self.items, self.visits, sug, now=now, days=DAYS)

    def test_recurring_groups_are_offered_as_projects_and_nothing_is_filed(self):
        before = copy.deepcopy(self.items)
        sug = sg.empty()
        added = self.propose(sug)
        self.assertEqual(len(added), sh.MAX_WAITING)                # H4: never more than three
        self.assertEqual(self.items, before)                       # H1: offered, never filed
        pure = [e for e in added if len(truth(e["ids"])) == 1]
        self.assertGreaterEqual(len(pure), 2)                      # generated stream: one may mix two clients
        for e in added:
            self.assertIn("separate sittings", e["reason"])
            self.assertGreaterEqual(len(e["ids"]), sh.NEW_MIN_THINGS)

    def test_old_work_is_not_looked_at(self):
        self.assertEqual(self.propose(sg.empty(), now=NOW + timedelta(days=DAYS + 1)), [])

    def test_lmemm_own_windows_are_left_out(self):
        first = next(iter(self.items))
        self.items[first]["app"] = "LMemM"
        things, timeline = sh.recent(self.items, self.visits, NOW, DAYS)
        self.assertNotIn(first, things)
        self.assertFalse(any(v["item"] == first for v in timeline))

    def test_your_projects_steer_the_rest(self):
        group = sh.find(self.reg, self.items, self.visits, NOW, days=DAYS)[0][2][1]
        pid = projects.create(self.reg, "Mine")
        projects.move_to(self.reg, self.items, group[:2], pid)
        sug = sg.empty()
        self.propose(sug)
        offered = [e for e in sug["items"] if e["pid"] == pid]
        self.assertTrue(offered)
        self.assertLessEqual(set(offered[0]["ids"]), set(group[2:]))
        self.assertFalse(any(set(e["ids"]) & set(group[:2]) for e in sug["projects"]))

    def test_a_dismissed_project_is_not_offered_again_and_offers_are_logged(self):
        sug = sg.empty()
        first = self.propose(sug)[0]
        sg.run(self.reg, self.items, sug, "dismiss_project", first["id"])
        self.propose(sug)
        self.assertNotIn(first["name"], [e["name"] for e in sug["projects"]])
        self.assertTrue(any(e["event"] == "offer" and e["target"] == first["name"] for e in sh.read_log()))

    def test_unanswered_suggestions_are_set_aside_and_come_back(self):
        sug = sg.empty()
        sh.MAX_WAITING, old = 1, sh.MAX_WAITING
        try:
            first = self.propose(sug)[0]
            self.assertEqual(self.propose(sug, NOW + timedelta(days=1)), [])
            later = self.propose(sug, NOW + timedelta(days=3))
            self.assertEqual(len(later), 1)                            # room made, another group offered
            self.assertNotEqual(later[0]["name"], first["name"])
            self.assertEqual([s["entry"]["name"] for s in sug["shelved"]], [first["name"]])
            self.assertEqual(self.propose(sug, NOW + timedelta(days=4)), [])
            back = self.propose(sug, NOW + timedelta(days=10))
            self.assertEqual([e["name"] for e in back], [first["name"]])   # rested a week: back first
            self.assertEqual(len(sug["projects"]), 1)                  # never more than the room
            events = [e["event"] for e in sh.read_log()]
            self.assertIn("shelve", events)
            self.assertIn("unshelve", events)
        finally:
            sh.MAX_WAITING = old

    def test_a_set_aside_suggestion_you_dismissed_meanwhile_does_not_return(self):
        sug = sg.empty()
        first = self.propose(sug)[0]
        sg.run(self.reg, self.items, sug, "dismiss_project", first["id"])
        sug["shelved"] = [{"kind": "projects", "entry": first, "shelved": NOW.isoformat()}]
        back = self.propose(sug, NOW + timedelta(days=8))
        self.assertNotIn(first["name"], [e["name"] for e in back])
        self.assertNotIn(first["name"], [s["entry"]["name"] for s in sug["shelved"]])

    def test_groups_with_the_same_name_become_one_suggestion(self):
        ids = list(self.items)[:8]
        for i in ids:
            self.items[i]["title"] = "Atlas plan"
        keys_of = {i: {"atlas"} for i in ids}
        groups = {"P1": {"label": "Atlas", "tasks": 3, "places": ids[:4], "names": []},
                  "P2": {"label": "Atlas", "tasks": 2, "places": ids[4:], "names": []}}
        planned = sh.plan(self.reg, self.items, groups, keys_of)
        self.assertEqual(len(planned), 1)
        _, kind, (name, got, why) = planned[0]
        self.assertEqual((kind, name, set(got), why), ("project", "Atlas", set(ids), "Used together in 5 separate sittings"))

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
        sh.find = lambda reg, items, visits: sh_find(reg, items, self.visits, now=NOW, days=DAYS)
        try:
            tracker.Tracker.shadow_pass(t)
        finally:
            sh.find = sh_find
        self.assertTrue(t.sug["projects"])
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

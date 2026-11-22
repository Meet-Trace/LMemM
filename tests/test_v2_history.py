"""Work History from Trace V2's vision Moments (v2_history.py, history_model H13)."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

import history_model as hm
import page_model as pm
import projects
import v2_history

NOW = datetime(2026, 11, 22, 15, 0)


def ns(clock):
    return int(datetime.strptime("2026-11-22 " + clock, "%Y-%m-%d %H:%M:%S").timestamp() * 1e9)


class V2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.goals, self.data = os.path.join(self.tmp.name, "g"), os.path.join(self.tmp.name, "d")
        os.makedirs(self.goals)
        os.makedirs(self.data)
        v2_history._CACHE.clear()
        db = sqlite3.connect(os.path.join(self.data, "observations.sqlite3"))
        db.executescript("""
            CREATE TABLE moment_revisions(moment_id TEXT, revision INTEGER, state TEXT);
            CREATE TABLE moment_member_index(moment_id TEXT, observation_id TEXT);
            CREATE TABLE observations(observation_id TEXT, app_name TEXT);""")
        self.db = db
        self.rows = []

    def moment(self, mid, a, b, state="stable", app="Terminal", what="User reads output.", why="Explore the repo.", obs=2):
        self.db.execute("INSERT INTO moment_revisions VALUES (?,1,?)", (mid, state))
        for k in range(obs):
            oid = f"{mid}-{k}"
            self.db.execute("INSERT INTO moment_member_index VALUES (?,?)", (mid, oid))
            self.db.execute("INSERT INTO observations VALUES (?,?)", (oid, app))
        self.rows.append({"id": mid, "start_ns": ns(a), "end_ns": ns(b), "what": what, "why": why})

    def save(self, groups=()):
        self.db.commit()
        goal = lambda key: [{"moment_id": r["id"], "goal": r[key]} for r in self.rows]
        doc = {"moments": [{"id": r["id"], "start_ns": r["start_ns"], "end_ns": r["end_ns"]} for r in self.rows],
               "models": {"lfm3-vl:latest": {"goals": goal("what")},
                          "lfm3-vl:latest (purpose)": {"goals": goal("why"), "groups": [list(g) for g in groups]}}}
        with open(os.path.join(self.goals, "moment_goals.json"), "w") as fh:
            json.dump(doc, fh)

    def page(self, v2="read", **hist):
        s = pm.new_state()
        s["hist"].update(hist)
        events = v2_history.read(self.goals, self.data) if v2 == "read" else v2
        return hm.view(projects.empty(), {}, s, NOW, v2=events)

    def rows_of(self, page):
        return [r for g in page["groups"] for r in g["rows"]]

    def test_a_described_moment_is_an_entry_with_the_purpose_as_title_and_the_description_below(self):
        self.moment("m1", "09:00:00", "09:05:00")
        self.save()
        row, = self.rows_of(self.page())
        self.assertEqual((row["title"], row["doing"], row["app"], row["span"], row["duration"], row["open"]),
                         ("Explore the repo.", "User reads output.", "Terminal", "09:00–09:05", "5m", False))

    def test_moments_the_pair_check_judged_the_same_goal_show_once(self):
        self.moment("m1", "09:00:00", "09:05:00")
        self.moment("m2", "09:20:00", "09:30:00", why="Look through the repo history.")
        self.moment("m3", "09:40:00", "09:45:00", app="Safari", why="Read about cats.")
        self.save(groups=[(0, 1)])
        rows = self.rows_of(self.page())
        self.assertEqual(len(rows), 2)
        terminal = [r for r in rows if r["app"] == "Terminal"][0]
        self.assertEqual((terminal["span"], terminal["visits"], terminal["unit"]), ("09:00–09:30", 2, "moments"))

    def test_a_forgotten_or_merged_moment_is_not_shown_even_though_the_json_keeps_its_text(self):
        self.moment("keep", "09:00:00", "09:05:00")
        self.moment("merged", "09:10:00", "09:15:00", state="merged")
        self.moment("gone", "09:20:00", "09:25:00")
        self.db.execute("DELETE FROM moment_revisions WHERE moment_id='gone'")
        self.save()
        self.assertEqual([r["id"] for r in self.rows_of(self.page())], ["keep"])

    def test_no_files_no_database_or_nothing_described_is_no_v2_data(self):
        self.assertIsNone(v2_history.read(self.goals, self.data))
        self.moment("m1", "09:00:00", "09:05:00", what="", why="")
        self.save()
        self.assertIsNone(v2_history.read(self.goals, self.data))
        self.assertIsNone(v2_history.read(self.goals, os.path.join(self.tmp.name, "missing")))

    def test_an_app_you_never_remember_is_hidden(self):
        self.moment("m1", "09:00:00", "09:05:00", app="Terminal")
        self.save()
        s = pm.new_state()
        page = hm.view(projects.empty(), {}, s, NOW, skip=[{"name": "Terminal"}], v2=v2_history.read(self.goals, self.data))
        self.assertEqual(self.rows_of(page), [])

    def test_search_reads_the_moment_text_and_old_days_are_not_cut_at_48_hours(self):
        self.moment("m1", "09:00:00", "09:05:00", why="Plan the launch.")
        self.save()
        page = self.page(q="launch")
        self.assertEqual([r["id"] for r in self.rows_of(page)], ["m1"])
        self.assertEqual(self.rows_of(self.page(q="zzz")), [])
        events = v2_history.read(self.goals, self.data)
        for e in events:
            e["start"] = e["start"].replace(day=1)
            e["end"] = e["end"].replace(day=1)
        self.assertIn("earlier", [s["id"] for s in self.page(v2=events)["scopes"]])

    def test_what_v2_has_not_described_yet_is_filled_from_the_rule_based_moments(self):
        from datetime import timedelta
        now = datetime.now().replace(microsecond=0)
        old_a = datetime.combine(now.date(), datetime.min.time()) + timedelta(hours=1)
        v2 = [{"v2": {"id": "m1", "title": "Old purpose", "line": "", "app": "Terminal", "activity": None}, "item": "m1",
               "start": old_a, "end": old_a + timedelta(minutes=5), "seconds": 300}]
        thing = {"id": "t", "app": "Docs", "title": "Fresh doc", "last_seen": now.isoformat(), "notes": []}
        rec = lambda vid, a, b: {"item_id": "t", "visit_id": vid, "t_start": a.timestamp(), "t_end": b.timestamp()}
        moments = [rec("before", old_a - timedelta(minutes=30), old_a - timedelta(minutes=20)),       # inside V2's period: not shown
                   rec("after", now - timedelta(minutes=10), now - timedelta(minutes=2))]              # after the last described Moment
        s = pm.new_state()
        page = hm.view(projects.empty(), {"t": thing}, s, now, v2=v2, moments=moments)
        self.assertEqual(sorted(r["title"] for r in self.rows_of(page)), ["Fresh doc", "Old purpose"])
        self.assertEqual([r["visits"] for r in self.rows_of(page)], [1, 1])

    def test_the_cache_follows_the_files(self):
        self.moment("m1", "09:00:00", "09:05:00")
        self.save()
        self.assertEqual(len(v2_history.read(self.goals, self.data)), 1)
        self.moment("m2", "10:00:00", "10:05:00")
        self.save()
        os.utime(os.path.join(self.goals, "moment_goals.json"), (1, 1))
        self.assertEqual(len(v2_history.read(self.goals, self.data)), 2)


if __name__ == "__main__":
    unittest.main()

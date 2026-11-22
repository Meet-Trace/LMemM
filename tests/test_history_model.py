"""Work History as data (history_model.py): days, order, merging, privacy, search and paging."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import history_model as hm
import page_model as pm
import projects

NOW = datetime(2026, 11, 22, 15, 0)


def stretch(item, a, b, seconds, app="STALE APP", doing="STALE DOING"):
    return {"from": a, "to": b, "seconds": seconds, "item": item, "app": app, "doing": doing}


def thing(i, title, app="Docs", project=None, doing=None, **more):
    item = {"id": i, "app": app, "title": title, "last_seen": "2026-11-22T10:00:00", "notes": [], **more}
    if doing:
        item["doing"] = doing
    if project:
        item["project_id"] = project
    return item


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        hm._CACHE.clear()
        self.reg = projects.empty()
        self.work = projects.create(self.reg, "Work")
        self.q3 = projects.create(self.reg, "Q3 plan", self.work)
        self.items = {i["id"]: i for i in (
            thing("a", "Q3 deck", "Keynote", self.q3, doing="Drafting slides"),
            thing("b", "Budget sheet", "Numbers", self.q3),
            thing("c", "Garden notes", "Notes"),
        )}

    def session(self, sid, timeline):
        with open(os.path.join(self.dir, sid + ".json"), "w") as fh:
            json.dump({"session": sid, "timeline": timeline}, fh)

    def state(self, **hist):
        s = pm.new_state()
        s["hist"].update(hist)
        return s

    def view(self, items=None, now=NOW, live=None, skip=(), moments=(), **hist):
        return hm.view(self.reg, self.items if items is None else items, self.state(**hist), now,
                       sessions_dir=self.dir, live=live, skip=skip, moments=moments)

    def rows(self, page):
        return [r for g in page["groups"] for r in g["rows"]]

    def today(self):
        self.session("20261122-090000", [stretch("a", "09:00:05", "09:20:00", 600), stretch("a", "09:20:00", "09:30:00", 300),
                                         stretch("b", "09:30:00", "09:30:40", 40), stretch("c", "09:31:00", "10:00:00", 1700)])

class History(Base):
    def test_today_is_in_time_order_with_name_app_project_and_time(self):
        self.today()
        page = self.view()
        self.assertEqual(page["heading"], "Today")
        self.assertEqual([r["title"] for r in self.rows(page)], ["Q3 deck", "Budget sheet", "Garden notes"])
        first = self.rows(page)[0]
        self.assertEqual((first["app"], first["project"], first["span"]), ("Keynote", "Work › Q3 plan", "09:00–09:30"))

    def test_adjacent_stretches_on_one_thing_merge_and_different_things_never_do(self):
        self.today()
        page = self.view()
        first = self.rows(page)[0]
        self.assertEqual(first["seconds"], 900)
        self.assertEqual(len(self.rows(page)), 3)

    def test_a_stretch_inside_one_minute_shows_only_its_start(self):
        self.today()
        self.assertEqual(self.rows(self.view())[1]["span"], "09:30")

    def test_a_thing_returned_to_later_is_a_second_entry(self):
        self.session("20261122-090000", [stretch("a", "09:00:00", "09:10:00", 600), stretch("b", "09:10:00", "09:20:00", 600),
                                         stretch("a", "09:20:00", "09:30:00", 600)])
        self.assertEqual([r["id"] for r in self.rows(self.view())], ["a", "b", "a"])

    def test_duration_only_from_sixty_seconds_and_description_only_when_stored(self):
        self.today()
        a, b, c = self.rows(self.view())
        self.assertEqual(a["duration"], "15m")
        self.assertEqual(a["doing"], "Drafting slides")
        self.assertIsNone(b["duration"])
        self.assertIsNone(b["doing"])
        self.assertIsNone(c["doing"])

    def test_an_ai_summary_line_is_the_description_and_a_repeat_of_the_title_is_not(self):
        self.items["b"]["summary"] = {"title": "Budget", "summary": "Checking Q3 numbers."}
        self.items["c"]["doing"] = "Garden notes"
        self.today()
        a, b, c = self.rows(self.view())
        self.assertEqual(b["doing"], "Checking Q3 numbers.")
        self.assertIsNone(c["doing"])

    def test_the_events_own_app_and_doing_are_never_shown(self):
        self.today()
        page = self.view()
        self.assertNotIn("STALE", repr(page))

    def test_a_forgotten_thing_is_gone_from_every_mode_and_from_search(self):
        self.today()
        del self.items["a"]
        for mode in ("timeline", "projects", "apps"):
            self.assertNotIn("a", [r["id"] for r in self.rows(self.view(mode=mode))])
        self.assertEqual(self.rows(self.view(q="deck")), [])

    def test_after_everything_is_deleted_history_is_empty_and_says_so(self):
        self.today()
        page = self.view(items={})
        self.assertEqual((page["groups"], page["empty"]), ([], hm.EMPTY))
        self.assertEqual(self.view(items={}, scope="week")["empty"], hm.EMPTY)

    def test_an_app_you_never_remember_is_hidden_in_the_timeline_and_search(self):
        self.today()
        skip = [{"id": "com.apple.Numbers", "name": "Numbers"}]
        self.assertEqual([r["id"] for r in self.rows(self.view(skip=skip))], ["a", "c"])
        self.assertEqual(self.rows(self.view(skip=skip, q="budget")), [])
        self.assertEqual([r["id"] for r in self.rows(self.view(skip=skip, mode="apps"))], ["c", "a"])
        self.assertEqual([r["id"] for r in self.rows(self.view(q="budget"))], ["b"])

    def test_the_clock_going_backwards_means_midnight_passed(self):
        self.session("20261121-233000", [stretch("a", "23:40:00", "23:55:00", 900), stretch("b", "00:10:00", "00:20:00", 600)])
        yesterday = self.view(scope="yesterday")
        today = self.view()
        self.assertEqual([r["id"] for r in self.rows(yesterday)], ["a"])
        self.assertEqual([r["id"] for r in self.rows(today)], ["b"])

    def test_a_long_session_still_places_a_day_it_did_not_start_on(self):
        self.session("20261120-220000", [stretch("a", "22:10:00", "23:50:00", 600), stretch("b", "00:10:00", "00:30:00", 600),
                                         stretch("c", "23:50:00", "23:59:00", 300)])
        got = self.view(scope="day", day="2026-11-21")
        self.assertEqual([r["id"] for r in self.rows(got)], ["b", "c"])

    def test_yesterday_this_week_and_a_chosen_day(self):
        self.today()
        self.session("20261121-100000", [stretch("b", "10:00:00", "10:30:00", 1800)])
        self.session("20261118-100000", [stretch("c", "10:00:00", "10:30:00", 1800)])
        self.assertEqual([r["id"] for r in self.rows(self.view(scope="yesterday"))], ["b"])
        week = self.view(scope="week")
        self.assertEqual([g["title"] for g in week["groups"]], ["Today", "Yesterday", "Wed 18 Nov"])
        self.assertEqual(self.rows(self.view(scope="day", day="2026-11-18"))[0]["id"], "c")
        self.assertEqual(self.view(scope="day", day="2026-11-10")["empty"], hm.NO_DAY)

    def test_earlier_pages_seven_days_at_a_time_and_skips_empty_days(self):
        for n in range(1, 12):
            self.session(f"202610{n:02d}-100000", [stretch("a", "10:00:00", "10:30:00", 1800)])
        self.session("20261001-120000", [stretch("gone", "12:00:00", "12:30:00", 1800)])
        first = self.view(scope="earlier")
        self.assertEqual(len(first["groups"]), 7)
        self.assertTrue(first["more"])
        self.assertEqual(first["groups"][0]["title"], "Sun 11 Oct")
        more = self.view(scope="earlier", n=14)
        self.assertEqual(len(more["groups"]), 11)
        self.assertFalse(more["more"])

    def test_projects_and_apps_group_and_keep_time_order(self):
        self.today()
        by_project = self.view(mode="projects")
        self.assertEqual([g["title"] for g in by_project["groups"]], ["Work › Q3 plan", "No project"])
        self.assertEqual([r["id"] for r in by_project["groups"][0]["rows"]], ["a", "b"])
        by_app = self.view(mode="apps")
        self.assertEqual(sorted(g["title"] for g in by_app["groups"]), ["Keynote", "Notes", "Numbers"])
        self.assertEqual(by_app["groups"][0]["title"], "Notes")

    def test_in_a_week_each_day_reads_top_to_bottom_and_the_newest_day_comes_first(self):
        self.today()
        self.session("20261121-100000", [stretch("a", "10:00:00", "10:30:00", 1800), stretch("b", "11:00:00", "11:30:00", 1800)])
        rows = self.view(scope="week", mode="projects")["groups"][0]["rows"]
        self.assertEqual([r["day_label"] for r in rows], ["Today", "Today", "Yesterday", "Yesterday"])
        self.assertEqual([r["id"] for r in rows], ["a", "b", "a", "b"])

    def test_the_summary_line_is_built_only_from_stored_seconds(self):
        self.today()
        self.assertEqual(self.view()["summary"], "44m · 3 things · 3 apps")

    def test_the_running_session_shows_before_it_is_saved(self):
        live = ("20261122-130000", [stretch("c", "13:00:00", "13:10:00", 600)])
        page = self.view(live=live)
        self.assertEqual([r["id"] for r in self.rows(page)], ["c"])
        self.session("20261122-130000", [stretch("a", "13:00:00", "13:05:00", 300)])
        self.assertEqual([r["id"] for r in self.rows(self.view(live=live))], ["c"])

    def test_an_unreadable_session_is_skipped_and_the_rest_show(self):
        self.today()
        with open(os.path.join(self.dir, "20261122-080000.json"), "w") as fh:
            fh.write("{not json")
        with open(os.path.join(self.dir, "20261122-070000.json"), "w") as fh:
            fh.write("[1, 2]")
        self.assertEqual(len(self.rows(self.view())), 3)

    def test_no_sessions_at_all_says_nothing_remembered_yet(self):
        page = self.view()
        self.assertEqual((page["groups"], page["empty"]), ([], hm.EMPTY))
        self.assertEqual(hm.view(self.reg, self.items, self.state(), NOW, sessions_dir=os.path.join(self.dir, "missing"))["empty"], hm.EMPTY)

    def test_a_day_with_sessions_but_nothing_left_says_so(self):
        self.session("20261122-090000", [stretch("gone", "09:00:00", "09:30:00", 1800)])
        self.assertEqual(self.view()["empty"], hm.NO_DAY)

    def test_only_the_files_for_the_day_shown_are_read(self):
        for n in range(1, 30):
            self.session(f"202610{n:02d}-100000", [stretch("a", "10:00:00", "10:30:00", 1800)])
        self.session("20261122-090000", [stretch("a", "09:00:00", "09:30:00", 1800)])
        seen = []
        real = hm._load
        with mock.patch.object(hm, "_load", side_effect=lambda p: seen.append(os.path.basename(p)) or real(p)):
            self.view()
        self.assertEqual(seen, ["20261029-100000.json", "20261122-090000.json"])

    def test_a_file_is_parsed_again_only_when_it_changes(self):
        self.today()
        self.view()
        with mock.patch("builtins.open", side_effect=AssertionError("re-read")):
            self.assertEqual(len(self.rows(self.view())), 3)
        self.session("20261122-090000", [stretch("c", "09:00:00", "09:30:00", 1800)])
        self.assertEqual([r["id"] for r in self.rows(self.view())], ["c"])

    def test_retention_expired_evidence_just_is_not_there(self):
        self.items["a"]["screenshot"] = "gone.jpg"
        self.items["a"]["content"] = None
        page = pm.thing_page(self.reg, self.items["a"], NOW, shots_dir=self.dir)
        self.assertIsNone(page["shot"])
        self.assertIsNone(page["screen"])
        self.today()
        self.assertEqual(len(self.rows(self.view())), 3)


class Search(Base):
    def setUp(self):
        super().setUp()
        self.items["a"]["notes"] = [{"at": "2026-11-20T10:00:00", "text": "Send to Zoë", "status": "done"}]
        self.items["b"]["content"] = {"excerpts": [{"id": "1", "text": "Revenue grew in the quarter"}]}
        self.items["c"]["content"] = "Tomato seedlings"
        self.items["c"]["last_seen"] = "2026-10-01T10:00:00"
        self.reg["projects"][self.q3]["aliases"] = ["Old Quarterly"]

    def ids(self, q, **hist):
        return [r["id"] for r in self.rows(self.view(q=q, **hist))]

    def test_it_matches_titles_projects_aliases_notes_and_kept_text_across_all_days(self):
        self.assertEqual(self.ids("budget"), ["b"])
        self.assertEqual(self.ids("work"), ["a", "b"])
        self.assertEqual(self.ids("quarterly"), ["a", "b"])
        self.assertEqual(self.ids("zoe"), ["a"])
        self.assertEqual(self.ids("revenue"), ["b"])
        self.assertEqual(self.ids("tomato"), ["c"])

    def test_case_and_accents_are_ignored(self):
        self.assertEqual(self.ids("ZOË"), ["a"])
        self.assertEqual(self.ids("zoe"), self.ids("ZOË"))

    def test_results_are_newest_first_and_carry_a_last_seen_stamp(self):
        rows = self.rows(self.view(q="e"))
        self.assertEqual(rows[0]["id"], "a")
        self.assertEqual(rows[-1]["when"], "1 Oct 10:00")

    def test_nothing_found_says_so(self):
        page = self.view(q="zzzz")
        self.assertEqual((page["groups"], page["empty"]), ([], hm.NONE_FOUND))

    def test_results_page_by_fifty(self):
        many = {f"t{n}": thing(f"t{n}", f"Report {n}") for n in range(120)}
        page = self.view(items=many, q="report")
        self.assertEqual((len(self.rows(page)), page["more"]), (50, 70))
        self.assertEqual(len(self.rows(self.view(items=many, q="report", lim=150))), 120)

    def test_the_search_code_uses_no_network_or_model(self):
        with open(hm.__file__) as fh:
            source = fh.read()
        for word in ("urllib", "requests", "socket", "narrator", "embedding", "semantic"):
            self.assertNotIn(word, source)


def mom(item, vid, a, b, seq=0):
    """A stored moment record; a and b are 'HH:MM:SS' on NOW's day."""
    t = lambda c: datetime.strptime("2026-11-22 " + c, "%Y-%m-%d %H:%M:%S").timestamp()
    return {"item_id": item, "visit_id": vid, "seq": seq, "t_start": t(a), "t_end": t(b)}


class MomentFeedTests(Base):
    def test_a_visit_is_one_entry_however_many_moments_it_has(self):
        recs = [mom("a", "v1", "09:00:00", "09:10:00"), mom("a", "v1", "09:10:00", "09:25:00", 1)]
        row, = self.rows(self.view(moments=recs))
        self.assertEqual((row["id"], row["span"], row["seconds"]), ("a", "09:00–09:25", 1500))

    def test_a_pass_through_tab_is_not_work_history(self):
        recs = [mom("a", "v1", "09:00:00", "09:10:00"), mom("b", "v2", "09:10:00", "09:10:08"),
                mom("a", "v3", "09:10:08", "09:20:00")]
        page = self.view(moments=recs)
        self.assertEqual([r["id"] for r in self.rows(page)], ["a"])          # the bounce is gone, and a before and after it is one entry
        self.assertEqual(self.rows(page)[0]["span"], "09:00–09:20")

    def test_moments_replace_the_session_stretches_from_their_start_but_older_ones_stay(self):
        self.session("20261122-080000", [stretch("c", "08:00:00", "08:30:00", 1800), stretch("b", "09:05:00", "09:05:05", 5),
                                         stretch("c", "09:05:05", "09:06:00", 55)])
        recs = [mom("a", "v1", "09:00:00", "09:10:00")]
        self.assertEqual([r["id"] for r in self.rows(self.view(moments=recs))], ["c", "a"])

    def test_the_running_sessions_tab_switches_are_not_shown_once_moments_exist(self):
        live = ("20261122-090000", [stretch("b", "09:05:00", "09:05:05", 5)])
        recs = [mom("a", "v1", "09:00:00", "09:10:00")]
        self.assertEqual([r["id"] for r in self.rows(self.view(moments=recs, live=live))], ["a"])

    def test_a_moment_for_a_forgotten_thing_or_a_never_remember_app_is_hidden(self):
        recs = [mom("zz", "v1", "09:00:00", "09:10:00"), mom("c", "v2", "09:10:00", "09:20:00")]
        self.assertEqual([r["id"] for r in self.rows(self.view(moments=recs))], ["c"])
        self.assertEqual(self.rows(self.view(moments=recs, skip=[{"name": "Notes"}])), [])

    def test_moments_alone_are_enough_for_a_page_and_a_day_jump(self):
        page = self.view(moments=[mom("a", "v1", "09:00:00", "09:10:00")])
        self.assertIsNone(page["empty"])
        self.assertEqual(page["jump"][0]["day"], "2026-11-22")

    def test_malformed_records_are_skipped(self):
        recs = [{"item_id": "a"}, {"item_id": 3, "visit_id": "v", "t_start": 1, "t_end": 99}, mom("a", "v1", "09:00:00", "09:10:00")]
        self.assertEqual([r["id"] for r in self.rows(self.view(moments=recs))], ["a"])

    def test_no_moments_leaves_the_session_feed_as_it_was(self):
        self.today()
        self.assertEqual(len(self.rows(self.view())), 3)


if __name__ == "__main__":
    unittest.main()

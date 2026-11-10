"""The project page as data (page_model.py): the tree, pick up, things, needs you and search."""

import unittest
from datetime import datetime, timedelta

import page_model as pm
import projects

NOW = datetime(2026, 10, 9, 15, 0)


def when(**delta):
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


def thing(i, title, app="Docs", project=None, seen=None, notes=(), also=(), done=()):
    item = {"id": i, "app": app, "doing": "Working", "title": title, "last_seen": seen or when(hours=1),
            "notes": [{"at": when(days=1, minutes=n), "text": t} for n, t in enumerate(notes)]}
    if project:
        item["project_id"] = project
    if also:
        item["also_in"] = list(also)
    return item


def world():
    reg = projects.empty()
    ids = {}
    ids["work"] = projects.create(reg, "Work")
    ids["q3"] = projects.create(reg, "Q3 plan", ids["work"])
    ids["budget"] = projects.create(reg, "Budget", ids["q3"])
    ids["home"] = projects.create(reg, "Home")
    items = {}
    for t in (thing("a", "Q3 deck", project=ids["q3"], seen=when(hours=2), notes=["send to Sam"]),
              thing("b", "Budget sheet", "Numbers", project=ids["budget"], seen=when(hours=1), also=[ids["home"]]),
              thing("c", "Old memo", project=ids["work"], seen=when(days=20)),
              thing("d", "Garden plan", project=ids["home"], seen=when(days=2), notes=["buy soil", "call Lee"]),
              thing("e", "Loose end", seen=when(days=3))):
        items[t["id"]] = t
    return reg, items, ids


def view(state=None, w=None):
    reg, items, ids = w or world()
    return pm.view(reg, items, state or pm.new_state(), NOW), reg, items, ids


class WordsTests(unittest.TestCase):
    def test_ago(self):
        for iso, text in ((when(seconds=10), "Just now"), (when(minutes=12), "12 min ago"), (when(hours=3), "3 h ago"),
                          (when(days=1, hours=1), "Yesterday"), (when(days=3), "3 days ago"), ("2026-10-01T09:00:00", "Oct 1"),
                          ("2025-03-02T09:00:00", "Mar 2, 2025"), (None, "")):
            self.assertEqual(pm.ago(iso, NOW), text, iso)

    def test_matching_ignores_case_and_accents(self):
        self.assertEqual(pm.fold("Café PLAN"), pm.fold("cafe plan"))


class IconTests(unittest.TestCase):
    def test_a_projects_colour_is_stable_and_one_of_eight(self):
        self.assertEqual({pm.hue(f"p{n}") for n in range(200)}, set(range(pm.HUES)))
        self.assertEqual(pm.hue("p7"), pm.hue("p7"))

    def test_every_place_a_project_is_drawn_carries_its_colour(self):
        v, reg, items, ids = view()
        self.assertTrue(all("hue" in r for r in v["side"]["tree"] if r["id"]))
        self.assertTrue(all("hue" in c for c in v["main"]["cards"]))
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", ids["work"]), NOW)["main"]
        self.assertEqual(page["hue"], pm.hue(ids["work"]))
        self.assertTrue(all("hue" in s for s in page["subs"]))
        found = pm.view(reg, items, pm.press(reg, pm.new_state(), "search", "plan"), NOW)["main"]
        self.assertTrue(all("hue" in p for p in found["projects"]))

    def test_cards_and_sub_projects_say_how_many_sub_projects_they_hold(self):
        v, reg, items, ids = view()
        self.assertEqual(v["main"]["cards"][0]["line"], "3 things · 1 sub-project · 1 open")


class TreeTests(unittest.TestCase):
    def test_the_tree_opens_and_closes_on_its_own(self):
        reg, items, ids = world()
        state = pm.new_state()
        names = lambda v: [(r["name"], r["level"]) for r in v["side"]["tree"]]
        self.assertEqual(names(pm.view(reg, items, state, NOW)), [("Work", 0), ("Home", 0)])
        state = pm.press(reg, state, "toggle", ids["work"])
        self.assertEqual(names(pm.view(reg, items, state, NOW)), [("Work", 0), ("Q3 plan", 1), ("Home", 0)])
        state = pm.press(reg, state, "toggle", ids["work"])
        self.assertEqual(len(pm.view(reg, items, state, NOW)["side"]["tree"]), 2)

    def test_going_to_a_deep_project_opens_the_way_to_it(self):
        reg, items, ids = world()
        state = pm.press(reg, pm.new_state(), "go", ids["budget"])
        rows = pm.view(reg, items, state, NOW)["side"]["tree"]
        self.assertEqual([r["name"] for r in rows], ["Work", "Q3 plan", "Budget", "Home"])
        self.assertEqual([r["name"] for r in rows if r["selected"]], ["Budget"])

    def test_counts_roll_up_by_main_project_only(self):
        v, reg, items, ids = view()
        work = next(r for r in v["side"]["tree"] if r["name"] == "Work")
        self.assertEqual((work["count"], work["open"]), (3, 1))             # a, b, c; b is also in Home but counts once
        home = next(r for r in v["side"]["tree"] if r["name"] == "Home")
        self.assertEqual((home["count"], home["open"]), (1, 2))

    def test_archived_projects_and_what_is_inside_them_are_not_there(self):
        reg, items, ids = world()
        projects.archive(reg, ids["q3"])
        state = pm.press(reg, pm.new_state(), "toggle", ids["work"])
        self.assertEqual([r["name"] for r in pm.view(reg, items, state, NOW)["side"]["tree"]], ["Work", "Home", "Archived"])

    def test_each_level_pages_itself_and_nothing_is_dropped(self):
        reg = projects.empty()
        for n in range(30):
            projects.create(reg, f"P{n:02}")
        state = pm.new_state()
        rows = pm.tree_rows(reg, state, projects.counts(reg, {}))
        self.assertEqual(len(rows), pm.SIDE_LIMIT + 1)
        self.assertEqual(rows[-1]["name"], "Show 8 more of 22")
        state = pm.press(reg, state, "more_side", rows[-1]["more"])
        rows = pm.tree_rows(reg, state, projects.counts(reg, {}))
        self.assertEqual((len(rows), rows[-1]["name"]), (17, "Show 8 more of 14"))
        for _ in range(3):
            state = pm.press(reg, state, "more_side", "top")
        self.assertEqual(len(pm.tree_rows(reg, state, projects.counts(reg, {}))), 30)

    def test_ten_thousand_projects_stay_quick(self):
        import time
        reg = projects.empty()
        for n in range(10_000):                                    # built directly: create() checks names one by one
            reg["projects"][f"p{n}"] = {"id": f"p{n}", "name": f"P{n}", "parent": f"p{n // 10}" if n >= 10 else None,
                                        "archived": False, "aliases": [], "created": ""}
        reg["next"] = 10_000
        state = pm.press(reg, pm.new_state(), "go", "p9999")
        began = time.time()
        pm.tree_rows(reg, state, projects.counts(reg, {}))
        self.assertLess(time.time() - began, 2.0)


class ProjectPageTests(unittest.TestCase):
    def page(self, project, **state):
        reg, items, ids = world()
        s = pm.press(reg, pm.new_state(), "go", ids[project])
        s.update(state)
        return pm.view(reg, items, s, NOW)["main"], ids

    def test_pick_up_is_the_things_with_open_notes_newest_first(self):
        page, _ = self.page("home")
        self.assertEqual([r["title"] for r in page["pick_up"]], ["Garden plan"])
        self.assertEqual(page["pick_up"][0]["notes"], ["buy soil", "call Lee"])
        work, _ = self.page("work")
        self.assertEqual([r["title"] for r in work["pick_up"]], ["Q3 deck"])         # found inside a sub-project

    def test_nothing_open_says_all_caught_up_and_the_last_thing(self):
        page, _ = self.page("budget")
        self.assertEqual(page["pick_up"], [])
        self.assertEqual(page["caught_up"]["title"], "All caught up")
        self.assertIn("Last worked on: Budget sheet, 1 h ago.", page["caught_up"]["line"])

    def test_an_empty_project_says_so_and_has_no_gaps(self):
        reg, items, ids = world()
        empty = projects.create(reg, "Empty")
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", empty), NOW)["main"]
        self.assertEqual(page["empty"]["title"], "Nothing here yet")
        self.assertNotIn("pick_up", page)

    def test_crumbs_and_meta(self):
        page, ids = self.page("budget")
        self.assertEqual([c["name"] for c in page["crumbs"]], ["Work", "Q3 plan", "Budget"])
        work, _ = self.page("work")
        self.assertEqual(work["meta"], "1 sub-project · 3 things · 1 open")

    def test_a_long_path_keeps_its_first_and_last_two(self):
        reg = projects.empty()
        parent = None
        for n in range(8):
            parent = projects.create(reg, f"L{n}", parent)
        chain = pm.crumbs(reg, parent)
        self.assertEqual([c["name"] for c in chain], ["L0", "…", "L6", "L7"])
        self.assertIn("L1 › L2 › L3 › L4 › L5", chain[1]["title"])

    def test_things_are_grouped_by_when_and_the_switch_changes_what_is_in_them(self):
        page, _ = self.page("work")
        self.assertEqual([(g["title"], [t["title"] for t in g["things"]]) for g in page["groups"]],
                         [("Today", ["Budget sheet", "Q3 deck"]), ("Earlier", ["Old memo"])])
        only, _ = self.page("work", deep=False)
        self.assertEqual([t["title"] for g in only["groups"] for t in g["things"]], ["Old memo"])
        self.assertEqual(only["deep"], {"on": False, "show": True})

    def test_a_thing_says_where_it_lives_only_when_it_is_not_here(self):
        page, _ = self.page("work")
        rows = {t["title"]: t for g in page["groups"] for t in g["things"]}
        self.assertEqual(rows["Old memo"]["sub"], "Docs")
        self.assertEqual(rows["Q3 deck"]["sub"], "Docs · Work › Q3 plan")
        self.assertEqual(rows["Budget sheet"]["sub"], "Numbers · Work › Q3 plan › Budget · also in Home")

    def test_filters_are_the_apps_that_are_really_there(self):
        page, _ = self.page("work")
        self.assertEqual(page["filters"], [{"app": "Docs", "count": 2}, {"app": "Numbers", "count": 1}])
        filtered, _ = self.page("work", app="Numbers")
        self.assertEqual([t["title"] for g in filtered["groups"] for t in g["things"]], ["Budget sheet"])
        self.assertEqual(filtered["total"], 1)

    def test_long_lists_say_how_many_are_left(self):
        reg = projects.empty()
        pid = projects.create(reg, "Big")
        items = {f"i{n}": thing(f"i{n}", f"T{n}", project=pid, seen=when(minutes=n)) for n in range(45)}
        state = pm.press(reg, pm.new_state(), "go", pid)
        page = pm.view(reg, items, state, NOW)["main"]
        self.assertEqual((sum(len(g["things"]) for g in page["groups"]), page["things_more"]), (20, 25))
        state = pm.press(reg, state, "more")
        page = pm.view(reg, items, state, NOW)["main"]
        self.assertEqual((sum(len(g["things"]) for g in page["groups"]), page["things_more"]), (40, 5))

    def test_sub_projects_page_too(self):
        reg = projects.empty()
        top = projects.create(reg, "Top")
        for n in range(10):
            projects.create(reg, f"S{n}", top)
        state = pm.press(reg, pm.new_state(), "go", top)
        page = pm.view(reg, {}, state, NOW)["main"]
        self.assertEqual((len(page["subs"]), page["subs_more"]), (6, 4))


class HomeAndNeedsTests(unittest.TestCase):
    def test_home_lists_top_level_projects_and_needs_you(self):
        v, reg, items, ids = view()
        main = v["main"]
        self.assertEqual((main["kind"], main["title"]), ("home", "All projects"))
        self.assertEqual([c["name"] for c in main["cards"]], ["Work", "Home"])
        self.assertEqual(main["needs"]["count"], 2)
        self.assertEqual([r["title"] for r in main["needs"]["rows"]], ["Q3 deck", "Garden plan"])
        self.assertEqual(main["unplaced"], 1)
        self.assertIsNone(main["empty"])

    def test_needs_you_sees_everything_with_an_open_note_anywhere(self):
        reg, items, ids = world()
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "needs"), NOW)["main"]
        self.assertEqual((page["title"], page["meta"]), ("Needs you", "2 things with open notes"))

    def test_first_run_and_no_projects_yet(self):
        v = pm.view(projects.empty(), {}, pm.new_state(), NOW)["main"]
        self.assertEqual(v["empty"]["title"], "Nothing here yet")
        v = pm.view(projects.empty(), {"x": thing("x", "A")}, pm.new_state(), NOW)["main"]
        self.assertEqual(v["empty"]["title"], "No projects yet")

    def test_a_project_that_was_deleted_falls_back_to_home(self):
        reg, items, ids = world()
        state = pm.press(reg, pm.new_state(), "go", ids["home"])
        projects.delete(reg, items, ids["home"], forget=False)
        self.assertEqual(pm.press(reg, state, "go", ids["home"])["view"], "home")
        self.assertEqual(pm.view(reg, items, state, NOW)["main"]["kind"], "home")


class ThingPageTests(unittest.TestCase):
    def open(self, tid, w=None):
        reg, items, ids = w or world()
        state = pm.press(reg, pm.press(reg, pm.new_state(), "go", ids["home"]), "thing", tid, items)
        return pm.view(reg, items, state, NOW)["main"], state, (reg, items, ids)

    def test_a_note_opens_its_thing_in_full(self):
        page, state, _ = self.open("d")
        self.assertEqual((page["kind"], page["title"], page["app"]), ("thing", "Garden plan", "Docs"))
        self.assertEqual([n["text"] for n in page["open"]], ["buy soil", "call Lee"])
        self.assertEqual(page["places"][0]["name"], "Home")
        self.assertTrue(page["places"][0]["main"])
        self.assertIn(("Visits", "0"), page["stats"])

    def test_the_page_leads_with_what_you_were_doing_and_shows_the_address_once(self):
        reg, items, ids = world()
        items["d"].update(doing="Searching domains", state={"url": "https://x.test/a", "page": "Garden"}, last_seen=NOW.isoformat(),
                          summary={"title": "Checking free .com names", "intent": "Pick a name", "of": "x", "model": "m", "v": 2})
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "d", items), NOW)["main"]
        self.assertEqual((page["headline"], page["intent"], page["reopen"]), ("Checking free .com names", "Pick a name", "https://x.test/a"))
        self.assertEqual(page["address"], "https://x.test/a")
        self.assertTrue(page["observed"].startswith("You were on x.test."))
        self.assertIn("You left yourself two reminders.", page["observed"])               # built from stored facts only
        self.assertNotIn("https://x.test/a", [v for _, v in page["latest"]])        # not repeated in the facts
        self.assertIn(("Page", "Garden"), page["latest"])

    def test_without_an_ai_line_the_stored_label_leads_and_nothing_is_invented(self):
        reg, items, ids = world()
        items["d"]["doing"] = "Editing Garden plan"
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "d", items), NOW)["main"]
        self.assertEqual((page["headline"], page["intent"], page["shot"]), ("Editing Garden plan", None, None))

    def test_a_kept_screenshot_is_offered_only_when_the_file_exists(self):
        import tempfile, os
        reg, items, ids = world()
        items["d"]["screenshot"] = "t.jpg"
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pm.thing_page(reg, items["d"], NOW, shots_dir=d)["shot"])
            open(os.path.join(d, "t.jpg"), "wb").close()
            self.assertEqual(pm.thing_page(reg, items["d"], NOW, shots_dir=d)["shot"], os.path.join(d, "t.jpg"))

    def test_finished_notes_are_listed_apart(self):
        reg, items, ids = world()
        n = items["d"]["notes"][0]
        items["d"]["notes_done"] = {__import__("notes").note_id(n): NOW.isoformat()}
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "d", items), NOW)["main"]
        self.assertEqual(([x["text"] for x in page["open"]], [x["text"] for x in page["done"]]), (["call Lee"], ["buy soil"]))

    def test_it_shows_what_lmemm_read_as_short_passages(self):
        reg, items, ids = world()
        text = "\n".join(f"Line {n} of the Q3 plan" for n in range(10)) + "\n\nLine 0 of the Q3 plan"
        items["a"]["content"] = {"version": 1, "excerpts": [
            {"id": "old", "text": "Older passage here", "last_seen": when(days=2), "source": {"app": "Docs"}},
            {"id": "new", "text": text, "last_seen": when(hours=1), "source": {"window": "Q3 deck", "app": "Docs"},
             "decision_quotes": ["We agreed on the Q3 numbers."]}]}
        items["a"]["state"] = {"to": "Sam", "subject": "Numbers"}
        state = pm.press(reg, pm.new_state(), "thing", "a", items)
        page = pm.view(reg, items, state, NOW)["main"]
        self.assertEqual(page["latest"], [("To", "Sam"), ("Subject", "Numbers")])
        card = page["screen"]
        self.assertEqual((card["place"], card["when"], card["count"]), ("Q3 deck · Docs", "1 h ago", 2))   # newest on top, one card
        self.assertEqual(card["decisions"], ["We agreed on the Q3 numbers."])
        new, old = card["versions"]                                                                         # the viewer's dated tabs
        self.assertEqual((new["place"], len(new["lines"])), (None, 10))                                    # place shown once; repeats dropped
        self.assertEqual((old["place"], old["when"], old["lines"]), ("Docs", "2 days ago", ["Older passage here"]))

    def test_a_ticked_note_stays_crossed_out_until_its_hold_is_over(self):
        reg, items, ids = world()
        import notes
        nid = notes.note_id(items["d"]["notes"][0])
        notes.set_done(items, [nid])
        state = pm.press(reg, pm.new_state(), "thing", "d", items)
        held = pm.view(reg, items, state, NOW, fading={nid})["main"]
        self.assertEqual([(n["text"], n["done"]) for n in held["open"]], [("buy soil", True), ("call Lee", False)])
        self.assertEqual((held["open_count"], held["done"]), (1, []))
        folded = pm.view(reg, items, state, NOW)["main"]
        self.assertEqual([n["text"] for n in folded["open"]], ["call Lee"])
        self.assertEqual([(n["id"], n["text"]) for n in folded["done"]], [(nid, "buy soil")])

    def test_a_passage_says_where_it_was_and_what_it_was_without_the_raw_text(self):
        src = {"url": "https://www.godaddy.com/domainsearch/find?q=trace", "app": "Chrome", "window": "Domain search"}
        self.assertEqual(pm.place_of(src), "godaddy.com/domainsearch/find?q=trace")                        # the page address
        self.assertEqual(pm.place_of({"app": "VS Code", "window": "lmemm.py — LMemM"}), "lmemm.py — LMemM · VS Code")
        self.assertEqual(pm.place_of({"app": "Notes", "window": "Notes"}), "Notes")
        self.assertEqual(pm.place_of({}), "Screen")
        content = {"excerpts": [{"id": "a", "text": "Istyronly with 3 yrtorm O", "source": src}, {"id": "b", "text": "older words"}]}
        card = pm.screen_of(content, NOW, summary="Searching for a domain")
        self.assertEqual((card["place"], card["summary"], card["url"]), ("Screen", "Searching for a domain", None))   # newest has no url
        self.assertEqual(pm.screen_of(content, NOW)["summary"], None)                                       # no sentence yet: nothing invented
        self.assertEqual(pm.screen_of({"excerpts": [{"id": "x", "text": "t", "source": src}]}, NOW)["url"], src["url"])
        self.assertIsNone(pm.screen_of({"excerpts": [{"id": "x", "text": "t", "source": {"url": "file:///etc/hosts"}}]}, NOW)["url"])

    def test_versions_with_the_same_age_still_have_different_tab_labels(self):
        self.assertEqual(pm.stamp(when(minutes=47), NOW), "Today 14:13")
        self.assertEqual(pm.stamp(when(days=1), NOW), "Yesterday 15:00")
        self.assertEqual(pm.stamp(when(days=2, hours=1), NOW), "Wed 14:00")
        self.assertEqual(pm.stamp(when(days=20), NOW), "19 Sep 15:00")
        self.assertEqual(pm.stamp("not a time", NOW), "")
        content = {"excerpts": [{"id": str(n), "text": f"t{n}", "last_seen": when(days=2, minutes=n * 7)} for n in range(3)]}
        labels = [v["label"] for v in pm.screen_of(content, NOW)["versions"]]
        self.assertEqual(len(set(labels)), 3, labels)                                                       # "2 days ago" x3 are now told apart

    def test_the_text_it_read_survives_every_shape_memory_has_had(self):
        now = NOW
        self.assertEqual(pm.screen_of("plain words", now)["versions"][0]["lines"], ["plain words"])
        for odd in (None, {}, {"excerpts": []}, {"excerpts": [None, {"text": 5}, {"text": "  "}]}, 5, []):
            self.assertIsNone(pm.screen_of(odd, now))
        self.assertEqual(pm.screen_of({"excerpts": [{"id": str(n), "text": f"t{n}"} for n in range(9)]}, now)["count"], pm.VERSIONS_SHOWN)

    def test_odd_stored_values_never_break_a_page(self):
        reg, items, ids = world()
        items["a"].update(state={"x": {"deep": 1}, "y": None, "z": 5}, notes=[{"at": None, "text": "hm"}], seconds=None, visits=None)
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "a", items), NOW)["main"]
        self.assertEqual(page["open"][0]["text"], "hm")

    def test_it_lists_every_project_a_thing_is_in(self):
        page, _, (reg, items, ids) = self.open("b")
        self.assertEqual([(p["name"], p["main"]) for p in page["places"]], [("Budget", True), ("Home", False)])

    def test_back_returns_to_the_page_you_came_from(self):
        page, state, (reg, items, ids) = self.open("d")
        again = pm.press(reg, state, "thing", "a", items)               # opening another keeps the first way back
        back = pm.press(reg, again, "back", None, items)
        self.assertEqual((back["view"], back["pid"], back["tid"], back["back"]), ("project", ids["home"], None, None))
        searched = pm.press(reg, pm.press(reg, pm.new_state(), "search", "soil"), "thing", "d", items)
        self.assertEqual(pm.press(reg, searched, "back", None, items)["q"], "soil")

    def test_a_thing_that_is_gone_or_never_was_does_not_break_the_page(self):
        reg, items, ids = world()
        state = pm.press(reg, pm.new_state(), "thing", "nope", items)
        self.assertEqual(state["view"], "home")
        state = pm.press(reg, pm.new_state(), "thing", "a", items)
        del items["a"]
        self.assertEqual(pm.view(reg, items, state, NOW)["main"]["kind"], "home")
        self.assertEqual(pm.press(reg, pm.new_state(), "back", None, items)["view"], "home")


class NotesInTheWindowTests(unittest.TestCase):
    """Ticking and adding use the pill's own functions, so the two always agree."""

    def test_a_note_added_to_a_thing_is_on_its_page_and_ticking_it_moves_it(self):
        import notes
        reg, items, ids = world()
        item = notes.record(items, {}, [], ("item", "e"), "ring the printer", when(minutes=1))
        self.assertEqual(item["id"], "e")
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "e", items), NOW)["main"]
        self.assertEqual([n["text"] for n in page["open"]], ["ring the printer"])
        nid = page["open"][0]["id"]
        self.assertEqual(notes.set_done(items, [nid]), [nid])
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "e", items), NOW)["main"]
        self.assertEqual(([n["text"] for n in page["open"]], [n["text"] for n in page["done"]]), ([], ["ring the printer"]))
        notes.set_done(items, [nid], done=False)                      # a tap on a finished note reopens it
        self.assertEqual(pm.view(reg, items, pm.press(reg, pm.new_state(), "thing", "e", items), NOW)["main"]["open_count"], 1)

    def test_adding_a_note_to_a_thing_that_is_gone_saves_nothing(self):
        import notes
        reg, items, ids = world()
        self.assertIsNone(notes.record(items, {}, [], ("item", "nope"), "x", when(minutes=1)))

    def test_the_tracker_gives_the_window_the_pills_own_functions(self):
        import ast
        import os
        with open(os.path.join(os.path.dirname(__file__), "..", "src", "tracker.py")) as fh:
            tree = ast.parse(fh.read())
        fns = {n.name: n for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("notes.set_done", ast.unparse(fns["widget_tick"]))
        add = ast.unparse(fns["window_add_note"])
        self.assertIn("notes.record", add)
        self.assertIn("('item', item_id)", add)
        self.assertIn("self.widget_tick, self.window_add_note", ast.unparse(fns["open_window"]))
        self.assertIn("main_window.tick()", ast.unparse(fns["tick"]))


class ArchivedTests(unittest.TestCase):
    def test_archived_projects_wait_on_their_own_page_and_come_back_whole(self):
        reg, items, ids = world()
        projects.archive(reg, ids["q3"])
        projects.archive(reg, ids["budget"])                                   # already hidden by Q3 plan
        v = pm.view(reg, items, pm.press(reg, pm.new_state(), "archived"), NOW)
        self.assertEqual(v["side"]["archived"], 1)                             # one thing to bring back, not two
        page = v["main"]
        self.assertEqual((page["kind"], [(r["name"], r["where"]) for r in page["rows"]]), ("archived", [("Q3 plan", "Work")]))
        self.assertIsNone(page["empty"])
        projects.restore(reg, ids["q3"])
        again = pm.view(reg, items, pm.press(reg, pm.new_state(), "archived"), NOW)
        self.assertEqual(again["side"]["archived"], 1)                         # Budget was archived on its own
        self.assertEqual(again["main"]["rows"][0]["name"], "Budget")

    def test_the_tree_keeps_an_archived_folder_that_opens_to_the_projects(self):
        reg, items, ids = world()
        self.assertFalse([r for r in pm.view(reg, items, pm.new_state(), NOW)["side"]["tree"] if r.get("folder")])
        projects.archive(reg, ids["q3"])
        closed = pm.view(reg, items, pm.new_state(), NOW)["side"]["tree"]
        self.assertEqual([(r["name"], r["expanded"], r["count"]) for r in closed if r.get("folder")], [("Archived", False, 1)])
        state = pm.press(reg, pm.new_state(), "archived")                      # opening the folder also expands it
        tree = pm.view(reg, items, state, NOW)["side"]["tree"]
        folder = [r for r in tree if r.get("folder")][0]
        self.assertEqual((folder["expanded"], folder["selected"]), (True, True))
        self.assertEqual([r["name"] for r in tree if r.get("archived")], ["Q3 plan"])

    def test_an_archived_project_opens_and_says_it_is_archived(self):
        reg, items, ids = world()
        projects.archive(reg, ids["q3"])
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", ids["q3"]), NOW)["main"]
        self.assertEqual((page["kind"], page["title"], page["archived"]), ("project", "Q3 plan", True))
        live = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", ids["work"]), NOW)["main"]
        self.assertFalse(live["archived"])

    def test_nothing_archived_says_so(self):
        reg, items, ids = world()
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "archived"), NOW)["main"]
        self.assertEqual(page["empty"]["title"], "Nothing archived")


class SuggestionPageTests(unittest.TestCase):
    def sugg(self):
        import suggestions as sg
        reg, items, ids = world()
        sug = sg.empty()
        sg.offer_project(sug, items, "Trip", ["a", "e"])
        sg.offer_items(sug, reg, items, ids["home"], ["c", "e"])
        return reg, items, ids, sg.view(sug, reg, items, NOW)

    def test_the_first_page_lists_projects_to_make_and_projects_with_things_waiting(self):
        reg, items, ids, sugg = self.sugg()
        home = pm.view(reg, items, pm.new_state(), NOW, sugg=sugg)["main"]["suggest"]
        self.assertEqual((home["count"], [p["name"] for p in home["projects"]], [(i["name"], i["count"]) for i in home["items"]]),
                         (2, ["Trip"], [("Home", 2)]))
        self.assertIsNone(pm.view(reg, items, pm.new_state(), NOW)["main"]["suggest"])

    def test_a_project_page_carries_only_its_own_suggestions(self):
        reg, items, ids, sugg = self.sugg()
        page = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", ids["home"]), NOW, sugg=sugg)["main"]
        self.assertEqual([t["id"] for t in page["suggested"]["things"]], ["c", "e"])
        other = pm.view(reg, items, pm.press(reg, pm.new_state(), "go", ids["work"]), NOW, sugg=sugg)["main"]
        self.assertIsNone(other["suggested"])


class SearchTests(unittest.TestCase):
    def results(self, q):
        reg, items, ids = world()
        return pm.view(reg, items, pm.press(reg, pm.new_state(), "search", q), NOW)["main"]

    def test_it_finds_projects_things_and_open_notes(self):
        r = self.results("PLAN")
        self.assertEqual([p["name"] for p in r["projects"]], ["Q3 plan"])
        self.assertEqual([t["title"] for t in r["things"]], ["Garden plan"])
        self.assertEqual([n["text"] for n in self.results("soil")["notes"]], ["buy soil"])

    def test_nothing_matching_says_so(self):
        self.assertTrue(self.results("zzz")["none"])

    def test_an_old_name_from_a_merge_still_finds_the_project(self):
        reg, items, ids = world()
        projects.merge(reg, items, ids["budget"], ids["q3"])
        r = pm.view(reg, items, pm.press(reg, pm.new_state(), "search", "budget"), NOW)["main"]
        self.assertEqual([p["name"] for p in r["projects"]], ["Q3 plan"])

    def test_clearing_goes_back_to_where_you_were(self):
        reg, items, ids = world()
        state = pm.press(reg, pm.new_state(), "go", ids["home"])
        state = pm.press(reg, pm.press(reg, state, "search", "x"), "clear")
        self.assertEqual((state["view"], state["pid"]), ("project", ids["home"]))
        self.assertEqual(pm.press(reg, pm.new_state(), "search", "   ")["view"], "home")


class ReadOnlyTests(unittest.TestCase):
    def test_looking_never_changes_memory_or_the_tree(self):
        import copy
        reg, items, ids = world()
        before = copy.deepcopy((reg, items))
        state = pm.new_state()
        for action, arg in (("toggle", ids["work"]), ("go", ids["budget"]), ("deep", False), ("app", "Docs"),
                            ("more", None), ("needs", None), ("search", "plan"), ("clear", None), ("home", None)):
            state = pm.press(reg, state, action, arg)
            pm.view(reg, items, state, NOW)
        self.assertEqual((reg, items), before)


class WindowWiringTests(unittest.TestCase):
    """window.py needs a Mac to import, so what it asks for is checked from its source."""

    def test_every_press_the_window_sends_is_one_the_model_understands(self):
        import os
        import re
        with open(os.path.join(os.path.dirname(__file__), "..", "src", "window.py")) as fh:
            source = fh.read()
        sent = set(re.findall(r'self\.press\("([a-z_]+)"', source))
        self.assertTrue({"go", "home", "toggle", "deep", "app", "more", "more_subs", "more_side", "needs", "search", "clear", "thing", "back"} <= sent, sent)
        reg, _items, ids = world()
        for action in sent:
            pm.press(reg, pm.new_state(), action, ids["work"] if action in {"go", "toggle"} else "x")      # none raises
        with open(os.path.join(os.path.dirname(__file__), "..", "src", "page_model.py")) as fh:
            model = fh.read()
        for action in sent:
            self.assertIn(f'"{action}"', model, action)


if __name__ == "__main__":
    unittest.main()

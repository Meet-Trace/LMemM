"""Big memories stay quick (step 11): thousands of projects, tens of thousands of things."""

import random
import time
import unittest
from datetime import datetime

import page_model as pm
import projects

NOW = datetime(2026, 10, 9, 12, 0)


def big(n_projects=3000, n_items=15000, seed=7):
    rnd = random.Random(seed)
    reg = projects.empty()
    ids = []
    for i in range(n_projects):
        parent = rnd.choice(ids[:max(1, len(ids) // 3)]) if ids and i % 3 else None
        pid = f"p{i}"
        reg["projects"][pid] = {"id": pid, "name": f"Project {i}", "parent": parent, "archived": i % 50 == 7, "aliases": [], "created": "2026-01-01T00:00:00"}
        ids.append(pid)
    reg["next"] = n_projects + 1
    items = {}
    for i in range(n_items):
        main = rnd.choice(ids)
        item = {"id": f"i{i}", "app": "Docs", "doing": "Working", "title": f"Thing {i}", "last_seen": f"2026-10-0{1 + i % 9}T10:{i % 60:02d}:00",
                "project_id": main, "notes": []}
        if i % 7 == 0:
            item["also_in"] = [rnd.choice(ids)]
        items[item["id"]] = item
    return reg, items, ids


class IndexTests(unittest.TestCase):
    def test_the_lookup_table_gives_exactly_the_answers_the_scan_does(self):
        reg, items, ids = big(600, 3000)
        sample = ids[::37] + [None]
        scan_kids = {p: projects.children(reg, p) for p in sample}
        scan_members = {p: [i["id"] for i in projects.members(reg, items, p, deep=True)] for p in ids[::37]}
        scan_desc = {p: projects.descendants(reg, p) for p in ids[::37]}
        with projects.indexed(reg, items):
            self.assertEqual({p: projects.children(reg, p) for p in sample}, scan_kids)
            self.assertEqual({p: [i["id"] for i in projects.members(reg, items, p, deep=True)] for p in ids[::37]}, scan_members)
            self.assertEqual({p: projects.descendants(reg, p) for p in ids[::37]}, scan_desc)
            with projects.indexed(reg, items):                                  # nesting keeps the outer table
                self.assertEqual(projects.children(reg, None), scan_kids[None])
        self.assertIsNone(projects._INDEX["reg"])                                # and it is gone afterwards
        projects.move(reg, "p5", None)                                           # a change after the block is seen at once
        self.assertIn("p5", projects.children(reg, None))

    def test_the_table_is_gone_even_when_the_page_fails(self):
        reg, items, ids = big(50, 100)
        try:
            with projects.indexed(reg, items):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIsNone(projects._INDEX["reg"])


class SpeedTests(unittest.TestCase):
    """Generous limits: they only catch a return to scanning everything for every project."""

    @classmethod
    def setUpClass(cls):
        cls.reg, cls.items, cls.ids = big()

    def seconds(self, state):
        start = time.perf_counter()
        pm.view(self.reg, self.items, state, NOW)
        return time.perf_counter() - start

    def test_the_first_page_a_project_and_a_search_each_draw_quickly(self):
        state = pm.new_state()
        self.assertLess(self.seconds(state), 1.0, "first page")
        self.assertLess(self.seconds(pm.press(self.reg, state, "go", self.ids[3])), 1.0, "a project")
        self.assertLess(self.seconds(pm.press(self.reg, state, "search", "Project 12")), 1.0, "a search")

    def test_the_sidebar_never_draws_more_rows_than_it_pages_to(self):
        v = pm.view(self.reg, self.items, pm.new_state(), NOW)
        self.assertLessEqual(len(v["side"]["tree"]), pm.SIDE_LIMIT + 2)         # the eight first, "Show more", the Archived folder


class HistorySpeedTests(unittest.TestCase):
    def test_a_day_pages_and_search_stay_quick_with_a_thousand_sessions_and_fifty_thousand_things(self):
        import json
        import os
        import tempfile
        from datetime import timedelta
        import history_model as hm
        reg, items, _ids = big(200, 50000)
        with tempfile.TemporaryDirectory() as d:
            for n in range(1000):
                when = NOW - timedelta(days=n // 5, minutes=n)
                events = [{"from": "10:00:00", "to": "10:30:00", "seconds": 1800, "item": f"i{(n * 7 + k) % 50000}"} for k in range(5)]
                with open(os.path.join(d, when.strftime("%Y%m%d-%H%M%S") + ".json"), "w") as fh:
                    json.dump({"timeline": events}, fh)
            state = pm.new_state()
            for scope in ("today", "week", "earlier"):
                state["hist"]["scope"] = scope
                start = time.perf_counter()
                page = hm.view(reg, items, state, NOW, sessions_dir=d)
                self.assertLess(time.perf_counter() - start, 0.15, scope)
            state["hist"]["scope"] = "today"
            seen = []
            real = hm._load
            hm._CACHE.clear()
            try:
                hm._load = lambda path: seen.append(path) or real(path)
                hm.view(reg, items, state, NOW, sessions_dir=d)
            finally:
                hm._load = real
            self.assertLessEqual(len(seen), 12)
            state["hist"]["q"] = "Thing 49"
            start = time.perf_counter()
            hm.view(reg, items, state, NOW, sessions_dir=d)
            self.assertLess(time.perf_counter() - start, 1.5, "search")


if __name__ == "__main__":
    unittest.main()

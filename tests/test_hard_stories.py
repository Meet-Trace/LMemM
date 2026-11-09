"""Round-5 teaching stories: look-alike twins and topic pivots, built without the words of the trail days."""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import make_hard_stories as hard  # noqa: E402
import make_judge_lessons as lessons  # noqa: E402

STORIES = ROOT / "research" / "judge_lessons"
FIXTURES = ROOT / "tests" / "fixtures" / "project_grouping"


def stories():
    return json.loads((STORIES / "stories_hard.json").read_text())


def day_words(day):
    items = json.loads((FIXTURES / f"{day}.json").read_text())["items"].values()
    return {it["title"] for it in items}


class HardStoriesTest(unittest.TestCase):
    def test_the_file_is_what_the_generator_writes(self):
        self.assertEqual(stories(), json.loads(json.dumps(hard.build())))

    def test_eight_look_alike_pairs_and_six_pivots_each_with_enough_windows(self):
        found = stories()
        pairs = {tuple(sorted([s["id"], *s["twins"]])) for s in found if s.get("twins")}
        self.assertEqual(len(pairs), 8)
        self.assertEqual(sum(1 for s in found if s.get("pivot")), 6)
        self.assertTrue(all(s["round"] == 5 and len(s["windows"]) >= 6 for s in found))

    def test_nothing_in_them_comes_from_a_trail_day(self):
        found = stories()
        lessons.check(found)                                            # the words of the days are refused
        titles = {w["title"] for s in found for w in s["windows"] + s["general"]}
        for day in ("pivot_day", "versions_day", "dev_day"):
            self.assertFalse(titles & day_words(day), day)

    def test_a_word_of_a_trail_day_a_one_sided_twin_and_a_pivot_without_a_bridge_are_refused(self):
        found = stories()
        spoiled = json.loads(json.dumps(found[0]))
        spoiled["windows"][1]["lines"].append("Wagtail idea memo")
        with self.assertRaises(ValueError):
            lessons.check([spoiled] + found[1:])
        lonely = json.loads(json.dumps(found[0]))
        lonely["twins"] = [found[2]["id"]]
        with self.assertRaises(ValueError):
            lessons.check([lonely] + found[1:])
        pivot = next(s for s in found if s.get("pivot"))
        cut = json.loads(json.dumps(pivot))
        cut["windows"] = [x for x in cut["windows"] if x["phase"] != "bridge"]
        with self.assertRaises(ValueError):
            lessons.check([cut] + [s for s in found if s is not pivot])


class LessonsOfHardStoriesTest(unittest.TestCase):
    def rows(self, subset, seed=3):
        return lessons.lessons(subset, random.Random(seed))

    def test_most_no_answers_of_a_twin_use_a_window_of_the_twin(self):
        found = {s["id"]: s for s in stories()}
        pair = [found["rb-meal-planner"], found["rb-image-upload"]]
        rest = [s for s in found.values() if s not in pair][:6]
        rows = self.rows(pair + rest)
        twin_titles = {w["title"] for w in found["rb-image-upload"]["windows"]}
        no = [r for r in rows if r["story"] == "rb-meal-planner" and r["kind"] == "pair" and r["label"] == 0]
        from_twin = [r for r in no if any(t in r["doc"] for t in twin_titles)]
        self.assertGreater(len(from_twin) / len(no), 0.6)
        # a story without a twin is not pulled towards anything in particular
        plain = [s for s in stories() if not s.get("twins")][:2] + rest
        self.assertTrue(self.rows(plain))

    def test_a_pivot_teaches_early_with_late_twice_and_each_phase_against_the_other(self):
        pivot = next(s for s in stories() if s["id"] == "cardgame-to-app")
        rest = [s for s in stories() if s["id"] != pivot["id"]][:6]
        rows = self.rows([pivot] + rest)
        early = {w["title"] for w in pivot["windows"] if w["phase"] == "early"}
        late = {w["title"] for w in pivot["windows"] if w["phase"] == "late"}
        cross = [r for r in rows if r["story"] == pivot["id"] and r["kind"] == "pair" and r["label"] == 1
                 and any(t in r["query"] for t in early) and any(t in r["doc"] for t in late)]
        # 3 early x 3 late windows, taught twice, in the direction early to late
        self.assertEqual(len(cross), 3 * 3 * 2)
        projects = [r for r in rows if r["story"] == pivot["id"] and r["kind"] == "project" and r["label"] == 1]
        late_in_early_project = [r for r in projects if any(t in r["doc"] for t in late)
                                 and not any(t in r["query"] for t in late)]
        early_in_late_project = [r for r in projects if any(t in r["doc"] for t in early)
                                 and not any(t in r["query"] for t in early)]
        self.assertTrue(late_in_early_project)
        self.assertTrue(early_in_late_project)

    def test_twins_land_in_the_same_split_and_every_split_has_hard_cases(self):
        with tempfile.TemporaryDirectory() as out:
            lessons.main(["--stories", str(STORIES), "--out", out])
            rows = {n: [json.loads(x) for x in (Path(out) / f"{n}.jsonl").read_text().splitlines()]
                    for n in ("train", "valid")}
        found = {s["id"]: s for s in stories()}
        where = {n: {r["story"] for r in rs} & set(found) for n, rs in rows.items()}
        self.assertFalse(where["train"] & where["valid"])
        self.assertTrue(where["valid"] and where["train"])
        for s in found.values():
            for t in s.get("twins", []):
                self.assertEqual(s["id"] in where["valid"], t in where["valid"], (s["id"], t))
        self.assertTrue(any(found[i].get("pivot") for i in where["valid"]))
        self.assertTrue(any(found[i].get("twins") for i in where["valid"]))

    def test_the_older_stories_split_the_same_way_with_or_without_round_five(self):
        old = [s for f in sorted(STORIES.glob("*.json")) if f.name != "stories_hard.json"
               for s in json.loads(f.read_text())]
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as only:
            kept = Path(only)
            for f in sorted(STORIES.glob("*.json")):
                if f.name != "stories_hard.json":
                    (kept / f.name).write_text(f.read_text())
            lessons.main(["--stories", str(kept), "--out", a])
            lessons.main(["--stories", str(STORIES), "--out", b])
            ids = {s["id"] for s in old}
            valid = {n: {json.loads(x)["story"] for x in (Path(d) / "valid.jsonl").read_text().splitlines()} & ids
                     for n, d in (("without", a), ("with", b))}
        self.assertEqual(valid["without"], valid["with"])


if __name__ == "__main__":
    unittest.main()

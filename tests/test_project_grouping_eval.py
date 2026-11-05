"""project_grouping_eval.py: scoring a grouping of things into projects (plan step 0)."""

import contextlib
import io
import json
import os
import tempfile
import unittest

import project_grouping_eval as pge

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping", "sample_day.json")


def thing(iid, label, last="2026-11-03T10:00:00", **extra):
    return {"id": iid, "app": extra.pop("app", "Safari"), "kind": extra.pop("kind", "page"),
            "title": iid, "label": label, "last_seen": last, **extra}


class LabelsTest(unittest.TestCase):
    def test_levels_cut_paths(self):
        items = {"a": thing("a", ["X/Pricing", "Y"])}
        self.assertEqual(pge.gold(items, "project"), {"a": {"X", "Y"}})
        self.assertEqual(pge.gold(items, "sub-project"), {"a": {"X/Pricing", "Y"}})

    def test_unknown_level_is_refused(self):
        with self.assertRaises(ValueError):
            pge.gold({}, "team")

    def test_pairs_share_a_group(self):
        self.assertEqual(pge.pairs({"a": {"X"}, "b": {"X", "Y"}, "c": {"Y"}, "d": set()}),
                         {("a", "b"), ("b", "c")})


class ScoreTest(unittest.TestCase):
    truth = {"a": {"X"}, "b": {"X"}, "c": {"X"}, "d": set()}

    def test_perfect(self):
        s = pge.score(self.truth, {"a": ["X"], "b": ["X"], "c": ["X"]})
        self.assertEqual((s["precision"], s["recall"], s["f1"]), (1.0, 1.0, 1.0))

    def test_group_names_do_not_matter(self):
        s = pge.score(self.truth, {"a": ["whatever"], "b": ["whatever"], "c": ["whatever"]})
        self.assertEqual((s["precision"], s["recall"]), (1.0, 1.0))

    def test_unlabelled_thing_is_a_false_merge(self):          # E1: hub items
        s = pge.score(self.truth, {"a": ["X"], "b": ["X"], "c": ["X"], "d": ["X"]})
        self.assertEqual(s["precision"], 0.5)
        self.assertEqual(s["recall"], 1.0)
        self.assertIn(("a", "d"), s["false_merges"])

    def test_missed_pairs_lower_recall(self):
        s = pge.score(self.truth, {"a": ["X"], "b": ["X"]})
        self.assertEqual(s["precision"], 1.0)
        self.assertAlmostEqual(s["recall"], 1 / 3)
        self.assertEqual(s["missed"], [("a", "c"), ("b", "c")])

    def test_nothing_predicted_is_undefined_not_perfect(self):  # E3
        s = pge.score(self.truth, {})
        self.assertIsNone(s["precision"])
        self.assertEqual(s["recall"], 0.0)
        self.assertIsNone(s["f1"])

    def test_two_projects_count_for_both(self):                 # E2
        truth = {"deck": {"A", "B"}, "a": {"A"}, "b": {"B"}}
        s = pge.score(truth, {"deck": ["A"], "a": ["A"], "b": ["B"]})
        self.assertEqual(s["precision"], 1.0)
        self.assertEqual(s["missed"], [("b", "deck")])

    def test_sub_project_level_is_stricter(self):
        items = {"s": thing("s", ["P/Pricing"]), "m": thing("m", ["P/Meetings"])}
        predicted = {"s": ["P"], "m": ["P"]}
        self.assertEqual(pge.score(pge.gold(items, "project"), predicted, "project")["precision"], 1.0)
        self.assertEqual(pge.score(pge.gold(items, "sub-project"), predicted, "sub-project")["precision"], 0.0)


class BaselinesTest(unittest.TestCase):
    def test_recent_groups_the_latest_k(self):
        items = {i: thing(i, [], last=f"2026-11-03T1{n}:00:00") for n, i in enumerate("abcdef")}
        groups = pge.baseline_recent(items, k=4)
        self.assertEqual({i for i, g in groups.items() if g}, {"c", "d", "e", "f"})

    def test_app_labels_use_project_of(self):
        items = {"m": thing("m", [], app="Gmail", kind="email"),
                 "c": thing("c", [], app="Visual Studio Code", kind="code_file", state={"project": "LMemM"})}
        self.assertEqual(pge.baseline_app_labels(items), {"m": {"Email"}, "c": {"LMemM"}})


class SampleDayTest(unittest.TestCase):
    def setUp(self):
        self.items = pge.load_day(FIXTURE)

    def test_sample_day_covers_the_apps_and_hard_cases(self):
        apps = {i["app"] for i in self.items.values()}
        for app in ("Microsoft Word", "Microsoft Excel", "Google Sheets", "Google Docs", "Gmail",
                    "Mail", "Safari", "Slack", "Microsoft Teams"):
            self.assertIn(app, apps)
        self.assertTrue(any(not i["label"] for i in self.items.values()), "needs hub items")
        self.assertTrue(any(len(i["label"]) > 1 for i in self.items.values()), "needs a shared item")
        self.assertTrue(any("/" in l for i in self.items.values() for l in i["label"]), "needs sub-projects")

    def test_todays_grouping_is_the_floor_to_beat(self):
        """Pins today's numbers so a later linker is compared against something fixed."""
        truth = pge.gold(self.items, "project")
        app = pge.score(truth, pge.baseline_app_labels(self.items))
        recent = pge.score(truth, pge.baseline_recent(self.items))
        self.assertEqual(app["true_pairs"], 84)
        self.assertLess(app["recall"], 0.1)
        self.assertLess(recent["recall"], 0.1)

    def test_report_runs(self):
        text = pge.report(self.items, {n: f(self.items) for n, f in pge.BASELINES.items()})
        self.assertIn("today: app/title labels", text)
        self.assertIn("sub-project", text)


class LoadDayTest(unittest.TestCase):
    def _write(self, doc):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(doc, fh)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_missing_label_is_explained(self):
        with self.assertRaisesRegex(ValueError, "no \"label\" list"):
            pge.load_day(self._write({"items": {"a": {"id": "a"}}}))

    def test_empty_day_is_refused(self):
        with self.assertRaises(ValueError):
            pge.load_day(self._write({"items": {}}))


class CliTest(unittest.TestCase):
    def test_scores_a_predicted_file(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "eval_project_grouping.py")
        spec = importlib.util.spec_from_file_location("eval_project_grouping", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        items = pge.load_day(FIXTURE)
        perfect = {iid: i["label"] for iid, i in items.items()}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(perfect, fh)
        self.addCleanup(os.unlink, fh.name)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--predicted", fh.name, "--mistakes", "0"])
        line = [l for l in out.getvalue().splitlines() if l.startswith(os.path.basename(fh.name)[:-5]) and "project " in l][0]
        self.assertIn("100%", line)


if __name__ == "__main__":
    unittest.main()

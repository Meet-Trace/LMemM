"""project_reasoner_tiny.py: fill-in-the-blank and multiple-choice reasoning for models under 1B."""

import json
import os
import unittest

import project_grouping_eval as pge
import project_reasoner_tiny as rt

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "project_grouping", "sample_day.json")


class Oracle:
    """Answers from the fixture's labels, with a twist: it spells topics differently from place to place
    ("LMemM" / "lmemm app"), so grouping must come through the numbered same-as question."""

    def __init__(self, items):
        self.items, self.n = items, 0

    def label(self, card_id):
        labels = self.items[card_id]["label"]
        return labels[0].split("/") if labels else None

    def __call__(self, system, user, schema, **kw):
        q = json.loads(user)
        if system == rt.TOPIC:
            lab = self.label(q["card"]["id"])
            self.n += 1
            return {"topic": "none" if not lab else (lab[0] if self.n % 2 else lab[0].upper() + " app")}
        if system == rt.SAME_AS:
            want = rt.norm(q["name"]).removesuffix(" app")
            hit = next((k for k, v in q["numbered"].items() if rt.norm(v).removesuffix(" app") == want), "0")
            return {"choice": int(hit)}
        if system.startswith("Read the card of ONE place"):
            lab = self.label(q["card"]["id"])
            return {"part": lab[1] if lab and len(lab) > 1 else "general"}
        raise AssertionError("unknown question")


class TinyTest(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as fh:
            doc = json.load(fh)
        self.items, self.timeline = doc["items"], doc["timeline"]

    def test_a_perfect_answerer_groups_through_multiple_choice(self):
        assigned, projects, _ = rt.run(self.items, self.timeline, Oracle(self.items))
        s = pge.score(pge.gold(self.items, "project"), assigned, "project")
        self.assertEqual(s["precision"], 1.0)
        self.assertGreater(s["recall"], 0.8)
        self.assertEqual(assigned["slack-rohan"], assigned["vscode-resume"])

    def test_parts_become_sub_projects(self):
        assigned, projects, _ = rt.run(self.items, self.timeline, Oracle(self.items))
        self.assertTrue(any(p.endswith("/Resume") for p in assigned["vscode-resume"]))
        s = pge.score(pge.gold(self.items, "sub-project"), assigned, "sub-project")
        self.assertGreater(s["precision"], 0.8)

    def test_inbox_and_none_are_not_filed(self):
        assigned, _, log = rt.run(self.items, self.timeline, Oracle(self.items))
        self.assertNotIn("gmail-inbox", assigned)
        self.assertNotIn("slack-general", assigned)
        self.assertTrue(any(e.get("step") == "hub gmail-inbox" for e in log))

    def test_the_topic_question_sees_what_else_was_open(self):
        seen = []
        oracle = Oracle(self.items)

        def spy(system, user, schema, **kw):
            if system == rt.TOPIC:
                seen.append(json.loads(user))
            return oracle(system, user, schema, **kw)
        rt.run(self.items, self.timeline, spy)
        rohan = next(q for q in seen if q["card"]["id"] == "slack-rohan")
        self.assertIn("resume.py", rohan["open_at_the_same_time"])
        self.assertTrue(all(len(json.dumps(q["card"])) < rt.CARD_CHARS + 600 for q in seen))

    def test_a_lone_topic_is_not_filed(self):
        items = {"a": {"id": "a", "title": "A", "label": []}, "b": {"id": "b", "title": "B", "label": []}}
        tl = [{"item": i, "from": "2026-11-03T09:00:00", "to": "2026-11-03T09:05:00"} for i in "ab"]
        names = iter(["Alpha", "Beta"])

        def model(system, user, schema, **kw):
            if system == rt.TOPIC:
                return {"topic": next(names)}
            if system == rt.SAME_AS:
                return {"choice": 0}
            return {"part": "x"}
        assigned, projects, _ = rt.run(items, tl, model)
        self.assertEqual((assigned, projects), ({}, {}))

    def test_votes_keep_the_majority_and_use_some_temperature(self):
        answers = iter([{"topic": "LMemM"}, {"topic": "Northwind"}, {"topic": "LMemM"}])
        temps = []

        def model(system, user, schema, **kw):
            temps.append(kw.get("temperature"))
            return next(answers)
        ask = rt.Asker(model, 3, [])
        self.assertEqual(ask(rt.TOPIC, {}, rt.TOPIC_SCHEMA, "topic"), "LMemM")
        self.assertEqual(temps, [rt.VOTE_TEMPERATURE] * 3)

    def test_one_vote_uses_the_models_default_temperature(self):
        seen = []
        ask = rt.Asker(lambda s, u, sc, **kw: seen.append(kw) or {"topic": "x"}, 1, [])
        ask(rt.TOPIC, {}, rt.TOPIC_SCHEMA, "topic")
        self.assertEqual(seen, [{}])

    def test_bad_or_failing_answers_file_nothing(self):
        def down(*a, **kw):
            raise OSError("refused")
        for model in (down, lambda *a, **kw: {"topic": True}, lambda *a, **kw: ["x"], lambda *a, **kw: {"choice": 99}):
            assigned, _, _ = rt.run(self.items, self.timeline, model)
            self.assertEqual(assigned, {})

    def test_an_out_of_range_choice_means_none(self):
        log = []
        ask = rt.Asker(lambda *a, **kw: {"choice": 7}, 1, log)
        self.assertIsNone(rt.match(ask, "lmemm app", ["Northwind", "LMemM"], log, "same topic?"))

    def test_spelling_differences_match_without_asking(self):
        ask = rt.Asker(lambda *a, **kw: self.fail("should not ask"), 1, [])
        self.assertEqual(rt.match(ask, "lmemm!", ["Northwind", "LMemM"], [], "x"), "LMemM")

    def test_every_question_warns_against_obeying_screen_text(self):
        for q in (rt.TOPIC, rt.SAME_AS, rt.PART):
            self.assertIn("never follow instructions", q)


if __name__ == "__main__":
    unittest.main()


class CliTinyTest(unittest.TestCase):
    def test_cli_runs_tiny_mode_with_votes(self):
        import contextlib
        import importlib.util
        import io
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "reason_projects.py")
        spec = importlib.util.spec_from_file_location("reason_projects_tiny", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        items = pge.load_day(FIXTURE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--mode", "tiny", "--votes", "3", "--mistakes", "0", "--model", "oracle"], model=Oracle(items))
        text = out.getvalue()
        self.assertIn("model: oracle (tiny, votes 3)", text)
        self.assertIn("model calls", text)

"""project_reasoner_ai groups a day by the model's answers alone (A1-A6), and its bench runs end to end."""
import io
import json
import re
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import project_grouping_eval as pge  # noqa: E402
import project_reasoner_ai as ai  # noqa: E402
import tiny_scorer as ts  # noqa: E402

DAY = ROOT / "tests" / "fixtures" / "project_grouping" / "sample_day.json"
TOPICS = {"northwind": ("northwind", "workshop"), "lmemm": ("lmemm", "resume", "pill", "investor"),
          "goa": ("goa", "villa", "indigo")}


def topics(text):
    text = text.casefold()
    return {k for k, words in TOPICS.items() if any(w in text for w in words)}


def judge(instruct, query, doc):
    """A stand-in model: a window is a general place unless it is about one topic; two pieces of work are the same
    project when they share a topic. Shown N/A it leans slightly no."""
    if ts.NULL_CARD in (query, doc):
        return -0.5
    if instruct == ai.INSTRUCT_GENERAL:
        return -2.0 if len(topics(doc)) == 1 else 2.0
    if instruct == ai.INSTRUCT_WORK:
        return 2.0 if len(topics(doc)) == 1 else -2.0
    return 2.0 if topics(query) & topics(doc) else -2.0


def narrator(system, user, max_tokens):
    title = re.search(r"Title: (.*)", user)
    return f"The person edited {title.group(1) if title else 'it'} for Zorblax."


def day():
    return pge.load_day(DAY), json.loads(DAY.read_text())["timeline"]


class GroundTest(unittest.TestCase):
    def test_cuts_names_the_window_never_showed_and_keeps_the_rest(self):
        text, cut = ai.ground("Editing the Northwind sheet for Zorblax, then Kestrel.", "Northwind pricing sheet")
        self.assertEqual(cut, ["Zorblax", "Kestrel"])
        self.assertEqual(text, "Editing the Northwind sheet for, then.")

    def test_sentence_starts_are_grammar_not_names(self):
        self.assertEqual(ai.ground("Reviewing flights. Booking the villa.", "villa")[1], [])

    def test_possessives_and_hyphens_are_checked_by_their_parts(self):
        self.assertEqual(ai.ground("Fixing LMemM's Resume-Pill bug", "lmemm resume pill")[1], [])
        self.assertEqual(ai.ground("Fixing Trace-Pill bug", "pill")[1], ["Trace-Pill"])


class StepsTest(unittest.TestCase):
    def test_average_link_merges_only_while_the_mean_answer_is_yes(self):
        s = {frozenset("ab"): 2.0, frozenset("bc"): 1.0, frozenset("ac"): -3.0}
        groups, merges, _ = ai.average_link(list("abc"), lambda x, y: s[frozenset((x, y))])
        self.assertEqual(sorted(map(sorted, groups)), [["a", "b"], ["c"]])     # mean of ac, bc is -1
        self.assertEqual(len(merges), 1)

    def test_a_rule_name_must_be_known(self):
        items, timeline = day()
        with self.assertRaises(ValueError):
            ai.run(items, timeline, judge, rule="vibes")


class RunTest(unittest.TestCase):
    def test_groups_by_the_models_answers_and_leaves_general_places_out(self):
        items, timeline = day()
        assigned, projects, log = ai.run(items, timeline, judge, narrator)
        self.assertNotIn("gmail-inbox", assigned)                  # judged general by the model, not by kind
        self.assertEqual(assigned["gmail-northwind"], assigned["word-agenda"])
        self.assertNotEqual(assigned["gmail-northwind"], assigned["vscode-resume"])
        self.assertEqual(assigned["gsheet-trip"], assigned["safari-villa"])
        for p in projects.values():
            self.assertEqual(len(p["members"]), len(set(p["members"])))
        f1 = pge.score(pge.gold(items, "project"), assigned)["f1"]
        self.assertGreater(f1, 0.7)
        said = [e for e in log if e["step"] == "understand"]
        self.assertTrue(all(e["cut"] == ["Zorblax"] for e in said))           # A6 on every sentence
        self.assertTrue(all("Zorblax" not in (p["description"] or "") for p in projects.values()))

    def test_no_kind_list_a_mailbox_the_model_calls_specific_is_filed(self):
        items, timeline = day()
        items["gmail-inbox"]["content"]["excerpts"] = [{"id": "x", "text": "Goa villa booking"}]
        items["gmail-inbox"]["activity"] = {}
        assigned, _, _ = ai.run(items, timeline, judge, narrator)
        self.assertIn("gmail-inbox", assigned)

    def test_calibration_removes_a_model_that_says_yes_to_everything(self):
        items, timeline = day()
        always = lambda instruct, query, doc: 1.0                     # noqa: E731  yes even to N/A
        raw, _, _ = ai.run(items, timeline, always, None, "raw", specific=False)
        cal, _, _ = ai.run(items, timeline, always, None, "calibrated", specific=False)
        self.assertEqual(ai.run(items, timeline, always, None, "raw")[0], {})  # raw: every window "general"
        self.assertEqual(len({p for ps in raw.values() for p in ps}), 1)      # raw: one project of everything
        self.assertEqual(pge.pairs(cal), set())                                # calibrated: nothing above habit

    def test_all_pairs_relates_across_sittings_too(self):
        items, timeline = day()
        _, _, log = ai.run(items, timeline, judge, None, all_pairs=True)
        self.assertEqual(sum(1 for e in log if e["step"] == "relate"), 1)

    def test_the_general_place_step_can_use_its_own_rule(self):
        items, timeline = day()
        always = lambda instruct, query, doc: 1.0                     # noqa: E731
        self.assertEqual(ai.run(items, timeline, always, None, "calibrated", gate_rule="raw")[0], {})
        self.assertNotEqual(ai.run(items, timeline, always, None, "calibrated")[0], {})

    def test_judge_and_narrator_cache_repeated_questions(self):
        calls = []
        j = ai.Judge(type("S", (), {"options_for": lambda self, i, q, d: calls.append(1) or {"yes": -0.2, "no": -2}})())
        self.assertGreater(j("i", "q", "d"), 0)
        j("i", "q", "d")
        self.assertEqual((len(calls), j.calls), (1, 1))
        taught = ai.Judge(j.scorer, j.cache, name="adapter=x")              # a taught judge never reuses them
        taught("i", "q", "d")
        self.assertEqual(len(calls), 2)
        wrote = []
        writer = type("W", (), {"write": lambda self, u, s, n: wrote.append(1) or "Editing a sheet."})()
        ollama = ai.Narrator(writer, j.cache)
        self.assertEqual((ollama("s", "u", 40), ollama("s", "u", 40)), ("Editing a sheet.", "Editing a sheet."))
        ai.Narrator(writer, j.cache, name="mlx=repo")("s", "u", 40)          # nor does another narrator model
        self.assertEqual(len(wrote), 2)


class FinderOptionsTest(unittest.TestCase):
    """What the app's project finder adds: your projects as seeds, retrieval, margins, shared resources."""

    def setUp(self):
        self.items, self.timeline = day()

    def test_a_task_two_projects_say_yes_to_is_filed_in_neither(self):
        both = dict(self.items["safari-villa"], title="Goa villa for the Northwind workshop offsite")
        seeds = [{"id": "trip", "members": ["gsheet-trip"]}, {"id": "client", "members": ["gmail-northwind"]}]
        loose = {"safari-villa": both}
        kw = dict(seeds=seeds, library=self.items, specific=False)
        assigned, summary, log = ai.run(loose, self.timeline, judge, **kw, abstain_shared=True)
        self.assertEqual((assigned, summary), ({}, {}))
        shared = log[-1]["shared"]
        self.assertEqual([s["task"] for s in shared], [["safari-villa"]])
        self.assertCountEqual(shared[0]["projects"], ["trip", "client"])
        filed, _, _ = ai.run(loose, self.timeline, judge, **kw)                   # without it: one of the two
        self.assertEqual(len(filed["safari-villa"]), 1)

    def test_retrieval_limits_which_projects_the_judge_is_asked_about(self):
        seeds = [{"id": i, "members": [i]} for i in self.items
                 if i not in ("safari-villa", "gmail-inbox", "slack-general")]
        asked = []

        def counting(instruct, query, doc):
            if instruct == ai.INSTRUCT_PROJECT:
                asked.append(query)
            return judge(instruct, query, doc)
        assigned, _, log = ai.run({"safari-villa": self.items["safari-villa"]}, self.timeline, counting,
                                  seeds=seeds, library=self.items, retrieve=ai.Retriever(None, k=3))
        entry = next(e for e in log if e["step"] == "assign")
        self.assertEqual((len(entry["retrieved"]), entry["of"], len(asked)), (3, len(seeds), 3))
        self.assertTrue(set(entry["retrieved"]) & {"gsheet-trip", "gmail-flights"}, entry["retrieved"])
        self.assertIn(assigned["safari-villa"][0].split(" [")[0], {"gsheet-trip", "gmail-flights"})

    def test_a_merge_or_a_join_must_clear_the_margin(self):
        s = {frozenset("ab"): 2.0, frozenset("bc"): 1.0, frozenset("ac"): -3.0}
        groups, _, _ = ai.average_link(list("abc"), lambda x, y: s[frozenset((x, y))], margin=2.5)
        self.assertEqual(len(groups), 3)
        seeds = [{"id": "trip", "members": ["gsheet-trip"]}]
        loose = {i: self.items[i] for i in ("safari-villa", "gmail-flights")}
        joined, _, _ = ai.run(loose, self.timeline, judge, seeds=seeds, library=self.items)
        self.assertEqual(joined, {i: ["trip [trip]"] for i in loose})
        apart, summary, _ = ai.run(loose, self.timeline, judge, seeds=seeds, library=self.items, margin=3.0)
        self.assertFalse(any(p.get("seed") for p in summary.values()))
        self.assertFalse([p for ps in apart.values() for p in ps if p.startswith("trip")])

    def test_a_second_opinion_without_the_narrator_can_veto_a_join(self):
        yes_to_projects = lambda instruct, query, doc: (2.0 if instruct == ai.INSTRUCT_PROJECT       # noqa: E731
                                                        else judge(instruct, query, doc))
        seeds = [{"id": "client", "members": ["gmail-northwind", "word-agenda"]}]
        loose = {"vscode-resume": self.items["vscode-resume"], "claude-resume": self.items["claude-resume"]}
        kw = dict(seeds=seeds, library=self.items, specific=False)
        trusting, _, _ = ai.run(loose, self.timeline, yes_to_projects, **kw)
        self.assertEqual(set(trusting), set(loose))                                  # joined: the model said yes
        self.assertTrue(all(v == ["client [client]"] for v in trusting.values()))
        for how in ("mean", "best"):
            vetoed, summary, log = ai.run(loose, self.timeline, yes_to_projects, **kw, confirm=how)
            self.assertFalse(any(p.get("seed") for p in summary.values()), how)
            entry = next(e for e in log if e["step"] == "assign")
            self.assertLess(entry["confirmed"]["client"], 0)
            self.assertIn("second opinion", entry["decision"])
        close = {"gmail-flights": self.items["gmail-flights"]}                    # a join the second opinion agrees with
        holiday = [{"id": "trip", "members": ["gsheet-trip", "safari-villa"]}]
        joined, _, log = ai.run(close, self.timeline, judge, seeds=holiday, library=self.items, confirm="mean")
        self.assertEqual(joined, {"gmail-flights": ["trip [trip]"]})
        self.assertGreater(next(e for e in log if e["step"] == "assign")["confirmed"]["trip"], 0)

    def test_without_descriptions_the_narrator_never_writes_one(self):
        described = []

        def narrate(system, user, max_tokens):
            if user.startswith(ai.DESCRIBE.split("{")[0]):
                described.append(user)
            return narrator(system, user, max_tokens)
        seeds = [{"id": "trip", "members": ["gsheet-trip"]}]
        loose = {i: self.items[i] for i in ("safari-villa", "gmail-flights")}
        kw = dict(seeds=seeds, library=self.items)
        _, summary, log = ai.run(loose, self.timeline, judge, narrate, **kw)
        self.assertTrue(described)                                                  # by default it does
        self.assertTrue(all(p["description"] for p in summary.values()))
        described.clear()
        assigned, summary, log = ai.run(loose, self.timeline, judge, narrate, **kw, use_description=False)
        self.assertEqual(described, [])
        self.assertTrue(all(not p["description"] for p in summary.values()))
        self.assertEqual(assigned, {i: ["trip [trip]"] for i in loose})              # the join does not need them

    def test_a_group_the_judge_calls_idle_looking_around_is_not_filed(self):
        def judge_idle_goa(instruct, query, doc):
            if instruct == ai.INSTRUCT_WORK:
                return -3.0 if "goa" in topics(doc) else 3.0
            return judge(instruct, query, doc)
        assigned, summary, log = ai.run(self.items, self.timeline, judge_idle_goa, narrator, worth=True)
        idle = [p for p in summary.values() if p.get("idle")]
        self.assertEqual(len(idle), 1)
        self.assertLess(idle[0]["work"], 0)
        self.assertEqual(set(idle[0]["members"]) & set(assigned), set())                # none of its things is filed
        self.assertTrue(set(idle[0]["members"]) & {"gsheet-trip", "safari-villa", "gmail-flights"})
        self.assertTrue(all(p["work"] > 0 for p in summary.values() if not p.get("idle")))
        filed, summary2, _ = ai.run(self.items, self.timeline, judge_idle_goa, narrator)      # the question is off by default
        self.assertFalse([p for p in summary2.values() if p.get("idle") or "work" in p])
        self.assertIn("gsheet-trip", filed)
        self.assertEqual(len(summary), len(summary2))

    def test_another_model_can_answer_the_work_question_alone(self):
        asked = {"judge": [], "work": []}

        def grouping(instruct, query, doc):
            asked["judge"].append(instruct)
            return judge(instruct, query, doc)

        def work(instruct, query, doc):
            asked["work"].append(instruct)
            return -3.0 if "goa" in topics(doc) else 3.0

        assigned, summary, _ = ai.run(self.items, self.timeline, grouping, narrator, worth=True, work_judge=work)
        self.assertEqual(set(asked["work"]), {ai.INSTRUCT_WORK})                     # it is asked only that
        self.assertNotIn(ai.INSTRUCT_WORK, asked["judge"])                            # and the grouping judge never is
        self.assertEqual(len([p for p in summary.values() if p.get("idle")]), 1)
        self.assertEqual(len(asked["work"]), len(summary))                            # one question a group

    def test_a_small_budget_stops_the_run_and_the_cache_carries_it_on(self):
        cache, answers = {}, []
        scorer = type("S", (), {"options_for": lambda self, i, q, d: answers.append(1) or (
            {"yes": -0.1, "no": -3.0} if judge(i, q, d) > 0 else {"yes": -3.0, "no": -0.1})})()
        whole = ai.run(self.items, self.timeline, ai.Judge(scorer, {}))[0]
        for _ in range(200):
            j = ai.Judge(scorer, cache, budget=ai.Budget(10))
            try:
                got = ai.run(self.items, self.timeline, j)[0]
                break
            except ai.OutOfBudget:
                self.assertLessEqual(j.calls, 10)
        self.assertEqual(got, whole)


class FakeOllama(BaseHTTPRequestHandler):
    """Writes "Working on <title>." and answers yes when the two sides share a topic."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/chat":
            title = re.search(r"Title: (.*)", body["messages"][-1]["content"])
            out = {"message": {"content": f"Working on {title.group(1) if title else 'this project'}."}}
        else:
            m = re.search(r"<Query>: (.*)\n<Document>: (.*)<\|im_end\|>", body["prompt"], re.S)
            instruct = re.search(r"<Instruct>: (.*)\n<Query>", body["prompt"]).group(1)
            s = judge(instruct, m.group(1), m.group(2))
            top = [{"token": "yes", "logprob": -1.0 + s / 2}, {"token": "no", "logprob": -1.0 - s / 2}]
            out = {"logprobs": [{"token": top[0]["token"], "logprob": top[0]["logprob"], "top_logprobs": top}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class BenchTest(unittest.TestCase):
    def test_bench_runs_end_to_end_on_a_fake_ollama(self):
        import bench_ai_grouping
        server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with redirect_stdout(io.StringIO()) as out:
                runs, table = bench_ai_grouping.main(["--narrator", "fake", "--judge", "fake", "--url", url,
                                                      "--days", "sample_day", "--blind"])
        finally:
            server.shutdown()
            server.server_close()
        text = out.getvalue()
        self.assertIn("what the model understood", text)
        self.assertIn("code (assign method)", text)
        self.assertEqual(len(runs), 2 * 2 * 2)                     # (ai, ai-unread) x (raw, calibrated) x blind
        self.assertTrue(all(r["scores"]["f1"] is not None for r in runs if not r["blind"]))
        ai_runs = [r for r in runs if r["variant"] == "ai" and not r["blind"]]
        self.assertTrue(all(r["scores"]["pair_auc"] > 0.5 and r["scores"]["general_auc"] > 0.5 for r in ai_runs))
        self.assertIn("pair AUC", text)


class MeaningDayTest(unittest.TestCase):
    """The meaning day is built so that word matching cannot group it: only reading can."""

    def test_no_name_or_number_repeats_inside_a_project_and_code_files_nothing(self):
        import project_reasoner_assign as assign
        import project_reasoner_graph as graph
        path = DAY.with_name("meaning_day.json")
        items, timeline = pge.load_day(path), json.loads(path.read_text())["timeline"]
        gold = pge.gold(items, "project")
        self.assertEqual(len({p for g in gold.values() for p in g}), 3)
        names = {i: {w for w in re.findall(r"\b[A-Z][a-z]{3,}\b|\d{3,}", " ".join(graph._lines(it)))}
                 for i, it in items.items()}
        for a in items:
            for b in items:
                if a < b and gold[a] & gold[b]:
                    self.assertLessEqual(names[a] & names[b], {"Kabir"}, (a, b))     # the one introducer
        assigned, _, _ = assign.run(items, timeline, graph.CapitalisedNames())
        self.assertEqual(assigned, {})


if __name__ == "__main__":
    unittest.main()

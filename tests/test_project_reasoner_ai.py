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

"""tiny_scorer reads a model's confidence (S1-S5) and the benchmark runs end to end on a fake Ollama."""
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

import tiny_scorer as ts  # noqa: E402


def _words(text):
    return {w.lower() for w in re.findall(r"[A-Za-z]{5,}", text)}


class FakeOllama(BaseHTTPRequestHandler):
    """Leans yes in proportion to the words two activities share, and always favours option 1 a bit
    (the habit S4 must cancel)."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/embed":
            out = {"embeddings": [[len(_words(t)), t.count("Northwind"), t.count("LMemM") + 1.0]
                                  for t in body["input"]]}
        else:
            text = body.get("prompt") or body["messages"][-1]["content"]
            parts = re.split(r"Activity B:|<Document>:|Option \d:", text)
            if "Option 1:" in text:
                opts = re.split(r"Option \d:", text)
                anchor = _words(opts[0])
                shared = [len(anchor & _words(o)) for o in opts[1:]]
                top = [{"token": str(k + 1), "logprob": -3.0 + 0.3 * s + (0.5 if k == 0 else 0)}
                       for k, s in enumerate(shared)]
            else:
                shared = len(_words(parts[0]) & _words(parts[-1])) if len(parts) > 1 else 0
                top = [{"token": "Yes", "logprob": -1.0 + 0.2 * shared}, {"token": "no", "logprob": -1.5}]
            out = {"logprobs": [{"token": top[0]["token"], "logprob": top[0]["logprob"], "top_logprobs": top}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ScorerTest(unittest.TestCase):
    def test_yes_no_reads_every_spelling_and_keeps_sign(self):
        self.assertGreater(ts.yes_no({"Yes": -0.5, " yes": -2.0, "No": -1.5}), 0)
        self.assertLess(ts.yes_no({"no": -0.1, "Yes": -3.0}), 0)

    def test_missing_word_gets_a_floor_below_the_least_likely_option(self):
        self.assertAlmostEqual(ts.yes_no({"Yes": -1.0, "Maybe": -4.0}), -1.0 - (-5.0))

    def test_lift_removes_each_cards_habit(self):
        self.assertEqual(ts.lift(3.0, 2.0, 2.0), 1.0)

    def test_rotated_options_cancel_a_slot_habit(self):
        # option 1 always gets +2; the true favourite (original index 2) still wins once rotated
        def ask(prompt):
            order = json.loads(prompt)
            return {str(slot + 1): (-1.0 if order[slot] == 2 else -2.0) + (2.0 if slot == 0 else 0.0)
                    for slot in range(3)}
        means, fixed = ts.option_scores(ask, json.dumps, 3)
        self.assertEqual(max(range(3), key=lambda i: means[i]), 2)
        self.assertEqual(fixed, 0)                      # one fixed order picks whatever sits in slot 1

    def test_no_logprobs_is_an_error_not_a_guess(self):
        with self.assertRaises(RuntimeError):
            ts.first_token_options({"message": {"content": "yes"}})

    def test_models_must_be_local(self):
        with self.assertRaises(ValueError):
            ts.ChatScorer("gemma3:1b", "http://example.com:11434")


class BenchEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_runs_every_method_and_scores_pairs(self):
        import bench_tiny_methods as bench
        with redirect_stdout(io.StringIO()) as out:
            rows = bench.main(["--chat", "fake", "--rerank", "fake-rr", "--embed", "fake-emb", "--url", self.url,
                               "--pairs", "12", "--picks", "3", "--questions", "same,names"])
        text = out.getvalue()
        self.assertIn("== both days", text)
        self.assertIn("pick the same-project option", text)
        self.assertTrue(all("chat:fake:same:lift" in r and "rerank:fake-rr:same-project" in r for r in rows))
        self.assertTrue(any(r["y"] == 1 for r in rows) and any(r["y"] == 0 for r in rows))
        # the fake model leans on shared words, so it must rank same-project pairs above chance
        self.assertGreater(bench.auc(rows, "chat:fake:same"), 0.5)


if __name__ == "__main__":
    unittest.main()

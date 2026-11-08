"""project_finder_ai.py: the project finder's AI method, on a fake Ollama that answers by topic."""

import json
import re
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import config  # noqa: E402
import project_finder as finder  # noqa: E402
import project_finder_ai as fai  # noqa: E402
import projects  # noqa: E402
from test_project_reasoner_ai import TOPICS, judge, topics  # noqa: E402

DAY = json.loads((ROOT / "tests" / "fixtures" / "project_grouping" / "sample_day.json").read_text())
NOW = datetime.fromisoformat(max(v["to"] for v in DAY["timeline"])) + timedelta(hours=1)


class FakeOllama(BaseHTTPRequestHandler):
    """Narrates "Working on <title>.", judges yes when both sides share a topic, embeds by topic, and
    keeps every request so a test can check how the models were asked."""
    models = ("qwen3.5:0.8b", "lmemm-judge:latest", "embeddinggemma:latest")
    seen = []

    def log_message(self, *args):
        pass

    def reply(self, out):
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"models": [{"name": m} for m in self.models]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body))
        if self.path == "/api/chat":
            title = re.search(r"Title: (.*)", body["messages"][-1]["content"])
            self.reply({"message": {"content": f"Working on {title.group(1) if title else 'this project'}."}})
        elif self.path == "/api/embed":
            self.reply({"embeddings": [[1.0 if k in topics(t) else 0.0 for k in sorted(TOPICS)] + [0.1]
                                       for t in body["input"]]})
        elif "prompt" not in body:                                          # unload
            self.reply({})
        else:
            m = re.search(r"<Query>: (.*)\n<Document>: (.*)<\|im_end\|>", body["prompt"], re.S)
            instruct = re.search(r"<Instruct>: (.*)\n<Query>", body["prompt"]).group(1)
            s = judge(instruct, m.group(1), m.group(2))
            top = [{"token": "yes", "logprob": -1.0 + s / 2}, {"token": "no", "logprob": -1.0 - s / 2}]
            self.reply({"logprobs": [{"token": "yes", "logprob": top[0]["logprob"], "top_logprobs": top}]})


def stream():
    items = {i: {k: v for k, v in it.items() if k != "label"} for i, it in json.loads(json.dumps(DAY["items"])).items()}
    return items, DAY["timeline"]


class FinderAITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = config.use_paths(self.tmp.name)
        self.paths.__enter__()
        FakeOllama.seen, FakeOllama.models = [], FakeOllama.models
        self.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.items, self.visits = stream()
        self.reg = projects.empty()
        self.calls = config.FINDER_CALLS
        fai._retriever = None

    def tearDown(self):
        config.FINDER_CALLS = self.calls
        FakeOllama.models = ("qwen3.5:0.8b", "lmemm-judge:latest", "embeddinggemma:latest")
        self.server.shutdown()
        self.server.server_close()
        self.paths.__exit__(None, None, None)
        self.tmp.cleanup()

    def find(self):
        return fai.find(self.reg, self.items, self.visits, NOW, days=30, url=self.url)

    def test_missing_models_skip_the_look_and_say_which(self):
        FakeOllama.models = ("qwen3.5:0.8b",)
        self.assertEqual(self.find(), [])
        self.assertIn("lmemm-judge", finder.read_log()[-1]["skipped"])

    def test_no_ollama_skips_the_look_and_says_so(self):
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{free.getsockname()[1]}"                      # nothing listens there
        self.assertEqual(fai.find(self.reg, self.items, self.visits, NOW, days=30, url=url), [])
        self.assertIn("not running", finder.read_log()[-1]["skipped"])

    def test_offers_pure_projects_files_nothing_and_asks_every_model_on_the_cpu(self):
        before = json.dumps(self.items, sort_keys=True)
        planned = self.find()
        self.assertEqual(json.dumps(self.items, sort_keys=True), before)              # offered, never filed
        self.assertTrue(planned)
        for _, kind, (name, ids, why) in planned:
            self.assertEqual(kind, "project")
            self.assertEqual(len({t for i in ids for t in topics(json.dumps(self.items[i]))} & set(TOPICS)), 1, ids)
            self.assertNotIn("gmail-inbox", ids)                                      # a general place
            self.assertIn("separate sittings", why)
        asked = [b for p, b in FakeOllama.seen if "prompt" in b or "messages" in b or "input" in b]
        self.assertTrue(asked)
        self.assertTrue(all(b["options"]["num_gpu"] == 0 and b["options"]["num_thread"] == config.FINDER_THREADS
                            for b in asked))
        self.assertTrue(all(b["keep_alive"] == 0 for p, b in FakeOllama.seen if p == "/api/chat"))   # cold narrator
        look = finder.read_log()[-1]
        self.assertGreater(look["calls"], 0)
        self.assertTrue(Path(fai.cache_path()).exists())

    def test_a_second_look_asks_nothing_new(self):
        first = self.find()
        FakeOllama.seen = []
        self.assertEqual(self.find(), first)
        self.assertEqual(finder.read_log()[-1]["calls"], 0)
        self.assertFalse([b for p, b in FakeOllama.seen if p in ("/api/chat",) or "prompt" in b])

    def test_a_small_budget_pauses_and_the_next_looks_carry_on(self):
        whole = self.find()
        Path(fai.cache_path()).unlink()
        config.FINDER_CALLS = 15
        for _ in range(100):
            planned = self.find()
            if not finder.read_log()[-1].get("paused"):
                break
            self.assertEqual(planned, [])
        self.assertEqual(planned, whole)
        self.assertGreater(sum(1 for e in finder.read_log() if e.get("paused")), 2)

    def test_things_for_your_own_project_are_offered_for_it(self):
        pid = projects.create(self.reg, "Holiday")
        projects.move_to(self.reg, self.items, ["gsheet-trip"], pid)
        planned = self.find()
        mine = [(target, ids) for _, kind, (target, ids, _) in planned if kind == "items"]
        self.assertEqual([t for t, _ in mine], [pid])
        self.assertIn("safari-villa", mine[0][1])
        self.assertTrue(all(topics(json.dumps(self.items[i])) == {"goa"} for i in mine[0][1]))
        self.assertFalse(any("gsheet-trip" in ids for _, kind, (_, ids, _) in planned if kind == "project"))

    def test_a_thing_in_two_groups_is_offered_in_neither(self):
        ids = list(self.items)[:8]
        summary = {"A [P1]": {"members": ids[:3] + [ids[7]], "tasks": 2},
                   "B [P2]": {"members": ids[3:], "tasks": 2}}
        planned = fai.plan(self.reg, self.items, summary)
        offered = [i for _, _, (_, got, _) in planned for i in got]
        self.assertEqual(sorted(offered), sorted(ids[:7]))                          # ids[7] in neither

    def test_the_tracker_switch_runs_the_ai_method(self):
        planned = finder.find(self.reg, self.items, self.visits, NOW, days=30, method="assign")
        self.assertNotIn("method", finder.read_log()[-1] if finder.read_log() else {})
        self.assertIsInstance(planned, list)


if __name__ == "__main__":
    unittest.main()

"""The judge's lessons are built without touching the test days, and train_judge.py trains, keeps and
reloads an adapter (on a tiny random Qwen3 when mlx is installed; skipped otherwise)."""
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import make_judge_lessons as lessons  # noqa: E402
import project_reasoner_ai as ai  # noqa: E402

STORIES = ROOT / "research" / "judge_lessons"
FIXTURES = ROOT / "tests" / "fixtures" / "project_grouping"

try:
    import mlx.core  # noqa: F401
    import mlx_lm  # noqa: F401
    HAVE_MLX = True
except ImportError:
    HAVE_MLX = False


class LessonsTest(unittest.TestCase):
    def test_lessons_are_balanced_use_the_pipelines_questions_and_split_by_story(self):
        with tempfile.TemporaryDirectory() as out:
            lessons.main(["--stories", str(STORIES), "--out", out])
            rows = {n: [json.loads(x) for x in (Path(out) / f"{n}.jsonl").read_text().splitlines()]
                    for n in ("train", "valid")}
        for name, rs in rows.items():
            self.assertAlmostEqual(sum(r["label"] for r in rs) / len(rs), 0.5, delta=0.05, msg=name)
            self.assertTrue({r["instruct"] for r in rs} <= {ai.INSTRUCT_PAIR, ai.INSTRUCT_PROJECT, ai.INSTRUCT_GENERAL})
        self.assertFalse({r["story"] for r in rows["train"]} & {r["story"] for r in rows["valid"]})

    def test_no_story_touches_a_test_day(self):
        stories = [s for f in sorted(STORIES.glob("*.json")) for s in json.loads(f.read_text())]
        lessons.check(stories)
        titles = {w["title"] for s in stories for w in s["windows"] + s.get("general", [])}
        titles |= {g["title"] for f in sorted((STORIES / "places").glob("*.json")) for g in json.loads(f.read_text())}
        for day in FIXTURES.glob("*.json"):
            fixture = {it["title"] for it in json.loads(day.read_text())["items"].values()}
            self.assertFalse(titles & fixture, day.name)

    def test_a_test_day_theme_is_refused(self):
        with self.assertRaises(ValueError):
            lessons.check([{"id": "x", "theme": "planning a wedding", "windows": [{}, {}, {}]}])

    def test_the_seed_stories_give_hundreds_of_lessons(self):
        stories = [s for f in sorted(STORIES.glob("*.json")) for s in json.loads(f.read_text())]
        import random
        rows = lessons.lessons(stories, random.Random(1))
        self.assertGreater(len(rows), 500)


class FakeTokenizer:
    """Words to ids by hash; "yes" and "no" get fixed ids, as the real tokenizer gives them one token each."""
    vocab, pad_token_id = 512, 0

    def convert_tokens_to_ids(self, word):
        return {"yes": 1, "no": 2}[word]

    def encode(self, text):
        return [3 + zlib.crc32(w.encode()) % (self.vocab - 3) for w in text.split()]


@unittest.skipUnless(HAVE_MLX, "mlx and mlx-lm are only installed on the Mac (or a scratch venv)")
class TrainTest(unittest.TestCase):
    def tiny(self):
        import mlx.core as mx
        from mlx_lm.models import qwen3
        mx.random.seed(0)
        return qwen3.Model(qwen3.ModelArgs(model_type="qwen3", hidden_size=32, num_hidden_layers=2,
                                           intermediate_size=64, num_attention_heads=2, rms_norm_eps=1e-6,
                                           vocab_size=FakeTokenizer.vocab, num_key_value_heads=1,
                                           max_position_embeddings=2048, head_dim=16, tie_word_embeddings=True,
                                           rope_theta=10000.0))

    def test_the_judge_reads_only_the_last_position_and_gets_the_same_answer(self):
        import mlx.core as mx
        import tiny_scorer as ts
        judge = ts.MlxRerankScorer.__new__(ts.MlxRerankScorer)
        judge.mx, judge.model, judge.tokenizer, judge.calls = mx, self.tiny(), FakeTokenizer(), 0
        judge.yes, judge.no = 1, 2
        got = judge.options_for("Same project?", "a budget sheet", "a chat with the builder")
        ids = FakeTokenizer().encode(ts.RerankScorer.TEMPLATE.format(
            instruct="Same project?", query="a budget sheet", doc="a chat with the builder"))
        full = judge.model(mx.array([ids]))[0, -1]
        full = full - mx.logsumexp(full)
        self.assertAlmostEqual(got["yes"], float(full[1]), places=4)
        self.assertAlmostEqual(got["no"], float(full[2]), places=4)
        self.assertEqual(judge.calls, 1)

    def test_step_size_warms_up_then_decays_to_a_tenth(self):
        import argparse
        import mlx.optimizers as optim
        import train_judge
        step = train_judge.schedule(optim, argparse.Namespace(lr=1e-4, iters=100, warmup=10))
        self.assertAlmostEqual(float(step(0)), 1e-6, places=8)
        self.assertAlmostEqual(float(step(10)), 1e-4, places=8)
        self.assertLess(float(step(55)), 1e-4)
        self.assertAlmostEqual(float(step(100)), 1e-5, places=7)

    def test_trains_keeps_the_best_adapter_and_reloads_it(self):
        import argparse
        import mlx.core as mx
        import train_judge
        from mlx_lm.tuner.utils import load_adapters
        rows = [{"instruct": "Same?", "query": f"window {i}", "doc": ("alpha " if i % 2 else "beta ") + f"note {i}",
                 "label": i % 2, "kind": "pair"} for i in range(64)]
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(model="tiny", lessons=Path(out), out=Path(out) / "adapter", iters=60, batch=4,
                                      accumulate=2, cache_mb=64, memory_mb=0, probe=0, lr=3e-3, warmup=5, clip=1.0, rank=4, scale=10.0, layers=2,
                                      max_tokens=200, eval_every=20, seed=1)
            model, tok = self.tiny(), FakeTokenizer()
            before, history, best = train_judge.train(model, tok, rows[:48], rows[48:], args, log=lambda *_: None)
            self.assertLess(history[-1]["train_loss"], 0.69)                     # learnt something
            self.assertGreaterEqual(best, before["all"]["auc"] or 0.0)
            ids, lengths, _ = train_judge.batch(mx, train_judge.encode(tok, rows[48:52], 200)[0], 0)
            base = train_judge.last_answer(mx, self.tiny(), ids, lengths)
            once = train_judge.last_answer(mx, load_adapters(self.tiny(), str(args.out)), ids, lengths)
            again = train_judge.last_answer(mx, load_adapters(self.tiny(), str(args.out)), ids, lengths)
            self.assertTrue(mx.allclose(once, again).item())                    # loads the same every time
            if best > (before["all"]["auc"] or 0.0):
                self.assertFalse(mx.allclose(base, once).item())                # and the kept adapter changes answers
            config = json.loads((args.out / "adapter_config.json").read_text())
            self.assertEqual((config["num_layers"], config["lora_parameters"]["rank"]), (2, 4))


if __name__ == "__main__":
    unittest.main()

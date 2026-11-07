#!/usr/bin/env python3
"""Teach Qwen3-Reranker-0.6B the judgements grouping needs, on lessons from make_judge_lessons.py.

    python scripts/train_judge.py --model mlx-community/Qwen3-Reranker-0.6B-4bit \
        --lessons lessons/ --out judge-adapter/ --iters 600

Research only, on Apple silicon with mlx-lm (the CPU build works too, slowly). The model stays 0.6B: a
small LoRA adapter is trained on its last layers and saved in the folder mlx-lm loads with
`load(repo, adapter_path=...)`, which is how tiny_scorer.MlxRerankScorer and bench_ai_grouping.py
--judge-adapter read it.

The loss is the decision itself. A lesson is the reranker's own prompt (tiny_scorer.RerankScorer.TEMPLATE)
and its label; the model reads it once and only its answer at the last position counts: cross-entropy over
the logits of "no" and "yes". That is exactly the number the pipeline reads (log P(yes) - log P(no)), so
nothing else about the model is trained. Validation reports AUC (same scale as the bench) and accuracy at
0, per kind of lesson; the adapter with the best validation AUC is kept.
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import tiny_scorer as ts  # noqa: E402


def read_lessons(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def encode(tokenizer, rows, max_tokens):
    """Token ids of each lesson's prompt; lessons longer than `max_tokens` are left out and counted."""
    out, skipped = [], 0
    for r in rows:
        ids = tokenizer.encode(ts.RerankScorer.TEMPLATE.format(instruct=r["instruct"], query=r["query"], doc=r["doc"]))
        if len(ids) > max_tokens:
            skipped += 1
            continue
        out.append((ids, r["label"], r.get("kind", "pair")))
    return out, skipped


def last_answer(mx, model, ids, lengths):
    """The model's logits at each lesson's last token, without the full vocabulary at every position."""
    h = model.model(ids)
    h = mx.take_along_axis(h, (lengths - 1).reshape(-1, 1, 1), axis=1).squeeze(1)
    if getattr(model, "lm_head", None) is not None:
        return model.lm_head(h)
    return model.model.embed_tokens.as_linear(h)


def _mlx(mx, name):
    """An MLX memory function by its current or older (mx.metal) name, or None."""
    return getattr(mx, name, None) or getattr(getattr(mx, "metal", None), name, None)


def batch(mx, examples, pad, multiple=64):
    """Right-padded to a multiple of `multiple` tokens, so the allocator sees a few sizes it can reuse."""
    longest = -(-max(len(ids) for ids, _, _ in examples) // multiple) * multiple
    ids = mx.array([ids + [pad] * (longest - len(ids)) for ids, _, _ in examples])
    lengths = mx.array([len(ids) for ids, _, _ in examples])
    labels = mx.array([label for _, label, _ in examples])
    return ids, lengths, labels


def auc(pairs):
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return None
    return sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg) / (len(pos) * len(neg))


def evaluate(mx, model, examples, yes, no, pad, size=2):
    scored = []
    for k in range(0, len(examples), size):
        chunk = examples[k:k + size]
        ids, lengths, _ = batch(mx, chunk, pad)
        logits = last_answer(mx, model, ids, lengths)
        mx.eval(logits)
        diff = (logits[:, yes] - logits[:, no]).tolist()
        del logits
        if _mlx(mx, "clear_cache"):
            _mlx(mx, "clear_cache")()
        scored += [(d, y, kind) for d, (_, y, kind) in zip(diff, chunk)]
    out = {}
    for kind in sorted({k for _, _, k in scored}) + ["all"]:
        rows = [(s, y) for s, y, k in scored if kind in ("all", k)]
        out[kind] = {"auc": auc(rows), "acc@0": sum((s > 0) == (y == 1) for s, y in rows) / len(rows) if rows else None,
                     "n": len(rows)}
    return out


def save(mx, model, out, args):
    from mlx.utils import tree_flatten
    out.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(out / "adapters.safetensors"), dict(tree_flatten(model.trainable_parameters())))
    (out / "adapter_config.json").write_text(json.dumps({
        "fine_tune_type": "lora", "num_layers": args.layers,
        "lora_parameters": {"rank": args.rank, "scale": args.scale, "dropout": 0.0},
        "trained_on": str(args.lessons), "iters": args.iters, "base_model": args.model}, indent=1))


def train(model, tokenizer, train_rows, valid_rows, args, log=print):
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx.utils import tree_flatten, tree_map

    yes, no = tokenizer.convert_tokens_to_ids("yes"), tokenizer.convert_tokens_to_ids("no")
    pad = getattr(tokenizer, "pad_token_id", None) or 0
    train_ex, skipped_t = encode(tokenizer, train_rows, args.max_tokens)
    valid_ex, skipped_v = encode(tokenizer, valid_rows, args.max_tokens)
    log(f"lessons: {len(train_ex)} train, {len(valid_ex)} valid ({skipped_t + skipped_v} too long, left out)")

    model.freeze()
    linear_to_lora_layers(model, args.layers, {"rank": args.rank, "scale": args.scale, "dropout": 0.0})
    trainable = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    log(f"trainable parameters: {trainable:,} (LoRA rank {args.rank} on the last {args.layers} layers)")

    def loss_fn(m, ids, lengths, labels):
        logits = last_answer(mx, m, ids, lengths)
        two = mx.stack([logits[:, no], logits[:, yes]], axis=1).astype(mx.float32)
        return nn.losses.cross_entropy(two, labels).mean()

    loss_and_grad = nn.value_and_grad(model, loss_fn)
    # On an 8 GB Mac MLX's buffer cache, which keeps freed buffers for reuse, is what grows into swap:
    # cap it, cap total memory, and clear it after each step.
    if _mlx(mx, "set_cache_limit"):
        _mlx(mx, "set_cache_limit")(args.cache_mb * 1024 * 1024)
    if _mlx(mx, "set_memory_limit") and args.memory_mb:
        _mlx(mx, "set_memory_limit")(args.memory_mb * 1024 * 1024)
    peak = (lambda: _mlx(mx, "get_peak_memory")() / 2**30) if _mlx(mx, "get_peak_memory") else (lambda: float("nan"))
    log("measuring the judge before training")
    opt = optim.Adam(learning_rate=args.lr)
    rnd = random.Random(args.seed)
    before = evaluate(mx, model, valid_ex, yes, no, pad)
    log(f"before: {json.dumps(before)}")
    best, history, t0, running = (before["all"]["auc"] or 0.0), [], time.monotonic(), []
    save(mx, model, args.out, args)                      # an untrained adapter is the floor
    for it in range(1, args.iters + 1):
        total, step_loss = None, 0.0                       # --accumulate small batches: same lesson count
        for _ in range(args.accumulate):                    # per step, a fraction of the memory
            ids, lengths, labels = batch(mx, rnd.sample(train_ex, min(args.batch, len(train_ex))), pad)
            loss, grads = loss_and_grad(model, ids, lengths, labels)
            total = grads if total is None else tree_map(lambda a, b: a + b, total, grads)
            mx.eval(total, loss)
            step_loss += loss.item() / args.accumulate
        opt.update(model, tree_map(lambda g: g / args.accumulate, total))
        mx.eval(model.parameters(), opt.state)
        del total, grads, loss
        if _mlx(mx, "clear_cache"):
            _mlx(mx, "clear_cache")()
        running.append(step_loss)
        if it <= 3 or it % 10 == 0:
            log(f"iter {it}: loss {step_loss:.3f}, {time.monotonic() - t0:.0f} s, peak memory {peak():.2f} GB")
        if args.probe and it >= args.probe:
            log(f"probe done: {it} steps, peak memory {peak():.2f} GB")
            return before, history, best
        if it % args.eval_every == 0 or it == args.iters:
            scores = evaluate(mx, model, valid_ex, yes, no, pad)
            mean = sum(running) / len(running)
            running = []
            history.append({"iter": it, "train_loss": mean, "valid": scores})
            kept = scores["all"]["auc"] is not None and scores["all"]["auc"] > best
            if kept:
                best = scores["all"]["auc"]
                save(mx, model, args.out, args)
            log(f"iter {it}: mean loss {mean:.3f}, valid {json.dumps(scores)}{' (kept)' if kept else ''}, "
                f"{time.monotonic() - t0:.0f} s, peak memory {peak():.2f} GB")
    (args.out / "history.json").write_text(json.dumps({"before": before, "history": history}, indent=1))
    return before, history, best


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="mlx-community/Qwen3-Reranker-0.6B-4bit")
    p.add_argument("--lessons", type=Path, required=True, help="folder with train.jsonl and valid.jsonl")
    p.add_argument("--out", type=Path, required=True, help="adapter folder to write")
    p.add_argument("--iters", type=int, default=600)
    p.add_argument("--batch", type=int, default=1, help="lessons per forward pass")
    p.add_argument("--accumulate", type=int, default=4, help="forward passes per optimiser step")
    p.add_argument("--cache-mb", type=int, default=256, help="cap on MLX's buffer cache")
    p.add_argument("--memory-mb", type=int, default=3000, help="cap on MLX's total memory (0: none)")
    p.add_argument("--probe", type=int, default=0, help="stop after this many steps and report peak memory")
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--rank", type=int, default=8)
    p.add_argument("--scale", type=float, default=20.0)
    p.add_argument("--layers", type=int, default=8, help="LoRA on this many last layers")
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--eval-every", type=int, default=50)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    from mlx_lm import load
    model, tokenizer = load(args.model)
    before, history, best = train(model, tokenizer, read_lessons(args.lessons / "train.jsonl"),
                                  read_lessons(args.lessons / "valid.jsonl"), args,
                                  log=lambda line: print(line, flush=True))
    if args.probe:
        return True
    print(f"best valid AUC {best:.3f} (before {before['all']['auc']}); adapter in {args.out}")
    return math.isfinite(best)


if __name__ == "__main__":
    main()

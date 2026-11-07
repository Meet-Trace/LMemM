#!/usr/bin/env python3
"""Which way of using a sub-1B model best tells whether two things belong to the same project?

    python3 scripts/bench_tiny_methods.py --chat gemma3:1b --chat qwen3.5:0.8b \
        --rerank qwen3-reranker --embed all-minilm:22m --embed embeddinggemma --out results.json

Research only: reads the two labelled days in tests/fixtures/project_grouping, writes nothing else.
Every model call goes to Ollama on this Mac. For each day it takes every kind of pair (same project,
different project in the same sitting = hard, different project elsewhere) and scores them with:

    code         shared names, folders, repos and rare words (the app's assign method's evidence)
    chat:Q       a chat model's confidence (src/tiny_scorer.py) for question Q, both orders, raw and
                 calibrated (lift). Q is one direct question or one of three narrower probes.
    rerank:I     Qwen3-Reranker-0.6B's yes-probability for instruction I, both orders, raw and lift
    embed:M      cosine of two cards' vectors from embedding model M
    combo:F      logistic regression over feature set F, trained on one day and tested on the other

It reports, per method: AUC (how often a same-project pair scores above a different-project pair; 0.5
is chance), AUC on hard pairs only, accuracy at 0 (would the plain yes/no answer be right?), and how
often swapping the order flips the answer. A second test asks the model to pick, out of 3 options,
the one that was part of the same project as an anchor (chance 33%), with every option in every slot.
"""
import argparse
import itertools
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import project_grouping_eval as pge  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import tiny_scorer as ts  # noqa: E402

DAYS = [ROOT / "tests" / "fixtures" / "project_grouping" / f for f in ("sample_day.json", "held_out_day.json")]
CARD_CHARS = 600

QUESTIONS = {
    "same": "Were A and B done for the same project (the same client, product, deliverable, trip or event)?",
    "same+usage": "Were A and B done for the same project (the same client, product, deliverable, trip or event)?",
    "names": "Do A and B name the same specific client, company, product, person or file?",
    "used-for": ("Was one of them used to do the other: read as a source for it, a request about it, "
                 "a reply to it, or notes for it?"),
    "goal": "Are A and B steps toward the same goal?",
}
RERANK = {
    "same-project": ("Given an activity a person did on their computer, judge whether the other activity was part "
                     "of the same project: the same client, product, deliverable, trip or event."),
    "used-for": ("Given an activity a person did on their computer, judge whether the other activity was used "
                 "to do it: a source read for it, a request about it, a reply to it, or notes for it."),
}
PICK = "Which option was done for the same project as this activity (the same client, product, deliverable, trip or event)?"


# ---------------------------------------------------------------- data

def render(item, budget=CARD_CHARS):
    """One card as plain lines, which tiny models read better than JSON."""
    c = graph.card(item, budget)
    lines = [f"App: {c.get('app')}", f"Title: {c.get('title')}"]
    for k, v in (c.get("details") or {}).items():
        lines.append(f"{k.title()}: {', '.join(v) if isinstance(v, list) else v}")
    lines += [f"- {t}" for t in c.get("text") or []]
    return "\n".join(lines)


def usage(a, b, timeline):
    order = sorted(timeline, key=lambda v: v["from"])
    ids = [v["item"] for v in order]
    if any({x, y} == {a, b} for x, y in zip(ids, ids[1:])):
        return "The person switched straight from one to the other."
    at = {}
    for v in order:
        at.setdefault(v["item"], []).append(datetime.fromisoformat(v["from"]))
    gaps = [abs((x - y).total_seconds()) for x in at.get(a, []) for y in at.get(b, [])]
    if not gaps:
        return "Nothing is known about when they were used."
    gap = min(gaps)
    if gap < 3600:
        return f"Used in the same sitting, about {max(1, round(gap / 60))} minutes apart."
    return "Used on the same day, hours apart." if gap < 86400 else "Used on different days."


def pick_pairs(items, timeline, n, seed):
    """Up to n/2 same-project pairs and n/2 different ones, half of those hard (same sitting)."""
    gold = pge.gold(items, "project")
    sit = {}
    for k, s in enumerate(reasoner.sittings(timeline)):
        for p in s:
            sit.setdefault(p, set()).add(k)
    same, hard, easy = [], [], []
    for a, b in itertools.combinations(sorted(items), 2):
        if gold[a] & gold[b]:
            same.append((a, b))
        else:
            (hard if sit.get(a, set()) & sit.get(b, set()) else easy).append((a, b))
    rnd = random.Random(seed)
    for xs in (same, hard, easy):
        rnd.shuffle(xs)
    half = n // 2
    diff = hard[:half // 2] + easy[:half - min(len(hard), half // 2)]
    return [(a, b, 1, False) for a, b in same[:half]] + [(a, b, 0, (a, b) in set(hard)) for a, b in diff], sit


def code_evidence(items, timeline):
    """{frozenset(a, b): evidence} from the assign method's entities, weighted by rarity."""
    ents = {}
    for i, it in items.items():
        names = graph.model_names(graph.Model(graph.CapitalisedNames(), []), it, [])
        ents[i] = assign.entities(it, names)
    df = {}
    for e in ents.values():
        for k in e:
            df[k] = df.get(k, 0) + 1
    n = max(len(items), 1)
    out = {}
    for a, b in itertools.combinations(items, 2):
        shared = ents[a].keys() & ents[b].keys()
        out[frozenset((a, b))] = sum(min(ents[a][k], ents[b][k]) * math.log(n / df[k]) for k in shared)
    return out


# ---------------------------------------------------------------- metrics

def auc(rows, key, only=None):
    rows = [r for r in rows if key in r and (only is None or only(r))]
    pos = [r[key] for r in rows if r["y"] == 1]
    neg = [r[key] for r in rows if r["y"] == 0]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def acc_at_zero(rows, key):
    rows = [r for r in rows if key in r]
    return sum(1 for r in rows if (r[key] > 0) == (r["y"] == 1)) / len(rows) if rows else None


def logistic(train, test, keys, steps=3000, lr=0.1, l2=0.01):
    """Plain logistic regression (no libraries), features standardised on the training day."""
    def col(rows, k):
        return [r[k] for r in rows]
    mu = {k: sum(col(train, k)) / len(train) for k in keys}
    sd = {k: (sum((x - mu[k]) ** 2 for x in col(train, k)) / len(train)) ** 0.5 or 1.0 for k in keys}

    def x(r):
        return [(r[k] - mu[k]) / sd[k] for k in keys] + [1.0]
    w = [0.0] * (len(keys) + 1)
    X, Y = [x(r) for r in train], [r["y"] for r in train]
    for _ in range(steps):
        grad = [l2 * wi for wi in w]
        for xi, yi in zip(X, Y):
            p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, xi))))
            for j, v in enumerate(xi):
                grad[j] += (p - yi) * v / len(X)
        w = [wi - lr * g for wi, g in zip(w, grad)]
    return [sum(a * b for a, b in zip(w, x(r))) for r in test], dict(zip(keys + ["bias"], w))


# ---------------------------------------------------------------- methods

def chat_pair_prompt(q, a_text, b_text, how):
    use = f"How they were used: {how}\n\n" if q == "same+usage" else ""
    return f"Activity A:\n{a_text}\n\nActivity B:\n{b_text}\n\n{use}{QUESTIONS[q]} Answer yes or no."


def score_chat(scorer, name, questions, items, cards, rows, timeline):
    null = {}
    for q in questions:
        for r in rows:
            a, b = r["a"], r["b"]
            how = usage(a, b, timeline)
            s_ab = ts.yes_no(scorer.options(chat_pair_prompt(q, cards[a], cards[b], how)))
            s_ba = ts.yes_no(scorer.options(chat_pair_prompt(q, cards[b], cards[a], how)))
            for i in (a, b):
                for side in ("A", "B"):
                    if (q, i, side) not in null:
                        x, y = (cards[i], ts.NULL_CARD) if side == "A" else (ts.NULL_CARD, cards[i])
                        null[(q, i, side)] = ts.yes_no(scorer.options(chat_pair_prompt(q, x, y, how)))
            # lift: the pair minus each card's habit in the slot it held (S2), both orders (S3)
            l_ab = ts.lift(s_ab, null[(q, a, "A")], null[(q, b, "B")])
            l_ba = ts.lift(s_ba, null[(q, b, "A")], null[(q, a, "B")])
            r[f"{name}:{q}"] = (s_ab + s_ba) / 2
            r[f"{name}:{q}:lift"] = (l_ab + l_ba) / 2
            r[f"{name}:{q}:first"] = s_ab
            r[f"{name}:{q}:flip"] = (s_ab > 0) != (s_ba > 0)
    # all three probes together: a pair counts when the model is sure on any of them
    probes = [q for q in ("names", "used-for", "goal") if q in questions]
    if probes:
        for r in rows:
            r[f"{name}:probes:lift"] = max(r[f"{name}:{q}:lift"] for q in probes)


def score_rerank(scorer, name, items, cards, rows):
    null = {}
    for key, instruct in RERANK.items():
        def s(q, d):
            return ts.yes_no(scorer.options_for(instruct, q, d))
        for r in rows:
            a, b = r["a"], r["b"]
            s_ab, s_ba = s(cards[a], cards[b]), s(cards[b], cards[a])
            for i in (a, b):
                if (key, i, "q") not in null:
                    null[(key, i, "q")] = s(cards[i], ts.NULL_CARD)
                    null[(key, i, "d")] = s(ts.NULL_CARD, cards[i])
            r[f"{name}:{key}"] = (s_ab + s_ba) / 2
            r[f"{name}:{key}:lift"] = (ts.lift(s_ab, null[(key, a, "q")], null[(key, b, "d")]) +
                                       ts.lift(s_ba, null[(key, b, "q")], null[(key, a, "d")])) / 2
            r[f"{name}:{key}:first"] = s_ab
            r[f"{name}:{key}:flip"] = (s_ab > 0) != (s_ba > 0)


def score_embed(embedder, name, cards, rows):
    ids = sorted({r["a"] for r in rows} | {r["b"] for r in rows})
    vecs = dict(zip(ids, embedder([cards[i] for i in ids])))
    for r in rows:
        r[name] = ts.cosine(vecs[r["a"]], vecs[r["b"]])
    return vecs


def pick_tasks(items, sit, n, seed):
    """(anchor, options, index of the true partner): two decoys from other projects, same sitting first."""
    gold = pge.gold(items, "project")
    rnd = random.Random(seed)
    out = []
    for a in sorted(items):
        if not gold[a]:
            continue
        mates = [b for b in items if b != a and gold[a] & gold[b]]
        others = [b for b in items if gold[b] and not gold[a] & gold[b]]
        if not mates or len(others) < 2:
            continue
        rnd.shuffle(others)
        others.sort(key=lambda b: not (sit.get(a, set()) & sit.get(b, set())))
        opts = [rnd.choice(sorted(mates))] + others[:2]
        true = opts[0]
        rnd.shuffle(opts)                       # the true partner must not always start in slot 1
        out.append((a, opts, opts.index(true)))
    rnd.shuffle(out)
    return out[:n]


def run_pick(scorer, cards, tasks):
    """Accuracy of picking the true partner: rotated and averaged (S4) vs one fixed order."""
    rotated = fixed = 0
    for a, opts, true in tasks:
        def prompt_for(order):
            listed = "\n\n".join(f"Option {k + 1}:\n{cards[opts[i]]}" for k, i in enumerate(order))
            return f"Activity:\n{cards[a]}\n\n{listed}\n\n{PICK} Answer with the option number."
        means, first = ts.option_scores(scorer.options, prompt_for, len(opts))
        rotated += max(range(len(opts)), key=lambda i: means[i]) == true
        fixed += first == true
    return rotated / len(tasks), fixed / len(tasks)


# ---------------------------------------------------------------- main

def report(rows, methods):
    print(f"{'method':34} {'AUC':>5} {'hard AUC':>8} {'acc@0':>6} {'flips':>6}")
    for m in methods:
        a, h = auc(rows, m), auc(rows, m, lambda r: r["y"] == 1 or r["hard"])
        z = acc_at_zero(rows, m) if not m.startswith(("embed", "code", "adjacent", "same_sitting")) else None
        flips = [r[m + ":flip"] for r in rows if m + ":flip" in r]
        f = f"{100 * sum(flips) / len(flips):5.0f}%" if flips else "     -"
        cells = [f"{v:5.2f}" if v is not None else "    -" for v in (a, h)]
        print(f"{m:34} {cells[0]:>5} {cells[1]:>8} {(f'{100 * z:5.0f}%' if z is not None else '     -'):>6} {f:>6}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chat", action="append", default=[], help="chat model tag (repeat)")
    p.add_argument("--rerank", action="append", default=[], help="Qwen3-Reranker model tag (repeat)")
    p.add_argument("--rerank-mlx", action="append", default=[], help="Qwen3-Reranker as an MLX repo id (repeat)")
    p.add_argument("--embed", action="append", default=[], help="embedding model tag[@prefix] (repeat)")
    p.add_argument("--questions", default=",".join(QUESTIONS), help="chat questions to ask")
    p.add_argument("--url", default="http://127.0.0.1:11434")
    p.add_argument("--embed-url", default=None, help="Ollama for embeddings (default --url)")
    p.add_argument("--pairs", type=int, default=40, help="pairs per day")
    p.add_argument("--picks", type=int, default=12, help="pick-one-of-3 tasks per day")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, default=None, help="write every score as JSON here")
    args = p.parse_args(argv)
    questions = [q for q in args.questions.split(",") if q]
    all_rows, picks, timing = [], {}, {}
    for day in DAYS:
        items = pge.load_day(day)
        timeline = json.loads(day.read_text()).get("timeline") or []
        cards = {i: render(it) for i, it in items.items()}
        pairs, sit = pick_pairs(items, timeline, args.pairs, args.seed)
        ev = code_evidence(items, timeline)
        switches = assign._switches(timeline)
        rows = [{"day": day.stem, "a": a, "b": b, "y": y, "hard": hard,
                 "code": ev[frozenset((a, b))], "adjacent": float(frozenset((a, b)) in switches),
                 "same_sitting": float(bool(sit.get(a, set()) & sit.get(b, set()))),
                 "titles": [items[a].get("title"), items[b].get("title")]} for a, b, y, hard in pairs]
        tasks = pick_tasks(items, sit, args.picks, args.seed)
        for tag in args.chat:
            s, t0 = ts.ChatScorer(tag, args.url), time.monotonic()
            score_chat(s, f"chat:{tag}", questions, items, cards, rows, timeline)
            picks[(day.stem, tag)] = run_pick(s, cards, tasks) if tasks else None
            timing[(day.stem, tag)] = (s.calls, time.monotonic() - t0)
        rerankers = [(t, lambda t=t: ts.RerankScorer(t, args.url)) for t in args.rerank]
        rerankers += [(t, lambda t=t: ts.MlxRerankScorer(t)) for t in args.rerank_mlx]
        for tag, make in rerankers:
            s, t0 = make(), time.monotonic()
            score_rerank(s, f"rerank:{tag}", items, cards, rows)
            timing[(day.stem, tag)] = (s.calls, time.monotonic() - t0)
        for spec in args.embed:
            tag, _, prefix = spec.partition("@")
            e, t0 = ts.Embedder(tag, args.embed_url or args.url, prefix), time.monotonic()
            score_embed(e, f"embed:{tag}", cards, rows)
            timing[(day.stem, tag)] = (e.calls, time.monotonic() - t0)
        all_rows += rows
        print(f"{day.stem}: {sum(r['y'] for r in rows)} same, {sum(1 for r in rows if r['hard'])} hard different, "
              f"{sum(1 for r in rows if r['y'] == 0 and not r['hard'])} easy different; {len(tasks)} pick tasks")

    methods = ["code", "adjacent", "same_sitting"]
    for tag in args.chat:
        for q in questions:
            methods += [f"chat:{tag}:{q}", f"chat:{tag}:{q}:lift"]
        if any(q in questions for q in ("names", "used-for", "goal")):
            methods.append(f"chat:{tag}:probes:lift")
    for tag in args.rerank + args.rerank_mlx:
        for k in RERANK:
            methods += [f"rerank:{tag}:{k}", f"rerank:{tag}:{k}:lift"]
    methods += [f"embed:{spec.partition('@')[0]}" for spec in args.embed]
    for day in sorted({r["day"] for r in all_rows}):
        print(f"\n== {day}")
        report([r for r in all_rows if r["day"] == day], methods)
    print("\n== both days")
    report(all_rows, methods)

    # does a model add anything beyond code? train on one day, test on the other
    print("\n== combined with code (logistic regression, trained on one day, tested on the other)")
    base = ["code", "adjacent", "same_sitting"]
    sets = {"code only": base}
    for m in methods:
        if m.endswith(":lift") or m.startswith("embed:") or (m.startswith("rerank:") and ":" in m[7:]):
            if not m.endswith(":first"):
                sets[f"code + {m}"] = base + [m]
    days = sorted({r["day"] for r in all_rows})
    for label, keys in sets.items():
        aucs = []
        for test_day in days:
            train = [r for r in all_rows if r["day"] != test_day]
            test = [dict(r) for r in all_rows if r["day"] == test_day]
            scores, _ = logistic(train, test, keys)
            for r, s in zip(test, scores):
                r["combo"] = s
            aucs.append(auc(test, "combo"))
        print(f"  {label:44} AUC " + " / ".join(f"{a:.2f}" for a in aucs) + f"  (tested on {' / '.join(days)})")

    print("\n== pick the same-project option out of 3 (chance 33%)")
    for (day, tag), res in picks.items():
        if res:
            print(f"  {day:14} {tag:16} every slot averaged {100 * res[0]:3.0f}%   one fixed order {100 * res[1]:3.0f}%")
    print("\n== calls and time")
    for (day, tag), (calls, secs) in timing.items():
        print(f"  {day:14} {tag:24} {calls:5d} calls {secs:6.0f} s ({secs / max(calls, 1):.2f} s a call)")
    if args.out:
        args.out.write_text(json.dumps({"rows": all_rows, "picks": {f"{d}|{t}": v for (d, t), v in picks.items()},
                                        "timing": {f"{d}|{t}": v for (d, t), v in timing.items()}}, indent=1))
    return all_rows


if __name__ == "__main__":
    main()

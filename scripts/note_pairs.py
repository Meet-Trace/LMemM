#!/usr/bin/env python3
"""Does "Gemma writes a note per card, all-minilm compares the notes" tell related work apart?

    python3 scripts/note_pairs.py
    python3 scripts/note_pairs.py --day tests/fixtures/project_grouping/held_out_day.json

Uses the same pairs as scripts/judge_pairs.py (every same-project pair and as many different ones).
Each thing gets one Gemma note (src/card_notes.py), then every pair is scored four ways:

    card    all-minilm on the plain card, no Gemma
    note    all-minilm on Gemma's note
    names   names the two notes share
    both    note similarity plus 0.1 for each shared name

For each score it prints how often a related pair scores higher than an unrelated one (1.0 = always,
0.5 = a coin), and the precision and recall at the best cut-off on this day. Then Gemma picks, for some
things, which of two others goes with it (one related, one not), in both orders.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import card_notes as cn  # noqa: E402
import judge_pairs  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402


def ranking(rows, key):
    """Chance that a related pair scores above an unrelated one (ties count half)."""
    yes = [r[key] for r in rows if r["truth"] == "yes"]
    no = [r[key] for r in rows if r["truth"] == "no"]
    if not yes or not no:
        return None
    wins = sum((y > n) + 0.5 * (y == n) for y in yes for n in no)
    return wins / (len(yes) * len(no))


def best_cut(rows, key):
    """(cut, precision, recall, f1) at the cut-off with the best F1 on these rows."""
    best = (None, 0.0, 0.0, 0.0)
    total = sum(1 for r in rows if r["truth"] == "yes")
    for cut in sorted({r[key] for r in rows}):
        said = [r for r in rows if r[key] >= cut]
        right = sum(1 for r in said if r["truth"] == "yes")
        if not said or not total:
            continue
        p, rc = right / len(said), right / total
        f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
        if f1 > best[3]:
            best = (cut, p, rc, f1)
    return best


def triplets(items, n, seed=7):
    """(main, related, unrelated) for up to n things that have both."""
    gold = pge.gold(items, "project")
    rnd, out = random.Random(seed), []
    ids = sorted(items)
    rnd.shuffle(ids)
    for m in ids:
        if not gold[m]:
            continue
        rel = [x for x in sorted(items) if x != m and gold[m] & gold[x]]
        unrel = [x for x in sorted(items) if x != m and not gold[m] & gold[x]]
        if rel and unrel:
            out.append((m, rnd.choice(rel), rnd.choice(unrel)))
        if len(out) >= n:
            break
    return out


def main(argv=None, model=None, embed=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=Path, default=judge_pairs.DEFAULT_DAY)
    p.add_argument("--model", default="gemma3:1b")
    p.add_argument("--url", default="http://127.0.0.1:11434", help="Ollama with the small model")
    p.add_argument("--embed-model", default="all-minilm:22m")
    p.add_argument("--embed-url", default="http://127.0.0.1:11455", help="Ollama with the embedding model")
    p.add_argument("--max-pairs", type=int, default=60)
    p.add_argument("--picks", type=int, default=30)
    p.add_argument("--show-notes", type=int, default=5)
    args = p.parse_args(argv)
    items = pge.load_day(args.day)
    timeline = json.loads(args.day.read_text()).get("timeline") or []
    model = model or reasoner.OllamaModel(args.model, args.url)
    embed = embed or cn.OllamaEmbedder(args.embed_model, args.embed_url)
    same, diff = judge_pairs.pick_pairs(items, args.max_pairs)
    used = sorted({x for pair in same + diff for x in pair})

    started = time.monotonic()
    notes = {i: cn.note(model, items[i]) for i in used}
    note_s = time.monotonic() - started
    started = time.monotonic()
    vec_card = dict(zip(used, embed([cn.card_text(items[i]) for i in used])))
    vec_note = dict(zip(used, embed([cn.note_text(items[i], notes[i]) for i in used])))
    embed_s = time.monotonic() - started

    code, _, _ = assign.run(items, timeline, graph.CapitalisedNames())
    rows = []
    for truth, pairs in (("yes", same), ("no", diff)):
        for a, b in pairs:
            shared = cn.shared_names(notes[a], notes[b])
            note_sim = cn.cosine(vec_note[a], vec_note[b])
            rows.append({"a": a, "b": b, "truth": truth, "card": cn.cosine(vec_card[a], vec_card[b]),
                         "note": note_sim, "names": shared, "both": note_sim + 0.1 * shared,
                         "code": 1 if set(code.get(a, [])) & set(code.get(b, [])) else 0})

    print(f"{args.day.name}: {len(same)} related pairs, {len(diff)} unrelated, {len(used)} things\n"
          f"notes: {len(used)} {args.model} calls in {note_s:.0f} s; embeddings: {embed_s:.1f} s\n")
    empty = sum(1 for n in notes.values() if not n["work"] and not n["topic"] and not n["names"])
    print(f"{empty} of {len(used)} notes came back empty (title and known names used instead)\n")
    print(f"{'score':8} {'related ranks higher':>21} {'cut':>6} {'precision':>10} {'recall':>7} {'F1':>5}")
    for key in ("card", "note", "names", "both", "code"):
        r = ranking(rows, key)
        cut, pr, rc, f1 = best_cut(rows, key)
        print(f"{key:8} {'n/a' if r is None else f'{r:.2f}':>21} {'' if cut is None else f'{cut:.2f}':>6} "
              f"{100 * pr:>9.0f}% {100 * rc:>6.0f}% {f1:>5.2f}")

    trips = triplets(items, args.picks)
    started = time.monotonic()
    picks = [cn.pick(model, items[m], items[r], items[u]) for m, r, u in trips]
    pick_s = time.monotonic() - started
    decided = [x for x in picks if x]
    right = sum(1 for x in decided if x == "a")
    print(f"\npick one of two: {len(trips)} questions, {2 * len(trips)} calls in {pick_s:.0f} s; "
          f"decided {len(decided)}, right {right} of those"
          + (f" ({100 * right / len(decided):.0f}%)" if decided else ""))
    for i in used[:args.show_notes]:
        print(f"  {items[i].get('title')}  ->  {cn.note_text(items[i], notes[i])}")
    return {"rows": rows, "notes": notes, "picks": list(zip(trips, picks))}


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Can a sub-1B model tell which project one thing belongs to, when it sees the project's profile?

    python3 scripts/bench_assign_reasoning.py --chat qwen3.5:0.8b --rerank-mlx mlx-community/Qwen3-Reranker-0.6B-4bit

Research only, on the two labelled days. bench_tiny_methods.py asked about two cards alone; the app's
real decision is "does this thing belong to this project?", where the project is everything already in
it. So each thing X is held out in turn and compared with every project of its day, each shown as a
profile built from its OTHER members (titles, details and the names that recur, all copied, nothing
written by a model). Inboxes and chat lists are left out, as the app leaves them out.

    code       shared names, folders and rare words between X and the project's members
    chat       "Was this activity done for this project?" read as calibrated confidence (tiny_scorer),
               with and without a line on how X was used beside the project's things
    choice     which of the day's projects X was done for, every project in every slot
    rerank     Qwen3-Reranker-0.6B: does X meet "done for this project" for the profile
    combo      logistic regression over code, usage and one model score, trained on one day and tested
               on the other

Reports top-1 accuracy (chance 33% with three projects a day), AUC over every (thing, project) pair,
and the score each method gives things that belong to no project, which should be low (abstain).
"""
import argparse
import copy
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import bench_tiny_methods as bench  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner as reasoner  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import tiny_scorer as ts  # noqa: E402
from project_reasoner_steps import HUB_KINDS  # noqa: E402

PROFILE_THINGS = 6
ASK = "Was this activity done for this project? Answer yes or no."
CHOOSE = "Which project was this activity done for? Answer with the option number."
INSTRUCT = ("Given a project described by the things already in it, judge whether this activity was done for "
            "the project: the same client, product, deliverable, trip or event.")


def entities_of(items):
    out = {}
    for i, it in items.items():
        names = graph.model_names(graph.Model(graph.CapitalisedNames(), []), it, [])
        out[i] = assign.entities(it, names)
    return out


def profile(items, members, ents):
    """A project as the model sees it: its things and the names they share. Copied, never written."""
    rich = sorted(members, key=lambda m: (-len(bench.render(items[m])), m))[:PROFILE_THINGS]
    lines = ["Things already in this project:"]
    for m in rich:
        it, extra = items[m], ""
        state = it.get("state") or {}
        for key in ("file", "chat", "subject", "workspace", "url"):
            if isinstance(state.get(key), str):
                extra = f" [{state[key]}]"
                break
        lines.append(f"- {it.get('title')} ({it.get('app')}){extra}")
    shared = Counter(k for m in members for k, p in ents[m].items() if p >= assign.PRIOR["channel"])
    names = [k for k, n in shared.most_common(8) if n >= 2 and not k.replace(".", "").isdigit()]
    if names:
        lines.append("Names that recur: " + ", ".join(names))
    return "\n".join(lines)


def usage_line(x, members, switches, sit):
    near = sum(1 for m in members if frozenset((x, m)) in switches)
    together = sum(1 for m in members if sit.get(x, set()) & sit.get(m, set()))
    return (f"How it was used: switched straight to or from {near} of the project's things; "
            f"open in the same sitting as {together} of them.")


def chat_prompt(x_text, prof, use=""):
    return f"Activity:\n{x_text}\n\nProject:\n{prof}\n\n{use + chr(10) + chr(10) if use else ''}{ASK}"


GENERIC_LABEL_WORDS = {"pricing", "trip", "health", "wedding", "project"}


def scrub(items):
    """Name-blind copy of a day: the names that identify each project (the client, product, person or
    place in its label: Northwind, Kestrel, LMemM, 180DC, Goa, Riya) are cut from every title, detail and
    text line. Topic words, numbers and people stay. This asks whether a method can see that a TAM sheet,
    a diabetes-care PDF and interview notes are one engagement when no line says "Kestrel", as on real
    days where names are missing or garbled by OCR."""
    gold = pge.gold(items, "project")
    marks = set()
    for p in {p for g in gold.values() for p in g}:
        marks.update(w.casefold() for w in re.findall(r"[A-Za-z0-9]{3,}", p) if w.casefold() not in GENERIC_LABEL_WORDS)
    pattern = re.compile("|".join(sorted((re.escape(m) for m in marks), key=len, reverse=True)), re.IGNORECASE)

    def clean(value):
        if isinstance(value, str):
            return re.sub(r"\s{2,}", " ", pattern.sub("", value)).strip(" -_/")
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: (v if k in ("id", "label", "kind", "app", "first_seen", "last_seen", "source") else clean(v))
                    for k, v in value.items()}
        return value
    return {i: clean(copy.deepcopy(it)) for i, it in items.items()}, sorted(marks)


def build(day, blind=False):
    items = {i: it for i, it in pge.load_day(day).items() if it.get("kind") not in HUB_KINDS}
    if blind:
        items, _ = scrub(items)
    timeline = [v for v in json.loads(day.read_text()).get("timeline") or [] if v["item"] in items]
    gold = pge.gold(items, "project")
    sit = {}
    for k, s in enumerate(reasoner.sittings(timeline)):
        for p in s:
            sit.setdefault(p, set()).add(k)
    switches = assign._switches(timeline)
    ents = entities_of(items)
    df = Counter(k for e in ents.values() for k in e)
    idf = {k: math.log(len(items) / c) for k, c in df.items()}
    projects = sorted({p for g in gold.values() for p in g})
    cards = {i: bench.render(it) for i, it in items.items()}
    rows = []
    for x in sorted(items):
        for p in projects:
            members = [m for m in items if m != x and p in gold[m]]
            if not members:
                continue
            pe = {}
            for m in members:
                for k, v in ents[m].items():
                    pe[k] = max(pe.get(k, 0), v)
            code = sum(min(v, pe[k]) * idf[k] for k, v in ents[x].items() if k in pe)
            rows.append({"day": day.stem, "x": x, "project": p, "y": int(p in gold[x]), "code": code,
                         "near": float(sum(1 for m in members if frozenset((x, m)) in switches)),
                         "together": float(sum(1 for m in members if sit.get(x, set()) & sit.get(m, set()))),
                         "profile": profile(items, members, ents),
                         "use": usage_line(x, members, switches, sit), "title": items[x].get("title")})
    return items, gold, cards, rows


def score_chat(s, name, cards, rows):
    null_x = {}
    for r in rows:
        for variant, use in (("", ""), ("+usage", r["use"])):
            key = f"{name}{variant}"
            pair = ts.yes_no(s.options(chat_prompt(cards[r["x"]], r["profile"], use)))
            if (r["x"], variant) not in null_x:
                null_x[(r["x"], variant)] = ts.yes_no(s.options(chat_prompt(cards[r["x"]], ts.NULL_CARD, use)))
            null_p = ts.yes_no(s.options(chat_prompt(ts.NULL_CARD, r["profile"], use)))
            r[key] = pair
            r[key + ":lift"] = ts.lift(pair, null_x[(r["x"], variant)], null_p)


def score_rerank(s, name, cards, rows):
    null_x = {}
    for r in rows:
        pair = ts.yes_no(s.options_for(INSTRUCT, r["profile"], cards[r["x"]]))
        if r["x"] not in null_x:
            null_x[r["x"]] = ts.yes_no(s.options_for(INSTRUCT, ts.NULL_CARD, cards[r["x"]]))
        null_p = ts.yes_no(s.options_for(INSTRUCT, r["profile"], ts.NULL_CARD))
        r[name] = pair
        r[name + ":lift"] = ts.lift(pair, null_x[r["x"]], null_p)


def score_choice(s, name, cards, rows):
    """Rotated choice among the day's projects (S4); written onto each row as the option's mean logprob."""
    by_x = {}
    for r in rows:
        by_x.setdefault(r["x"], []).append(r)
    for x, rs in by_x.items():
        if len(rs) < 2:
            continue

        def prompt_for(order, rs=rs, x=x):
            listed = "\n\n".join(f"Option {k + 1}:\n{rs[i]['profile']}" for k, i in enumerate(order))
            return f"Activity:\n{cards[x]}\n\n{listed}\n\n{CHOOSE}"
        means, first = ts.option_scores(s.options, prompt_for, len(rs))
        for i, r in enumerate(rs):
            r[name + ":choice"] = means[i]
            r[name + ":choice-first"] = 1.0 if first == i else 0.0


def top1(rows, key):
    """Of the things that belong to some project, how often the top-scoring project is a right one."""
    by_x = {}
    for r in rows:
        if key in r:
            by_x.setdefault(r["x"], []).append(r)
    hits = [max(rs, key=lambda r: (r[key], r["project"]))["y"] for rs in by_x.values() if any(r["y"] for r in rs)]
    return sum(hits) / len(hits) if hits else None


def nowhere(rows, key):
    """Share of belongs-nowhere things whose best score is below the median best score of things that do
    belong: how well the score could abstain on them."""
    by_x = {}
    for r in rows:
        if key in r:
            by_x.setdefault(r["x"], []).append(r)
    best = {x: max(r[key] for r in rs) for x, rs in by_x.items()}
    homed = sorted(best[x] for x, rs in by_x.items() if any(r["y"] for r in rs))
    lost = [best[x] for x, rs in by_x.items() if not any(r["y"] for r in rs)]
    if not homed or not lost:
        return None
    median = homed[len(homed) // 2]
    return sum(1 for b in lost if b < median) / len(lost)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chat", action="append", default=[])
    p.add_argument("--rerank-mlx", action="append", default=[])
    p.add_argument("--rerank", action="append", default=[])
    p.add_argument("--url", default="http://127.0.0.1:11434")
    p.add_argument("--no-choice", action="store_true")
    p.add_argument("--blind", action="store_true", help="cut every project-marking name first (see scrub)")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    all_rows, timing = [], {}
    for day in bench.DAYS:
        items, gold, cards, rows = build(day, args.blind)
        for tag in args.chat:
            s, t0 = ts.ChatScorer(tag, args.url), time.monotonic()
            score_chat(s, f"chat:{tag}", cards, rows)
            if not args.no_choice:
                score_choice(s, f"chat:{tag}", cards, rows)
            timing[(day.stem, tag)] = (s.calls, time.monotonic() - t0)
        rerankers = [(t, lambda t=t: ts.RerankScorer(t, args.url)) for t in args.rerank]
        rerankers += [(t, lambda t=t: ts.MlxRerankScorer(t)) for t in args.rerank_mlx]
        for tag, make in rerankers:
            s, t0 = make(), time.monotonic()
            score_rerank(s, f"rerank:{tag}", cards, rows)
            timing[(day.stem, tag)] = (s.calls, time.monotonic() - t0)
        lost = sorted({r["x"] for r in rows} - {x for x in gold if gold[x]})
        print(f"{day.stem}: {len({r['x'] for r in rows})} things, {len({r['project'] for r in rows})} projects, "
              f"{len(rows)} (thing, project) pairs; belongs nowhere: {', '.join(items[x]['title'] for x in lost)}")
        all_rows += rows

    keys = ["code", "near", "together"]
    for tag in args.chat:
        keys += [f"chat:{tag}", f"chat:{tag}:lift", f"chat:{tag}+usage", f"chat:{tag}+usage:lift"]
        if not args.no_choice:
            keys += [f"chat:{tag}:choice", f"chat:{tag}:choice-first"]
    for tag in args.rerank + args.rerank_mlx:
        keys += [f"rerank:{tag}", f"rerank:{tag}:lift"]
    days = sorted({r["day"] for r in all_rows})
    for scope in days + ["both days"]:
        rs = [r for r in all_rows if scope == "both days" or r["day"] == scope]
        print(f"\n== {scope}\n{'method':48} {'top-1':>6} {'AUC':>5} {'abstains':>8}")
        for k in keys:
            t, a, n = top1(rs, k), bench.auc(rs, k), nowhere(rs, k)
            cells = [f"{100 * t:5.0f}%" if t is not None else "     -", f"{a:5.2f}" if a is not None else "    -",
                     f"{100 * n:7.0f}%" if n is not None else "       -"]
            print(f"{k:48} {cells[0]:>6} {cells[1]:>5} {cells[2]:>8}")

    print("\n== combined with code and usage (trained on one day, tested on the other): top-1 / AUC per test day")
    base = ["code", "near", "together"]
    sets = {"code + usage": base}
    for k in keys[3:]:
        if not k.endswith("choice-first"):
            sets[f"code + usage + {k}"] = base + [k]
    for label, feats in sets.items():
        cells = []
        for test_day in days:
            train = [r for r in all_rows if r["day"] != test_day]
            test = [dict(r) for r in all_rows if r["day"] == test_day]
            scores, w = bench.logistic(train, test, feats)
            for r, s in zip(test, scores):
                r["combo"] = s
            cells.append(f"{100 * top1(test, 'combo'):3.0f}% / {bench.auc(test, 'combo'):.2f}")
        print(f"  {label:60} " + "   ".join(cells) + f"   (tested on {' / '.join(days)})")
    print("\n== calls and time")
    for (day, tag), (calls, secs) in timing.items():
        print(f"  {day:14} {tag:40} {calls:5d} calls {secs:6.0f} s")
    if args.out:
        args.out.write_text(json.dumps(all_rows, indent=1))
    return all_rows


if __name__ == "__main__":
    main()

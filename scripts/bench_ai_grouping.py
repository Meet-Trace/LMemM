#!/usr/bin/env python3
"""Group each labelled day with the AI-first pipeline (src/project_reasoner_ai.py) and score it against
the labels, beside the app's code method.

    python3 scripts/bench_ai_grouping.py --narrator qwen3.5:0.8b \
        --judge-mlx mlx-community/Qwen3-Reranker-0.6B-4bit --blind --cache ai_cache.json --out ai_runs.json

Research only: reads the two labelled days in tests/fixtures/project_grouping, writes only --cache and
--out. Every model runs on this Mac. Variants (one shared cache, so a question asked twice costs once):

    ai            narrate each window, judge specific or general, relate within sittings, relate tasks
                  to every project
    ai-unread     the same without the narrator: the judge reads the raw windows only
    ai-allpairs   relate every pair of the day, not only pairs within a sitting
    ai-allpairs-unread  every pair of the day, without the narrator
    finder        what the app runs (project_finder_ai.py): candidates retrieved per task (--embed,
                  --candidates), a task two projects both claim is filed in neither, joins above --margin
    finder-work-steps  finder-work with the narrator given each window a little at a time: a sentence from the
                  app and title, then again with what was typed, then with what was read. Compare narrator
                  models (--narrator, --cpu) on finder-work and finder-work-steps: after each run a line says
                  how many sentences, seconds a call and how many windows got an invented name
    finder-work-links  finder-work with the beta exact links (exact_links.py): versions of one file in one folder
                  and one git branch of one repository start as one task and the judge is told so
    ai-nogeneral  skip the specific-or-general step, so inboxes go in and the pair judge must keep them out

Each variant runs with both decision rules (raw, calibrated). --blind adds runs on a copy of each day
with the project-marking names cut (bench_assign_reasoning.scrub). Scores: pair precision, recall and
F1 at project level, B-cubed F1, how much was filed, and how much was mixed with something wrong.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import bench_assign_reasoning as profiles  # noqa: E402
import bench_tiny_methods as bench  # noqa: E402
import project_grouping_eval as pge  # noqa: E402
import project_reasoner_ai as ai  # noqa: E402
import project_reasoner_assign as assign  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402
import tiny_scorer as ts  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "project_grouping"
VARIANTS = {"ai": {}, "ai-unread": {"unread": True}, "ai-allpairs": {"all_pairs": True},
            "ai-nogeneral": {"specific": False}, "ai-gate-raw": {"gate_rule": "raw"},
            "ai-allpairs-nogeneral": {"all_pairs": True, "specific": False},
            "ai-allpairs-unread": {"all_pairs": True, "unread": True},
            "finder": {"retrieve": True, "abstain_shared": True},
            "finder-nodesc": {"retrieve": True, "abstain_shared": True, "use_description": False},
            "finder-mean": {"retrieve": True, "abstain_shared": True, "confirm": "mean"},
            "finder-work": {"retrieve": True, "abstain_shared": True, "confirm": "mean", "worth": True},
            "finder-work-steps": {"retrieve": True, "abstain_shared": True, "confirm": "mean", "worth": True,
                                  "say_steps": True},
            "finder-work-links": {"retrieve": True, "abstain_shared": True, "confirm": "mean", "worth": True,
                                  "links": True},
            "finder-nodesc-mean": {"retrieve": True, "abstain_shared": True, "use_description": False,
                                   "confirm": "mean"},
            "finder-nodesc-best": {"retrieve": True, "abstain_shared": True, "use_description": False,
                                   "confirm": "best"}}


def scores(truth, assigned, log=()):
    """Grouping scores, plus how well the model's raw answers rank, whatever the threshold:
    pair AUC    same-project pairs above different-project pairs, over the pairs the judge was asked
    general AUC things that belong to no project above things that do, on the general-place answer"""
    s, b = pge.score(truth, assigned, "project"), pge.bcubed(truth, assigned, "project")
    pairs, general = {}, []
    for e in log:
        if e["step"] == "relate":
            for k, v in e["pairs"].items():
                a, c = k.split(" + ")
                pairs[k] = {"s": v, "y": int(bool(truth[a] & truth[c]))}
        elif e["step"] == "general":
            general.append({"s": e["score"], "y": int(not truth[e["id"]])})
    return {"precision": s["precision"], "recall": s["recall"], "f1": s["f1"], "b3": b["f1"],
            "filed": b["filed"], "wrong_mix": b["wrong_mix"], "false_merges": s["false_merges"], "missed": s["missed"],
            "pair_auc": bench.auc(list(pairs.values()), "s"), "pairs_asked": len(pairs),
            "general_auc": bench.auc(general, "s")}


def pct(x):
    return "    -" if x is None else f"{100 * x:4.0f}%"


def explain(items, summary, log, truth):
    """The model's own account of the day: each project, what it understood of each thing, and why."""
    out = []
    said = {e["id"]: e for e in log if e["step"] == "understand"}
    general = {e["id"]: e for e in log if e["step"] == "general"}
    for path, p in summary.items():
        out.append(f"  {path}: {p['description'] or ''}"
                   + (f"  [IDLE looking around, not filed: work score {p['work']:+.1f}]" if p.get("idle") else ""))
        for m in p["members"]:
            true = ", ".join(sorted(truth[m])) or "no project"
            line = said.get(m, {}).get("said") or items[m].get("title")
            out.append(f"     - {m} [{true}]: {line}")
    left = [i for i in items if not any(i in p["members"] for p in summary.values())]
    for i in left:
        why = general.get(i)
        reason = f"general place, P(general) {ai.chance(why['score']):.2f}" if why and why["score"] > 0 else "in no project"
        out.append(f"  not filed: {i} [{', '.join(sorted(truth[i])) or 'no project'}] ({reason})")
    cuts = [(e["id"], e["cut"]) for e in log if e["step"] == "understand" and e.get("cut")]
    if cuts:
        out.append("  names the narrator wrote that the window never showed (cut): "
                   + "; ".join(f"{i}: {', '.join(c)}" for i, c in cuts))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--narrator", default=None, help="Ollama chat model that writes (qwen3.5:0.8b)")
    p.add_argument("--narrator-mlx", default=None, help="the narrator's MLX build, run with mlx-lm instead of Ollama")
    p.add_argument("--judge-mlx", default=None, help="Qwen3-Reranker repo for mlx-lm")
    p.add_argument("--judge-adapter", default=None, help="LoRA adapter folder from train_judge.py for --judge-mlx")
    p.add_argument("--judge", default=None, help="Ollama reranker tag")
    p.add_argument("--judge-chat", default=None, help="Ollama chat model used as the judge instead")
    p.add_argument("--work-judge", default=None, help="Ollama reranker tag that answers only the 'is this "
                                                       "deliberate work?' question (finder-work), another model "
                                                       "than the one grouping")
    p.add_argument("--url", default="http://127.0.0.1:11434")
    p.add_argument("--cpu", type=int, default=0, help="Ollama models on the CPU only, with this many threads")
    p.add_argument("--narrator-cold", action="store_true", help="unload the Ollama narrator after each sentence "
                                                                 "(a warm prompt cache changed its sentences)")
    p.add_argument("--embed", default=None, help="Ollama embedding model for the finder variant's retrieval")
    p.add_argument("--candidates", type=int, default=5, help="projects a task is judged against (finder)")
    p.add_argument("--margin", type=float, default=0.0, help="log-odds a join must clear (finder)")
    p.add_argument("--bias-pair", type=float, default=0.0, help="shift of the judge's pair answers (see ai.run bias)")
    p.add_argument("--bias-project", type=float, default=0.0, help="shift of the judge's project answers (see ai.run bias)")
    p.add_argument("--variants", default="ai,ai-unread")
    p.add_argument("--rules", default=",".join(ai.RULES))
    p.add_argument("--blind", action="store_true", help="also run on name-blind copies of each day")
    p.add_argument("--days", default=None, help="comma-separated fixture stems, such as meaning_day "
                                                "(default: the two labelled days)")
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    variants = [v for v in args.variants.split(",") if v]
    rules = [r for r in args.rules.split(",") if r]
    for v in variants:
        if v not in VARIANTS:
            p.error(f"unknown variant {v}; choose from {', '.join(VARIANTS)}")
    cpu = ts.cpu_only(args.cpu) if args.cpu else None
    if args.judge_mlx:
        scorer = ts.MlxRerankScorer(args.judge_mlx, args.judge_adapter)
    elif args.judge:
        scorer = ts.RerankScorer(args.judge, args.url, timeout=300, extra=cpu)
    elif args.judge_chat:
        scorer = ts.ChatScorer(args.judge_chat, args.url, extra=cpu)
    else:
        p.error("name a judge: --judge-mlx, --judge or --judge-chat")
    cache = json.loads(args.cache.read_text()) if args.cache and args.cache.exists() else {}
    judge = ai.Judge(scorer, cache, name=(f"adapter={args.judge_adapter}" if args.judge_adapter
                                          else f"ollama={args.judge}" if args.judge and args.cpu else ""))
    work_judge = (ai.Judge(ts.RerankScorer(args.work_judge, args.url, timeout=300, extra=cpu), cache,
                           name=f"ollama={args.work_judge}" if args.cpu else "") if args.work_judge else None)
    if args.narrator_mlx:
        narrator = ai.Narrator(ts.MlxChatScorer(args.narrator_mlx), cache, name=f"mlx={args.narrator_mlx}")
    elif args.narrator:
        # a sentence is cached under the model that wrote it, so narrators compared on one cache never read each other's
        tag = ("cold=" if args.narrator_cold else "") + ("cpu=" if args.cpu else "")
        narrator = ai.Narrator(ts.ChatScorer(args.narrator, args.url, timeout=300, extra=cpu,
                                             keep_alive=0 if args.narrator_cold else None),
                               cache, name=f"{tag}{args.narrator}" if tag else "")
    else:
        narrator = None
    retriever = ai.Retriever(ts.Embedder(args.embed, args.url, timeout=300, extra=cpu) if args.embed else None,
                             k=args.candidates)
    if narrator is None:
        variants = [v for v in variants if VARIANTS[v].get("unread")] or ["ai-unread"]

    days = bench.DAYS if not args.days else [FIXTURES / f"{stem}.json" for stem in args.days.split(",")]
    runs, table = [], []
    for day in days:
        for blind in ([False, True] if args.blind else [False]):
            items = pge.load_day(day)
            if blind:
                items, _ = profiles.scrub(items)
            timeline = json.loads(day.read_text()).get("timeline") or []
            truth = pge.gold(items, "project")
            scope = day.stem + (" (names cut)" if blind else "")
            assigned, _, _ = assign.run(items, timeline, graph.CapitalisedNames())
            table.append((scope, "code (assign method)", scores(truth, assigned), 0, 0.0))
            for v in variants:
                opts = dict(VARIANTS[v])
                unread = opts.pop("unread", False)
                if args.bias_pair or args.bias_project:
                    opts["bias"] = {"pair": args.bias_pair, "project": args.bias_project}
                if work_judge is not None and opts.get("worth"):
                    opts["work_judge"] = work_judge
                if opts.pop("retrieve", False):
                    opts.update(retrieve=retriever, margin=args.margin)
                for rule in rules:
                    w0, ws = (work_judge.calls, work_judge.seconds) if work_judge else (0, 0.0)
                    j0, js, n0, ns, t0 = judge.calls, judge.seconds, (narrator.calls if narrator else 0), \
                        (narrator.seconds if narrator else 0.0), time.monotonic()
                    assigned, summary, log = ai.run(items, timeline, judge, None if unread else narrator, rule, **opts)
                    calls = judge.calls - j0 + ((narrator.calls - n0) if narrator else 0) \
                        + ((work_judge.calls - w0) if work_judge else 0)
                    secs = judge.seconds - js + ((narrator.seconds - ns) if narrator else 0.0) \
                        + ((work_judge.seconds - ws) if work_judge else 0.0)
                    sc = scores(truth, assigned, log)
                    table.append((scope, f"{v} {rule}", sc, calls, secs))
                    said = [e for e in log if e["step"] == "understand"]
                    reader = None
                    if narrator is not None and not unread and said:
                        n_calls, n_secs = narrator.calls - n0, narrator.seconds - ns
                        invented = sum(1 for e in said if e.get("cut"))
                        reader = {"sentences": len(said), "new_calls": n_calls, "seconds": n_secs,
                                  "seconds_a_call": n_secs / n_calls if n_calls else None,
                                  "invented": invented, "invented_share": invented / len(said)}
                        print(f"   reader {scope} {v} {rule}: {len(said)} sentences, {n_calls} new calls"
                              + (f", {n_secs / n_calls:.2f} s a call" if n_calls else " (all from the cache)")
                              + f", {invented} with a name the window never showed ({100 * invented / len(said):.0f}%)")
                    runs.append({"day": day.stem, "blind": blind, "variant": v, "rule": rule, "scores": sc,
                                 "assigned": assigned, "projects": summary, "log": log, "reader": reader,
                                 "new_calls": calls, "seconds": secs, "wall": time.monotonic() - t0})
                    if v == "ai" and rule == rules[0]:
                        print(f"\n== {scope}: what the model understood ({v}, {rule})")
                        print(explain(items, summary, log, truth))
                    if args.cache:
                        args.cache.write_text(json.dumps(cache))
            print(f"\n== {scope}\n{'method':28} {'pair P':>6} {'pair R':>6} {'pair F1':>7} {'B3 F1':>6} "
                  f"{'filed':>6} {'mixed':>6} {'pair AUC':>8} {'pairs':>5} {'gen AUC':>7} {'new calls':>9} {'secs':>6}")
            for sc_name, method, sc, calls, secs in table:
                if sc_name == scope:
                    auc = lambda x: "     -" if x is None else f"{x:6.2f}"  # noqa: E731
                    print(f"{method:28} {pct(sc['precision']):>6} {pct(sc['recall']):>6} {pct(sc['f1']):>7} "
                          f"{pct(sc['b3']):>6} {pct(sc['filed']):>6} {pct(sc['wrong_mix']):>6} {auc(sc.get('pair_auc')):>8} "
                          f"{sc.get('pairs_asked', 0):5d} {auc(sc.get('general_auc')):>7} {calls:9d} {secs:6.0f}")
            sys.stdout.flush()

    print("\n== mean over days")
    for blind in ([False, True] if args.blind else [False]):
        rows = [t for t in table if t[0].endswith(" (names cut)") == blind]
        for method in dict.fromkeys(t[1] for t in rows):
            mine = [t[2] for t in rows if t[1] == method]
            f1 = [s["f1"] or 0.0 for s in mine]
            b3 = [s["b3"] or 0.0 for s in mine]
            print(f"  {'names cut  ' if blind else 'names kept '} {method:28} pair F1 {pct(sum(f1) / len(f1))}   "
                  f"B3 F1 {pct(sum(b3) / len(b3))}")
    if args.out:
        args.out.write_text(json.dumps(runs, indent=1))
    return runs, table


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Turn written project stories into lessons for the judge (Qwen3-Reranker-0.6B). Research only.

    python3 scripts/make_judge_lessons.py --stories research/judge_lessons/stories.json --out lessons/

A story is one invented project told as the windows a person used for it, none taken from anyone's data:
    {"id": "dog-adoption", "theme": "adopting a rescue dog",
     "windows": [{"app": "Safari", "title": "...", "details": {"url": "..."}, "lines": ["..."],
                  "did": "one sentence a good narrator would write"}, ...],
     "general": [{...a general place seen during it: an inbox, a feed...}]}

Lessons are written with the pipeline's own instructions and text layout (project_reasoner_ai), so the judge
learns the exact questions it is asked:

    pair      two windows of one story -> yes; a window of another story -> no, half of them a story that
              shares a word with it (a pitch deck and a wooden deck), so shared words stop being the answer
    project   2 to 4 windows of a story as the project, one more window -> yes; another story's -> no
    general   a general place -> yes; a story's window -> no. General places come from the stories and from a
              shared pool (places/*.json, a list of windows), which is split into train and valid the same way
    work      3 to 5 windows of a story -> yes (deliberate work to keep track of); 3 to 4 windows of an
              "aimless" story ({"aimless": true}: reading logs, scrolling feeds, poking in folders) or of
              general places -> no. Aimless stories are used for this question only.

The narrator's sentence is kept in half the lessons and dropped in the rest, so the judge reads windows
well whether or not the sentence is good. Stories are split into train and valid, never one story in
both. Themes of the test days are refused (TEST_THEMES): those days must stay unseen.

Round 5 (stories with "round": 5, written by make_hard_stories.py) teaches the cases the judge got wrong:
    twins     a story names a look-alike story ("twins"); three in four of its "no" answers use a window of the
              twin (the same repository on another branch, the same file name in another folder), and the two
              always land in the same split, so the validation set holds look-alikes too
    pivots    a story that changes topic ("pivot": true, windows marked "phase": early, bridge, late); pairs of
              an early and a late window are taught twice, and a late window is judged against a project made
              only of early windows, and the other way round
Such stories may not use the words of the invented trail days (EXAM_WORDS): those days stay the exam.
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import project_reasoner_ai as ai  # noqa: E402
import project_reasoner_graph as graph  # noqa: E402

TEST_THEMES = ("wedding", "fundrais", "investor", "marathon", "running", "pricing", "market sizing", "goa",
               "lmemm", "northwind", "kestrel", "diabetes",
               # near relatives of the no-shared-words day, so it tests reading, not recall of a lesson
               "fitness", "gym", "cycling", "triathlon", "sangeet", "mehendi", "ceremony", "seed round", "angel",
               "venture", "pitch deck")
# The words of the invented trail days (make_trail_days.py), refused in round-5 stories whole word by whole word.
EXAM_WORDS = re.compile(
    r"\b(wagtail|biscuit\w*|rover|sitters?|walkers?|pupp(?:y|ies)|dogs?|pets?|labradors?|vaccinat\w*|shelter|crates?"
    r"|gst|stripe|pincodes?|harbour|proposals?|lantern|quartz|granite|contractors?|countertops?|flat renovation"
    r"|ledgerly|tidepool|csv|login|timeouts?|sentry|nextcloud|homeserver|docker|annual report|monsoon|mumbai"
    r"|lofi|budget_v\d|report_v\d|report_final)\b", re.I)
DOC_CHARS = 1400
TWIN_SHARE = 0.75          # of a twin story's "no" answers, the share that use a window of its twin


def item(window):
    """A story window in the app's item shape, so the pipeline's own text layout applies."""
    return {"app": window["app"], "title": window["title"], "state": dict(window.get("details") or {}),
            "activity": {"reading": {"text": list(window.get("lines") or [])}}}


def activity(window, said, budget=None):
    head = f"What the person was doing: {window['did']}\n" if said and window.get("did") else ""
    return head + ai.window_text(item(window), budget or ai.ACTIVITY_CHARS)


def group_doc(windows, said):
    """A group of windows as the pipeline shows it to the judge (project_reasoner_ai's group_doc)."""
    budget = max(120, ai.ACTIVITY_CHARS // len(windows) * 2)
    return "\n\n".join(activity(w, said, budget) for w in windows)[:ai.TASK_CHARS]


def brief(window, said):
    if said and window.get("did"):
        return f"{window['did']} ({window['title']}, {window['app']})"
    lines = (window.get("lines") or [])[:1]
    return f"{window['title']} ({window['app']})" + (f": {lines[0]}" if lines else "")


def words(window):
    return graph.tokens(" ".join([window["title"]] + list((window.get("details") or {}).values())
                                 + list(window.get("lines") or [])))


def story_text(s):
    """Everything a story says, as one text."""
    parts = [s["id"], s["theme"]]
    for win in list(s["windows"]) + list(s.get("general") or []):
        parts += [win["app"], win["title"], win.get("did", "")] + [str(v) for v in (win.get("details") or {}).values()]
        parts += list(win.get("lines") or [])
    return "\n".join(parts)


def check(stories):
    seen = set()
    for s in stories:
        if s["id"] in seen:
            raise ValueError(f"Two stories are called {s['id']}.")
        seen.add(s["id"])
        theme = s["theme"].casefold()
        hit = [t for t in TEST_THEMES if t in theme]
        if hit:
            raise ValueError(f"Story {s['id']} is about {hit[0]}, a theme of the test days. Leave it out.")
        if len(s["windows"]) < 3:
            raise ValueError(f"Story {s['id']} needs at least 3 windows.")
        if s.get("round"):
            word = EXAM_WORDS.search(story_text(s))
            if word:
                raise ValueError(f"Story {s['id']} uses '{word.group(0)}', a word of the trail days. Leave it out.")
    ids = {s["id"] for s in stories}
    by_id = {s["id"]: s for s in stories}
    for s in stories:
        for t in s.get("twins") or []:
            if t not in ids or s["id"] not in (by_id[t].get("twins") or []):
                raise ValueError(f"Story {s['id']} names {t} as its twin, which does not name it back.")
        if s.get("pivot") and {x.get("phase") for x in s["windows"]} != {"early", "bridge", "late"}:
            raise ValueError(f"Pivot story {s['id']} needs early, bridge and late windows.")


def lessons(stories, rnd, places=(), split="train"):
    out = []
    aimless = [s for s in stories if s.get("aimless")]
    stories = [s for s in stories if not s.get("aimless")]
    flat = [(s["id"], w) for s in stories for w in s["windows"]]

    def work(windows, label, story):
        said = rnd.random() < 0.5
        out.append({"kind": "work", "instruct": ai.INSTRUCT_WORK, "query": ai.QUERY_WORK,
                    "doc": group_doc(windows, said), "label": label, "story": story})

    twins = {s["id"]: set(s.get("twins") or []) for s in stories}

    def other(sid, near):
        """A window of another story; with `near`, one sharing a word with `near` when there is one.
        A story with a twin draws most of its answers from the twin, the look-alike project."""
        pool = [(o, w) for o, w in flat if o != sid]
        if twins.get(sid) and rnd.random() < TWIN_SHARE:
            look_alikes = [(o, w) for o, w in pool if o in twins[sid]]
            if look_alikes:
                pool = look_alikes
        if near is not None:
            mine = words(near)
            close = [(o, w) for o, w in pool if mine & words(w)]
            if close:
                return rnd.choice(close)[1]
        return rnd.choice(pool)[1]

    for s in stories:
        ws = s["windows"]
        for i, a in enumerate(ws):
            for b in ws[i + 1:]:
                across = s.get("pivot") and {a.get("phase"), b.get("phase")} == {"early", "late"}
                for q, d in ((a, b), (b, a)) * (2 if across else 1):
                    said = rnd.random() < 0.5
                    out.append({"kind": "pair", "instruct": ai.INSTRUCT_PAIR, "query": activity(q, said),
                                "doc": activity(d, said), "label": 1, "story": s["id"]})
                    neg = other(s["id"], q if rnd.random() < 0.5 else None)
                    out.append({"kind": "pair", "instruct": ai.INSTRUCT_PAIR, "query": activity(q, said),
                                "doc": activity(neg, said), "label": 0, "story": s["id"]})
        for held in ws:
            rest = [w for w in ws if w is not held]
            said = rnd.random() < 0.5
            members = rnd.sample(rest, min(len(rest), rnd.randint(2, 4)))
            query = "Activities already in it:\n" + "\n".join(f"- {brief(m, said)}" for m in members)
            for doc, label in ((held, 1), (other(s["id"], held if rnd.random() < 0.5 else None), 0)):
                out.append({"kind": "project", "instruct": ai.INSTRUCT_PROJECT, "query": query,
                            "doc": activity(doc, said)[:DOC_CHARS], "label": label, "story": s["id"]})
        if s.get("pivot"):         # a project of one topic, a window of the other, in both directions
            for held_phase, member_phases in (("late", ("early", "bridge")), ("early", ("late", "bridge"))):
                members_pool = [m for m in ws if m.get("phase") in member_phases]
                for held in (w for w in ws if w.get("phase") == held_phase):
                    said = rnd.random() < 0.5
                    members = rnd.sample(members_pool, min(len(members_pool), rnd.randint(2, 3)))
                    query = "Activities already in it:\n" + "\n".join(f"- {brief(m, said)}" for m in members)
                    for doc, label in ((held, 1), (other(s["id"], held if rnd.random() < 0.5 else None), 0)):
                        out.append({"kind": "project", "instruct": ai.INSTRUCT_PROJECT, "query": query,
                                    "doc": activity(doc, said)[:DOC_CHARS], "label": label, "story": s["id"]})
        for _ in range(2):
            work(rnd.sample(ws, min(len(ws), rnd.randint(3, 5))), 1, s["id"])
        for g in s.get("general") or []:
            for w, label in ((g, 1), (rnd.choice(ws), 0)):
                out.append({"kind": "general", "instruct": ai.INSTRUCT_GENERAL, "query": ai.QUERY_GENERAL,
                            "doc": activity(w, rnd.random() < 0.5), "label": label, "story": s["id"]})
    for s in aimless:
        for _ in range(5):
            work(rnd.sample(s["windows"], min(len(s["windows"]), rnd.randint(3, 4))), 0, s["id"])
    for _ in range(len(places) // 2 + (3 if places else 0)):                   # a few general places together
        work(rnd.sample(list(places), min(len(places), rnd.randint(3, 4))), 0, f"places:{split}")
    for g in places:                       # each pool place with and without the sentence, against a story window
        for said in (True, False):
            for w, label in ((g, 1), (rnd.choice(flat)[1], 0)):
                out.append({"kind": "general", "instruct": ai.INSTRUCT_GENERAL, "query": ai.QUERY_GENERAL,
                            "doc": activity(w, said), "label": label, "story": f"place:{g['title']}"})
    return out


def twin_clusters(stories):
    """Stories grouped by twin (a pair, or one alone), each group in id order, groups in id order."""
    by_id = {s["id"]: s for s in stories}
    seen, groups = set(), []
    for sid in sorted(by_id):
        if sid in seen:
            continue
        group, todo = [], [sid]
        while todo:
            cur = todo.pop()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            group.append(cur)
            todo += list(by_id[cur].get("twins") or [])
        groups.append(sorted(group))
    return groups


def split_hard(stories, valid, seed):
    """Round-5 stories into train and valid by group, so a story and its twin are never apart: a share of the
    twin pairs and a share of the pivots (at least one of each) go to validation."""
    pairs = [g for g in twin_clusters(stories) if len(g) > 1]
    alone = [g for g in twin_clusters(stories) if len(g) == 1]
    rnd = random.Random(seed + 5)
    rnd.shuffle(pairs)
    rnd.shuffle(alone)
    taken = pairs[:max(1, round(len(pairs) * valid))] + alone[:max(1, round(len(alone) * valid))]
    ids = {i for g in taken for i in g}
    by_id = {s["id"]: s for s in stories}
    return {"valid": [by_id[i] for i in sorted(ids)], "train": [s for s in stories if s["id"] not in ids]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stories", type=Path, default=ROOT / "research" / "judge_lessons",
                   help="a stories file, or a folder whose *.json files are all read")
    p.add_argument("--places", type=Path, default=ROOT / "research" / "judge_lessons" / "places",
                   help="a folder of general places (*.json lists of windows), split like the stories")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--valid", type=float, default=0.15, help="share of stories kept for validation")
    p.add_argument("--skip", default="", help="comma-separated stories file names (without .json) to leave out, "
                   "e.g. stories_aimless,stories_tooling")
    p.add_argument("--kinds", default="", help="comma-separated kinds of lesson to keep: pair, project, general, work "
                   "(all when empty). The grouping judge (judge_v3) was taught without work lessons.")
    p.add_argument("--hard-valid", type=float, default=0.25,
                   help="share of the round-5 twin pairs and pivots kept for validation")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    files = sorted(args.stories.glob("*.json")) if args.stories.is_dir() else [args.stories]
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    files = [f for f in files if f.stem not in skip]
    kinds = {x.strip() for x in args.kinds.split(",") if x.strip()}
    if kinds - {"pair", "project", "general", "work"}:
        p.error("--kinds takes pair, project, general and work")
    stories = [s for f in files for s in json.loads(f.read_text())]
    check(stories)
    rnd = random.Random(args.seed)
    hard = [s for s in stories if s.get("round")]
    order = sorted((s for s in stories if not s.get("round")), key=lambda s: s["id"])
    rnd.shuffle(order)                     # as before round 5, so the older stories split the same way
    n_valid = max(1, round(len(order) * args.valid))
    split = {"valid": order[:n_valid], "train": order[n_valid:]}
    if hard:
        for name, group in split_hard(hard, args.hard_valid, args.seed).items():
            split[name] += group
    pool = [g for f in (sorted(args.places.glob("*.json")) if args.places.is_dir() else [])
            for g in json.loads(f.read_text())]
    rnd.shuffle(pool)
    n_pool = round(len(pool) * args.valid)
    places = {"valid": pool[:n_pool], "train": pool[n_pool:]}
    args.out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, group in split.items():
        rows = lessons(group, rnd, places[name], name)
        rows = [r for r in rows if not kinds or r["kind"] in kinds]
        rnd.shuffle(rows)
        with open(args.out / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts[name] = {k: sum(1 for r in rows if r["kind"] == k) for k in ("pair", "project", "general", "work")}
        counts[name]["yes share"] = round(sum(r["label"] for r in rows) / max(1, len(rows)), 2)
        counts[name]["round 5"] = sum(1 for r in rows if r["story"] in {s["id"] for s in hard})
    print(json.dumps({"stories": {k: len(v) for k, v in split.items()}, "lessons": counts}, indent=1))
    return counts


if __name__ == "__main__":
    main()

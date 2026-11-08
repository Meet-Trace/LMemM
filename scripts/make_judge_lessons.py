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
"""
import argparse
import json
import random
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
DOC_CHARS = 1400


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


def lessons(stories, rnd, places=(), split="train"):
    out = []
    aimless = [s for s in stories if s.get("aimless")]
    stories = [s for s in stories if not s.get("aimless")]
    flat = [(s["id"], w) for s in stories for w in s["windows"]]

    def work(windows, label, story):
        said = rnd.random() < 0.5
        out.append({"kind": "work", "instruct": ai.INSTRUCT_WORK, "query": ai.QUERY_WORK,
                    "doc": group_doc(windows, said), "label": label, "story": story})

    def other(sid, near):
        """A window of another story; with `near`, one sharing a word with `near` when there is one."""
        pool = [(o, w) for o, w in flat if o != sid]
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
                for q, d in ((a, b), (b, a)):
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stories", type=Path, default=ROOT / "research" / "judge_lessons",
                   help="a stories file, or a folder whose *.json files are all read")
    p.add_argument("--places", type=Path, default=ROOT / "research" / "judge_lessons" / "places",
                   help="a folder of general places (*.json lists of windows), split like the stories")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--valid", type=float, default=0.15, help="share of stories kept for validation")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    files = sorted(args.stories.glob("*.json")) if args.stories.is_dir() else [args.stories]
    stories = [s for f in files for s in json.loads(f.read_text())]
    check(stories)
    rnd = random.Random(args.seed)
    order = sorted(stories, key=lambda s: s["id"])
    rnd.shuffle(order)
    n_valid = max(1, round(len(order) * args.valid))
    split = {"valid": order[:n_valid], "train": order[n_valid:]}
    pool = [g for f in (sorted(args.places.glob("*.json")) if args.places.is_dir() else [])
            for g in json.loads(f.read_text())]
    rnd.shuffle(pool)
    n_pool = round(len(pool) * args.valid)
    places = {"valid": pool[:n_pool], "train": pool[n_pool:]}
    args.out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, group in split.items():
        rows = lessons(group, rnd, places[name], name)
        rnd.shuffle(rows)
        with open(args.out / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts[name] = {k: sum(1 for r in rows if r["kind"] == k) for k in ("pair", "project", "general", "work")}
        counts[name]["yes share"] = round(sum(r["label"] for r in rows) / max(1, len(rows)), 2)
    print(json.dumps({"stories": {k: len(v) for k, v in split.items()}, "lessons": counts}, indent=1))
    return counts


if __name__ == "__main__":
    main()

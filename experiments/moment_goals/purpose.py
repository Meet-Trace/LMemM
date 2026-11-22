import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grouping import embed

PURPOSE_PROMPT = """You are given what a person did during one short moment on their computer.

What they did (an action, not a reason):
{action}

What was on screen (context only):
{context}

State why they were doing it. Name the outcome they are working towards, in plain words, as if finishing the sentence "They are trying to ...". Do not repeat the action. Do not name a goal you cannot infer from the evidence; say "unclear" instead.

Reply with exactly two lines:
PURPOSE: <the outcome they are working towards, at most 20 words>
WHY HERE: <why this app or tool serves that outcome, at most 15 words>"""

MATCH_PROMPT = """Two moments from one person's day. Decide whether they serve the SAME goal, meaning the same specific outcome the person is working towards, so that finishing one would be part of finishing the other.

Being in the same app, using the same tool, mentioning the same word, or looking at related material is NOT enough. Two moments in a terminal can serve different goals. A browser and a terminal can serve the same goal.

Moment A
Action: {action_a}
Purpose: {purpose_a}
Why here: {why_a}

Moment B
Action: {action_b}
Purpose: {purpose_b}
Why here: {why_b}

Reply with exactly two lines:
VERDICT: SAME or DIFFERENT
REASON: <one sentence naming the shared outcome, or the two different outcomes>"""

UNCLEAR = re.compile(r"^\W*(unclear|unknown|not clear|insufficient|n/?a)\b", re.I)


def parse_purpose(text):
    text = text or ""
    purpose = re.search(r"PURPOSE\s*:\s*(.+)", text, re.I)
    why = re.search(r"WHY HERE\s*:\s*(.+)", text, re.I)
    p = purpose.group(1).strip() if purpose else ""
    w = why.group(1).strip() if why else ""
    if not p or UNCLEAR.match(p):
        return {"purpose": "", "why": w, "clear": False}
    return {"purpose": p, "why": w, "clear": True}


def parse_verdict(text):
    text = text or ""
    verdict = re.search(r"VERDICT\s*:\s*\W*(SAME|DIFFERENT)", text, re.I)
    reason = re.search(r"REASON\s*:\s*(.+)", text, re.I)
    if not verdict:
        return {"verdict": "unknown", "reason": (reason.group(1).strip() if reason else text.strip()[:160])}
    return {"verdict": verdict.group(1).upper(), "reason": reason.group(1).strip() if reason else ""}


def build_groups(count, same, minimum_share=0.75):
    pairs = sorted(same)
    clusters = {i: {i} for i in range(count)}
    owner = list(range(count))
    for a, b in pairs:
        ca, cb = owner[a], owner[b]
        if ca == cb:
            continue
        cross = [(x, y) for x in clusters[ca] for y in clusters[cb]]
        agreeing = sum(1 for x, y in cross if (min(x, y), max(x, y)) in same)
        if agreeing / len(cross) < minimum_share:
            continue
        clusters[ca] |= clusters[cb]
        for member in clusters[cb]:
            owner[member] = ca
        del clusters[cb]
    return sorted((sorted(c) for c in clusters.values()), key=lambda c: c[0])


def candidate_pairs(vectors, floor, neighbours):
    sim = vectors @ vectors.T
    n = len(vectors)
    chosen = set()
    for i in range(n):
        order = sorted((j for j in range(n) if j != i), key=lambda j: -sim[i, j])
        for j in order[:neighbours]:
            if sim[i, j] >= floor:
                chosen.add((min(i, j), max(i, j)))
    return sorted(chosen), sim


def chat(host, model, prompt, num_predict, think, timeout=300):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "keep_alive": "10m",
            "options": {"num_ctx": 4096, "num_predict": num_predict, "temperature": 0}}
    if think != "default":
        body["think"] = think == "on"
    data = json.dumps(body).encode()
    request = urllib.request.Request(host + "/api/chat", data, {"Content-Type": "application/json"})
    start = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.loads(response.read())
    message = reply.get("message", {})
    return (message.get("content") or "").strip(), time.time() - start


def key_of(*parts):
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


def clock(ns):
    return time.strftime("%H:%M", time.localtime(ns / 1_000_000_000))


def explain(group, moments, purposes, verdicts):
    lines = ["What each Moment was for:"]
    for i in sorted(group, key=lambda i: moments[i]["start_ns"]):
        lines.append(f"{clock(moments[i]['start_ns'])}  {purposes[i]['purpose']}  [why here: {purposes[i]['why'] or 'n/a'}]")
    lines.append("Goal checks between pairs:")
    for (a, b), v in sorted(verdicts.items()):
        if a in group and b in group:
            lines.append(f"{clock(moments[a]['start_ns'])} and {clock(moments[b]['start_ns'])}: {v['verdict']}. {v['reason']}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--goals", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--embed-model", default="embeddinggemma")
    parser.add_argument("--floor", type=float, default=0.35)
    parser.add_argument("--neighbours", type=int, default=6)
    parser.add_argument("--share", type=float, default=0.75)
    parser.add_argument("--think", choices=("default", "on", "off"), default="off")
    parser.add_argument("--context-chars", type=int, default=1800)
    args = parser.parse_args(argv)
    judge = args.judge_model or args.model
    goals_dir = os.path.abspath(os.path.expanduser(args.goals))
    path = os.path.join(goals_dir, "moment_goals.json")
    with open(path) as handle:
        result = json.load(handle)
    cache_path = os.path.join(goals_dir, "purpose_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    moments = result["moments"]
    entries = {g["moment_id"]: g for g in result["models"][args.model]["goals"]}
    indexes = [i for i, m in enumerate(moments) if entries.get(m["id"], {}).get("goal")]
    print(f"moments {len(moments)}, described {len(indexes)}")
    purposes = {}
    for n, i in enumerate(indexes, 1):
        action = entries[moments[i]["id"]]["goal"]
        context = moments[i]["packet"][: args.context_chars]
        prompt = PURPOSE_PROMPT.format(action=action, context=context)
        k = key_of("purpose", args.model, prompt)
        if k not in cache:
            text, seconds = chat(args.host, args.model, prompt, 120, args.think)
            cache[k] = {"raw": text, "seconds": seconds}
        parsed = parse_purpose(cache[k]["raw"])
        purposes[i] = {**parsed, "action": action, "raw": cache[k]["raw"], "seconds": cache[k]["seconds"], "prompt": prompt}
        print(f"  purpose {n}/{len(indexes)}: {parsed['purpose'] or '(unclear)'}")
    json.dump(cache, open(cache_path, "w"))
    clear = [i for i in indexes if purposes[i]["clear"]]
    print(f"clear purposes {len(clear)}, unclear {len(indexes) - len(clear)}")
    verdicts, same = {}, set()
    if len(clear) >= 2:
        vectors = embed(args.host, args.embed_model, [purposes[i]["purpose"] for i in clear])
        pairs, _ = candidate_pairs(vectors, args.floor, args.neighbours)
        print(f"pairs to check by goal: {len(pairs)} of {len(clear) * (len(clear) - 1) // 2}")
        for n, (x, y) in enumerate(pairs, 1):
            a, b = clear[x], clear[y]
            pa, pb = purposes[a], purposes[b]
            prompt = MATCH_PROMPT.format(action_a=pa["action"], purpose_a=pa["purpose"], why_a=pa["why"] or "n/a",
                                         action_b=pb["action"], purpose_b=pb["purpose"], why_b=pb["why"] or "n/a")
            k = key_of("match", judge, prompt)
            if k not in cache:
                text, seconds = chat(args.host, judge, prompt, 100, args.think)
                cache[k] = {"raw": text, "seconds": seconds}
            v = parse_verdict(cache[k]["raw"])
            v["raw"] = cache[k]["raw"]
            verdicts[(a, b)] = v
            if v["verdict"] == "SAME":
                same.add((a, b))
            print(f"  {n}/{len(pairs)} {v['verdict']}: {v['reason'][:100]}")
        json.dump(cache, open(cache_path, "w"))
    position = {i: p for p, i in enumerate(clear)}
    inverse = {p: i for i, p in position.items()}
    local_same = {(position[a], position[b]) for a, b in same}
    local_groups = build_groups(len(clear), local_same, args.share) if clear else []
    groups = [[inverse[p] for p in g] for g in local_groups]
    reasons = {}
    number = 0
    for g in groups:
        if len(g) < 2:
            continue
        number += 1
        reasons[str(number)] = explain(set(g), moments, purposes, verdicts)
    left_out = [i for i in indexes if i not in clear]
    key = f"{args.model} (purpose)"
    result["models"][key] = {
        "goals": [{"moment_id": moments[i]["id"], "goal": purposes[i]["purpose"] or "unclear purpose", "keywords": [],
                   "raw": purposes[i]["raw"], "seconds": purposes[i]["seconds"], "prompt_sent": purposes[i]["prompt"]} for i in indexes],
        "groups": groups, "group_reasons": reasons,
        "verdicts": [{"a": moments[a]["id"], "b": moments[b]["id"], "verdict": v["verdict"], "reason": v["reason"], "raw": v["raw"]} for (a, b), v in verdicts.items()],
        "unclear": [moments[i]["id"] for i in left_out],
    }
    with open(path, "w") as handle:
        json.dump(result, handle, indent=1)
    print(f"\nActivities (same goal only): {[g for g in groups if len(g) > 1]}")
    print(f"wrote '{key}' to the viewer file")
    return 0


if __name__ == "__main__":
    sys.exit(main())

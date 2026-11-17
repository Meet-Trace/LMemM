import argparse
import json
import os
import statistics
import sys
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grouping import embed, group

S = 1_000_000_000


def pairs_of(groups, ids):
    return {frozenset((ids[a], ids[b])) for g in groups for a, b in combinations(g, 2)}


def score(found, truth, scope):
    found = {p for p in found if p <= scope}
    hit = len(found & truth)
    precision = hit / len(found) if found else None
    recall = hit / len(truth) if truth else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {"found": len(found), "hit": hit, "precision": precision, "recall": recall, "f1": f1}


def time_groups(moments, gap_s):
    order = sorted(range(len(moments)), key=lambda i: moments[i]["start_ns"])
    groups, current = [], []
    for i in order:
        if current and (moments[i]["start_ns"] - moments[current[-1]]["end_ns"]) / S > gap_s:
            groups.append(sorted(current))
            current = []
        current.append(i)
    if current:
        groups.append(sorted(current))
    return groups


def separation(sim, indexes, labels, ids):
    same, different = [], []
    for a, b in combinations(range(len(indexes)), 2):
        (same if labels[ids[indexes[a]]] == labels[ids[indexes[b]]] else different).append(float(sim[a, b]))
    return same, different


def fmt(value):
    return "n/a" if value is None else f"{value:.0%}"


def line(name, s):
    return f"  {name:<34} pairs {s['found']:<4} precision {fmt(s['precision'])}  recall {fmt(s['recall'])}  f1 {s['f1']:.0%}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--goals", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--embed-model", default="embeddinggemma")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--time-gap-s", type=float, default=600)
    args = parser.parse_args(argv)
    goals_dir = os.path.abspath(os.path.expanduser(args.goals))
    path = os.path.join(goals_dir, "moment_goals.json")
    with open(path) as handle:
        result = json.load(handle)
    labels_path = os.path.join(goals_dir, "labels.json")
    labels = {}
    if os.path.exists(labels_path):
        with open(labels_path) as handle:
            labels = json.load(handle)
    moments = result["moments"]
    ids = [m["id"] for m in moments]
    entries = {g["moment_id"]: g for g in result["models"][args.model]["goals"]}
    described = [i for i, m in enumerate(moments) if entries.get(m["id"], {}).get("goal")]
    print(f"moments {len(moments)}, with a description {len(described)}, labelled {sum(1 for i in ids if labels.get(i))}")
    vectors = embed(args.host, args.embed_model, [entries[ids[i]]["goal"] for i in described])
    sim = vectors @ vectors.T
    labelled = [i for i in ids if labels.get(i)]
    truth = {frozenset((a, b)) for a, b in combinations(labelled, 2) if labels[a] == labels[b]}
    scope = set(labelled)
    print(f"true same-task pairs among labelled Moments: {len(truth)}")
    if truth:
        located = [i for i in described if ids[i] in scope]
        if len(located) >= 2:
            sub = np.array([[sim[described.index(a), described.index(b)] for b in located] for a in located])
            same, different = separation(sub, located, labels, ids)
            if same and different:
                print(f"description similarity: same task {statistics.fmean(same):.2f} on average, different tasks {statistics.fmean(different):.2f}")
        print("\nBaselines (pair precision / recall against your labels):")
        print(line("raw screen text", score(pairs_of(result.get("baseline_packet_groups", []), ids), truth, scope)))
        print(line(f"within {int(args.time_gap_s)} s of each other", score(pairs_of(time_groups(moments, args.time_gap_s), ids), truth, scope)))
        print("\nDescription meaning at each threshold:")
        best = None
        for threshold in (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9):
            groups = [[described[j] for j in g] for g in group(vectors, threshold)]
            s = score(pairs_of(groups, ids), truth, scope)
            print(line(f"threshold {threshold:.2f}", s))
            if best is None or s["f1"] > best[0]["f1"]:
                best = (s, threshold)
        print(f"\nbest threshold on this data: {best[1]:.2f} (f1 {best[0]['f1']:.0%}); with few labels this is only indicative")
    chosen = args.threshold if args.threshold is not None else (best[1] if truth else 0.7)
    groups = [[described[j] for j in g] for g in group(vectors, chosen)]
    result["models"][args.model]["groups"] = groups
    result["models"][args.model]["group_threshold"] = chosen
    with open(path, "w") as handle:
        json.dump(result, handle, indent=1)
    print(f"\nwrote Activity groups at threshold {chosen:.2f} for the viewer: {[g for g in groups if len(g) > 1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
import os
import sys
from itertools import combinations


def pairs_from_groups(groups, ids):
    return {frozenset((ids[a], ids[b])) for g in groups for a, b in combinations(g, 2)}


def main():
    out = sys.argv[1]
    result = json.load(open(os.path.join(out, "moment_goals.json")))
    labels = json.load(open(os.path.join(out, "labels.json")))
    ids = [m["id"] for m in result["moments"]]
    labelled = [i for i in ids if labels.get(i)]
    truth = {frozenset((a, b)) for a, b in combinations(labelled, 2) if labels[a] == labels[b]}
    scope = set(labelled)

    def report(name, groups):
        found = {p for p in pairs_from_groups(groups, ids) if p <= scope}
        hit = len(found & truth)
        precision = hit / len(found) if found else None
        recall = hit / len(truth) if truth else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
        fmt = lambda v: "n/a" if v is None else f"{v:.0%}"
        print(f"{name:<28} pairs found {len(found):<4} precision {fmt(precision)}  recall {fmt(recall)}  f1 {f1:.0%}")
    print(f"labelled moments {len(labelled)}, true pairs {len(truth)}")
    report("raw screen text", result["baseline_packet_groups"])
    for model, data in result["models"].items():
        report(f"goal meaning ({model})", data["groups"])


if __name__ == "__main__":
    main()

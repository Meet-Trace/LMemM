#!/usr/bin/env python3
"""EXPERIMENT, NOT PRODUCTION: why does candidate retrieval miss related Visit pairs, and which cheap, label-free additions help?

No model is used. Reads the annotated dataset (read-only), reuses `llm_activity.retrieve` UNCHANGED as the baseline, and writes
only into the OUT folder. Standard library only.

  python3 -I experiments/llm_activity/retrieval_diagnosis.py DATASET OUT [--private]

Sections: 1 diagnosis of missed related pairs; 2 connectivity through intermediate Visits; 3 a FIXED set of label-free
expansions compared with the baseline (recall, precision, reachable recall, leakage), reported on two pair halves.

Overfitting: the labels are never used to build candidates, and the expansion settings are a fixed, pre-declared list (not
searched). Still, this is one day with three Activities, one of which holds about 80% of the related pairs, and the same labels
are used to look at the result. Everything here is EXPLORATORY. A held-out claim needs another labelled day."""
import argparse
import collections
import itertools
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_activity as L  # noqa: E402

CONFIGS = {   # fixed up front; nothing below is tuned on the labels
    "E1 two-hop (edges score>=1)": {"kind": "twohop", "min_edge": 1.0, "cap": 6},
    "E1b two-hop (edges score>=3)": {"kind": "twohop", "min_edge": 3.0, "cap": 6},
    "E2 context text (cos>=0.10)": {"kind": "context", "min_cos": 0.10, "top": 8, "radius": 2, "gap_s": 600.0, "w": 0.5},
    "E2b context text (cos>=0.20)": {"kind": "context", "min_cos": 0.20, "top": 8, "radius": 2, "gap_s": 600.0, "w": 0.5},
    "E3 same episode (gap<=20 min)": {"kind": "episode", "episode_gap_s": 1200.0, "min_cos": 0.02, "top": 6},
    "E4 wider time (6 visits, 30 min)": {"kind": "temporal", "n": 6, "gap_s": 1800.0},
}
COMBINED = "E5 combined (E1+E2+E3)"


# ------------------------------------------------------------------ helpers

def pure_pairs(day):
    pure = [v for v in day.order if day.gold[v]["kind"] == "pure"]
    rel, unrel = [], []
    for a, b in itertools.combinations(pure, 2):
        (rel if day.truth(a, b) else unrel).append(day.key(a, b))
    return rel, unrel


def task_of(day, k):
    return sorted(day.gold[k[0]]["labels"] & day.gold[k[1]]["labels"])[0]


def half(k):
    return zlib.crc32(f"{k[0]}|{k[1]}".encode()) & 1


def cos_pair(day, a, b):
    return L.af.content_similarity(day.F[a], day.F[b], day.fs.corpus, day.fs.params)


def bag_cos(day, A, B):
    idf = day.fs.corpus.idf
    w = lambda t: idf.get(t, 0.0) ** 2
    na = sum(w(t) * A[t] ** 2 for t in A) ** 0.5
    nb = sum(w(t) * B[t] ** 2 for t in B) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return sum(w(t) * A[t] * B[t] for t in set(A) & set(B)) / (na * nb)


# ------------------------------------------------------------------ 1 diagnosis of missed pairs

def classify(day, k, pool, potential):
    if k in potential:
        return "cut by per-visit cap (had a source)"
    evs = day.pair_ev.get(k)
    if evs and not any(e.polarity == "support" for e in evs):
        return "engine evidence exists but is not supportive"
    a, b = k
    need = day.fs.params.min_bag_tokens
    if len(day.F[a].bag) < need or len(day.F[b].bag) < need:
        return "too little readable text on one side"
    c = cos_pair(day, a, b)["cos_idf"]
    if c >= L.CFG["min_cos"]:
        return "text similar (cos>=0.05) but not in either visit's top 8"
    if c > 0:
        return "weak text overlap (0<cos<0.05)"
    return "no shared text, artifact or identifier"


def section_diagnosis(day, pool, potential, related, private):
    miss = [k for k in related if k not in pool]
    out = [f"1. WHY RELATED PAIRS WERE MISSED   ({len(miss)} of {len(related)} not presented)"]
    for title, ks in (("all tasks", miss), ("Finding Images only", [k for k in miss if task_of(day, k).startswith("Finding Images")])):
        cnt = collections.Counter(classify(day, k, pool, potential) for k in ks)
        out.append(f"  {title} ({len(ks)} missed):")
        for name, n in cnt.most_common():
            out.append(f"    {n:5d}  {100 * n / max(1, len(ks)):3.0f}%  {name}")
    out.append("  missed pairs by time gap:")
    cg = collections.Counter(L.gap_bucket(day.gap(*k)) for k in miss)
    tg = collections.Counter(L.gap_bucket(day.gap(*k)) for k in related)
    for b in ("<5 min", "5-30 min", "30-120 min", ">120 min"):
        out.append(f"    {b:10s} missed {cg[b]:4d} of {tg[b]:4d}")
    out.append("  missed pairs by app pair (top 8):")
    ap = collections.Counter(tuple(sorted((L.clip(day.card(k[0])["app"], 16), L.clip(day.card(k[1])["app"], 16)))) for k in miss)
    for (x, y), n in ap.most_common(8):
        out.append(f"    {n:4d}  {x} <-> {y}")
    thin = [v for v in day.order if day.gold[v]["kind"] == "pure" and len(day.F[v].bag) < day.fs.params.min_bag_tokens]
    out.append(f"  labelled visits with too little text to compare ({day.fs.params.min_bag_tokens}+ tokens needed): {len(thin)} of "
               f"{sum(1 for v in day.order if day.gold[v]['kind'] == 'pure')}")
    out.append("  sample of missed pairs (evenly spread over the day, 14 pairs):")
    sample = [miss[i] for i in range(0, len(miss), max(1, len(miss) // 14))][:14]
    for k in sample:
        c = cos_pair(day, *k)
        out.append(f"    {task_of(day, k)[:22]:22s} gap {L.minutes(day.gap(*k)):>11s}  cos {c['cos_idf']:.2f}  rare-shared {c['rare_shared']}  "
                   f"-> {classify(day, k, pool, potential)}")
        out.append(f"        {day.describe(k[0], private)}")
        out.append(f"        {day.describe(k[1], private)}")
    return "\n".join(out)


# ------------------------------------------------------------------ 2 connectivity

def components(ids, edges):
    parent = {v: v for v in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    return {v: find(v) for v in ids}


def bfs_path(adj, a, b):
    prev, q = {a: None}, collections.deque([a])
    while q:
        x = q.popleft()
        if x == b:
            path = []
            while x is not None:
                path.append(x)
                x = prev[x]
            return path[::-1]
        for y in sorted(adj[x]):
            if y not in prev:
                prev[y] = x
                q.append(y)
    return None


def section_connectivity(day, pool, related, unrelated):
    ids = day.order
    adj = collections.defaultdict(set)
    for a, b in pool:
        adj[a].add(b)
        adj[b].add(a)
    miss = [k for k in related if k not in pool]
    hops, clean = collections.Counter(), 0
    for k in miss:
        p = bfs_path(adj, *k)
        if p is None:
            hops["no path"] += 1
            continue
        hops[f"{len(p) - 1} hops" if len(p) - 1 < 4 else "4+ hops"] += 1
        task = task_of(day, k)
        if all(task in day.gold[v]["labels"] and day.gold[v]["kind"] == "pure" for v in p[1:-1]):
            clean += 1
    out = ["2. CONNECTIVITY THROUGH INTERMEDIATE VISITS (baseline candidate graph, shortest path between the endpoints)",
           f"  the {len(miss)} missed related pairs:"]
    for name in ("2 hops", "3 hops", "4+ hops", "no path"):
        out.append(f"    {name:8s} {hops[name]:5d}  {100 * hops[name] / max(1, len(miss)):3.0f}%")
    reach = len(miss) - hops["no path"]
    out.append(f"  reachable at all: {reach}/{len(miss)};  of those, the shortest path runs ONLY through visits labelled with the same "
               f"task (a clean bridge): {clean}/{max(1, reach)} = {100 * clean / max(1, reach):.0f}%")
    comp = components(ids, pool)
    sizes = collections.Counter(comp.values())
    out.append(f"  connected components of the candidate graph: {len(sizes)}; largest {max(sizes.values())} visits; "
               f"visits with no candidate at all: {sum(1 for v in ids if not adj[v])}")
    by = collections.defaultdict(collections.Counter)
    for v in ids:
        g = day.gold[v]
        by[comp[v]]["noise" if g["kind"] == "pure" and not g["labels"] else "unlabelled/mixed" if g["kind"] != "pure" else sorted(g["labels"])[0][:18]] += 1
    out.append("  the five largest components and what is in them (label mix shows where a bridge could join different tasks):")
    for c, n in sizes.most_common(5):
        out.append(f"    {n:3d} visits: " + ", ".join(f"{k} {v}" for k, v in by[c].most_common()))
    return "\n".join(out)


# ------------------------------------------------------------------ 3 expansions (label-free)

def expand(day, pool, cfg):
    ids, extra = day.order, set()
    kind = cfg["kind"]
    if kind == "twohop":
        adj = collections.defaultdict(dict)
        for (a, b), v in pool.items():
            if v["score"] >= cfg["min_edge"]:
                adj[a][b] = v["score"]
                adj[b][a] = v["score"]
        cand = collections.defaultdict(list)
        for b, nb in adj.items():
            for a, c in itertools.combinations(sorted(nb, key=day.pos.get), 2):
                k = day.key(a, c)
                if k not in pool:
                    cand[k].append(min(nb[a], nb[c]))
        per = collections.defaultdict(list)
        for k, s in cand.items():
            sc = max(s) + 0.1 * len(s)
            per[k[0]].append((sc, k))
            per[k[1]].append((sc, k))
        for v, lst in per.items():
            for _, k in sorted(lst, key=lambda t: (-t[0], day.pos[t[1][0]], day.pos[t[1][1]]))[: cfg["cap"]]:
                extra.add(k)
    elif kind == "context":
        aug = {}
        for i, v in enumerate(ids):
            bag = dict(day.F[v].bag_map())
            for j in range(max(0, i - cfg["radius"]), min(len(ids), i + cfg["radius"] + 1)):
                u = ids[j]
                if u != v and day.gap(u, v) <= cfg["gap_s"]:
                    for t, w in day.F[u].bag_map().items():
                        bag[t] = bag.get(t, 0.0) + cfg["w"] * w
            aug[v] = bag
        for a in ids:
            sc = sorted(((bag_cos(day, aug[a], aug[b]), b) for b in ids if b != a), key=lambda t: (-t[0], day.pos[t[1]]))
            for c, b in sc[: cfg["top"]]:
                if c >= cfg["min_cos"] and day.key(a, b) not in pool:
                    extra.add(day.key(a, b))
    elif kind == "episode":
        ep, cur = {}, 0
        for i, v in enumerate(ids):
            if i and day.gap(ids[i - 1], v) > cfg["episode_gap_s"]:
                cur += 1
            ep[v] = cur
        for a in ids:
            sc = sorted(((cos_pair(day, a, b)["cos_idf"], b) for b in ids if b != a and ep[b] == ep[a]),
                        key=lambda t: (-t[0], day.pos[t[1]]))
            for c, b in sc[: cfg["top"]]:
                if c >= cfg["min_cos"] and day.key(a, b) not in pool:
                    extra.add(day.key(a, b))
    elif kind == "temporal":
        for i, a in enumerate(ids):
            for b in ids[i + 1: i + 1 + cfg["n"]]:
                if day.gap(a, b) <= cfg["gap_s"] and day.key(a, b) not in pool:
                    extra.add(day.key(a, b))
    return extra


def metrics_row(day, name, pairs, base, related, unrelated):
    rel, unr = set(related), set(unrelated)
    hit = [k for k in related if k in pairs]
    fi = [k for k in related if task_of(day, k).startswith("Finding Images")]
    mid = [k for k in related if 300 <= day.gap(*k) < 1800]
    lab = [k for k in pairs if k in rel or k in unr]
    added = [k for k in pairs if k not in base and (k in rel or k in unr)]
    h = [(sum(1 for k in related if k in pairs and half(k) == i), sum(1 for k in related if half(k) == i)) for i in (0, 1)]
    comp = components(day.order, pairs)
    sizes = collections.Counter(comp.values())
    reach = sum(1 for k in related if comp[k[0]] == comp[k[1]])
    leak = sum(1 for k in unrelated if comp[k[0]] == comp[k[1]])
    return {"name": name, "pairs": len(pairs), "per_visit": 2 * len(pairs) / max(1, len(day.order)),
            "recall": len(hit) / max(1, len(related)), "half0": h[0][0] / max(1, h[0][1]), "half1": h[1][0] / max(1, h[1][1]),
            "fi": sum(1 for k in fi if k in pairs) / max(1, len(fi)), "mid": sum(1 for k in mid if k in pairs) / max(1, len(mid)),
            "prec": sum(1 for k in lab if k in rel) / max(1, len(lab)),
            "added": len(pairs) - len(base), "added_prec": sum(1 for k in added if k in rel) / max(1, len(added)) if added else None,
            "reach": reach / max(1, len(related)), "leak": leak / max(1, len(unrelated)), "largest": max(sizes.values())}


def section_expansions(day, pool, related, unrelated, out_dir):
    base = set(pool)
    sets = {"BASELINE (current retrieve)": set(base)}
    for name, cfg in CONFIGS.items():
        sets[name] = base | expand(day, pool, cfg)
    sets[COMBINED] = base | expand(day, pool, CONFIGS["E1 two-hop (edges score>=1)"]) | \
        expand(day, pool, CONFIGS["E2 context text (cos>=0.10)"]) | expand(day, pool, CONFIGS["E3 same episode (gap<=20 min)"])
    rows = [metrics_row(day, n, s, base, related, unrelated) for n, s in sets.items()]
    out = ["3. LABEL-FREE EXPANSIONS vs THE BASELINE (exploratory; settings fixed in advance, not searched)",
           "   recall: related pairs presented. halves: recall on two stable halves of the pairs (a hash split, only a consistency check).",
           "   FI = Finding Images recall; 5-30 = recall for pairs 5-30 min apart; prec = share of labelled presented pairs that are related;",
           "   added-prec = same, for the ADDED pairs only; reach = related pairs whose two visits end up in one connected component",
           "   of the candidate graph; leak = unrelated labelled pairs that end up in one component (the transitive false-merge risk).",
           f"   {'setting':36s} {'pairs':>6s} {'/visit':>6s} {'recall':>6s} {'h0':>4s} {'h1':>4s} {'FI':>4s} {'5-30':>5s} {'prec':>5s} {'add-p':>5s} {'reach':>6s} {'leak':>5s} {'big':>4s}"]
    p = lambda x: "  -" if x is None else f"{100 * x:3.0f}%"
    for r in rows:
        out.append(f"   {r['name'][:36]:36s} {r['pairs']:6d} {r['per_visit']:6.1f} {p(r['recall']):>6s} {p(r['half0']):>4s} {p(r['half1']):>4s} "
                   f"{p(r['fi']):>4s} {p(r['mid']):>5s} {p(r['prec']):>5s} {p(r['added_prec']):>5s} {p(r['reach']):>6s} {p(r['leak']):>5s} {r['largest']:4d}")
    with open(os.path.join(out_dir, "retrieval_variants.json"), "w", encoding="utf-8") as fh:
        json.dump({n: sorted(map(list, s)) for n, s in sets.items()}, fh)
        # candidate sets only; no model output, no labels
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset")
    ap.add_argument("out")
    ap.add_argument("--private", action="store_true", help="hide window titles in the printed samples")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    day = L.Day(a.dataset)
    pool, potential = L.retrieve(day)
    related, unrelated = pure_pairs(day)
    parts = [L.recall_report(day, pool, potential), section_diagnosis(day, pool, potential, related, a.private),
             section_connectivity(day, pool, related, unrelated), section_expansions(day, pool, related, unrelated, a.out)]
    text = "\n\n".join(parts)
    with open(os.path.join(a.out, "retrieval_diagnosis.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()

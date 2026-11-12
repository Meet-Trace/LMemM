#!/usr/bin/env python3
"""EXPERIMENT, NOT PRODUCTION: can a small local language model infer Activities better than the deterministic engine?

Isolated: reads recorded Moments and an annotated dataset, imports the existing modules from ../../src, changes none of them,
and writes only into the OUT folder you name. Standard library only. Everything runs locally (Ollama on 127.0.0.1).

  A  deterministic baseline   the existing inference (activity_inference.infer), default parameters
  B  LLM only                 the model judges pairs from compact Visit cards, then group-level questions decide cluster merges.
                              No engine evidence, no cannot-links.
  C  hybrid                   the same cached model answers, but every link and merge passes deterministic validation:
                              cannot-links, evidence completeness, verifiable cited evidence, group consistency (a merge is refused when judged-different cross pairs outnumber its same links), no unsupported
                              transitive merges, explicit abstention.

Commands (see README.md for the exact sequence):
  env      document memory/hardware state (run after a clean restart) and what else is running
  cpucheck load the model and verify, empirically, that generation runs on the CPU
  retrieve build the candidate pool from four sources and report candidate recall (no model)
  judge    ask the model about candidate pairs (cached, resumable) while sampling memory/GPU
  build    group-level questions and the B and C reconstructions (cached)
  report   evaluate A, B, C: pairwise judgement separately from reconstruction, examples, metrics

Self-reported model confidence is recorded but is NEVER used as a probability or to choose between answers."""
import argparse
import collections
import hashlib
import itertools
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

import activity_abstention as ab          # noqa: E402
import activity_contract as ac            # noqa: E402
import activity_evaluation as ae          # noqa: E402
import activity_evidence as ev            # noqa: E402
import activity_features as af            # noqa: E402
import activity_inference as inf          # noqa: E402
import activity_input as ai               # noqa: E402

PROMPT_VERSION = "pair-v1+group-v1"
DECISIONS = ("same_activity", "different_activity", "uncertain")
CONF = ("low", "medium", "high")
OPTIONS = {"temperature": 0, "seed": 0, "num_ctx": 2048, "num_predict": 150, "num_thread": 2, "num_gpu": 0}
CFG = {"per_visit": 12, "neighbours": 3, "neighbour_gap_s": 1800.0, "top_text": 8, "min_cos": 0.05}
STOP = frozenset("the and for with from this that are was not you your has have but all any can will its into out new one two "
                 "com www http https page app".split())

SCHEMA = {"type": "object", "properties": {
    "decision": {"type": "string", "enum": list(DECISIONS)},
    "evidence": {"type": "string"},
    "explanation": {"type": "string"},
    "confidence": {"type": "string", "enum": list(CONF)}},
    "required": ["decision", "evidence", "explanation", "confidence"]}

SYSTEM = (
    "You help decide whether two observed moments of someone's computer use belong to the SAME underlying task (an Activity: "
    "one piece of work with one goal, such as writing a specific document, fixing a specific bug, or finding images for a "
    "specific design). You only see short cards, not the screen.\n"
    "Rules:\n"
    "- The same app, the same window title or similar words do NOT mean the same task. One app can serve several tasks.\n"
    "- Different apps CAN serve one task (a browser search, a chat and an editor all working on the same thing).\n"
    "- Say same_activity only if the cards show a shared, specific goal or object, and quote that evidence.\n"
    "- Say different_activity if the cards clearly serve different goals.\n"
    "- Say uncertain if the cards do not give enough to tell. Abstaining is correct when unsure.\n"
    "- evidence: a short phrase copied from the cards (at most 12 words). explanation: at most 20 words.\n"
    "- confidence is your own rough feeling (low, medium or high).\n"
    "Reply with JSON only.")


# ================================================================ the day

def clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def hms(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


class Day:
    """The annotated dataset prepared exactly as the existing replay does. Nothing here writes."""

    def __init__(self, dataset_dir):
        self.ds = ae.load_dataset(dataset_dir)
        self.rp = ac.ReplayParams()
        self.prepared = ai.prepare(self.ds.records, max_intra_visit_gap_s=self.rp.max_intra_visit_gap_s)
        self.fs, self.es = ev.analyse(self.prepared, None, self.rp)
        self.F = {f.visit_id: f for f in self.fs.visits}
        self.V = {v.visit_id: v for v in self.prepared.visits}
        self.order = [v.visit_id for v in self.prepared.visits]
        self.pos = {v: i for i, v in enumerate(self.order)}
        self.gold = {v: ab.visit_gold(self.V[v], self.ds.gold, self.ds.unsure) for v in self.order}
        self.affected = set()
        for members in self.es.skipped_visits.values():
            self.affected |= set(members)
        self.pair_ev = collections.defaultdict(list)
        for e in self.es.items:
            self.pair_ev[tuple(sorted((e.source, e.target), key=self.pos.get))].append(e)
        self._cards = {}

    def key(self, a, b):
        return (a, b) if self.pos[a] < self.pos[b] else (b, a)

    def truth(self, a, b):
        """True/False when both visits are pure-labelled (noise is together with nothing), else None (not scored)."""
        ga, gb = self.gold[a], self.gold[b]
        if ga["kind"] != "pure" or gb["kind"] != "pure":
            return None
        return bool(ga["labels"] & gb["labels"])

    def cannot(self, a, b):
        return self.es.constraints.is_cannot_link(a, b)

    def gap(self, a, b):
        a, b = self.key(a, b)
        return max(0.0, self.V[b].t_start - self.V[a].t_end)

    def card(self, v):
        if v not in self._cards:
            vis, m0 = self.V[v], self.V[v].moments[0]
            terms, idents, lines = [], [], []
            for m in vis.moments:
                d = m.get("digest") or {}
                terms += [t for t in d.get("terms") or [] if t not in terms]
                idents += [t for t in d.get("identifiers") or [] if t not in idents]
                lines += [x for x in d.get("lines") or [] if x not in lines]
            self._cards[v] = {"id": v, "t0": vis.t_start, "dwell": round(self.F[v].dwell_s), "app": m0.get("app") or "",
                              "title": clip(m0.get("window") or m0.get("url") or "", 100), "terms": terms[:8],
                              "identifiers": idents[:4], "summary": clip(" | ".join(lines), 160)}
        return self._cards[v]

    def describe(self, v, private=False):
        c = self.card(v)
        t = "" if private else "  " + c["title"]
        return f"{v} {hms(c['t0'])} {c['dwell']:4d}s {clip(c['app'], 14):14s}{t}"

    def label_text(self, v):
        g = self.gold[v]
        return "unlabelled" if g["kind"] == "unlabelled" else "mixed" if g["kind"] == "mixed" else ("|".join(sorted(g["labels"])) or "noise")


def card_text(c, name, other=None, gap=None):
    lines = [f"Visit {name}", f"  app: {c['app']}", f"  time: {hms(c['t0'])}, {c['dwell']} s on screen", f"  window: {c['title'] or '(none)'}"]
    if c["terms"]:
        lines.append("  words seen: " + ", ".join(c["terms"]))
    if c["identifiers"]:
        lines.append("  identifiers: " + ", ".join(c["identifiers"]))
    if c["summary"]:
        lines.append("  summary: " + c["summary"])
    return "\n".join(lines)


def minutes(s):
    return f"{s / 60:.0f} minutes" if s >= 90 else f"{s:.0f} seconds"


# ================================================================ candidate retrieval (four sources, no model)

SOURCES = ("engine", "temporal", "text", "shared")


def retrieve(day, cfg=CFG):
    """-> {pair: {"sources": [...], "score": float}} for presented pairs, plus `potential` (sources before the per-visit cap).
    Sources: engine = any support evidence the existing evidence layer lists; temporal = neighbours in canonical order within
    a gap; text = top idf-cosine matches anywhere in the day (not time-limited); shared = a shared non-hub artifact or a shared
    non-hub identifier token."""
    ids = day.order
    potential = collections.defaultdict(set)
    score = collections.defaultdict(float)
    for (a, b), evs in day.pair_ev.items():
        if any(e.polarity == "support" for e in evs):
            k = day.key(a, b)
            potential[k].add("engine")
            score[k] += 3.0
    for i, a in enumerate(ids):
        for j in range(i + 1, min(len(ids), i + 1 + cfg["neighbours"])):
            b = ids[j]
            if day.gap(a, b) <= cfg["neighbour_gap_s"]:
                potential[(a, b)].add("temporal")
                score[(a, b)] += 1.0
    cos = {}
    for a, b in itertools.combinations(ids, 2):
        cos[(a, b)] = af.content_similarity(day.F[a], day.F[b], day.fs.corpus, day.fs.params)["cos_idf"]
    for a in ids:
        top = sorted((b for b in ids if b != a), key=lambda b: (-cos[day.key(a, b)], day.pos[b]))[: cfg["top_text"]]
        for b in top:
            k = day.key(a, b)
            if cos[k] >= cfg["min_cos"]:
                potential[k].add("text")
    for k in list(potential):
        if "text" in potential[k]:
            score[k] += cos[k]
    by_art, by_ident = collections.defaultdict(list), collections.defaultdict(list)
    for v in ids:
        for art in day.F[v].artifacts:
            if art["type"] != "item" and not art["hub_pattern"]:
                by_art[(art["type"], art["key"])].append(v)
        for t in day.F[v].identifiers:
            by_ident[t].append(v)
    for groups in (by_art, by_ident):
        for members in groups.values():
            if 2 <= len(members) <= 12:                 # a token or artifact shared by many visits is not specific
                for a, b in itertools.combinations(members, 2):
                    k = day.key(a, b)
                    potential[k].add("shared")
                    score[k] += 2.0 / (len(members) - 1)
    per = collections.defaultdict(list)
    for k in potential:
        per[k[0]].append(k)
        per[k[1]].append(k)
    keep = set()
    for v, ks in per.items():
        for k in sorted(ks, key=lambda k: (-score[k], day.pos[k[0]], day.pos[k[1]]))[: cfg["per_visit"]]:
            keep.add(k)
    pool = {k: {"sources": sorted(potential[k]), "score": round(score[k], 4)} for k in keep}
    return pool, {k: sorted(v) for k, v in potential.items()}


def gap_bucket(s):
    return "<5 min" if s < 300 else "5-30 min" if s < 1800 else "30-120 min" if s < 7200 else ">120 min"


def pct(a, b):
    return "-" if not b else f"{100 * a / b:.0f}%"


def recall_report(day, pool, potential):
    pure = [v for v in day.order if day.gold[v]["kind"] == "pure"]
    related = [day.key(a, b) for a, b in itertools.combinations(pure, 2) if day.truth(a, b)]
    unrelated = [day.key(a, b) for a, b in itertools.combinations(pure, 2) if day.truth(a, b) is False]
    out = ["CANDIDATE RETRIEVAL (before any model)",
           f"  visits {len(day.order)}   pure-labelled {len(pure)}   related pairs in your labels {len(related)}   unrelated labelled pairs {len(unrelated)}",
           f"  candidate pairs presented to the model: {len(pool)}  (about {2 * len(pool) / max(1, len(day.order)):.1f} per visit)"]
    pr = [k for k in related if k in pool]
    out.append(f"  CANDIDATE RECALL (related pairs presented): {len(pr)}/{len(related)} = {pct(len(pr), len(related))}")
    out.append("  recall by source, before the per-visit cap (a pair can have several sources):")
    for s in SOURCES:
        hit = [k for k in related if s in potential.get(k, ())]
        out.append(f"    {s:9s} {len(hit):5d}/{len(related)} = {pct(len(hit), len(related))}")
    anyp = [k for k in related if k in potential]
    out.append(f"    any source (before cap): {len(anyp)}/{len(related)} = {pct(len(anyp), len(related))}")
    out.append("  recall by time gap between the two visits (presented):")
    for b in ("<5 min", "5-30 min", "30-120 min", ">120 min"):
        ks = [k for k in related if gap_bucket(day.gap(*k)) == b]
        out.append(f"    {b:10s} {sum(1 for k in ks if k in pool):5d}/{len(ks)} = {pct(sum(1 for k in ks if k in pool), len(ks))}")
    per_task = collections.defaultdict(lambda: [0, 0])
    for k in related:
        lab = sorted(day.gold[k[0]]["labels"] & day.gold[k[1]]["labels"])[0]
        per_task[lab][1] += 1
        per_task[lab][0] += k in pool
    out.append("  recall by labelled Activity:")
    for lab, (h, n) in sorted(per_task.items()):
        out.append(f"    {lab[:40]:40s} {h}/{n} = {pct(h, n)}")
    judged = [k for k in pool if day.truth(*k) is not None]
    rel_in = sum(1 for k in judged if day.truth(*k))
    out.append(f"  candidate precision (labelled presented pairs that are related): {rel_in}/{len(judged)} = {pct(rel_in, len(judged))}")
    out.append("  (the model can only ever see presented pairs: recall here is the ceiling for pairwise recall below)")
    return "\n".join(out)


# ================================================================ the model client (Ollama on loopback) and its cache

class Missing(RuntimeError):
    """An answer is not in the cache and calls are not allowed in this command."""


class Client:
    """Ollama /api/chat with a JSON schema, deterministic options, and an on-disk answer cache keyed by everything that
    could change the answer. The cache holds the model's reply and token/latency numbers, not the prompt."""

    def __init__(self, out_dir, model, host="127.0.0.1:11436", options=None, allow_calls=True):
        self.model, self.host, self.allow = model, host, allow_calls
        self.options = dict(OPTIONS, **(options or {}))
        self.path = os.path.join(out_dir, f"cache_{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.jsonl")
        self.cache = {}
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        r = json.loads(line)
                        self.cache[r["key"]] = r
        self.new_calls = 0

    def key(self, system, user):
        body = json.dumps({"m": self.model, "pv": PROMPT_VERSION, "o": self.options, "s": system, "u": user, "f": SCHEMA}, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def ask(self, system, user):
        k = self.key(system, user)
        if k in self.cache:
            return self.cache[k]
        if not self.allow:
            raise Missing(k)
        t0 = time.time()
        req = {"model": self.model, "stream": False, "format": SCHEMA, "options": self.options, "keep_alive": "30m",
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        data = json.dumps(req).encode("utf-8")
        r = urllib.request.Request(f"http://{self.host}/api/chat", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=600) as resp:
            body = json.loads(resp.read())
        wall = time.time() - t0
        rec = {"key": k, "model": self.model, "content": (body.get("message") or {}).get("content", ""), "wall_s": round(wall, 3),
               "prompt_tokens": body.get("prompt_eval_count"), "output_tokens": body.get("eval_count"),
               "total_ns": body.get("total_duration"), "load_ns": body.get("load_duration"), "at": round(time.time(), 1)}
        self.cache[k] = rec
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self.new_calls += 1
        return rec


def parse(rec):
    """Strict parse of a reply: any failure becomes an explicit 'invalid' answer that counts as abstention."""
    try:
        d = json.loads(rec["content"])
        dec = d["decision"]
        if dec not in DECISIONS:
            raise ValueError
        return {"decision": dec, "evidence": str(d.get("evidence", "")), "explanation": str(d.get("explanation", "")),
                "confidence": d.get("confidence") if d.get("confidence") in CONF else "low", "valid": True}
    except Exception:
        return {"decision": "uncertain", "evidence": "", "explanation": "unparseable reply", "confidence": "low", "valid": False}


# ================================================================ pair judgement

def pair_prompt(day, a, b):
    ca, cb = day.card(a), day.card(b)
    user = (card_text(ca, "A") + "\n\n" + card_text(cb, "B") + f"\n\nThe two visits are {minutes(day.gap(a, b))} apart in time."
            "\nDo A and B belong to the same underlying task?")
    return SYSTEM, user


def judge_pair(day, client, x, y):
    """Both orders unless the first says different_activity. Returns the combined verdict dict."""
    x, y = day.key(x, y)
    r1 = parse(client.ask(*pair_prompt(day, x, y)))
    r2 = parse(client.ask(*pair_prompt(day, y, x))) if r1["decision"] != "different_activity" else None
    d1, d2 = r1["decision"], (r2["decision"] if r2 else None)
    if d1 == "same_activity" and d2 == "same_activity":
        verdict = "same"
    elif d1 == "different_activity" and d2 in (None, "different_activity"):
        verdict = "different"
    elif {d1, d2} == {"same_activity", "different_activity"}:
        verdict = "conflict"
    else:
        verdict = "uncertain"
    confs = [r["confidence"] for r in (r1, r2) if r]
    return {"pair": [x, y], "verdict": verdict, "orders": [d1, d2], "evidence": [r1["evidence"]] + ([r2["evidence"]] if r2 else []),
            "explanation": [r1["explanation"]] + ([r2["explanation"]] if r2 else []),
            "confidence": min(confs, key=CONF.index), "valid": r1["valid"] and (r2 is None or r2["valid"])}


def stratified_subset(day, pool, n):
    """A deterministic, representative subset: proportional within (truth, sources) strata, chosen by hash."""
    strata = collections.defaultdict(list)
    for k in pool:
        t = day.truth(*k)
        strata[("related" if t else "unrelated" if t is False else "unscored", tuple(pool[k]["sources"]))].append(k)
    frac = min(1.0, n / max(1, len(pool)))
    out = []
    for s in sorted(strata):
        ks = sorted(strata[s], key=lambda k: hashlib.sha256(("|".join(k)).encode()).hexdigest())
        out += ks[: max(1, math.ceil(len(ks) * frac))]
    return sorted(out, key=lambda k: (day.pos[k[0]], day.pos[k[1]]))[:n]


# ================================================================ evidence-phrase verification (deterministic, for C)

def toks(s):
    return {t for t in re.findall(r"[A-Za-z0-9_.]{3,}", str(s).lower()) if t not in STOP}


def card_tokens(c):
    return toks(" ".join([c["app"], c["title"], c["summary"], " ".join(c["terms"]), " ".join(c["identifiers"])]))


def phrase_ok(day, a, b, phrases):
    """The cited evidence must be checkable: every content token of at least one phrase appears in the cards of A or B, and
    at least one such token appears in BOTH cards, or the pair also has deterministic evidence. Cannot be satisfied by a quote
    the model made up."""
    ta, tb = card_tokens(day.card(a)), card_tokens(day.card(b))
    for p in phrases:
        t = toks(p)
        if t and t <= (ta | tb) and (t & ta & tb):
            return True
    return any(e.polarity == "support" for e in day.pair_ev.get(day.key(a, b), ()))


# ================================================================ group-level reasoning and reconstruction (B and C)

GROUP_SYSTEM = SYSTEM.replace("two observed moments", "two GROUPS of observed moments").replace(
    "Do A and B", "Do the groups A and B")


def group_prompt(day, ga, gb):
    def block(name, g):
        shown = sorted(g, key=lambda v: day.pos[v])
        if len(shown) > 6:
            shown = sorted(sorted(shown, key=lambda v: -day.card(v)["dwell"])[:6], key=lambda v: day.pos[v])
        return f"Group {name} ({len(g)} visits, showing {len(shown)}):\n" + "\n".join(card_text(day.card(v), f"{name}{i + 1}") for i, v in enumerate(shown))
    return GROUP_SYSTEM, block("A", ga) + "\n\n" + block("B", gb) + "\n\nDo group A and group B belong to the same underlying task?"


def group_same(day, client, ga, gb):
    r1 = parse(client.ask(*group_prompt(day, ga, gb)))
    if r1["decision"] == "different_activity":
        return False, r1
    r2 = parse(client.ask(*group_prompt(day, gb, ga)))
    return (r1["decision"] == "same_activity" and r2["decision"] == "same_activity"), r1


def matching_size(links, a_set, b_set):
    used, n = set(), 0
    for x, y in links:
        if x in b_set:
            x, y = y, x
        if x in a_set and y in b_set and x not in used and y not in used:
            used |= {x, y}
            n += 1
    return n


def reconstruct(day, pairs, client, mode, group_oracle=None):
    """Build clusters from pair verdicts.
    A link is a pair whose combined verdict is 'same'. Order is chronological, not confidence (confidence is uncalibrated).
    Singletons attach to a cluster with a direct link; a singleton linked equally to two clusters attaches to neither (it must
    not bridge them). Two established clusters merge only with at least two vertex-disjoint direct links, or when a group-level
    question says same in both orders. Mode 'C' adds the deterministic checks. Returns (result, log)."""
    cfg = {"B": {}, "C": {}, "oracle": {}}[mode]
    links, rejected, judged = {}, [], {}
    for k, p in pairs.items():
        judged[k] = p
        if p["verdict"] != "same":
            continue
        if mode == "C":
            a, b = k
            if day.cannot(a, b):
                rejected.append((k, "cannot_link"))
                continue
            if a in day.affected or b in day.affected:
                rejected.append((k, "incomplete_evidence"))
                continue
            if not phrase_ok(day, a, b, p["evidence"]):
                rejected.append((k, "evidence_not_verifiable"))
                continue
        links[k] = p
    cluster = {}
    members = {}
    nxt = itertools.count()
    log = {"attach": [], "merge": [], "blocked": [], "rejected": rejected, "group_calls": 0, "ties": []}

    def new(v):
        c = next(nxt)
        cluster[v] = c
        members[c] = [v]
        return c

    def different_inside(sa, sb):
        return [k for k in judged if judged[k]["verdict"] in ("different", "conflict")
                and ((k[0] in sa and k[1] in sb) or (k[0] in sb and k[1] in sa))]

    def cross_links(sa, sb):
        return [k for k in links if (k[0] in sa and k[1] in sb) or (k[0] in sb and k[1] in sa)]

    def blocked_by_checks(sa, sb):
        if mode != "C":
            return None
        for x in sa:
            for y in sb:
                if day.cannot(x, y):
                    return "cannot_link"
        against = len(different_inside(sa, sb))
        if against > len(cross_links(sa, sb)):             # more judged-different cross pairs than same links: the group disagrees
            return "group_inconsistent"
        if any(v in day.affected for v in list(sa) + list(sb)):
            return "incomplete_evidence"
        return None

    for v in day.order:
        new(v)
    ordered = sorted(links, key=lambda k: (day.pos[k[1]], day.pos[k[0]]))
    # phase 1: singleton-singleton links form pairs; later links attach singletons
    changed = True
    while changed:
        changed = False
        for k in ordered:
            a, b = k
            ca, cb = cluster[a], cluster[b]
            if ca == cb:
                continue
            sa, sb = set(members[ca]), set(members[cb])
            if len(sa) == 1 or len(sb) == 1:
                if len(sa) > 1 or len(sb) > 1:                      # a singleton meets an established cluster
                    single = a if len(sa) == 1 else b
                    into = collections.Counter(cluster[o] for pair in links if single in pair for o in pair
                                               if o != single and cluster[o] != cluster[single] and len(members[cluster[o]]) > 1)
                    top = sorted(into.values(), reverse=True)
                    if len(top) >= 2 and top[0] == top[1]:           # linked equally to two clusters: it must not bridge them
                        if single not in log["ties"]:
                            log["ties"].append(single)
                        continue
                why = blocked_by_checks(sa, sb)
                if why:
                    log["blocked"].append((k, why))
                    continue
                keep, drop = (ca, cb) if len(sa) >= len(sb) else (cb, ca)
                for v in members[drop]:
                    cluster[v] = keep
                members[keep] += members.pop(drop)
                log["attach"].append(k)
                changed = True
                break
    # phase 2: merges between established clusters
    changed = True
    while changed:
        changed = False
        cs = sorted({c for c in members if len(members[c]) >= 2}, key=lambda c: day.pos[min(members[c], key=day.pos.get)])
        for ca, cb in itertools.combinations(cs, 2):
            if ca not in members or cb not in members:
                continue
            sa, sb = set(members[ca]), set(members[cb])
            cl = cross_links(sa, sb)
            if not cl:
                continue
            why = blocked_by_checks(sa, sb)
            if why:
                log["blocked"].append((cl[0], why))
                continue
            route = None
            if matching_size(cl, sa, sb) >= 2:
                route = "two_independent_links"
            else:
                log["group_calls"] += 1
                if group_oracle is not None:
                    ok = group_oracle(sa, sb)
                else:
                    ok, _ = group_same(day, client, sorted(sa, key=day.pos.get), sorted(sb, key=day.pos.get))
                if ok:
                    route = "group_confirmed"
                else:
                    log["blocked"].append((cl[0], "group_question_not_same"))
            if route:
                for v in members[cb]:
                    cluster[v] = ca
                members[ca] += members.pop(cb)
                log["merge"].append({"route": route, "a": sorted(sa), "b": sorted(sb), "links": len(cl)})
                changed = True
                break
    groups = [sorted(m, key=day.pos.get) for m in members.values() if len(m) >= 2]
    in_group = {v for g in groups for v in g}
    states = {}
    for v in day.order:
        if v in in_group:
            continue
        ps = [p for k, p in judged.items() if v in k]
        if any(p["verdict"] == "conflict" for p in ps):
            states[v] = (ac.AMBIGUOUS, "margin_too_small")
        elif any(p["verdict"] == "uncertain" for p in ps):
            states[v] = (ac.AMBIGUOUS, "insufficient_families")
        elif v in log["ties"]:
            states[v] = (ac.AMBIGUOUS, "margin_too_small")
        elif any(k for k, _ in rejected if v in k):
            states[v] = (ac.AMBIGUOUS, "cannot_link" if any(k for k, w in rejected if v in k and w == "cannot_link") else "insufficient_families")
        elif ps:
            states[v] = (ac.UNASSIGNED, "no_candidate")
        else:
            states[v] = (ac.UNASSIGNED, "no_candidate")
    log["links"] = sorted(links)
    return build_result(day, groups, states), log


def build_result(day, groups, states):
    start = {v.visit_id: v.t_start for v in day.prepared.visits}
    rows, acts = [], []
    for g in groups:
        aid = ac.activity_id(ac.earliest_member(start, g))
        acts.append({"id": aid, "members": sorted(g)})
        for v in g:
            rows.append({"visit_id": v, "state": ac.ASSIGNED, "activity_id": aid, "reason": None, "candidates": [], "internal_shift": None})
    for v, (st, why) in states.items():
        rows.append({"visit_id": v, "state": st, "activity_id": None, "reason": why, "candidates": [], "internal_shift": None})
    return {"visits": rows, "activities": acts, "relations": {}}


def gold_pair_verdicts(day, pool):
    """What a PERFECT judge would say about the presented pairs (for an upper bound only, never a result)."""
    out = {}
    for k in pool:
        t = day.truth(*k)
        out[k] = {"pair": list(k), "verdict": "same" if t else "different" if t is False else "uncertain", "orders": [], "evidence": [],
                  "explanation": [], "confidence": "high", "valid": True}
    return out


def gold_group_oracle(day):
    def f(sa, sb):
        la = collections.Counter(l for v in sa for l in day.gold[v]["labels"])
        lb = collections.Counter(l for v in sb for l in day.gold[v]["labels"])
        return bool(la and lb and la.most_common(1)[0][0] in lb and lb.most_common(1)[0][0] in la)
    return f


# ================================================================ evaluation

def pair_eval(day, pool, pairs):
    """Pairwise judgement on its own: only the pairs the model was shown and that have a scored truth."""
    rows = [(k, pairs[k], day.truth(*k)) for k in pool if k in pairs and day.truth(*k) is not None]
    rel = [r for r in rows if r[2]]
    unrel = [r for r in rows if not r[2]]
    cnt = collections.Counter((r[2], r[1]["verdict"]) for r in rows)
    tp, fp = cnt[(True, "same")], cnt[(False, "same")]
    out = [f"  judged candidate pairs with a scored truth: {len(rows)}   related {len(rel)}   unrelated {len(unrel)}",
           f"  verdicts on RELATED pairs:   same {cnt[(True, 'same')]}  different {cnt[(True, 'different')]}  uncertain/conflict {cnt[(True, 'uncertain')] + cnt[(True, 'conflict')]}",
           f"  verdicts on UNRELATED pairs: same {cnt[(False, 'same')]}  different {cnt[(False, 'different')]}  uncertain/conflict {cnt[(False, 'uncertain')] + cnt[(False, 'conflict')]}",
           f"  pairwise precision of 'same': {pct(tp, tp + fp)}   pairwise recall on presented related pairs: {pct(tp, len(rel))}",
           f"  'same' rate overall {pct(cnt[(True, 'same')] + cnt[(False, 'same')], len(rows))}   (a model that says yes to everything would show ~100% here)",
           f"  abstention (uncertain or order conflict) on all scored pairs: {pct(sum(1 for r in rows if r[1]['verdict'] in ('uncertain', 'conflict')), len(rows))}",
           f"  order sensitivity: pairs where the two orders disagreed {sum(1 for r in rows if r[1]['verdict'] == 'conflict')}; unparseable replies {sum(1 for r in rows if not r[1]['valid'])}"]
    out.append("  self-reported confidence, DESCRIPTIVE ONLY (uncalibrated: not a probability; no threshold is derived from it):")
    for c in CONF:
        s = [r for r in rows if r[1]["verdict"] == "same" and r[1]["confidence"] == c]
        out.append(f"    'same' with weakest stated confidence {c:6s}: {len(s):4d} pairs, {pct(sum(1 for r in s if r[2]), len(s))} of them related in this sample")
    return "\n".join(out), rows


def together(pred, x, y):
    return x in pred and y in pred and pred[x] == pred[y]


def recon_eval(day, result, name, log=None):
    out = ae.run_dataset(day.ds, lambda prepared, params: result, params=day.rp, max_pairs=10 ** 6)
    h, t = out["headline"], out["tallies"]
    rows = {r["visit_id"]: r for r in result["visits"]}
    n = len(rows)
    assigned = sum(1 for r in rows.values() if r["state"] == ac.ASSIGNED)
    amb = sum(1 for r in rows.values() if r["state"] == ac.AMBIGUOUS)
    acts = result["activities"]
    pred = ac.visit_predictions(result)
    direct = set(map(tuple, log["links"])) if log else None
    chained = chained_false = 0
    if direct is not None:
        for g in acts:
            for x, y in itertools.combinations(sorted(g["members"], key=day.pos.get), 2):
                if (x, y) not in direct:
                    chained += 1
                    chained_false += day.truth(x, y) is False
    pc = lambda v: "-" if v is None else f"{100 * v:.0f}%"   # noqa: E731
    line = (f"  {name:3s} precision {pc(h['precision_of_assigned']):>5s}"
            f"  recall {pc(h['recall_of_eligible']):>5s}"
            f"  false-merge pairs {t['fp']:4d}  missed pairs {t['fn']:5d}  coverage {pct(assigned, n):>4s}  ambiguous {pct(amb, n):>4s}"
            f"  activities {len(acts)} (>1 visit: {sum(1 for a in acts if len(a['members']) > 1)})")
    if direct is not None:
        line += f"  chained pairs {chained} (false {chained_false})"
    return line, out, pred


def bridge_report(day, log, name):
    lines = [f"  {name}: singleton attachments {len(log['attach'])}, cluster merges {len(log['merge'])}, group-level questions asked {log['group_calls']}, "
             f"blocked {len(log['blocked'])}, ties left unattached {len(log['ties'])}"]
    bad = 0
    for m in log["merge"]:
        la = collections.Counter(l for v in m["a"] for l in day.gold[v]["labels"])
        lb = collections.Counter(l for v in m["b"] for l in day.gold[v]["labels"])
        pure = all(day.gold[v]["kind"] == "pure" for v in m["a"] + m["b"])
        wrong = pure and not (set(la) & set(lb))
        bad += wrong
        lines.append(f"     merge of {len(m['a'])}+{len(m['b'])} visits via {m['route']} ({m['links']} direct link(s)): "
                     f"{'WRONG BRIDGE' if wrong else 'consistent with labels' if pure else 'not fully scored'}  "
                     f"[{' / '.join(sorted(la)[:2]) or 'noise'} + {' / '.join(sorted(lb)[:2]) or 'noise'}]")
    lines.append(f"     incorrect bridges between established clusters: {bad} of {len(log['merge'])}")
    if log["rejected"]:
        c = collections.Counter(w for _, w in log["rejected"])
        lines.append(f"     links rejected by deterministic validation: {dict(c)}")
    if log["blocked"]:
        c = collections.Counter(w for _, w in log["blocked"])
        lines.append(f"     merges/attachments blocked: {dict(c)}")
    return "\n".join(lines)


def examples(day, preds, pairs, logs, private=False, n=4):
    A, B, C = preds["A"], preds["B"], preds["C"]
    pure = [v for v in day.order if day.gold[v]["kind"] == "pure"]
    rel = [day.key(a, b) for a, b in itertools.combinations(pure, 2) if day.truth(a, b)]
    unrel = [day.key(a, b) for a, b in itertools.combinations(pure, 2) if day.truth(a, b) is False]
    rej = {k: w for k, w in logs["C"]["rejected"]}
    blk = {k: w for k, w in logs["C"]["blocked"]}

    def show(title, ks, extra=None):
        out = [f"\n  {title}: {len(ks)} pair(s)" + ("" if ks else "  (none)")]
        for k in ks[:n]:
            out.append(f"    {day.describe(k[0], private)}  [{day.label_text(k[0])}]")
            out.append(f"    {day.describe(k[1], private)}  [{day.label_text(k[1])}]" + (f"   <- {extra(k)}" if extra else ""))
            p = pairs.get(k)
            if p and not private and p["explanation"]:
                out.append(f"        model: {p['verdict']}; \"{clip(p['explanation'][0], 110)}\"")
        return "\n".join(out)

    out = ["EXAMPLES (pairs of pure-labelled visits; labels are yours, not ground truth)"]
    out.append(show("LLM-only (B) joins a related pair that the deterministic engine (A) misses", [k for k in rel if together(B, *k) and not together(A, *k)]))
    out.append(show("deterministic engine (A) joins a related pair that LLM-only (B) misses", [k for k in rel if together(A, *k) and not together(B, *k)]))
    out.append(show("hybrid (C) prevents a wrong LLM-only (B) merge", [k for k in unrel if together(B, *k) and not together(C, *k)],
                    lambda k: "C rejected the link: " + rej.get(k, blk.get(k, "group consistency or a different cluster outcome"))))
    out.append(show("all three approaches miss a related pair", [k for k in rel if not (together(A, *k) or together(B, *k) or together(C, *k))]))
    amb = [k for k in unrel if (together(B, *k) or together(C, *k) or together(A, *k)) and day.gold[k[0]]["labels"] == day.gold[k[1]]["labels"] == set()]
    out.append(show("AMBIGUOUS ANNOTATION: noise-labelled visits that an approach grouped (noise is 'together with nothing', so these count as false merges; "
                    "check whether you labelled coherent self-observation as noise)", amb))
    return "\n".join(out)


def metrics_report(clients, out_dir):
    lines = ["COST AND RESOURCES (from the recorded runs)"]
    for c in clients:
        recs = [r for r in c.cache.values() if r.get("model") == c.model]
        if not recs:
            continue
        w = sorted(r["wall_s"] for r in recs)
        pt = sum(r.get("prompt_tokens") or 0 for r in recs)
        ot = sum(r.get("output_tokens") or 0 for r in recs)
        lines.append(f"  {c.model}: {len(recs)} calls, wall mean {statistics.mean(w):.2f}s median {statistics.median(w):.2f}s p95 {w[int(0.95 * (len(w) - 1))]:.2f}s, "
                     f"total {sum(w) / 60:.1f} min; tokens prompt {pt} + output {ot} (mean {pt / len(recs):.0f} + {ot / len(recs):.0f} per call)")
    sp = os.path.join(out_dir, "samples.jsonl")
    if os.path.exists(sp):
        rows = [json.loads(x) for x in open(sp, encoding="utf-8") if x.strip()]
        rss = [r["runner_rss_mb"] for r in rows if r.get("runner_rss_mb")]
        gpu = [r["gpu_util_pct"] for r in rows if r.get("gpu_util_pct") is not None]
        swap = [r["swap_used_mb"] for r in rows if r.get("swap_used_mb") is not None]
        cpu = [r["runner_cpu_pct"] for r in rows if r.get("runner_cpu_pct") is not None]
        lines.append(f"  sampled every second ({len(rows)} samples): runner peak RSS {max(rss):.0f} MB" if rss else "  no runner RSS samples (server pid not found)")
        if gpu:
            lines.append(f"  GPU device utilisation: max {max(gpu)}%  mean {statistics.mean(gpu):.1f}%")
        if cpu:
            lines.append(f"  runner CPU: mean {statistics.mean(cpu):.0f}%  max {max(cpu):.0f}%  (100% = one core)")
        if swap:
            lines.append(f"  swap used: start {swap[0]:.0f} MB, end {swap[-1]:.0f} MB, max {max(swap):.0f} MB")
    return "\n".join(lines)


# ================================================================ machine probes (macOS) and the sampler

def sh(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def serve_pid(host):
    port = host.rsplit(":", 1)[-1]
    out = sh("lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t")
    return int(out.split()[0]) if out else None


def runner_pids(host):
    pid = serve_pid(host)
    if not pid:
        return []
    kids = sh("pgrep", "-P", str(pid)).split()
    return [pid] + [int(k) for k in kids]


def swap_used_mb():
    m = re.search(r"used = ([\d.]+)M", sh("sysctl", "-n", "vm.swapusage"))
    return float(m.group(1)) if m else None


def gpu_util():
    m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', sh("ioreg", "-r", "-d", "1", "-w0", "-c", "AGXAccelerator"))
    return int(m.group(1)) if m else None


def free_mb():
    vm = sh("vm_stat")
    page = int((re.search(r"page size of (\d+)", vm) or [0, 16384])[1])
    d = {}
    for line in vm.splitlines():
        if ":" in line and "Pages" in line:
            k, v = line.split(":")
            d[k.strip()] = int(v.strip().rstrip(".") or 0) * page / 1e6
    return d


class Sampler(threading.Thread):
    """Samples once a second: runner RSS and CPU (children of the `ollama serve` we started), GPU utilisation, swap, free memory."""

    def __init__(self, out_dir, host):
        super().__init__(daemon=True)
        self.path, self.host, self.stop_ev = os.path.join(out_dir, "samples.jsonl"), host, threading.Event()

    def run(self):
        with open(self.path, "a", encoding="utf-8") as fh:
            while not self.stop_ev.is_set():
                pids = runner_pids(self.host)
                rss = cpu = None
                if pids:
                    out = sh("ps", "-o", "rss=,%cpu=", "-p", ",".join(map(str, pids))).splitlines()
                    vals = [x.split() for x in out if x.strip()]
                    rss = sum(float(v[0]) for v in vals) / 1024 if vals else None
                    cpu = sum(float(v[1]) for v in vals) if vals else None
                f = free_mb()
                fh.write(json.dumps({"t": round(time.time(), 1), "runner_rss_mb": rss, "runner_cpu_pct": cpu, "gpu_util_pct": gpu_util(),
                                     "swap_used_mb": swap_used_mb(), "free_mb": f.get("Pages free"), "compressed_mb": f.get("Pages occupied by compressor")}) + "\n")
                fh.flush()
                self.stop_ev.wait(1.0)

    def stop(self):
        self.stop_ev.set()
        self.join(timeout=5)


def http_json(host, path, body=None):
    req = urllib.request.Request(f"http://{host}{path}", data=json.dumps(body).encode() if body else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run_manifest(out_dir, model, host):
    info = {}
    try:
        show = http_json(host, "/api/show", {"model": model})
        det = show.get("details", {})
        info = {"quantization": det.get("quantization_level"), "parameter_size": det.get("parameter_size"), "format": det.get("format"),
                "family": det.get("family"), "context_length": next((v for k, v in show.get("model_info", {}).items() if k.endswith(".context_length")), None)}
        info["runtime_version"] = http_json(host, "/api/version").get("version")
    except Exception as e:
        info["error"] = str(e)
    doc = {"model": model, "host": host, "prompt_version": PROMPT_VERSION, "options": OPTIONS, "retrieval": CFG, "model_info": info,
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": "confidence in replies is recorded, not used as a probability"}
    with open(os.path.join(out_dir, f"run_{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    return doc


# ================================================================ commands

def cmd_env(a):
    os.makedirs(a.out, exist_ok=True)
    lines = [f"ENVIRONMENT SNAPSHOT  {time.strftime('%Y-%m-%d %H:%M:%S')}  label: {a.label}",
             f"boot time: {sh('sysctl', '-n', 'kern.boottime')}", f"uptime: {sh('uptime')}",
             f"RAM bytes: {sh('sysctl', '-n', 'hw.memsize')}   swap: {sh('sysctl', '-n', 'vm.swapusage')}",
             "memory_pressure: " + (sh("memory_pressure", "-Q") or "n/a"), "vm_stat:", sh("vm_stat"),
             "top processes by resident memory (MB):"]
    for line in sh("ps", "-Ao", "rss=,comm=", "-r").splitlines()[:12]:
        rss, _, comm = line.strip().partition(" ")
        lines.append(f"   {int(rss) / 1024:8.0f}  {comm.strip()[-60:]}")
    lines.append("ollama processes: " + (sh("pgrep", "-fl", "ollama") or "none"))
    lines.append("LMemM running: " + (sh("pgrep", "-fl", "lmemm") or "no"))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(a.out, f"env_{a.label}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


def cmd_cpucheck(a):
    os.makedirs(a.out, exist_ok=True)
    pid = serve_pid(a.host)
    if not pid:
        sys.exit(f"no Ollama server listening on {a.host}. Start one first: OLLAMA_HOST={a.host} ollama serve")
    client = Client(os.path.join(a.out, "cpucheck_cache"), a.model, a.host)
    sampler = Sampler(a.out, a.host)
    sampler.start()
    time.sleep(2)
    base_gpu = gpu_util()
    t0 = time.time()
    client.cache.clear()
    for i in range(3):
        client.key = (lambda s, u, i=i: hashlib.sha256(f"{i}{time.time()}".encode()).hexdigest())       # never cached: real generations
        client.ask(SYSTEM, f"Visit A\n  app: Terminal\n  window: pytest\nVisit B\n  app: Code\n  window: test_{i}.py\nSame task?")
    time.sleep(1)
    ps = http_json(a.host, "/api/ps").get("models", [])
    sampler.stop()
    rows = [json.loads(x) for x in open(sampler.path, encoding="utf-8") if x.strip() and json.loads(x)["t"] >= t0 - 1]
    gpu = [r["gpu_util_pct"] for r in rows if r["gpu_util_pct"] is not None]
    cpu = [r["runner_cpu_pct"] for r in rows if r["runner_cpu_pct"] is not None]
    rss = [r["runner_rss_mb"] for r in rows if r["runner_rss_mb"]]
    vram = [m.get("size_vram", 0) for m in ps if m.get("name", "").startswith(a.model.split(":")[0])]
    lines = ["CPU-ONLY VERIFICATION (empirical)", f"  model {a.model}, options num_gpu={OPTIONS['num_gpu']} num_thread={OPTIONS['num_thread']}",
             "  `ollama ps` view of the server: " + (sh("env", f"OLLAMA_HOST={a.host}", "ollama", "ps").replace("\n", " | ") or "n/a"),
             f"  /api/ps size_vram (bytes in GPU memory): {vram}",
             f"  GPU device utilisation before {base_gpu}%, during generation max {max(gpu) if gpu else 'n/a'}%",
             f"  runner CPU during generation: max {max(cpu):.0f}% (100% = one core)" if cpu else "  runner CPU: no samples",
             f"  runner RSS: max {max(rss):.0f} MB" if rss else "  runner RSS: no samples",
             f"  seconds per tiny generation: {(time.time() - t0) / 3:.1f}"]
    ok = bool(vram) and all(v == 0 for v in vram) and (not gpu or max(gpu) < 10) and bool(cpu) and max(cpu) > 80
    lines.append("  VERDICT: " + ("CPU-only confirmed (no GPU memory used, GPU idle, CPU busy)" if ok else
                                  "NOT CONFIRMED as CPU-only: do not benchmark until this is explained (see numbers above)"))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(a.out, "cpucheck.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


def cmd_retrieve(a):
    day = Day(a.dataset)
    pool, potential = retrieve(day)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "candidates.json"), "w", encoding="utf-8") as fh:
        json.dump({"config": CFG, "pairs": [{"pair": list(k), **v} for k, v in sorted(pool.items(), key=lambda kv: (day.pos[kv[0][0]], day.pos[kv[0][1]]))]}, fh)
    print(recall_report(day, pool, potential))


def select_pairs(day, pool, a):
    ks = sorted(pool, key=lambda k: (-pool[k]["score"], day.pos[k[0]], day.pos[k[1]]))
    if a.subset:
        ks = stratified_subset(day, pool, a.subset)
    if a.max_pairs:
        ks = ks[: a.max_pairs]
    return ks


def cmd_judge(a):
    day = Day(a.dataset)
    pool, _ = retrieve(day)
    ks = select_pairs(day, pool, a)
    client = Client(a.out, a.model, a.host)
    run_manifest(a.out, a.model, a.host)
    sampler = Sampler(a.out, a.host)
    sampler.start()
    t0 = time.time()
    try:
        for i, k in enumerate(ks, 1):
            judge_pair(day, client, *k)
            if i % 20 == 0 or i == len(ks):
                el = time.time() - t0
                print(f"  {i}/{len(ks)} pairs, {client.new_calls} new calls, {el / 60:.1f} min elapsed", flush=True)
    finally:
        sampler.stop()
    print(f"done: {len(ks)} pairs judged ({client.new_calls} new model calls); cache {client.path}")


def collect_pairs(day, pool, client, a, subset_only=False):
    ks = select_pairs(day, pool, a)
    pairs = {}
    for k in ks:
        try:
            pairs[k] = judge_pair(day, client, *k)
        except Missing:
            continue
    return pairs, ks


def cmd_build(a):
    day = Day(a.dataset)
    pool, _ = retrieve(day)
    client = Client(a.out, a.model, a.host)
    sampler = Sampler(a.out, a.host)
    sampler.start()
    try:
        pairs, ks = collect_pairs(day, pool, Client(a.out, a.model, a.host, allow_calls=False), a)
        print(f"{len(pairs)}/{len(ks)} pairs are in the cache" + ("" if len(pairs) == len(ks) else " (run `judge` for the rest)"))
        for mode in ("B", "C"):
            res, log = reconstruct(day, pairs, client, mode)
            print(f"{mode}: {len(res['activities'])} activities, {log['group_calls']} group questions, {client.new_calls} new calls so far")
    finally:
        sampler.stop()


def cmd_report(a):
    day = Day(a.dataset)
    pool, potential = retrieve(day)
    client = Client(a.out, a.model, a.host, allow_calls=False)
    pairs, ks = collect_pairs(day, pool, client, a)
    out = ["=" * 78, "LLM ACTIVITY INFERENCE EXPERIMENT (experimental results, not production)",
           f"dataset {day.ds.name}   visits {len(day.order)}   model {a.model}   prompt {PROMPT_VERSION}   options {OPTIONS}", "=" * 78, "",
           recall_report(day, pool, potential), "",
           "PAIRWISE JUDGEMENT (model quality conditional on the candidates it was shown)"]
    scope = [k for k in pool if k in pairs]
    out.append(f"  pairs judged {len(pairs)} of {len(ks)} selected ({len(pool)} candidates in all)")
    ptext, _ = pair_eval(day, {k: pool[k] for k in scope}, pairs)
    out.append(ptext)
    results, logs, preds = {}, {}, {}
    ra = inf.infer(day.prepared, day.rp)
    results["A"], logs["A"] = ra, None
    group_client = Client(a.out, a.model, a.host, allow_calls=False)
    complete = len(pairs) == len(pool)
    out.append("")
    out.append("COMPLETE ACTIVITY RECONSTRUCTION (existing evaluation conventions; unlabelled and unsure visits left out)")
    if not complete:
        out.append(f"  NOTE: only {len(pairs)} of {len(pool)} candidate pairs have model answers, so B and C below reconstruct from a PARTIAL pool; "
                   "run `judge` without --subset/--max-pairs for the full comparison")
    for mode in ("B", "C"):
        try:
            results[mode], logs[mode] = reconstruct(day, pairs, group_client, mode)
        except Missing:
            out.append(f"  {mode}: a group-level answer is not cached; run `build` first")
            results[mode], logs[mode] = None, None
    for name in ("A", "B", "C"):
        if results.get(name) is None:
            continue
        line, ev_out, pred = recon_eval(day, results[name], name, logs[name])
        out.append(line)
        preds[name] = pred
    orc_pairs = gold_pair_verdicts(day, pool)
    ro, lo = reconstruct(day, orc_pairs, None, "oracle", group_oracle=gold_group_oracle(day))
    line, _, _ = recon_eval(day, ro, "UB", lo)
    out.append(line + "   <- upper bound: a perfect judge on the same candidates (not a result)")
    out.append("")
    out.append("BRIDGES AND GROUP REASONING")
    for mode in ("B", "C"):
        if logs.get(mode):
            out.append(bridge_report(day, logs[mode], mode))
    if all(k in preds for k in ("A", "B", "C")):
        out.append("")
        out.append(examples(day, preds, pairs, logs, a.private))
    out.append("")
    out.append(metrics_report([client], a.out))
    out.append("")
    out.append("CAUTIONS: your labels are not ground truth; 23 can't-tell visits are excluded from every score; one Activity dominates the pair count; "
               "model confidence is uncalibrated and unused; candidate recall bounds pairwise recall.")
    text = "\n".join(out)
    print(text)
    with open(os.path.join(a.out, f"report_{re.sub(r'[^A-Za-z0-9._-]', '_', a.model)}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, data=True):
        if data:
            p.add_argument("dataset")
        p.add_argument("out")
        p.add_argument("--model", default="gemma3:1b")
        p.add_argument("--host", default="127.0.0.1:11436")
        p.add_argument("--private", action="store_true")
        p.add_argument("--subset", type=int, default=0, help="a deterministic representative subset of this many candidate pairs")
        p.add_argument("--max-pairs", type=int, default=0)
    e = sub.add_parser("env")
    e.add_argument("out")
    e.add_argument("--label", default="after-restart")
    e.set_defaults(fn=cmd_env)
    c = sub.add_parser("cpucheck")
    common(c, data=False)
    c.set_defaults(fn=cmd_cpucheck)
    for name, fn in (("retrieve", cmd_retrieve), ("judge", cmd_judge), ("build", cmd_build), ("report", cmd_report)):
        p = sub.add_parser(name)
        common(p)
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

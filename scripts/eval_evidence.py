#!/usr/bin/env python3
"""Before/after evaluation of the shared evidence record (docs/adr/0001-unified-observation.md).

    python3 scripts/eval_evidence.py --memory data/memory/memory.json        # health of stored OCR-only text
    python3 scripts/eval_evidence.py --log data/memory/evidence_eval.jsonl   # paired before/after per frame

--log reads the opt-in file the tracker writes when config.EVIDENCE_LOG is True: for every frame, the text the
old code would have stored (OCR alone, "before") and what is stored now (OCR merged with the trail's read, "after").
With accessibility off there are no trail reads, so after == before; the report says so instead of inventing gains.

Metrics (all proxies; none needs hand labels):
  garbled lines        lines with OCR-noise shapes inside a word ("Lead$", "ag0", "8x") - a heuristic, not ground truth
  line churn           distinct raw texts per distinct line (case/punctuation/look-alike folded); 1.0 = a line is written one way
  useful coverage      distinct lines of 12+ characters and 3+ words, per frame
  grounded frames      frames whose stored text has at least one line the app supplied (needs the trail)
  gate                 how often an app read was refused because its lines disagreed with the OCR

--simulate answers a different question: is the matching safe? It takes real pages from memory.json (lines that do not
look garbled) as a stand-in for an accessibility read, corrupts a copy the way real OCR garbles ("s" -> "$", "o" -> "0",
"l" -> "1", dropped space, "m" -> "rn"), merges the two, and counts: garbled lines repaired, lines wrongly replaced
(a false positive: the OCR line ended up as a different line's text), and what leaks when the app read is from
ANOTHER page that shares the window title. It simulates the app read; it is not a measurement of accessibility.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from observation import _similar, fold  # noqa: E402

NOISE = re.compile(r"[A-Za-z][\$&@][A-Za-z]|[A-Za-z]\$\b|\b[a-z]+[0-9][a-z]+\b|\b[a-z]{2,}[0-9]\b|[A-Za-z]'\.[A-Za-z]|[A-Za-z][|\\][A-Za-z]")


def garbled(line):
    return bool(NOISE.search(line))


def useful(line):
    return len(line) >= 12 and len(re.findall(r"\w+", line)) >= 3


def stats(frames):
    """frames: list of (key, [lines]). Returns the metrics for one side."""
    lines = [l for _, ls in frames for l in ls]
    raw_by_fold = defaultdict(set)
    for _, ls in frames:
        for l in ls:
            raw_by_fold[fold(l)].add(l)
    distinct = {f for f in raw_by_fold if f}
    uniq = [l for l in lines if useful(l)]
    return {
        "frames": len(frames),
        "lines": len(lines),
        "garbled_lines": sum(1 for l in lines if garbled(l)),
        "garbled_share": round(sum(1 for l in lines if garbled(l)) / len(lines), 4) if lines else 0.0,
        "distinct_lines": len(distinct),
        "line_churn": round(sum(len(v) for v in raw_by_fold.values()) / len(distinct), 3) if distinct else 1.0,
        "useful_distinct": len({fold(l) for l in uniq}),
        "useful_per_frame": round(len(uniq) / len(frames), 2) if frames else 0.0,
    }


def from_memory(path):
    items = json.load(open(path)).get("items", [])
    frames = []
    for it in items:
        for e in (it.get("content") or {}).get("excerpts", []):
            if isinstance(e.get("text"), str):
                frames.append((it.get("id"), e["text"].split("\n")))
    return stats(frames)


def from_log(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    before = [(r.get("window"), (r.get("before") or "").split("\n") if r.get("before") else []) for r in rows]
    after = [(r.get("window"), (r.get("after") or "").split("\n") if r.get("after") else []) for r in rows]
    grounded = sum(1 for r in rows if (r.get("provenance") or {}).get("ax_only", 0) + (r.get("provenance") or {}).get("agreed", 0)
                   + (r.get("provenance") or {}).get("differed", 0) > 0)
    out = {"frames": len(rows), "frames_with_trail_read": sum(1 for r in rows if r.get("matched")),
           "frames_with_trail_read_nearby": sum(1 for r in rows if r.get("ax_near")),
           "gate": dict(Counter(r.get("gate") for r in rows)), "grounded_frames": grounded,
           "grounded_share": round(grounded / len(rows), 3) if rows else 0.0,
           "lines_preserved_as_ocr_variants": sum(len(r.get("variants") or []) for r in rows),
           "before": stats(before), "after": stats(after)}
    return out


def corrupt(line, rnd):
    """One to two OCR-style edits; returns the line unchanged if no edit applies."""
    edits = [("s", "$"), ("o", "0"), ("l", "1"), ("m", "rn"), ("e", "c"), (" ", "")]
    out = line
    for _ in range(rnd.choice([1, 1, 2])):
        old, new = rnd.choice(edits)
        spots = [m.start() for m in re.finditer(re.escape(old), out)]
        if spots:
            i = rnd.choice(spots)
            out = out[:i] + new + out[i + 1:]
    return out


def simulate(path, distinct=True, seed=11, pages_max=400, rate=0.4):
    import random
    from observation import Line, Observation, merge_report
    rnd = random.Random(seed)
    pages = []
    for it in json.load(open(path)).get("items", []):
        clean = []
        for e in (it.get("content") or {}).get("excerpts", []):
            for l in (e.get("text") or "").split("\n"):
                l = " ".join(l.split())
                if useful(l) and not garbled(l) and len(l) < 200 and not any(
                        fold(l) == fold(c) or (distinct and _similar(fold(l), fold(c), 0.7)) for c in clean):
                    clean.append(l)        # distinct=False keeps look-alike lines ("Table 3 ..." / "Table 5 ...") to stress the matching
        if len(clean) >= 6:
            pages.append(clean[:30])
    rnd.shuffle(pages)
    pages = pages[:pages_max]
    mk = lambda reader, lines, src, **kw: Observation(
        t=0.0, reader=reader, bundle_id="x", window="Untitled", lines=[
            Line(t, src, None if src == "ax" else 0.9, "paragraph", [0, 0, 10, 10], True) for t in lines], **kw)
    tot = {"pages": len(pages), "ocr_lines": 0, "corrupted": 0, "garbled_before": 0, "garbled_after": 0,
           "repaired": 0, "left_unmatched": 0, "wrongly_replaced": 0, "gate_refused_same_page": 0}
    cross = {"pairs": 0, "gate_refused": 0, "leaked_pairs": 0}
    for k, page in enumerate(pages):
        noisy = [corrupt(l, rnd) if rnd.random() < rate else l for l in page]
        out, report = merge_report([mk("trail", page, "ax"), mk("tracker", noisy, "ocr")])
        if report["gate"] != "merged":
            tot["gate_refused_same_page"] += 1
        by_variant = {}
        for m in out:
            by_variant[m.text] = m
            for v in m.variants:
                by_variant[v["text"]] = m
        for truth, seen in zip(page, noisy):
            tot["ocr_lines"] += 1
            changed = seen != truth
            tot["corrupted"] += changed
            tot["garbled_before"] += garbled(seen)
            m = by_variant.get(seen)
            final = m.text if m else seen
            tot["garbled_after"] += garbled(final)
            right = final == truth or fold(final) == fold(truth)
            if changed and right:
                tot["repaired"] += 1
            elif changed and final == seen:
                tot["left_unmatched"] += 1
            elif final != seen and not right:
                tot["wrongly_replaced"] += 1
        other = pages[(k + 1) % len(pages)]
        if other is not page:
            cross["pairs"] += 1
            out2, rep2 = merge_report([mk("trail", other, "ax"), mk("tracker", noisy, "ocr")])
            cross["gate_refused"] += rep2["gate"] != "merged"
            cross["leaked_pairs"] += any(m.source == "ax" for m in out2)
    tot["repair_rate_of_corrupted"] = round(tot["repaired"] / tot["corrupted"], 3) if tot["corrupted"] else 0
    tot["wrong_replacement_rate"] = round(tot["wrongly_replaced"] / tot["ocr_lines"], 5) if tot["ocr_lines"] else 0
    return {"same_page": tot, "app_read_from_another_page_with_the_same_title": cross}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memory", help="memory.json: report the health of the stored (OCR-only) text")
    ap.add_argument("--log", help="evidence_eval.jsonl: paired before/after per frame")
    ap.add_argument("--simulate", help="memory.json: stress the matching with simulated OCR noise on real pages")
    a = ap.parse_args()
    if not (a.memory or a.log or a.simulate):
        ap.error("give --memory, --log and/or --simulate")
    out = {}
    if a.memory:
        out["memory_json_ocr_only"] = from_memory(a.memory)
    if a.simulate:
        out["simulation_distinct_lines"] = simulate(a.simulate, True)
        out["simulation_look_alike_lines_kept"] = simulate(a.simulate, False)
    if a.log:
        out["paired_log"] = from_log(a.log)
        if not out["paired_log"]["frames_with_trail_read"]:
            out["note"] = ("No frame had a matching accessibility read: accessibility was off or never agreed with the "
                           "screen. The paired comparison cannot show a gain in that case.")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

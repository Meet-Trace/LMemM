import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from context import serialize
from inference import build_prompt, parse_output
from trace_v2 import paths
from trace_v2.moments.builder import ALGORITHM
from trace_v2.moments.similarity import S
from trace_v2.observations import ObservationStore
from trace_v2.observations.queries import _decode
from trace_v2.structure.processor import read_connection

def call(host, path, body, timeout=300):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(host + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def moments(conn):
    rows = conn.execute(
        "SELECT r.moment_id, r.revision, r.state, r.start_ns, r.end_ns, r.members_encoding, r.members FROM moment_revisions r "
        "JOIN moments m USING (moment_id) WHERE m.algorithm=? AND r.state!='merged' AND r.revision=("
        "SELECT max(x.revision) FROM moment_revisions x WHERE x.moment_id=r.moment_id) ORDER BY r.start_ns", (ALGORITHM,)).fetchall()
    out = []
    for moment_id, revision, state, start, end, enc, blob in rows:
        members = [m["o"] for m in _decode(enc, blob) if m["role"] == "core"]
        if len(members) >= 2:
            out.append({"id": moment_id, "revision": revision, "state": state, "start_ns": start, "end_ns": end,
                        "members": members})
    return out


def ax_strings(node, out, depth=0):
    if not isinstance(node, dict) or depth > 12 or len(out) > 400:
        return
    for key in ("title", "value", "description", "label", "help"):
        value = node.get(key)
        if isinstance(value, str) and len(value.strip()) >= 4:
            out.append(value.strip()[:300])
    for child in node.get("children") or []:
        ax_strings(child, out, depth + 1)


def lines_of(conn, oid):
    row = conn.execute("SELECT raw_encoding, raw FROM extractions WHERE observation_id=? AND reader='ocr' AND status='succeeded' "
                       "ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()
    out = []
    if row:
        raw = _decode(*row) or {}
        height = (raw.get("image_size") or [0, 0])[1] or 0
        for line in raw.get("lines", []):
            text = line.get("text", "").strip()
            box = line.get("box")
            if not text:
                continue
            if height and box and (box[1] < 0.04 * height or box[1] > 0.95 * height):
                continue
            out.append(text)
    ax = conn.execute("SELECT raw_encoding, raw FROM extractions WHERE observation_id=? AND reader='accessibility' "
                      "AND status='succeeded' ORDER BY revision DESC LIMIT 1", (oid,)).fetchone()
    if ax:
        found = []
        ax_strings(_decode(*ax), found)
        out.extend(found)
    return out


def record_of(conn, oid):
    row = conn.execute("SELECT captured_at_ns, app_name, window_title, context_json FROM observations WHERE observation_id=?", (oid,)).fetchone()
    if row is None:
        return None
    try:
        url = ((json.loads(row[3]) or {}).get("window") or {}).get("url")
    except ValueError:
        url = None
    events = [{"type": kind, "t_ns": t, "payload": json.loads(payload)} for t, kind, payload in conn.execute(
        "SELECT occurred_at_ns, type, payload_json FROM interaction_events WHERE observation_id=? ORDER BY seq", (oid,))]
    return {"t_ns": row[0], "app": row[1] or "unknown", "title": row[2], "url": url, "events": events}


def neighbours(conn, start_ns, end_ns, members, count=1):
    marks = ",".join("?" * len(members))
    before = conn.execute(f"SELECT observation_id FROM observations WHERE captured_at_ns<? AND observation_id NOT IN ({marks}) "
                          "ORDER BY captured_at_ns DESC LIMIT ?", (start_ns, *members, count)).fetchall()
    after = conn.execute(f"SELECT observation_id FROM observations WHERE captured_at_ns>? AND observation_id NOT IN ({marks}) "
                         "ORDER BY captured_at_ns LIMIT ?", (end_ns, *members, count)).fetchall()
    return [r[0] for r in reversed(before)], [r[0] for r in after]


def steps(conn, members, sample):
    n = len(members)
    k = max(1, min(sample, n))
    out = []
    for i in range(k):
        chunk = members[i * n // k:(i + 1) * n // k]
        if not chunk:
            continue
        records = [record_of(conn, oid) for oid in chunk]
        records = [r for r in records if r]
        if not records:
            continue
        middle = chunk[len(chunk) // 2]
        rep = record_of(conn, middle) or records[0]
        events = [e for r in records for e in r["events"]]
        out.append({"tag": f"O{len(out) + 1}", "ids": chunk, "t_ns": records[0]["t_ns"], "app": rep["app"], "title": rep["title"],
                    "url": rep["url"], "events": events, "lines": lines_of(conn, middle)})
    return out


def packet(conn, moment, budget, sample):
    members = moment["members"]
    sequence = steps(conn, members, sample)
    before_ids, after_ids = neighbours(conn, moment["start_ns"], moment["end_ns"], members)
    context = lambda ids: [dict(r, tag="B", lines=[]) for r in (record_of(conn, i) for i in ids) if r]
    built = serialize(sequence, context(before_ids), context(after_ids), budget)
    return built["text"], built["tags"]


def goal(host, model, text, tags, threads):
    started = time.perf_counter()
    prompt = build_prompt(text)
    reply = call(host, "/api/generate", {
        "model": model, "prompt": prompt, "stream": False, "format": "json", "keep_alive": "5m",
        "options": {"num_gpu": 0, "num_thread": threads, "temperature": 0, "num_ctx": 4096, "num_predict": 320}})
    elapsed = time.perf_counter() - started
    parsed = parse_output(reply.get("response", ""), tags)
    return {"goal": parsed["description"] or "", "keywords": [], "parsed": parsed, "raw": reply.get("response", ""),
            "seconds": round(elapsed, 2), "eval_count": reply.get("eval_count"), "prompt_eval_count": reply.get("prompt_eval_count")}


def embed(host, model, texts, threads):
    reply = call(host, "/api/embed", {"model": model, "input": texts, "keep_alive": "5m",
                                      "options": {"num_gpu": 0, "num_thread": threads}})
    vectors = np.asarray(reply["embeddings"], dtype=np.float32)
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)


def group(vectors, threshold):
    n = len(vectors)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    sim = vectors @ vectors.T
    order = sorted(((sim[i, j], i, j) for i in range(n) for j in range(i + 1, n) if sim[i, j] >= threshold), reverse=True)
    clusters = {i: {i} for i in range(n)}
    for value, i, j in order:
        a, b = find(i), find(j)
        if a == b:
            continue
        merged = clusters[a] | clusters[b]
        pairs = [sim[x, y] for x in merged for y in merged if x < y]
        if min(pairs) < threshold * 0.8:
            continue
        parent[b] = a
        clusters[a] = merged
        del clusters[b]
    return sorted((sorted(v) for v in clusters.values()), key=lambda c: c[0])


def ps(host):
    try:
        return [{"model": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram")} for m in call(host, "/api/ps", None, 10).get("models", [])]
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--models", default="gemma3:1b,qwen2.5:0.5b")
    parser.add_argument("--embed-model", default="embeddinggemma")
    parser.add_argument("--budget", type=int, default=4500)
    parser.add_argument("--sample", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.72)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    directory = paths.check_data_dir(os.path.abspath(os.path.expanduser(args.data_dir)))
    store = ObservationStore(directory)
    conn = read_connection(store)
    os.makedirs(args.out, exist_ok=True)
    cache_path = os.path.join(args.out, "goal_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    items = moments(conn)
    if args.limit:
        items = items[:args.limit]
    for m in items:
        m["packet"], m["tags"] = packet(conn, m, args.budget, args.sample)
        m["prompt"] = build_prompt(m["packet"])
        m["packet_sha"] = hashlib.sha256(m["prompt"].encode()).hexdigest()[:16]
    result = {"data_dir": str(directory), "settings": {k: v for k, v in vars(args).items()}, "models": {}, "ps": []}
    result["settings"] = {k: str(v) if not isinstance(v, (int, float, str)) else v for k, v in vars(args).items()}
    packets_vec = embed(args.host, args.embed_model, [m["packet"] for m in items], args.threads) if items else np.zeros((0, 1))
    result["baseline_packet_groups"] = group(packets_vec, args.threshold) if items else []
    for model in args.models.split(","):
        goals, times = [], []
        for m in items:
            key = f"{model}|{m['id']}|{m['packet_sha']}"
            if key not in cache:
                cache[key] = goal(args.host, model, m["packet"], m["tags"], args.threads)
                json.dump(cache, open(cache_path, "w"))
            goals.append(cache[key])
            times.append(cache[key]["seconds"])
        result["ps"].append({"after": model, "loaded": ps(args.host)})
        texts = [g["goal"] or f"unassigned moment {i}" for i, g in enumerate(goals)]
        vectors = embed(args.host, args.embed_model, texts, args.threads) if items else np.zeros((0, 1))
        times.sort()
        result["models"][model] = {
            "goals": [{"moment_id": m["id"], **g} for m, g in zip(items, goals)],
            "groups": group(vectors, args.threshold) if items else [],
            "empty_goals": sum(1 for g in goals if not g["goal"]),
            "seconds_median": times[len(times) // 2] if times else None,
            "seconds_max": times[-1] if times else None,
        }
    result["moments"] = [{k: m[k] for k in ("id", "revision", "state", "start_ns", "end_ns", "members", "packet", "prompt", "tags")} for m in items]
    json.dump(result, open(os.path.join(args.out, "moment_goals.json"), "w"), indent=1)
    conn.close()
    store.close()
    stamp = lambda ns: time.strftime("%H:%M:%S", time.localtime(ns / S))
    for model, data in result["models"].items():
        print(f"\n== {model}  median {data['seconds_median']} s  max {data['seconds_max']} s  empty {data['empty_goals']}")
        for i, g in enumerate(data["goals"]):
            m = items[i]
            print(f"  M{i:<3} {stamp(m['start_ns'])}-{stamp(m['end_ns'])}  {g['parsed']['status'][:4]} {g['parsed']['confidence']:.2f}  {g['goal'][:100]}")
        print("  groups by goal meaning:", [c for c in data["groups"] if len(c) > 1])
    print("\nbaseline groups by raw screen text:", [c for c in result["baseline_packet_groups"] if len(c) > 1])
    print("loaded models:", json.dumps(result["ps"]))


if __name__ == "__main__":
    main()

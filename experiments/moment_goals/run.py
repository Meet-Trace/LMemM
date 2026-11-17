import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from trace_v2 import paths
from trace_v2.moments.builder import ALGORITHM
from trace_v2.moments.inference import OUTPUT_SCHEMA, build_context, build_prompt, parse_interpretation, serialize
from trace_v2.moments.inference.load import load_window
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


def interpret(host, model, prompt, valid_ids, threads):
    started = time.perf_counter()
    reply = call(host, "/api/generate", {
        "model": model, "prompt": prompt, "stream": False, "format": OUTPUT_SCHEMA, "keep_alive": "5m",
        "options": {"num_gpu": 0, "num_thread": threads, "temperature": 0, "repeat_penalty": 1.15, "num_ctx": 4096, "num_predict": 400}})
    elapsed = time.perf_counter() - started
    raw = reply.get("response", "")
    parsed = parse_interpretation(raw, valid_ids)
    return {"goal": parsed.description or "", "status": parsed.status, "confidence": parsed.confidence,
            "evidence": list(parsed.evidence), "alternative": parsed.alternative, "unassigned": list(parsed.unassigned),
            "problems": list(parsed.problems), "keywords": [], "raw": raw, "seconds": round(elapsed, 2),
            "eval_count": reply.get("eval_count"), "prompt_eval_count": reply.get("prompt_eval_count")}


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
    parser.add_argument("--budget", type=int, default=1400)
    parser.add_argument("--sample", type=int, default=16)
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
        records, before, after = load_window(conn, m["members"])
        context = build_context(records, before, after, max_observations=args.sample)
        m["packet"] = serialize(context, args.budget)
        m["prompt"] = build_prompt(m["packet"])
        m["ids"] = context["ids"]
        m["packet_sha"] = hashlib.sha256(m["prompt"].encode()).hexdigest()[:16]
    result = {"data_dir": str(directory), "settings": {k: v for k, v in vars(args).items()}, "models": {}, "ps": []}
    result["settings"] = {k: str(v) if not isinstance(v, (int, float, str)) else v for k, v in vars(args).items()}
    packets_vec = embed(args.host, args.embed_model, [m["packet"] for m in items], args.threads) if items else np.zeros((0, 1))
    result["baseline_packet_groups"] = group(packets_vec, args.threshold) if items else []
    for model in args.models.split(","):
        goals, times = [], []
        for m in items:
            key = f"{model}|{m['id']}|{m['packet_sha']}|v3"
            if key not in cache:
                cache[key] = interpret(args.host, model, m["prompt"], m["ids"], args.threads)
                json.dump(cache, open(cache_path, "w"))
            goals.append(cache[key])
            times.append(cache[key]["seconds"])
        result["ps"].append({"after": model, "loaded": ps(args.host)})
        texts = [g["goal"] or "unknown" for g in goals]
        vectors = embed(args.host, args.embed_model, texts, args.threads) if items else np.zeros((0, 1))
        times.sort()
        result["models"][model] = {
            "goals": [{"moment_id": m["id"], **g} for m, g in zip(items, goals)],
            "groups": group(vectors, args.threshold) if items else [],
            "empty_goals": sum(1 for g in goals if not g["goal"]),
            "seconds_median": times[len(times) // 2] if times else None,
            "seconds_max": times[-1] if times else None,
        }
    result["moments"] = [{k: m[k] for k in ("id", "revision", "state", "start_ns", "end_ns", "members", "ids", "packet", "prompt")} for m in items]
    json.dump(result, open(os.path.join(args.out, "moment_goals.json"), "w"), indent=1)
    conn.close()
    store.close()
    stamp = lambda ns: time.strftime("%H:%M:%S", time.localtime(ns / S))
    for model, data in result["models"].items():
        print(f"\n== {model}  median {data['seconds_median']} s  max {data['seconds_max']} s  empty {data['empty_goals']}")
        for i, g in enumerate(data["goals"]):
            m = items[i]
            print(f"  M{i:<3} {stamp(m['start_ns'])}-{stamp(m['end_ns'])}  [{g['status']} {g['confidence']:.2f}] {g['goal'][:100]}")
        print("  groups by goal meaning:", [c for c in data["groups"] if len(c) > 1])
    print("\nbaseline groups by raw screen text:", [c for c in result["baseline_packet_groups"] if len(c) > 1])
    print("loaded models:", json.dumps(result["ps"]))


if __name__ == "__main__":
    main()

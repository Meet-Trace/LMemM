import json
import urllib.request

import numpy as np


def embed(host, model, texts, threads=0, timeout=300):
    options = {"num_gpu": 0}
    if threads:
        options["num_thread"] = threads
    body = json.dumps({"model": model, "input": texts, "keep_alive": "5m", "options": options}).encode()
    request = urllib.request.Request(host + "/api/embed", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        vectors = np.asarray(json.loads(response.read())["embeddings"], dtype=np.float32)
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
    return sorted((sorted(int(i) for i in v) for v in clusters.values()), key=lambda c: c[0])

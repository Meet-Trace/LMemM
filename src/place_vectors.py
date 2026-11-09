"""LMemM - one meaning vector per place, from a tiny embedding model on the Mac. Prototype.

all-minilm (about 22M parameters, already in the app's own Ollama) reads each place's card once and
returns a vector; project_reasoner_assign.run(vectors=...) compares them. On the two labelled days the
plain card ranked a related pair above an unrelated one 75-80% of the time, better than notes written
by gemma3:1b or qwen3.5:0.8b (scripts/note_pairs.py), so no generative model is used here.

Vectors are cached by the text's hash, so a place is embedded again only when its card changes.
WordHashEmbedder is a stand-in with no model, for tests and machines without Ollama.
"""

import hashlib
import json
import math
from pathlib import Path

from card_notes import OllamaEmbedder, card_text, unit
from project_reasoner_graph import tokens

BATCH = 64


class WordHashEmbedder:
    """Words hashed into `dim` buckets: similar only when the cards share words. No model."""

    name = "word-hash"

    def __init__(self, dim=256):
        self.dim = dim

    def __call__(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in tokens(t):
                v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            out.append(unit(v) if any(v) else v)
        return out


def vectors(items, embed, cache=None, screen=False, ids=None):
    """{place: unit vector} for `ids` (default all items). `cache` is a JSON file path or None."""
    ids = list(items if ids is None else ids)
    tag = getattr(embed, "model", None) or getattr(embed, "name", "embed")
    store = {}
    if cache and Path(cache).exists():
        store = json.loads(Path(cache).read_text())
    texts = {i: card_text(items[i], screen=screen) for i in ids}
    key = {i: hashlib.sha1(f"{tag}\n{texts[i]}".encode()).hexdigest() for i in ids}
    todo = sorted({key[i]: i for i in ids if key[i] not in store}.items())
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        for (k, _), v in zip(chunk, embed([texts[i] for _, i in chunk])):
            store[k] = [round(x, 5) for x in v]
    if cache and todo:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps(store))
    return {i: store[key[i]] for i in ids}


def ollama(model="all-minilm:22m", url="http://127.0.0.1:11455"):
    return OllamaEmbedder(model, url)


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1)

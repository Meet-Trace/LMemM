import math
from collections import Counter
from dataclasses import dataclass, field

PARAMS = {
    "weights": {"text": 0.45, "title": 0.2, "layout": 0.2, "app": 0.1, "host": 0.05},
    "join": 0.3,
    "secondary": 0.22,
    "merge": 0.55,
    "split": 0.2,
    "tau_s": 120.0,
    "recent": 3,
    "centroid_share": 0.5,
    "min_members": 3,
    "stabilise_s": 900.0,
    "candidate_horizon_s": 1800.0,
    "ready_wait_s": 45.0,
    "split_min_side": 3,
    "split_candidates": 4,
    "max_split_members": 200,
    "cell_presence": 0.25,
    "text_gate": 0.3,
    "gate_floor": 0.2,
    "revision_every_s": 600.0,
}
S = 1_000_000_000


@dataclass
class Profile:
    tok: Counter = field(default_factory=Counter)
    title: frozenset = frozenset()
    apps: dict = field(default_factory=dict)
    hosts: dict = field(default_factory=dict)
    cells: frozenset = frozenset()


def profile_of(f):
    cells = frozenset(f["text_cells"]) | frozenset(c + 1000 for c in f["elem_cells"])
    return Profile(Counter(f["tok"]), frozenset(f["title"]), {f["app"]: 1.0} if f["app"] else {},
                   {f["host"]: 1.0} if f["host"] else {}, cells)


def centroid(features, presence):
    n = len(features)
    if n == 0:
        return Profile()
    tok, apps, hosts, title, cells = Counter(), Counter(), Counter(), Counter(), Counter()
    for f in features:
        tok.update(f["tok"])
        title.update(f["title"])
        if f["app"]:
            apps[f["app"]] += 1
        if f["host"]:
            hosts[f["host"]] += 1
        cells.update(frozenset(f["text_cells"]) | frozenset(c + 1000 for c in f["elem_cells"]))
    return Profile(tok, frozenset(k for k, v in title.items() if v / n >= presence),
                   {k: v / n for k, v in apps.items()}, {k: v / n for k, v in hosts.items()},
                   frozenset(k for k, v in cells.items() if v / n >= presence))


def _cosine(a, b, idf):
    if not a or not b:
        return None
    dot = na = nb = 0.0
    for k, v in a.items():
        wa = math.sqrt(v) * idf(k)
        na += wa * wa
        wb = b.get(k)
        if wb:
            dot += wa * math.sqrt(wb) * idf(k)
    for kb, v in b.items():
        wb = math.sqrt(v) * idf(kb)
        nb += wb * wb
    if na == 0 or nb == 0:
        return None
    return dot / math.sqrt(na * nb)


def _jaccard(a, b):
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def _overlap(a, b):
    if not a or not b:
        return None
    return sum(min(v, b.get(k, 0.0)) for k, v in a.items())


def components(a, b, idf):
    return {"text": _cosine(a.tok, b.tok, idf), "title": _jaccard(a.title, b.title), "layout": _jaccard(a.cells, b.cells),
            "app": _overlap(a.apps, b.apps), "host": _overlap(a.hosts, b.hosts)}


def combine(parts, weights):
    num = den = 0.0
    for k, v in parts.items():
        if v is not None:
            num += weights[k] * v
            den += weights[k]
    return num / den if den else 0.0


def similarity(a, b, idf, params):
    parts = components(a, b, idf)
    score = combine(parts, params["weights"])
    text = parts["text"]
    if text is not None:
        floor = params["gate_floor"]
        score *= floor + (1.0 - floor) * min(1.0, text / params["text_gate"])
    return score


def time_factor(gap_s, tau):
    gap = max(0.0, gap_s)
    return 1.0 / (1.0 + (gap / tau) ** 2)


class Rep:
    """A Moment being built. The centroid of its core members is kept as running sums, so adding an observation costs
    the size of that observation and building the centroid costs the size of the vocabulary, never the member count."""

    def __init__(self, moment_id, params):
        self.moment_id, self.params = moment_id, params
        self.members = []
        self.features = {}
        self.start = self.end = None
        self._recent = []
        self.written_ns = None           # when the latest revision was written by this process (None: never)
        self.structural = False          # a merge or split changed it: the next revision is written at once
        self._n_core = 0
        self._reset_sums()

    def _reset_sums(self):
        self._tok, self._title, self._apps, self._hosts, self._cells = Counter(), Counter(), Counter(), Counter(), Counter()
        self._n = 0
        self._centroid = None
        self._stale = False

    def clear(self):
        self.members, self.features, self.start, self.end, self._recent = [], {}, None, None, []
        self._reset_sums()

    def has_core(self):
        return self._n_core > 0

    def _note_core(self, oid, f):
        self._n_core += 1
        self._recent = sorted(self._recent + [(f["t"], oid)])[-self.params["recent"]:]

    def _add_sums(self, f):
        self._tok.update(f["tok"])
        self._title.update(f["title"])
        if f["app"]:
            self._apps[f["app"]] += 1
        if f["host"]:
            self._hosts[f["host"]] += 1
        self._cells.update(frozenset(f["text_cells"]) | frozenset(c + 1000 for c in f["elem_cells"]))
        self._n += 1

    def add(self, oid, f, role, score):
        """Core members move the Moment's start and end and the centroid; secondary members (overlap) do neither."""
        self.members.append({"o": oid, "t": f["t"], "role": role, "s": round(score, 4)})
        self.features[oid] = f
        if role == "core":
            self.start = f["t"] if self.start is None else min(self.start, f["t"])
            self.end = f["t"] if self.end is None else max(self.end, f["t"])
            self._note_core(oid, f)
            if not self._stale:
                self._add_sums(f)
            self._centroid = None

    def promote(self, oid, score):
        """A secondary member became core (after a merge): the sums are rebuilt on the next use."""
        for m in self.members:
            if m["o"] == oid and m["role"] != "core":
                m["role"], m["s"] = "core", score
                f = self.features[oid]
                self.start = f["t"] if self.start is None else min(self.start, f["t"])
                self.end = f["t"] if self.end is None else max(self.end, f["t"])
                self._note_core(oid, f)
                self._stale = True
                self._centroid = None

    def core(self):
        return [m for m in self.members if m["role"] == "core"]

    def centroid(self):
        if self._stale:
            self._reset_sums()
            for m in self.core():
                self._add_sums(self.features[m["o"]])
        if self._centroid is None:
            n, presence = self._n, self.params["cell_presence"]
            if n == 0:
                self._centroid = Profile()
            else:
                self._centroid = Profile(
                    Counter(self._tok), frozenset(k for k, v in self._title.items() if v / n >= presence),
                    {k: v / n for k, v in self._apps.items()}, {k: v / n for k, v in self._hosts.items()},
                    frozenset(k for k, v in self._cells.items() if v / n >= presence))
        return self._centroid

    def score(self, f, idf):
        p = self.params
        prof = profile_of(f)
        c = similarity(prof, self.centroid(), idf, p)
        recent = [self.features[o] for _, o in self._recent]
        r = max((similarity(prof, profile_of(x), idf, p) for x in recent), default=0.0)
        ctx = p["centroid_share"] * c + (1 - p["centroid_share"]) * r
        return ctx * time_factor((f["t"] - self.end) / S, p["tau_s"]), ctx


def rep_similarity(a, b, idf, params):
    return similarity(a.centroid(), b.centroid(), idf, params)

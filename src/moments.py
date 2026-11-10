"""LMemM - moments: the small, time-ordered digest of what was on screen, one record per stretch of a visit.

Step 2, PR 1 (docs/adr/0002-moments-and-evaluation.md). Pure Python, no macOS imports, no model calls.
A Moment says WHERE you were, WHEN, HOW you interacted and a DIGEST of what was there. It says nothing about
which activity or project it belongs to: that is for later PRs, and adjacency is never membership.

    visit    one continuous presence on one thing (item). A new visit starts when the item changes, or when
             more than `visit_gap` seconds passed with no frame (pause, idle, lock, sleep).
    moment   a stretch of one visit where nothing material changed. A visit holds one or more moments.

Rules (each has a test):
    M1  Separate visits are never merged, however short and even if the content is identical. A, a 15 s look
        at B, then A again is three visits. Coming back is a new visit that points at the previous one.
    M2  Inside a visit, a frame extends the open moment. A new moment starts only when the interaction
        category changed (after `min_category_s`), when the text changed materially (>= `new_terms` new
        terms or >= 1 new identifier; a repaint or OCR flicker is not material), or after `max_moment_s`.
    M3  The digest is small: terms, identifiers, a few short quoted lines. Lines that look like secrets are
        dropped, URLs lose their query and fragment, and nothing the tracker skipped ever gets here.
    M4  Nothing here reads a key: interaction is a category plus counts and ages.
"""

import hashlib
import re
import threading
from collections import deque
from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit

import trail_engine

SCHEMA = 1
TERM_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{3,19}(?![A-Za-z0-9'’-])")   # words only: no digits inside, at most 20 letters
LONG_RUN = re.compile(r"[\w-]{20,}")
KEEP_OVERLAP = 0.6          # a text change only counts when this share of the moment's known words is still there
IDENT_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+\.\w{1,6}\b"                 # a path with a file name
    r"|\b[\w-]+\.(?:py|js|ts|tsx|swift|md|txt|csv|xlsx?|docx?|pdf|json|ya?ml|html|css|sql|sh|key|pptx?)\b"
    r"|\b[a-z]+(?:_[a-z0-9]+)+\b"                       # snake_case
    r"|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"           # CamelCase
    r"|#\d{1,6}\b")                                     # issue / PR numbers
STOP = frozenset("""this that with from have been were will would there their what when where which while about after
before because could should into over than then them they your more most other some such only also just like very
much many each both these those here still every again being does done make made take took will shall""".split())


def clean_url(url):
    """Domain and path only: the query and fragment can carry tokens and names. -> (url, domain)"""
    if not url:
        return None, None
    try:
        p = urlsplit(url if "//" in url else "//" + url)
    except ValueError:
        return None, None
    domain = (p.hostname or "").lower() or None
    path = p.path.rstrip("/") if p.path else ""
    return ((domain or "") + path) or None, domain


def scrub(context, gone_ids, gone_visits):
    """Remove from one moment's context every pointer to a forgotten moment or visit. Works on stored records too."""
    if context.get("prev_moment_id") in gone_ids:
        context["prev_moment_id"] = None
        context["gap_s"] = None
    sib = context.get("same_item_before")
    if sib and sib.get("visit_id") in gone_visits:
        context["same_item_before"] = None


def keylike(line):
    """A long unbroken run that mixes letters and digits (or flips case again and again) looks like a key or
    token, however OCR garbled it, so the whole line is dropped. trail_engine.SECRET only catches exact shapes."""
    for run in LONG_RUN.findall(line or ""):
        if "_" in run:
            continue
        flips = sum(1 for a, b in zip(run, run[1:]) if a.isalpha() and b.isalpha() and a.islower() != b.islower())
        if any(c.isdigit() for c in run) or flips >= 4:
            return True
    return False


def _norm(line):
    return re.sub(r"\s+", " ", line or "").strip()


def digest_of(text, keep_lines=True, max_terms=12, max_idents=8, max_lines=3, line_chars=80):
    """A small description of a screen's text. `text`: newline-joined lines or a list of lines."""
    raw = text.split("\n") if isinstance(text, str) else list(text or [])
    lines = [l for l in trail_engine.redact([_norm(l) for l in raw]) if not keylike(l)]
    joined = "\n".join(lines)
    idents = []
    for m in IDENT_RE.finditer(joined):
        s = m.group(0)
        if s not in idents:
            idents.append(s)
    freq, first = {}, {}
    for i, m in enumerate(TERM_RE.finditer(joined)):
        w = m.group(0).lower().strip("-'’")
        if len(w) < 4 or w in STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
        first.setdefault(w, i)
    # distinctive: longer and repeated words first, ties by first appearance (deterministic)
    terms = sorted(freq, key=lambda w: (-(len(w) + 2 * min(freq[w], 3)), first[w]))
    quoted = []
    if keep_lines:
        for l in sorted((l for l in lines if len(l) >= 12 and len(re.findall(r"\w+", l)) >= 3),
                        key=lambda l: -len(set(TERM_RE.findall(l)))):
            quoted.append(l[:line_chars])
            if len(quoted) == max_lines:
                break
    return {"terms": terms[:max_terms * 3], "identifiers": idents[:max_idents * 3],
            "lines": quoted,
            "text_hash": hashlib.sha1(("\n".join(sorted(set(lines)))).encode()).hexdigest()[:12] if lines else None}


@dataclass
class Moment:
    id: str
    visit_id: str
    seq: int
    t_start: float
    t_end: float
    item_id: str
    app: str = None
    bundle_id: str = None
    window: str = None
    url: str = None
    domain: str = None
    kind: str = None
    target: str = None
    ref: str = None
    n_frames: int = 1
    obs_id: str = None
    interaction: dict = field(default_factory=dict)     # {"category": ..., "seconds": {cat: s}, "ages": {...}}
    digest: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)         # prev_moment_id, gap_s, same_item_before, trigger, pinned
    continued: bool = False
    schema: int = SCHEMA

    @property
    def dwell_s(self):
        return max(0.0, self.t_end - self.t_start)

    def to_dict(self):
        d = asdict(self)
        d["dwell_s"] = round(self.dwell_s, 1)
        return d

    @classmethod
    def from_dict(cls, d):
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in names})


class MomentBuffer:
    """Turns frames into moments. `observe` is O(1) and does no I/O except through `sink`
    (called with each CLOSED moment). The open moment is closed on a visit change, `close()`, or `idle_flush`."""

    def __init__(self, sink=None, visit_gap=90.0, min_category_s=10.0, max_moment_s=300.0,
                 new_terms=3, window_s=1800.0, max_moments=400, keep_lines=True, clock_id=None):
        self.sink = sink
        self.visit_gap, self.min_category_s, self.max_moment_s = visit_gap, min_category_s, max_moment_s
        self.new_terms, self.window_s = new_terms, window_s
        self.keep_lines = keep_lines
        self.recent = deque(maxlen=max_moments)         # closed moments, oldest first
        self.lock = threading.RLock()
        self.open = None                                 # the moment being extended
        self._seen_terms = set()
        self._seen_idents = set()
        self._seen_hash = None
        self._pending = (set(), set())
        self._last_t = None
        self._visit_n = 0
        self._visit_start = None
        self._last_visit = {}                            # item_id -> {"visit_id", "ended_at", "visits"}
        self._prev_id = None
        self._prev_end = None
        self._clock_id = clock_id or "v"

    # ------------------------------------------------------------ input

    def observe(self, t, item_id, app=None, bundle_id=None, window=None, url=None, kind=None, target=None,
                ref=None, category=None, ages=None, text="", obs_id=None, provenance=None, trigger=None,
                pinned=False, dt=0.0):
        """One resolved frame at wall-clock `t`. Returns the moments this frame closed (usually none)."""
        closed = []
        with self.lock:
            dg = digest_of(text, self.keep_lines)
            terms, idents = set(dg["terms"]), set(dg["identifiers"])
            o = self.open
            new_visit = (o is None or o.item_id != item_id
                         or (self._last_t is not None and t - self._last_t > self.visit_gap))
            if new_visit:
                if o is not None:
                    if self._last_t is not None and t - self._last_t <= self.visit_gap:
                        o.t_end = t              # leaving it: the time up to the switch was spent there (as the timeline does)
                    closed.append(self._close(o))
                self._start(t, item_id, app, bundle_id, window, url, kind, target, ref, category, ages, dg,
                            obs_id, provenance, trigger, pinned, new_visit_after=self._last_t)
            else:
                cat_changed = category and category != o.interaction.get("category") \
                    and t - o.t_start >= self.min_category_s
                # A text change is material only when the new words PERSIST into a second frame: OCR noise
                # (a fast pass misreading the same screen) does not repeat, a real change does.
                new_t, new_i = terms - self._seen_terms, idents - self._seen_idents
                differs = bool(dg["text_hash"]) and dg["text_hash"] != self._seen_hash
                kept_t, kept_i = new_t & self._pending[0], new_i & self._pending[1]
                # additions are real only when what was already there is still there: re-reading a static page
                # (a different OCR pass garbles the same pixels the same way every frame) loses old words too
                known = self._seen_terms
                stable = not known or len(terms & known) / len(known) >= KEEP_OVERLAP
                changed = differs and stable and (len(kept_t) >= self.new_terms or bool(kept_i))
                self._pending = (new_t - kept_t, new_i - kept_i) if differs and not changed else (set(), set())
                too_long = t - o.t_start >= self.max_moment_s
                if cat_changed or changed or too_long:
                    o.t_end = t                  # the time up to this frame belongs to the moment it was spent in
                    closed.append(self._close(o))
                    # only a real text change brings this frame's digest; a flip of category or the length cap
                    # keeps the digest already established (a single fast-OCR frame must not replace it)
                    self._next(o, t, category, ages, dg if changed else None, obs_id, provenance, trigger, pinned,
                               too_long and not (cat_changed or changed))
                else:
                    self._extend(o, t, category, dt, dg, kept_t, kept_i, settled=not self._pending[0] and not self._pending[1])
            self._last_t = t
            self._trim(t)
        return closed

    def close(self):
        """Close the open moment (shutdown, a long idle). Returns it as a list."""
        with self.lock:
            if self.open is None:
                return []
            m = self._close(self.open)
            self.open = None
            return [m]

    def forget(self, since=None, app=None):
        """Drop what matches from memory, the OPEN moment included, WITHOUT writing it: a forgotten open moment
        must not come back on the next frame or at shutdown. `since`: moments that ended at or after it (None =
        all); `app`: name or bundle id (None = any). Nothing left may point at what was forgotten: links to a
        forgotten moment or visit are scrubbed from the survivors and from the visit memory. The next frame starts
        a new visit. Returns how many were dropped."""
        def hit(m):
            return (since is None or m.t_end >= since) and (app is None or app in (m.app, m.bundle_id))
        with self.lock:
            gone = [m for m in self.recent if hit(m)]
            if self.open is not None and hit(self.open):
                gone.append(self.open)
                self.open = None
                self._seen_terms, self._seen_idents, self._seen_hash = set(), set(), None
                self._pending = (set(), set())
            gone_ids = {m.id for m in gone}
            keep = [m for m in self.recent if m.id not in gone_ids]
            self.recent.clear()
            self.recent.extend(keep)
            alive = {m.visit_id for m in keep} | ({self.open.visit_id} if self.open else set())
            gone_visits = {m.visit_id for m in gone} - alive
            for m in self.recent:
                scrub(m.context, gone_ids, gone_visits)
            if self.open:
                scrub(self.open.context, gone_ids, gone_visits)
            for k in [k for k, v in self._last_visit.items() if v["visit_id"] in gone_visits]:
                del self._last_visit[k]
            if self._prev_id in gone_ids:
                last = self.recent[-1] if self.recent else None
                self._prev_id, self._prev_end = (last.id, last.t_end) if last else (None, None)
            return len(gone)

    def interrupt(self):
        """The tracker stopped looking (pause, idle, lock, sleep): close the open moment at its last frame, with no
        time credited for the stop, and make the next frame start a new visit. Returns the closed moments."""
        with self.lock:
            out = self.close()
            self._last_t = None
            self._seen_terms, self._seen_idents, self._seen_hash = set(), set(), None
            self._pending = (set(), set())
            return out

    def idle_flush(self, now):
        """No frame for longer than `visit_gap`: the visit is over; close its moment."""
        with self.lock:
            if self.open is not None and self._last_t is not None and now - self._last_t > self.visit_gap:
                return self.close()
        return []

    # ------------------------------------------------------------ views

    def visits(self):
        """Closed moments grouped by visit, oldest first: [[Moment, ...], ...] (plus the open one)."""
        with self.lock:
            ms = list(self.recent) + ([self.open] if self.open else [])
        out, cur = [], None
        for m in ms:
            if cur is None or cur[0].visit_id != m.visit_id:
                cur = []
                out.append(cur)
            cur.append(m)
        return out

    # ------------------------------------------------------------ internals

    def _start(self, t, item_id, app, bundle_id, window, url, kind, target, ref, category, ages, dg,
               obs_id, provenance, trigger, pinned, new_visit_after):
        self._visit_n += 1
        visit_id = f"{self._clock_id}{int(t)}-{self._visit_n}"
        before = self._last_visit.get(item_id)
        same = None
        if before:
            same = {"visit_id": before["visit_id"], "ended_at": before["ended_at"],
                    "gap_s": round(t - before["ended_at"], 1), "visits_before": before["visits"]}
        u, domain = clean_url(url)
        m = Moment(id=f"{visit_id}.0", visit_id=visit_id, seq=0, t_start=t, t_end=t, item_id=item_id, app=app,
                   bundle_id=bundle_id, window=window, url=u, domain=domain, kind=kind, target=target, ref=ref,
                   obs_id=obs_id, provenance=dict(provenance or {}),
                   interaction=self._interaction(category, ages), digest=self._digest(dg))
        m.context = {"prev_moment_id": self._prev_id,
                     "gap_s": round(t - self._prev_end, 1) if self._prev_end is not None else None,
                     "same_item_before": same, "trigger": trigger, "pinned": bool(pinned)}
        self._visit_start = t
        self._begin(m, dg)
        self._last_visit[item_id] = {"visit_id": visit_id, "ended_at": t, "app": app, "bundle_id": bundle_id,
                                     "visits": (before["visits"] + 1) if before else 1}

    def _next(self, o, t, category, ages, dg, obs_id, provenance, trigger, pinned, continued):
        seq = o.seq + 1
        m = Moment(id=f"{o.visit_id}.{seq}", visit_id=o.visit_id, seq=seq, t_start=t, t_end=t,
                   item_id=o.item_id, app=o.app, bundle_id=o.bundle_id, window=o.window, url=o.url,
                   domain=o.domain, kind=o.kind, target=o.target, ref=o.ref, obs_id=obs_id,
                   provenance=dict(provenance or {}) if dg is not None else dict(o.provenance), continued=continued,
                   interaction=self._interaction(category, ages),
                   digest=self._digest(dg) if dg is not None else dict(o.digest, lines=list(o.digest.get("lines") or [])))
        m.context = {"prev_moment_id": o.id, "gap_s": 0.0, "same_item_before": o.context.get("same_item_before"),
                     "trigger": trigger, "pinned": bool(pinned)}
        if dg is not None:
            self._begin(m, dg)
        else:
            self.open = m                       # same text as before: the seen sets stay as they are
        self._touch(o, t)

    def _begin(self, m, dg):
        self.open = m
        self._pending = (set(), set())
        self._seen_terms = set(dg["terms"])
        self._seen_idents = set(dg["identifiers"])
        self._seen_hash = dg["text_hash"]

    def _extend(self, o, t, category, dt, dg, kept_t, kept_i, settled):
        """Another frame of the same moment. Words are added to the digest only once they persisted into a second
        frame (`kept_*`), so a one-frame OCR misread never enters it."""
        o.t_end = t
        o.n_frames += 1
        secs = o.interaction.setdefault("seconds", {})
        cat = category or o.interaction.get("category")
        if cat and dt:
            secs[cat] = round(secs.get(cat, 0) + dt, 1)
        d = o.digest
        d["terms"] = (d["terms"] + sorted(kept_t - set(d["terms"])))[:12]
        d["identifiers"] = (d["identifiers"] + sorted(kept_i - set(d["identifiers"])))[:8]
        if settled or kept_t or kept_i:
            self._seen_terms |= kept_t
            self._seen_idents |= kept_i
        if settled:
            self._seen_hash = dg["text_hash"] or self._seen_hash
        self._touch(o, t)

    def _touch(self, o, t):
        v = self._last_visit.setdefault(o.item_id, {"visit_id": o.visit_id, "app": o.app, "bundle_id": o.bundle_id, "visits": 1})
        v["ended_at"] = t

    def _close(self, m):
        self._prev_id, self._prev_end = m.id, m.t_end
        self.recent.append(m)
        if self.open is m:
            self.open = None
        if self.sink:
            try:
                self.sink(m)
            except Exception:           # a full disk must never stop the tracker
                pass
        return m

    def _trim(self, now):
        while self.recent and now - self.recent[0].t_end > self.window_s:
            self.recent.popleft()

    def _digest(self, dg):
        return {"terms": dg["terms"][:12], "identifiers": dg["identifiers"][:8], "lines": list(dg["lines"]),
                "text_hash": dg["text_hash"]}

    @staticmethod
    def _interaction(category, ages):
        out = {"category": category, "seconds": {}}
        if ages:
            out["ages"] = {k: (round(v, 1) if v < 1e8 else None) for k, v in ages.items()
                           if k in ("key", "scroll", "click") and v is not None}
        return out

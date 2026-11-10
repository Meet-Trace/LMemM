#!/usr/bin/env python3
"""
LMemM - one record of what was seen, for both readers.

The screenshot tracker (OCR) and the accessibility trail (the app's own text) each describe the same
screen. Both write an `Observation`; `merge()` turns observations of one place into one list of lines that
says which readers saw each line and whether they agreed. Pure Python, no macOS imports.

    Line         text, source ("ax" the app's own text | "ocr" read from pixels), conf, kind, box
    Observation  one reader's look at one place: app, pid, window, url, lines, input signals, screenshot ref
    merge(obs)   -> [MergedLine]: where both readers saw a line, the app's text is the line and the OCR
                 original stays as a variant; everything else is kept as read, by one source only
    ObservationLog  the few most recent observations, so the tracker can find the trail's read of its screen

Provenance and confidence are kept apart. `source` says who read a line. `conf` is the reader's own
extraction confidence (OCR reports one; the accessibility tree does not, so it is None, never "1.0").
`agreement` says whether a second reader saw the same line. None of these says the text is correct or relevant.

When the OCR has no substantial line to check against (a canvas or Electron view it cannot read), the app's text is kept
as it is: single-source (`agreement == "single"`, `sources == ["ax"]`), never corroborated. When both readers have
substantial lines, the agreement gate below applies. An app read that cannot name its app and page is never used.

Matching is conservative: a line is only replaced when exactly one counterpart fits, each app line is claimed by
at most one fuzzy match, and a whole observation is ignored unless enough of its lines agree with the OCR
(so a different window that happens to share a title is not merged). Originals are never altered; matching
compares a normalised copy.

ObservationLog lifecycle: in memory only, nothing is written to disk. It keeps at most OBS_LOG_SIZE entries, drops
entries older than OBS_RETENTION seconds (measured from the newest observation), accepts observations in any
order, ignores an exact repeat of one it already holds, and is guarded by one lock. It ends with the process.
Design and migration: docs/adr/0001-unified-observation.md
"""

import bisect
import hashlib
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import config

SOURCES = ("ax", "ocr")          # strongest first; native APIs and browser DOM would go before these
SINGLE, AGREED, DIFFERED = "single", "agreed", "differed"

_LOOKALIKE = str.maketrans({"$": "s", "0": "o", "1": "l", "5": "s"})
_NOT_WORD = re.compile(r"[\W_]+")
_URL_BASE = re.compile(r"^(?:https?://)?(?:www\.)?([^#?]*?)/?(?:[?][^#]*)?(#.*)?$", re.I)
MIN_CONTAIN = 8                  # characters: a shorter line is only matched whole, never as a piece of another


def fold(text):
    """A comparison copy of a line: lower case, a few OCR look-alike characters unified, punctuation and spaces gone.
    Only ever used to compare; the stored text is the original."""
    return _NOT_WORD.sub("", (text or "").casefold().translate(_LOOKALIKE))


@dataclass
class Line:
    text: str
    source: str = "ocr"
    conf: float = None           # the reader's extraction confidence; None = this source reports none (accessibility)
    kind: str = None             # layout kind from the OCR resolver (paragraph, sidebar_item, ...)
    box: list = None             # [x, y, w, h] pixels, OCR only
    in_window: bool = True       # OCR only: inside the foreground window


@dataclass
class Observation:
    t: float                     # wall-clock seconds
    reader: str                  # "tracker" | "trail"
    app: str = None
    bundle_id: str = None
    pid: int = None
    window_id: int = None        # the OS window number, when the reader knows it (the tracker does, the trail cannot)
    window: str = None
    url: str = None
    lines: list = field(default_factory=list)
    interaction: dict = field(default_factory=dict)   # seconds since last key/scroll/click, pointer; never characters
    evidence_ref: str = None     # the thumbnail's file name, if one is kept
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = hashlib.sha1(repr(self.signature()).encode()).hexdigest()[:12]

    def signature(self):
        """What makes two observations the same read, ignoring when they happened."""
        return (self.reader, self.bundle_id, self.pid, self.window_id, self.window, self.url,
                tuple((l.source, l.text) for l in self.lines))

    @property
    def source(self):
        return "ax" if any(l.source == "ax" for l in self.lines) else "ocr"

    @property
    def domain(self):
        m = re.match(r"^(?:\w+://)?([^/:?#]+)", self.url or "")
        return m.group(1).lower() if m else None


@dataclass
class MergedLine:
    text: str                    # the original text of the primary source (never normalised)
    source: str                  # the primary source: "ax" when the app supplied the line, else "ocr"
    conf: float                  # the primary source's extraction confidence (None for accessibility)
    sources: list                # every reader that saw the line, strongest first
    agreement: str               # single (one reader) | agreed (two readers, same line) | differed (two readers, texts differ)
    kind: str = None
    in_window: bool = True
    variants: list = field(default_factory=list)      # [{"text", "source", "conf"}]: other readings of the same line


# ---------------------------------------------------------------- building observations

def from_ocr(res, meta, now, evidence_ref=None):
    """The tracker's OCR result (resolver output) as an Observation. Every OCR line is kept; `in_window`
    says whether it sits inside the foreground window (needs meta["window_region"])."""
    region = meta.get("window_region")
    inside = lambda b: True
    if region:
        W, H = res["image_size"]["w"], res["image_size"]["h"]
        x, y, w, h = region[0] * W, region[1] * H, region[2] * W, region[3] * H
        inside = lambda b: not (b[0] < x or b[1] < y or b[0] + b[2] > x + w or b[1] + b[3] > y + h)
    lines = [Line(" ".join(o["text"].split()), "ocr", float(o.get("conf", 0) or 0), o.get("kind"), o.get("box"),
                  bool(region) and inside(o["box"]))
             for o in res["objects"] if o.get("text") and o["text"].strip()]
    return Observation(t=now, reader="tracker", app=meta.get("app"), bundle_id=meta.get("bundle_id"),
                       pid=meta.get("pid"), window_id=meta.get("win_id"),
                       window=meta.get("tab_title") or meta.get("window"), url=meta.get("url"), lines=lines,
                       interaction={"inputs": meta.get("inputs"), "pointer": meta.get("pointer")},
                       evidence_ref=evidence_ref)


def from_trail(front, snap, lines, source, now, confs=None):
    """The trail's read of a place. `lines`: its visible text; `source`: "ax", or "ocr" when it fell back to a screenshot."""
    conf = (lambda i: None) if source == "ax" else (lambda i: float(confs[i]) if confs and i < len(confs) else 0.0)
    return Observation(t=now, reader="trail", app=front.get("app"), bundle_id=front.get("bundle_id"),
                       pid=front.get("pid"), window=snap.title or None, url=snap.url or None,
                       lines=[Line(t, source, conf(i)) for i, t in enumerate(lines) if t and t.strip()])


# ---------------------------------------------------------------- same place?

def _url_parts(url):
    m = _URL_BASE.match((url or "").strip())
    return (m.group(1).lower(), m.group(2) or "") if m else ("", "")


def same_place(a, b):
    """True only when two observations describe the same app, process, page and window title. When unsure,
    False (an unmerged OCR line is a smaller mistake than a line from another page). Two windows of one app
    that share a title cannot be told apart here; merge() also demands that the lines themselves agree."""
    if a.bundle_id and b.bundle_id and a.bundle_id != b.bundle_id:
        return False
    if not (a.bundle_id and b.bundle_id) and (not a.app or a.app != b.app):
        return False
    if a.pid and b.pid and a.pid != b.pid:
        return False
    if a.window_id and b.window_id and a.window_id != b.window_id:
        return False
    (ua, fa), (ub, fb) = _url_parts(a.url), _url_parts(b.url)
    if ua and ub and (ua != ub or (fa and fb and fa != fb)):
        return False
    ta, tb = fold(a.window), fold(b.window)
    if ta and tb and ta != tb and not (min(len(ta), len(tb)) >= 4 and (ta in tb or tb in ta)):
        return False
    return bool((ua and ub) or (ta and tb))        # must share something concrete beyond the app


# ---------------------------------------------------------------- merging

def _contains(ocr, app):
    """An OCR line that is a wrapped piece of one app line."""
    return len(ocr) >= MIN_CONTAIN and ocr in app and len(ocr) >= 0.5 * len(app)


def _similar(ocr, app, threshold):
    f, g = sorted((ocr, app), key=len)
    if not f or len(f) < 0.7 * len(g):
        return False
    s = SequenceMatcher(None, f, g)
    return s.quick_ratio() >= threshold and s.ratio() >= threshold


def _variant(line):
    return {"text": line.text[:200], "source": line.source, "conf": None if line.conf is None else round(line.conf, 2)}


def _add_variant(merged, line):
    v = _variant(line)
    if line.text != merged.text and v not in merged.variants and len(merged.variants) < config.MAX_VARIANTS:
        merged.variants.append(v)


def merge_report(observations, threshold=None):
    """merge() plus how it went: (lines, {"matched": n, "ocr_lines": n, "gate": "merged"|"ax_only"|"disagrees"|"unreliable_context"}).
    The report is for the evaluation log; callers that only want the lines use merge()."""
    threshold = config.OBS_MATCH if threshold is None else threshold
    ax, ocr = [], []
    for o in observations:
        for l in o.lines:
            (ax if l.source == "ax" else ocr).append(l)

    # distinct app lines, in reading order (an app line repeated exactly is one line)
    app, app_fold = [], {}
    for l in ax:
        f = fold(l.text)
        if not f:
            continue
        if f in app_fold:
            _add_variant(app[app_fold[f]], l)
            continue
        app_fold[f] = len(app)
        app.append(MergedLine(l.text, "ax", l.conf, ["ax"], SINGLE, l.kind, True))
    ofold = [fold(l.text) for l in ocr]

    # for each OCR line: how it could match an app line
    exact, piece, similar, span = {}, {}, {}, {}
    app_keys = list(app_fold)
    for i, f in enumerate(ofold):
        if not f:
            continue
        if f in app_fold:
            exact[i] = app_fold[f]
            continue
        pieces = [j for j, g in enumerate(app_keys) if _contains(f, g)]
        if pieces:
            if len(pieces) == 1:
                piece[i] = [pieces[0]]          # several candidates = ambiguous: left unmatched
            continue
        near = [j for j, g in enumerate(app_keys) if _similar(f, g, threshold)]
        if len(near) == 1:
            similar[i] = near[0]
            continue
        if len(near) > 1:
            continue                            # ambiguous
        parts = [j for j, g in enumerate(app_keys) if len(g) >= MIN_CONTAIN and g in f]
        if len(parts) >= 2 and sum(len(app_keys[j]) for j in parts) >= 0.8 * len(f):
            span[i] = parts
    claims = {}
    for i, j in similar.items():
        claims.setdefault(j, []).append(i)
    taken = set(exact.values()) | {j for js in piece.values() for j in js}
    similar = {i: j for i, j in similar.items() if len(claims[j]) == 1 and j not in taken}   # one OCR line per fuzzy app line,
                                                                                         # and not one already read exactly

    matched = {**{i: ([j], AGREED) for i, j in exact.items()}, **{i: (js, AGREED) for i, js in piece.items()},
               **{i: ([j], DIFFERED) for i, j in similar.items()}, **{i: (js, AGREED) for i, js in span.items()}}

    # does this app read describe the same screen as the OCR? Otherwise it is another window, or stale
    substantial = [i for i, f in enumerate(ofold) if len(f) >= MIN_CONTAIN and ocr[i].in_window]
    agree = sum(1 for i in substantial if i in matched)
    report = {"matched": 0, "ocr_lines": len(ocr), "gate": "merged"}
    if app:
        reliable = all(o.bundle_id and (o.url or o.window) for o in observations if any(l.source == "ax" for l in o.lines))
        if not reliable:
            report["gate"] = "unreliable_context"      # the app read cannot say which app and page it came from
        elif not substantial:
            report["gate"] = "ax_only"                 # the OCR has nothing to check against: keep the app text, single-source
        elif agree < min(config.OBS_MIN_AGREE, len(substantial)) or agree < config.OBS_MIN_SHARE * len(substantial):
            report["gate"] = "disagrees"
    if report["gate"] in ("unreliable_context", "disagrees"):
        app, matched = [], {}                     # use the OCR alone, as if the app had said nothing

    out = list(app)
    for i, l in enumerate(ocr):
        if i in matched:
            for j in matched[i][0]:
                app[j].sources = ["ax", "ocr"]
                app[j].agreement = matched[i][1] if app[j].agreement != DIFFERED else DIFFERED
                _add_variant(app[j], l)
            report["matched"] += 1
        elif ofold[i]:
            out.append(MergedLine(l.text, "ocr", l.conf, ["ocr"], SINGLE, l.kind, l.in_window))
    return out, report


def merge(observations, threshold=None):
    return merge_report(observations, threshold)[0]


def counts(merged_lines):
    """Lines by provenance: {"ax_only", "ocr_only", "agreed", "differed"}."""
    out = {"ax_only": 0, "ocr_only": 0, "agreed": 0, "differed": 0}
    for m in merged_lines:
        out[m.source + "_only" if m.agreement == SINGLE else m.agreement] += 1
    return out


# ---------------------------------------------------------------- the meeting point

class ObservationLog:
    """The most recent observations, shared by the tracker thread and the trail thread. In memory only."""

    def __init__(self, size=None, retention=None):
        self.size = size or config.OBS_LOG_SIZE
        self.retention = config.OBS_RETENTION if retention is None else retention
        self._items = []                 # kept sorted by t, oldest first
        self._lock = threading.Lock()

    def add(self, obs):
        """Keep `obs`. An exact repeat of a read already held only refreshes its time; returns the kept observation.
        Observations may arrive out of order: they are slotted in by time. Anything older than `retention`
        seconds before the newest observation is dropped, and so is the oldest beyond the size limit."""
        with self._lock:
            sig = obs.signature()
            for held in self._items:
                if held.signature() == sig:
                    if obs.t > held.t:
                        self._items.remove(held)
                        held.t = obs.t
                        bisect.insort(self._items, held, key=lambda o: o.t)
                    return held
            bisect.insort(self._items, obs, key=lambda o: o.t)
            cutoff = self._items[-1].t - self.retention
            self._items = [o for o in self._items if o.t >= cutoff][-self.size:]
            return obs

    def near(self, probe, source="ax", max_age=None):
        """Other readers' observations with `source` lines read within `max_age` seconds of `probe`, newest first."""
        max_age = config.OBS_MAX_AGE if max_age is None else max_age
        with self._lock:
            items = list(self._items)
        return [o for o in reversed(items)
                if o is not probe and o.reader != probe.reader and o.source == source and abs(o.t - probe.t) <= max_age]

    def match(self, probe, source="ax", max_age=None):
        """The newest observation with `source` lines, of the same place as `probe`, read within `max_age`
        seconds of it; None if there is none. (merge() still checks that its lines agree with the probe's.)"""
        return next((o for o in self.near(probe, source, max_age) if same_place(o, probe)), None)

    def __len__(self):
        with self._lock:
            return len(self._items)

    def clear(self):
        with self._lock:
            self._items = []


LOG = ObservationLog()


# ---------------------------------------------------------------- opt-in evaluation log

def append_eval(path, record, max_bytes=20_000_000):
    """One JSON line per frame for scripts/eval_evidence.py. Local, owner-only, size-capped; off unless
    config.EVIDENCE_LOG. Holds the same text the stored excerpts hold (before = OCR alone, after = merged)."""
    import json
    import os
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            return False
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False

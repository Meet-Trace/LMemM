"""LMemM - visit features for activity inference: what each visit contains, derived only from persisted Moments.

Step 2, PR 2B (docs/adr/0003-activity-inference-design.md). Pure, deterministic Python: no scoring, no assignment, no
clustering, no model, no I/O, no clock, no write to the data directory. Input is `activity_input.Prepared` (validated,
de-duplicated, canonically ordered); output is a `FeatureSet` that PR 2C consumes without touching a raw Moment again.

    Prepared visits  ->  extract_features(prepared, params, fparams)  ->  FeatureSet   (this module)
                         build_evidence(feature_set, params, fparams)  ->  EvidenceSet  (activity_evidence.py)

Only fields already in the Moment schema are read: item_id, ref, app, bundle_id, kind, target, window, url, domain,
digest (terms, identifiers, lines, text_hash), provenance, n_frames and timestamps. Nothing typed, pasted or copied can
appear: the input check refuses unknown fields and this module reads no other.

What a VisitFeatures holds
    artifacts   observable identities with a class: H1 (PR / issue identity), I (identity only: the item, a URL, a file,
                a document id, a conversation, a chat, a search query), L (version lineage: a commit sha). Each has a
                fixed specificity prior by kind (a placeholder, never derived from assignments).
    namespaces  soft contexts, never evidence of the same work alone: domain, section, repo, project, branch. A branch is
                recorded only when unambiguous (see _branch_from_url / _split_project).
    tokens      screen-derived words and identifiers from the digest, with a source weight, and the words of the title,
                target and URL kept apart (`context_words`) so a later step can keep one piece of text from counting twice.
    quality     provenance factor x frame factor x content factor, each documented below; all dimensionless.
    glance / weak_evidence   explicit flags from declared parameters. A glance is a short, single-frame visit that is not named by
                what it is. Weak evidence is low quality, or no distinctive (non-generic) content word: a page of only
                generic words is weak however long it lasted. `has_substance` is neither.
    internal_shift   a descriptor of topic change inside the visit (a visit is never split).

Document frequency (FeatureSet.stats). `df` is the number of distinct eligible visits containing a token; `N` the number
of eligible visits (a visit with at least one content token). Classes follow the ADR and are mutually exclusive; the
order of the tests is the precedence:
    single  df == 1                               (a token seen once can never be shared)
    hub     df > hub_share * N                     (ADR: 0.2; hub takes precedence over rare)
    rare    df <= max(rare_floor, ceil(rare_share * N))     (ADR: 3 and 0.05)
    common  everything else
`single` is decided first because a token seen in one visit cannot be a hub; the ADR text read literally would call it a
hub whenever 0.2 * N < 1 (N < 5). The ADR lists the tests in another order but states that hub beats rare; this is the
same rule with that one clarification. Tokens in GENERIC_TOKENS are flagged `generic` and never count as distinctive.

Time. Nothing here uses an absolute time. Timestamps appear only through differences (dwell, session gaps), compared with
parameters declared as time-valued: `ReplayParams.glance_dwell_s`, `ReplayParams.internal_shift_min_dwell_s` and
`FeatureParams.cannot_link_min_dwell_s`, `FeatureParams.session_gap_s`. Translating all timestamps changes nothing; scaling
all timestamps and all of those parameters together by any k > 0 changes no flag, class or evidence (`stable_dict`
drops the fields that carry seconds so tests can compare).
"""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field, fields, replace
from fractions import Fraction
from types import MappingProxyType

import moments

SCHEMA = 1
GENERIC_TOKENS = frozenset((
    "meeting notes update review summary project report email draft schedule team plan status document weekly follow "
    "agenda inbox calendar message thanks reply forward attached comment page home untitled google chrome window "
    "search new tab").split())                    # a placeholder list: words that identify nothing on their own
CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
INTEGRATION_BRANCHES = frozenset(("main", "master", "develop", "trunk"))
UNNAMED_KINDS = frozenset(("app_window", "terminal", "mailbox", "chat_list", "folder"))
SPECIFICITY = {                                   # fixed priors by kind, placeholders (design 3.2); never from assignments
    "pr": 1.0, "issue": 1.0, "ref": 0.8, "commit": 0.9, "document": 0.7, "file": 0.7, "search_query": 0.9,
    "conversation": 0.6, "url": 0.5, "chat": 0.4, "item": 0.5}
ITEM_SPECIFICITY = {"terminal": 0.2, "mailbox": 0.2, "app_window": 0.2, "chat_list": 0.2, "folder": 0.3, "web_page": 0.5,
                    "reading_page": 0.5, "code_file": 0.7, "document": 0.7, "video": 0.7, "email": 0.6, "meeting": 0.5}
CLASS_OF = {"pr": "H1", "issue": "H1", "ref": "H1", "commit": "L"}      # every other artifact kind is class I

_FILE = re.compile(r"^(?:.*/)?([^/\\]+\.[A-Za-z][A-Za-z0-9]{0,5})$")
_REF = re.compile(r"(?<![\w&])#(\d{1,6})\b")
_PR = re.compile(r"^/([^/]+)/([^/]+)/(?:-/)?(pull|pulls|merge_requests|issues)/(\d{1,7})(?:/.*)?$")
_COMMIT = re.compile(r"^/([^/]+)/([^/]+)/(?:-/)?commits?/([0-9a-fA-F]{7,40})(?:/.*)?$")
_TREE = re.compile(r"^/([^/]+)/([^/]+)/(?:-/)?tree/([^/]+)$")
_BLOB_SHA = re.compile(r"^/([^/]+)/([^/]+)/(?:-/)?blob/([0-9a-fA-F]{40})(?:/.*)?$")
_REPO = re.compile(r"^/([^/]+)/([^/]+)(?:/.*)?$")
_GDOC = re.compile(r"^/(document|spreadsheets|presentation|forms)/(?:u/\d+/)?d/([A-Za-z0-9_-]{6,})(?:/.*)?$")
_PROJECT_SPLIT = re.compile(r"\s+[—–-]\s+")
_BRANCH_SUFFIX = re.compile(r"\s*\(([^()\s]{1,80})\)\s*$")
_URL_WORDS = re.compile(r"[A-Za-z][A-Za-z'’-]{3,19}")


# ---------------------------------------------------------------- parameters

@dataclass(frozen=True)
class FeatureParams:
    """Placeholders until fixtures and real labels calibrate them. Time-valued fields end in _s and are listed in TIME."""
    # time-valued (seconds): scaled together with the timestamps by `scaled`
    cannot_link_min_dwell_s: float = 60.0      # a visit must last this long to take part in a PR / branch cannot-link
    session_gap_s: float = 1800.0              # visits of one artifact further apart than this are different sessions
    # token weights by source (dimensionless)
    w_term: float = 1.0
    w_identifier: float = 1.5
    w_stem: float = 0.75
    w_line: float = 0.5
    # document-frequency classes (ADR 0003)
    hub_share: float = 0.2
    rare_share: float = 0.05
    rare_floor: int = 3
    small_window: int = 10                     # fewer eligible visits than this: classes are reported as unreliable
    # quality factors
    q_ocr_only: float = 0.8
    q_single_frame: float = 0.6
    q_empty: float = 0.5
    weak_quality: float = 0.5                  # below this, evidence is weak
    # glance and internal shift (dimensionless parts; the dwell parts live in ReplayParams)
    glance_frames: int = 2
    shift_cos_max: float = 0.30
    shift_min_tokens: int = 4
    # specificity heuristics (UNCALIBRATED: see docs/adr/0004-activity-features-and-evidence.md)
    recurrence_sessions: int = 5               # sessions at which recurrence reaches 1
    volatility_min_tokens: int = 4             # a visit needs this many distinctive tokens to take part in volatility
    volatility_max_visits: int = 30            # visits of one artifact compared (the first ones, in canonical order)
    volatility_weight: float = 0.5             # how much volatility can lower a specificity prior (0 to 1)
    recurrence_weight: float = 0.5             # how much recurrence can lower a specificity prior (0 to 1)
    # evidence construction
    max_group: int = 40                        # SUPPORT evidence only: visits per key beyond which pairs use a successor window
    x_pr_restricts: bool = True                # a restriction between different PR / issue numbers (the design's original intent);
                                               # False keeps the fact (`distinct_identity`) and makes the restriction inactive
    max_restriction_pairs: int = 500_000       # restrictions are never truncated: beyond this the build FAILS (ConstraintOverflowError)

    TIME = ("cannot_link_min_dwell_s", "session_gap_s")

    def scaled(self, k):
        return replace(self, **{n: getattr(self, n) * k for n in self.TIME})


def undeclared_time_fields():
    """Seconds-valued FeatureParams fields (named *_s) that are not listed in TIME. Must be empty."""
    return sorted(f.name for f in fields(FeatureParams) if f.name.endswith("_s") and f.name not in FeatureParams.TIME)


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class Artifact:
    kind: str            # item url file document search_query conversation chat | pr issue ref | commit
    key: str             # normalised identity, e.g. "github.com/acme/billing#12" or "ref#12" (scope None)
    cls: str             # H1 | I | L
    specificity: float   # fixed prior by kind
    scope: str           # repository key for a scoped PR / issue / commit; None for an unscoped "#N"
    sources: tuple       # where it was seen: url, target, window, identifier, item_id
    moments: tuple       # ids of the moments that carried it


@dataclass(frozen=True)
class Namespace:
    kind: str            # domain section repo project branch
    key: str
    scope: str           # for a branch: the repo or project it was seen under
    via: tuple           # url | window | target
    integration: bool    # a branch only: main, master, develop, trunk
    moments: tuple


@dataclass(frozen=True)
class Token:
    text: str
    weight: float        # the largest source weight (before quality and idf)
    sources: tuple       # term | identifier | stem | line
    generic: bool


@dataclass(frozen=True)
class VisitFeatures:
    visit_id: str
    moments: tuple
    item_id: str
    app: str
    bundle_id: str
    kind: str
    t_start: float
    t_end: float
    dwell_s: float
    frames: int
    artifacts: tuple
    namespaces: tuple
    tokens: tuple
    context_words: tuple
    quality: float
    quality_parts: MappingProxyType
    glance: bool
    weak_evidence: bool
    flag_reasons: tuple
    internal_shift: MappingProxyType
    issues: tuple        # optional fields that were missing or malformed and therefore ignored (names only)

    @property
    def has_substance(self):
        return not (self.glance or self.weak_evidence)


@dataclass(frozen=True)
class TokenStats:
    n_eligible: int
    df: MappingProxyType
    classes: MappingProxyType
    idf: MappingProxyType
    small_window: bool


@dataclass(frozen=True)
class ArtifactStats:
    """Observable reuse statistics of one artifact, from raw features only (never from assignments). UNCALIBRATED heuristics."""
    key: tuple                    # (kind, key)
    n_visits: int
    n_sessions: int
    recurrence: float             # min(1, (sessions - 1) / recurrence_sessions)
    volatility: float             # 1 - mean pairwise cosine of the visits' weighted bags; 0 when undefined
    volatility_defined: bool      # at least two visits with volatility_min_tokens distinctive tokens
    n_bags: int                   # visits that took part in volatility
    prior: float                  # the fixed specificity prior of the artifact's kind
    effective_specificity: float  # prior * (1 - volatility_weight * volatility) * (1 - recurrence_weight * recurrence)


@dataclass(frozen=True)
class FeatureSet:
    visits: tuple
    stats: TokenStats
    artifact_stats: MappingProxyType
    params: MappingProxyType
    schema: int = SCHEMA

    def visit(self, visit_id):
        return next((v for v in self.visits if v.visit_id == visit_id), None)


# ---------------------------------------------------------------- small helpers

def norm(text):
    """Casefolded, NFKC, single-spaced. The one text normalisation used for keys."""
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def _s(v):
    return v if isinstance(v, str) and v.strip() else None


def _split_url(url):
    """'host/path' as stored by the Moment layer (query and fragment already removed) -> (host, '/path')."""
    u = _s(url)
    if not u:
        return None, ""
    u = re.sub(r"^[a-zA-Z]+://", "", u.strip())
    host, _, rest = u.partition("/")
    host = host.casefold()
    if host.startswith("www."):
        host = host[4:]
    return host or None, ("/" + rest.rstrip("/")) if rest.rstrip("/") else ""


def token_class(df, n, fp=None):
    """single | hub | rare | common. Mutually exclusive; `df` distinct visits, `n` eligible visits (see the module docstring)."""
    fp = fp or FeatureParams()
    if not (isinstance(df, int) and isinstance(n, int) and 1 <= df <= n):
        raise ValueError(f"df {df!r} must be an integer in 1..{n!r}")
    if df == 1:
        return "single"
    if df > Fraction(fp.hub_share).limit_denominator(10000) * n:
        return "hub"
    if df <= max(fp.rare_floor, math.ceil(Fraction(fp.rare_share).limit_denominator(10000) * n)):
        return "rare"
    return "common"


def idf(df, n):
    """ln((n + 1) / (df + 0.5)) / ln(n + 1), clipped to [0, 1]. Pure arithmetic on counts."""
    if n < 1:
        return 0.0
    return min(1.0, max(0.0, math.log((n + 1) / (df + 0.5)) / math.log(n + 1)))


# ---------------------------------------------------------------- artifacts and namespaces

class _Collector:
    """Gathers artifacts and namespaces for one visit with their sources and moments, then freezes them in canonical order."""

    def __init__(self):
        self.art, self.ns = {}, {}

    def artifact(self, kind, key, scope, source, moment_ids):
        if not key:
            return
        spec = ITEM_SPECIFICITY.get(scope, SPECIFICITY["item"]) if kind == "item" else SPECIFICITY[kind]
        slot = self.art.setdefault((kind, key), {"scope": scope if kind != "item" else None, "spec": spec, "src": set(), "mom": set()})
        slot["src"].add(source)
        slot["mom"].update(moment_ids)

    def namespace(self, kind, key, scope, via, moment_ids, integration=False):
        if not key:
            return
        slot = self.ns.setdefault((kind, key, scope), {"via": set(), "mom": set(), "int": integration})
        slot["via"].add(via)
        slot["mom"].update(moment_ids)

    def freeze(self):
        arts = tuple(Artifact(k, key, CLASS_OF.get(k, "I"), s["spec"], s["scope"], tuple(sorted(s["src"])), tuple(sorted(s["mom"])))
                     for (k, key), s in sorted(self.art.items()))
        nss = tuple(Namespace(k, key, sc, tuple(sorted(s["via"])), s["int"], tuple(sorted(s["mom"])))
                    for (k, key, sc), s in sorted(self.ns.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")))
        return arts, nss


def _file_artifact(name):
    m = _FILE.match(name.strip()) if isinstance(name, str) else None
    return norm(m.group(1)) if m else None


def _split_project(window, target, kind):
    """(project, branch) for an editor visit. Window 'file - project (branch)' as the editor title rule reads it
    (understand.editor), falling back to the 'project/file' target. The branch is only the parenthesised suffix of the project."""
    project = None
    t = _s(window)
    if t:
        parts = [p.strip() for p in _PROJECT_SPLIT.split(t.lstrip("● ").strip()) if p.strip()]
        project = parts[1] if len(parts) > 1 else None
    if not project and _s(target) and "/" in target:
        project = target.rsplit("/", 1)[0]
    branch = None
    if project:
        m = _BRANCH_SUFFIX.search(project)
        if m:
            branch, project = m.group(1), project[:m.start()].strip()
    return (norm(project) if project else None), branch


def _branch_from_url(path):
    """A branch only when the path is exactly .../tree/<ref> with one segment (a ref with a slash cannot be told from a path)."""
    m = _TREE.match(path)
    if m and not re.fullmatch(r"[0-9a-fA-F]{40}", m.group(3)):
        return f"{m.group(1)}/{m.group(2)}".casefold(), m.group(3)
    return None, None


def _visit_artifacts(first, moments_, ids_all):
    """Artifacts and namespaces of one visit. `first` supplies the visit-constant fields (V1)."""
    c = _Collector()
    host, path = _split_url(first.get("url"))
    kind, target, window = _s(first.get("kind")), _s(first.get("target")), _s(first.get("window"))
    c.artifact("item", _s(first.get("item_id")), kind, "item_id", ids_all)
    if host:
        c.artifact("url", host + path, None, "url", ids_all)
        c.namespace("domain", host, None, "url", ids_all)
        seg = path.split("/")[1] if path else ""
        if seg:
            c.namespace("section", f"{host}/{seg}".casefold(), None, "url", ids_all)
        d = _GDOC.match(path) if host == "docs.google.com" else None
        if d:
            c.artifact("document", f"{host}/{d.group(1)}/{d.group(2)}", None, "url", ids_all)
        if host in CODE_HOSTS:
            r = _REPO.match(path)
            repo = f"{host}/{r.group(1)}/{r.group(2)}".casefold() if r else None
            if repo:
                c.namespace("repo", repo, None, "url", ids_all)
            pr = _PR.match(path)
            if pr:
                kind_ = "issue" if pr.group(3) == "issues" else "pr"
                c.artifact(kind_, f"{repo}#{int(pr.group(4))}", repo, "url", ids_all)
            cm = _COMMIT.match(path) or _BLOB_SHA.match(path)
            if cm:
                c.artifact("commit", f"{repo}@{cm.group(3).casefold()[:40]}", repo, "url", ids_all)
            brepo, ref = _branch_from_url(path)
            if ref:
                c.namespace("branch", ref.casefold(), brepo and f"repo:{host}/{brepo}", "url", ids_all, ref.casefold() in INTEGRATION_BRANCHES)
    if kind == "web_search" and target:
        c.artifact("search_query", norm(target), None, "target", ids_all)
    elif kind == "ai_conversation" and target:
        c.artifact("conversation", norm(target), None, "target", ids_all)
    elif kind == "chat" and target:
        c.artifact("chat", norm(f"{first.get('app')}|{target}"), None, "target", ids_all)
    if kind == "code_file":
        project, branch = _split_project(window, target, kind)
        base = _file_artifact(target.rsplit("/", 1)[-1]) if target else None
        if base:
            c.artifact("file", base, None, "target", ids_all)
        if project:
            c.namespace("project", project, None, "window", ids_all)
        if branch:
            c.namespace("branch", norm(branch), f"project:{project}" if project else None, "window", ids_all, norm(branch) in INTEGRATION_BRANCHES)
    # identifiers: files and unscoped "#N" references, per moment so provenance is exact
    for m in moments_:
        mid = (m["id"],)
        digest = m.get("digest") if isinstance(m.get("digest"), dict) else {}
        for ident in digest.get("identifiers") or []:
            if not isinstance(ident, str):
                continue
            r = _REF.fullmatch(ident.strip())
            if r:
                c.artifact("ref", f"ref#{int(r.group(1))}", None, "identifier", mid)
                continue
            base = _file_artifact(ident)
            if base:
                c.artifact("file", base, None, "identifier", mid)
    if window:
        for r in _REF.finditer(window):
            c.artifact("ref", f"ref#{int(r.group(1))}", None, "window", ids_all)
    return c.freeze()


# ---------------------------------------------------------------- tokens

def _word_ok(w):
    return 4 <= len(w) <= 40 and w not in moments.STOP


def _moment_tokens(m, fp):
    """{token: [weight, sources]} for one moment, from the digest only. Malformed members are skipped, never guessed."""
    out, bad = {}, []

    def add(tok, w, src):
        if tok and _word_ok(tok):
            slot = out.setdefault(tok, [0.0, set()])
            slot[0] = max(slot[0], w)
            slot[1].add(src)

    d = m.get("digest")
    if d is not None and not isinstance(d, dict):
        return out, ["digest"]
    d = d or {}
    for name, w, src in (("terms", fp.w_term, "term"), ("identifiers", fp.w_identifier, "identifier"), ("lines", fp.w_line, "line")):
        vals = d.get(name)
        if vals is None:
            continue
        if not isinstance(vals, (list, tuple)):
            bad.append(f"digest.{name}")
            continue
        for v in vals:
            if not isinstance(v, str):
                bad.append(f"digest.{name}")
                continue
            if src == "term":
                add(norm(v), w, src)
            elif src == "identifier":
                ident = norm(v)
                if _REF.fullmatch(ident):
                    continue                              # "#12" is an artifact, not a word
                add(ident, w, src)
                stem = _file_artifact(ident)
                if stem:
                    add(stem.rsplit(".", 1)[0].rsplit("/", 1)[-1], fp.w_stem, "stem")
            else:
                for word in moments.TERM_RE.findall(v):
                    add(norm(word).strip("-'’"), w, src)
    return out, sorted(set(bad))


def _visit_tokens(moments_, fp):
    merged, bad = {}, []
    for m in moments_:
        toks, b = _moment_tokens(m, fp)
        bad += b
        for t, (w, srcs) in toks.items():
            slot = merged.setdefault(t, [0.0, set()])
            slot[0] = max(slot[0], w)
            slot[1].update(srcs)
    tokens = tuple(Token(t, w, tuple(sorted(s)), t in GENERIC_TOKENS) for t, (w, s) in sorted(merged.items()))
    return tokens, sorted(set(bad))


def _context_words(first):
    """Words of the title, target and URL path: kept apart from content so one piece of text is not counted twice."""
    words = set()
    for v in (first.get("window"), first.get("target"), first.get("url")):
        if isinstance(v, str):
            words.update(w for w in (norm(x).strip("-'’") for x in _URL_WORDS.findall(v)) if _word_ok(w))
    return tuple(sorted(words))


# ---------------------------------------------------------------- quality, flags, shift

def _quality(moments_, tokens, fp):
    ax = total = 0
    for m in moments_:
        p = m.get("provenance")
        if isinstance(p, dict):
            a = sum(v for k, v in p.items() if k in ("ax_only", "agreed") and isinstance(v, (int, float)) and not isinstance(v, bool))
            b = sum(v for k, v in p.items() if k in ("ocr_only", "differed") and isinstance(v, (int, float)) and not isinstance(v, bool))
            ax, total = ax + a, total + a + b
    prov = fp.q_ocr_only + (1.0 - fp.q_ocr_only) * (ax / total) if total > 0 else fp.q_ocr_only   # unknown keys say nothing
    frames = max((m.get("n_frames") if isinstance(m.get("n_frames"), int) else 1) for m in moments_)
    frame = fp.q_single_frame if frames <= 1 else 1.0
    content = 1.0 if tokens else fp.q_empty
    parts = {"provenance": round(prov, 6), "frames": frame, "content": content}
    return round(prov * frame * content, 6), MappingProxyType(parts)


def _bag(tokens_by_moment, stats_idf):
    return {t: w * stats_idf.get(t, 0.0) for t, w in tokens_by_moment.items()}


def _cosine(a, b):
    dot = sum(a[t] * b[t] for t in sorted(a) if t in b)
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _internal_shift(moments_, fp, params, stats_idf, dwell):
    """Topic change inside a visit, as a descriptor. Compares the first moment's weighted bag with every later moment whose
    digest text differs (text_hash). Flag: lowest cosine below shift_cos_max, both bags with at least shift_min_tokens
    tokens, and dwell at least ReplayParams.internal_shift_min_dwell_s. The visit is never split."""
    base = {"flag": False, "cos_min": None, "from_moment": None, "to_moment": None, "new_tokens": (), "dropped_tokens": (),
            "evaluated": False, "reason": None}
    if len(moments_) < 2:
        return MappingProxyType({**base, "reason": "single_moment"})
    first, first_tokens = moments_[0], _moment_tokens(moments_[0], fp)[0]
    h0 = (first.get("digest") or {}).get("text_hash") if isinstance(first.get("digest"), dict) else None
    best = None
    for m in moments_[1:]:
        d = m.get("digest") if isinstance(m.get("digest"), dict) else {}
        if d.get("text_hash") == h0 and h0 is not None:
            continue
        toks = _moment_tokens(m, fp)[0]
        if len(toks) < fp.shift_min_tokens or len(first_tokens) < fp.shift_min_tokens:
            continue
        cos = _cosine(_bag({t: v[0] for t, v in first_tokens.items()}, stats_idf), _bag({t: v[0] for t, v in toks.items()}, stats_idf))
        if best is None or cos < best[0]:
            best = (cos, m["id"], toks)
    if best is None:
        return MappingProxyType({**base, "reason": "no_comparable_moment"})
    cos, mid, toks = best
    new = tuple(sorted((t for t in toks if t not in first_tokens), key=lambda t: (-stats_idf.get(t, 0.0), t))[:5])
    gone = tuple(sorted((t for t in first_tokens if t not in toks), key=lambda t: (-stats_idf.get(t, 0.0), t))[:5])
    long_enough = dwell >= params.internal_shift_min_dwell_s
    return MappingProxyType({"flag": bool(cos < fp.shift_cos_max and long_enough), "cos_min": round(cos, 9),
                             "from_moment": first["id"], "to_moment": mid, "new_tokens": new, "dropped_tokens": gone,
                             "evaluated": True, "reason": None if long_enough else "dwell_below_minimum"})


def _is_named(first, arts):
    """A visit is named by what IT is, not by words on its screen: its kind is one that names a resource (not a bare window,
    terminal, mailbox or folder), or it has an artifact that came from its own identity (URL, document id, target, title),
    not only from identifiers seen in its content."""
    kind = _s(first.get("kind"))
    return (kind is not None and kind not in UNNAMED_KINDS) or any(
        a.kind != "item" and set(a.sources) - {"identifier"} for a in arts)


# ---------------------------------------------------------------- specificity heuristics (uncalibrated)

def recurrence_of(sessions, fp):
    """min(1, (sessions - 1) / recurrence_sessions), clipped to [0, 1]: 0 for one sitting, 1 from `recurrence_sessions` + 1."""
    if sessions < 1:
        raise ValueError("an artifact has at least one session")
    return round(min(1.0, max(0.0, (sessions - 1) / fp.recurrence_sessions)), 9)


def volatility_of(bags):
    """(1 - mean pairwise cosine over all unordered pairs of the given weighted bags, defined). Fewer than two bags: (0.0, False).
    Identical bags give 0, bags with no token in common give 1. Pure arithmetic on the bags, in sorted-token order."""
    if len(bags) < 2:
        return 0.0, False
    total, n = 0.0, 0
    for i in range(len(bags)):
        for j in range(i + 1, len(bags)):
            total += _cosine(bags[i], bags[j])
            n += 1
    return round(min(1.0, max(0.0, 1.0 - total / n)), 9), True


def effective_specificity(prior, volatility, recurrence, fp):
    """prior * (1 - volatility_weight * volatility) * (1 - recurrence_weight * recurrence), in [0, prior]."""
    return round(prior * (1.0 - fp.volatility_weight * volatility) * (1.0 - fp.recurrence_weight * recurrence), 9)


# ---------------------------------------------------------------- the entry point

def extract_features(prepared, params, fparams=None):
    """Prepared visits -> FeatureSet. Deterministic; the same Prepared and parameters always give the same result."""
    fp = fparams or FeatureParams()
    rows = []
    for v in prepared.visits:
        ms = list(v.moments)
        first = ms[0]
        ids_all = tuple(m["id"] for m in ms)
        tokens, bad = _visit_tokens(ms, fp)
        arts, nss = _visit_artifacts(first, ms, ids_all)
        quality, parts = _quality(ms, tokens, fp)
        dwell = sum(max(0.0, m["t_end"] - m["t_start"]) for m in ms)
        frames = sum((m.get("n_frames") if isinstance(m.get("n_frames"), int) else 1) for m in ms)
        named = _is_named(first, arts)
        reasons = []
        glance = dwell < params.glance_dwell_s and frames < fp.glance_frames and not named
        if glance:
            reasons.append("short_dwell_single_frame_unnamed")
        distinctive = any(not t.generic for t in tokens)
        weak = (quality < fp.weak_quality) or not distinctive
        if quality < fp.weak_quality:
            reasons.append("low_quality")
        if not distinctive:
            reasons.append("no_distinctive_content")
        rows.append(dict(v=v, first=first, ms=ms, tokens=tokens, bad=bad, arts=arts, nss=nss, quality=quality, parts=parts,
                         dwell=dwell, frames=frames, glance=glance, weak=weak, reasons=tuple(reasons)))

    eligible = [r for r in rows if r["tokens"]]
    n = len(eligible)
    df = {}
    for r in eligible:
        for t in r["tokens"]:
            df[t.text] = df.get(t.text, 0) + 1
    classes = {t: token_class(c, n, fp) for t, c in df.items()}
    idfs = {t: round(idf(c, n), 9) for t, c in df.items()}
    stats = TokenStats(n, MappingProxyType(dict(sorted(df.items()))), MappingProxyType(dict(sorted(classes.items()))),
                       MappingProxyType(dict(sorted(idfs.items()))), n < fp.small_window)

    feats = []
    for r in rows:
        shift = _internal_shift(r["ms"], fp, params, idfs, r["dwell"])
        first = r["first"]
        feats.append(VisitFeatures(
            visit_id=r["v"].visit_id, moments=tuple(m["id"] for m in r["ms"]), item_id=first.get("item_id"),
            app=first.get("app"), bundle_id=first.get("bundle_id"), kind=first.get("kind"),
            t_start=r["ms"][0]["t_start"], t_end=max(m["t_end"] for m in r["ms"]), dwell_s=r["dwell"], frames=r["frames"],
            artifacts=r["arts"], namespaces=r["nss"], tokens=r["tokens"], context_words=_context_words(first),
            quality=r["quality"], quality_parts=r["parts"], glance=r["glance"], weak_evidence=r["weak"],
            flag_reasons=r["reasons"], internal_shift=shift, issues=tuple(r["bad"])))
    feats.sort(key=lambda f: (f.t_start, f.visit_id))

    by_art = {}
    for f in feats:
        for a in f.artifacts:
            by_art.setdefault((a.kind, a.key), []).append((f, a))
    bags = {f.visit_id: {t.text: t.weight * idfs.get(t.text, 0.0) for t in f.tokens if not t.generic} for f in feats}
    astats = {}
    for key, pairs in sorted(by_art.items()):
        vs = [f for f, _ in pairs]
        prior = pairs[0][1].specificity
        sessions, horizon = 0, None
        for f in vs:                                   # already ordered by (t_start, visit_id)
            if horizon is None or f.t_start - horizon > fp.session_gap_s:
                sessions += 1
            horizon = f.t_end if horizon is None else max(horizon, f.t_end)
        recurrence = recurrence_of(sessions, fp)
        qualifying = [bags[f.visit_id] for f in vs if len(bags[f.visit_id]) >= fp.volatility_min_tokens][:fp.volatility_max_visits]
        volatility, defined = volatility_of(qualifying)
        astats[key] = ArtifactStats(key, len(vs), sessions, recurrence, volatility, defined, len(qualifying), prior,
                                    effective_specificity(prior, volatility, recurrence, fp))
    return FeatureSet(tuple(feats), stats, MappingProxyType(astats), MappingProxyType({
        "feature_params": _plain(fp), "glance_dwell_s": params.glance_dwell_s,
        "internal_shift_min_dwell_s": params.internal_shift_min_dwell_s}))


# ---------------------------------------------------------------- views

def weighted_bag(visit, stats):
    """token -> source weight x quality x idf, for the content similarity of PR 2C. Generic tokens are left out."""
    return {t.text: round(t.weight * visit.quality * stats.idf.get(t.text, 0.0), 9) for t in visit.tokens if not t.generic}


def _plain(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (MappingProxyType, dict)):
        return {str(k): _plain(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    return obj


TIME_FIELDS = frozenset(("t_start", "t_end", "dwell_s", "cannot_link_min_dwell_s", "session_gap_s", "glance_dwell_s",
                         "internal_shift_min_dwell_s"))


def as_dict(feature_set):
    """A plain, JSON-ready, canonically ordered view (keys sorted)."""
    return json.loads(json.dumps(_plain(feature_set), sort_keys=True))


def stable_dict(feature_set):
    """as_dict without the fields that carry seconds, for comparing results under translation or scaling."""
    def drop(o):
        if isinstance(o, dict):
            return {k: drop(v) for k, v in o.items() if k not in TIME_FIELDS}
        if isinstance(o, list):
            return [drop(x) for x in o]
        return o
    return drop(as_dict(feature_set))


def fingerprint(feature_set):
    return hashlib.sha256(json.dumps(as_dict(feature_set), sort_keys=True).encode("utf-8")).hexdigest()[:16]

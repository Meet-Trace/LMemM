"""LMemM - deterministic visit features for activity inference (Step 2, PR 2B). Pure Python, offline, no model.

    Prepared (activity_input.Prepared)  ->  extract()  ->  FeatureSet   (one VisitFeatures per visit + corpus statistics)

This module says WHAT EACH VISIT CONTAINS. It never says which visits belong together: that is evidence
(activity_evidence.py) and, later, scoring and assignment (PR 2C). No score, no clustering, no model, no I/O.

Inputs. Only fields already in the persisted Moment: item_id, app, bundle_id, kind, window, url, target, ref,
n_frames, provenance (frame counts per source), digest (terms, identifiers, lines) and timestamps. Nothing typed,
pasted or clipboard-derived exists in a Moment and nothing is added. interaction and context are never read.

What is extracted (schemas: see VisitFeatures and Corpus; rules: docs/adr/0004-activity-features-and-evidence.md)
    artifacts    identifiable things: the item itself, a document or page URL path, a file (name, folder when the window
                 shows it), a PR or issue (repository plus number), a chat thread. Each carries `id_bearing` (it names one
                 thing) and `hub_pattern` (it is a container: inbox, calendar, chat, search, a service's home page).
    namespaces   boundaries around artifacts: domain, repository, folder, locator (host plus first path segment).
    branch       a git branch only where the title or URL shows it; repository-backed or workspace-derived (see branch_of).
    lineage      a version chain key (same stem, app, folder or domain) for files and documents.
    bag          weighted tokens (design 14.3): terms and identifiers 1.0, title/target/URL-path words 1.0, line
                 words 0.5, times the visit quality q(V); hub-like tokens dropped.
    corpus       df, idf and the token class (hub, single, rare, common) over the eligible visits (14.7).
    specificity  per artifact, prior(kind) x breadth, from raw features only; never from an assignment (13.3).
    quality      provenance mix, frame count, content richness and the factor q(V).
    glance, substance, weak_evidence, internal_shift   per-visit flags with explicit reasons.

Time. No absolute time is used. Durations and gaps are differences of timestamps, so translating time changes nothing;
every time threshold comes from ReplayParams (declared there), so scaling timestamps and those parameters by k > 0
changes only the seconds-valued outputs (dwell_s, gap values), by exactly the factor k, and no flag or class.
"""

import math
import posixpath
import re
from dataclasses import dataclass, field

import exact_links
import moments
from activity_contract import ReplayParams

# ---------------------------------------------------------------- parameters

TOKEN_SOURCE_WEIGHT = {"terms": 1.0, "identifiers": 1.0, "title": 1.0, "target": 1.0, "url": 1.0, "lines": 0.5}
KNOWN_KINDS = frozenset({"reading_page", "code_file", "document", "mail", "chat", "terminal"})
HUB_KINDS = frozenset({"mail", "chat", "calendar", "search", "new_tab"})
HUB_SEGMENTS = frozenset({"inbox", "calendar", "search", "newtab", "boards", "dashboard", "feed", "notifications",
                          "home", "new-tab"})
CHROME_WORDS = frozenset({"untitled", "new", "tab", "menu", "file", "edit", "view", "window", "help", "home", "page",
                          "www", "http", "https", "com", "org", "net", "html", "index", "undefined", "loading"})
SHORT_STOP = frozenset("the and for are was you not but all can has had his her its out one two who how why".split())
PRIOR_BY_TYPE = {"document": 1.0, "file": 1.0, "pr": 1.0, "issue": 1.0, "page": 0.8, "chat": 0.6}
PRIOR_BY_KIND = {"code_file": 1.0, "document": 1.0, "reading_page": 0.8, "chat": 0.6, "mail": 0.4, "terminal": 0.3}
PRIOR_OTHER = 0.5


@dataclass(frozen=True)
class FeatureParams:
    """Dimensionless parameters (no seconds: every time threshold lives in ReplayParams). All are placeholders to be
    tuned on the fixtures and later on real labels; they are explicit so nothing is hidden in code."""
    hub_df_numerator: int = 1                # a token is hub-like when df > N * hub_df_numerator / hub_df_denominator
    hub_df_denominator: int = 5              # (0.2 N, evaluated in integers)
    rare_min_cap: int = 3                    # rare when 2 <= df <= max(rare_min_cap, ceil(N * rare_numerator / rare_denominator))
    rare_numerator: int = 1
    rare_denominator: int = 20               # (0.05 N)
    min_token_len: int = 3
    min_bag_tokens: int = 4                  # cos_idf is 0 when either bag has fewer tokens
    cos_floor: float = 0.15                  # x_E = clip((cos - cos_floor) / cos_span, 0, 1) * min(1, R / rare_cap_r)
    cos_span: float = 0.35
    rare_cap_r: int = 2
    q_ocr_only: float = 0.8
    q_single_frame: float = 0.6
    q_empty_digest: float = 0.5
    min_frames: int = 2                      # substance: frames needed
    thin_tokens: int = 4                     # content richness: fewer tokens than this is "thin"
    shift_overlap_max: float = 0.2           # internal shift: token overlap (Jaccard) below this between first and last
    f_pattern_hub: float = 0.2               # breadth factors, each in (0, 1]
    vol_ref: float = 0.3                     # mean pairwise cos_idf at which volatility stops demoting
    vol_floor: float = 0.1
    vol_max_visits: int = 20                 # volatility looks at the first 20 visits of an artifact
    spec_low: float = 0.4                    # specificity bands (13.3): below low = cannot carry a link alone
    spec_high: float = 0.7


# ---------------------------------------------------------------- tokens and classes

def token_class(df, n, p=FeatureParams()):
    """hub, single, rare or common, mutually exclusive and tested in that order (design 14.7). Integer arithmetic,
    so there is no rounding at a boundary: hub iff df > N/5; rare iff 2 <= df <= max(3, ceil(N/20))."""
    if df * p.hub_df_denominator > n * p.hub_df_numerator:
        return "hub"
    if df == 1:
        return "single"
    cap = max(p.rare_min_cap, -(-n * p.rare_numerator // p.rare_denominator))
    return "rare" if 2 <= df <= cap else "common"


def idf(df, n):
    """ln((N+1)/(df+1)) / ln(N+1), in [0, 1]."""
    return math.log((n + 1) / (df + 1)) / math.log(n + 1) if n > 0 else 0.0


_WORD = re.compile(r"[a-z][a-z0-9']*|\d+[a-z][a-z0-9']*")


def _words(text, p):
    out = []
    for w in _WORD.findall((text or "").casefold()):
        w = w.strip("'")
        if len(w) >= p.min_token_len and not w.isdigit() and w not in moments.STOP and w not in SHORT_STOP and w not in CHROME_WORDS:
            out.append(w)
    return out


def _path_words(value, p):
    """Words of a URL or target, without the host: the host is a namespace, not content."""
    if not value:
        return []
    clean, domain = moments.clean_url(value)
    s = (clean or value)
    if domain and s.startswith(domain):
        s = s[len(domain):]
    elif "." in s.split("/")[0] and "/" in s:
        s = s.split("/", 1)[1]
    return _words(s.replace("/", " ").replace("-", " ").replace("_", " ").replace(".", " "), p)


def _ident_token(s, p):
    s = (s or "").strip().casefold()
    return s if len(s) >= p.min_token_len and not s.isdigit() else None


# ---------------------------------------------------------------- artifacts

GIT_HOSTS = ("github.", "gitlab.", "bitbucket.")
PR_SEGMENTS = {"pull": "pr", "pull-requests": "pr", "merge_requests": "pr", "issues": "issue"}
TITLE_REF = re.compile(r"\b(Pull Request|Merge Request|Issue)s? #(\d+)\s*[·|\-–—]\s*([\w.-]+/[\w.-]+)")
_SPLIT = re.compile(r"\s[-—–]\s")


def _segments(url):
    """(host, [segments]) of a URL or None. Query and fragment are dropped (moments.clean_url) before anything else."""
    clean, domain = moments.clean_url(url)
    if not clean:
        return None
    host = domain or ""
    path = clean[len(host):] if host and clean.startswith(host) else clean
    return host, [s for s in path.split("/") if s and s != "-"]


def _is_git_host(host):
    return any(h in host for h in GIT_HOSTS)


def _art(type_, key, fields, id_bearing, hub, **extra):
    d = {"type": type_, "key": key, "fields": tuple(sorted(fields)), "id_bearing": bool(id_bearing),
         "hub_pattern": bool(hub)}
    d.update(extra)
    return d


_BRANCH_SUFFIX = re.compile(r"\s*\(([^()]+)\)\s*$")


def _workspace(first):
    """The workspace a window title names beside the file ('file.py - workspace [- app]'), raw (a '(branch)' suffix kept),
    or None. Only for code_file / document visits whose first title part is the target's file name."""
    target = first.get("target") or ""
    base = posixpath.basename(target.rstrip("/"))
    if not base or first.get("url") or first.get("kind") not in ("code_file", "document"):
        return None
    app = (first.get("app") or "").casefold()
    parts = [x.strip() for x in _SPLIT.split(first.get("window") or "") if x.strip() and x.strip().casefold() != app]
    if len(parts) >= 2 and posixpath.basename(parts[0]).casefold() == base.casefold():
        return parts[1]
    return None


def _file_names(first, p):
    """(basename, folder) of the file a visit shows, or (None, None). Only code_file visits and documents whose target is
    a file name; the folder only when the target holds one or the window title names the workspace ('f.py - project').
    A '(branch)' suffix on the workspace is not part of the folder."""
    kind, target = first.get("kind"), first.get("target") or ""
    if not target or "." not in posixpath.basename(target) or kind not in ("code_file", "document"):
        return None, None
    if first.get("url"):                         # a web document is a URL artifact, not a file
        return None, None
    base = posixpath.basename(target.rstrip("/"))
    folder = posixpath.dirname(target.rstrip("/")) or None
    if folder is None:
        ws = _workspace(first)
        if ws:
            folder = _BRANCH_SUFFIX.sub("", ws).strip().casefold() or None
    return base, folder


def artifacts_of(first, p=FeatureParams()):
    """The artifacts one visit shows, from its constant fields (the first moment) only. Sorted, de-duplicated."""
    kind, window = first.get("kind"), first.get("window") or ""
    out = {}

    def add(a):
        out.setdefault((a["type"], a["key"]), a)

    seg = _segments(first.get("url")) if first.get("url") else None
    if seg is None and first.get("target") and kind in ("reading_page", "document", "mail", "chat") \
            and "." in (first["target"].split("/")[0]):
        seg = _segments(first["target"])                         # a web target without a url field
    hub_kind = kind in HUB_KINDS
    if seg:
        host, segs = seg
        hub_seg = not segs or any(s.casefold() in HUB_SEGMENTS for s in segs)
        git = _is_git_host(host) and len(segs) >= 2
        repo = f"{host}/{segs[0].casefold()}/{re.sub(r'[.]git$', '', segs[1].casefold())}" if git else None
        num = None
        if git and len(segs) >= 4 and segs[2] in PR_SEGMENTS and segs[3].isdigit():
            num = (PR_SEGMENTS[segs[2]], int(segs[3]))
            add(_art(num[0], f"{repo}#{num[1]}", ("url",), True, False, repo=repo, number=num[1]))
        key = f"{host}/{'/'.join(s.casefold() for s in segs)}"
        generic = git and len(segs) <= 2 or (git and len(segs) == 3 and num is None)   # repo home, lists, tabs
        id_bearing = bool(segs) and not generic and (len(segs) >= 2 or kind == "document"
                                                      or any(c.isdigit() for s in segs for c in s))
        add(_art("document" if kind == "document" else "page", key, ("url",), id_bearing,
                 hub_kind or hub_seg or generic and not num, host=host))
    else:
        base, folder = _file_names(first, p)
        if base:
            key = f"{folder or ''}/{base.casefold()}"
            add(_art("file", key, ("target", "window") if folder and "/" not in (first.get("target") or "") else ("target",),
                     True, False, name=base.casefold(), folder=folder, folder_known=bool(folder)))
        elif kind == "chat" and first.get("target"):
            add(_art("chat", f"{(first.get('app') or '').casefold()}/{first['target'].casefold()}", ("target",),
                     True, True))
    m = TITLE_REF.search(window)                                  # a PR or issue the title names, with its repository
    if m:
        t = "issue" if m.group(1) == "Issue" else "pr"
        repo = f"github.com/{m.group(3).casefold()}" if (not seg or not _is_git_host(seg[0])) else None
        if seg and _is_git_host(seg[0]) and len(seg[1]) >= 2:
            repo = f"{seg[0]}/{seg[1][0].casefold()}/{re.sub(r'[.]git$', '', seg[1][1].casefold())}"
        if repo:
            add(_art(t, f"{repo}#{int(m.group(2))}", ("window",), True, False, repo=repo, number=int(m.group(2))))
    primary_hub = any(a["hub_pattern"] for a in out.values()) or hub_kind
    if first.get("item_id"):
        add(_art("item", first["item_id"], ("item_id",), True, primary_hub))
    return [out[k] for k in sorted(out)]


def namespaces_of(first, artifacts):
    """Boundaries around the artifacts: domain, repo, folder, locator (host + first path segment, never a bare domain)."""
    ns = set()
    seg = _segments(first.get("url")) if first.get("url") else None
    if seg:
        host, segs = seg
        if host:
            ns.add(("domain", host))
        if _is_git_host(host) and len(segs) >= 2:
            ns.add(("repo", f"{host}/{segs[0].casefold()}/{re.sub(r'[.]git$', '', segs[1].casefold())}"))
        elif host and segs:
            ns.add(("locator", f"{host}/{segs[0].casefold()}"))
    elif first.get("domain"):
        ns.add(("domain", first["domain"].casefold()))
    for a in artifacts:
        if a["type"] == "file" and a.get("folder"):
            ns.add(("folder", a["folder"]))
        if a["type"] in ("pr", "issue"):
            ns.add(("repo", a["repo"]))
    return sorted(ns)


# ---------------------------------------------------------------- branch and lineage

def branch_of(first, lines):
    """The one git branch a window shows, or None. {'name', 'scope', 'repo', 'workspace'}.

    scope 'repo'       the page is on a git host, so the repository is `host/owner/repo` (a real, global identity).
                       Only this scope may produce `same_branch` support and `different_branch` cannot-links.
    scope 'workspace'  a '(branch)' suffix on an editor workspace name. A workspace name is NOT a repository identity
                       (two checkouts can share a name, one repository can have several), so it never produces a
                       cannot-link; it yields only soft same/different-workspace-branch evidence within one name.
    scope 'none'       a branch seen with no repository (a terminal title or output; a Moment has no working directory).
                       Kept as a feature, never paired.
    Two branches in one window, a default branch, or an ambiguous deep path give no branch."""
    names, kind = set(), first.get("kind")
    window = first.get("window") or ""
    seg = _segments(first.get("url")) if first.get("url") else None
    repo = None
    if seg and _is_git_host(seg[0]) and len(seg[1]) >= 2:
        repo = f"{seg[0]}/{seg[1][0].casefold()}/{re.sub(r'[.]git$', '', seg[1][1].casefold())}"
    if kind == "terminal":
        for rx in exact_links.TITLE:
            names |= {n for n in (exact_links._valid(m, True) for m in rx.findall(window)) if n}
        for line in lines:
            for rx in exact_links.COMMANDS:
                names |= {n for n in (exact_links._valid(m, False) for m in rx.findall(line)) if n}
            for rx in exact_links.OUTPUT:
                names |= {n for n in (exact_links._valid(m, False) for m in rx.findall(line)) if n}
    if seg and _is_git_host(seg[0]) and re.search(r"/(?:pull|merge_requests|compare)/", "/" + "/".join(seg[1])):
        for line in [window] + list(lines):
            for k, rx in enumerate(exact_links.PULL):
                for m in rx.findall(line):
                    n = exact_links._valid(m[1] if k else m[0], True)
                    if n:
                        names.add(n)
    if seg and repo and len(seg[1]) == 4 and seg[1][2] == "tree":
        n = exact_links._valid(seg[1][3], False)                  # a branch's root page; deeper paths are ambiguous
        if n:
            names.add(n)
    workspace = None
    ws = _workspace(first)
    if ws:
        m = _BRANCH_SUFFIX.search(ws)
        n = exact_links._valid(m.group(1), False) if m else None
        if n:
            names.add(n)
            workspace = _BRANCH_SUFFIX.sub("", ws).strip().casefold()
    if len(names) != 1:                                           # none, or two branches in one window: no signal
        return None
    name = next(iter(names))
    if workspace and not repo:
        return {"name": name, "scope": "workspace", "repo": None, "workspace": workspace}
    if repo:
        return {"name": name, "scope": "repo", "repo": repo, "workspace": None}
    return {"name": name, "scope": "none", "repo": None, "workspace": None}


def lineage_of(first, artifacts):
    """A version-chain key {'stem', 'ext', 'scope', 'name'} for a file or a titled web document, else None.
    Same stem, same extension and same scope (app + folder or domain) are the chain; the full name tells versions apart."""
    app = (first.get("app") or "").casefold()
    for a in artifacts:
        if a["type"] == "file" and a.get("name"):
            s, ext = exact_links.stem(a["name"])
            if len(s) >= 3:
                return {"stem": s, "ext": ext, "scope": f"{app}|{a.get('folder') or ''}", "name": a["name"]}
        if a["type"] == "document" and first.get("window"):
            title = [x for x in _SPLIT.split(first["window"]) if x.strip().casefold() != app]
            if title:
                s, ext = exact_links.stem(title[0].strip())
                if len(s) >= 3:
                    return {"stem": s, "ext": "", "scope": f"{app}|{a.get('host', '')}", "name": title[0].strip().casefold()}
    return None


# ---------------------------------------------------------------- per visit

@dataclass(frozen=True)
class VisitFeatures:
    visit_id: str
    t_start: float
    t_end: float
    dwell_s: float
    moment_ids: tuple
    app: str
    bundle_id: str
    kind: str
    known_kind: bool
    n_frames: int
    artifacts: tuple                    # dicts: type, key, fields, id_bearing, hub_pattern, ... (sorted)
    namespaces: tuple                   # (type, value) pairs, sorted
    branch: object                      # {'name','scope','repo','workspace'} or None
    lineage: object                     # {'stem','ext','scope','name'} or None
    bag: tuple                          # (token, weight) sorted by token, hub-like tokens dropped
    key_tokens: frozenset               # tokens that come from the artifact key (url path, target, file name)
    identifiers: tuple                  # specific identifiers the screen showed (lower case, sorted), hub-like dropped
    quality: dict
    glance: bool
    weak_evidence: bool
    substance: dict                     # {'ok': bool, 'reasons': [...]}
    internal_shift: object              # {'from_moment_id','to_moment_id','overlap','new_identifiers'} or None

    def bag_map(self):
        return dict(self.bag)

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["moment_ids"] = list(self.moment_ids)
        d["artifacts"] = [dict(a, fields=list(a["fields"])) for a in self.artifacts]
        d["namespaces"] = [list(n) for n in self.namespaces]
        d["bag"] = [list(b) for b in self.bag]
        d["key_tokens"] = sorted(self.key_tokens)
        d["identifiers"] = list(self.identifiers)
        return d


def _quality(visit, n_tokens, digest_empty, p):
    prov = {}
    for m in visit.moments:
        for k, v in (m.get("provenance") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                prov[k] = prov.get(k, 0) + v
    ax, ocr = prov.get("ax", 0) > 0, prov.get("ocr", 0) > 0
    source = "ax+ocr" if ax and ocr else "ax" if ax else "ocr" if ocr else "none"
    frames = sum(int(m.get("n_frames") or 0) for m in visit.moments)
    q = p.q_ocr_only if source in ("ocr", "none") else 1.0
    if frames <= 1:
        q *= p.q_single_frame
    if digest_empty:
        q = p.q_empty_digest
    content = "empty" if digest_empty or n_tokens == 0 else "thin" if n_tokens < p.thin_tokens else "ok"
    return {"q": q, "source": source, "frames": frames, "digest_empty": digest_empty, "tokens": n_tokens,
            "content": content}


def _raw_bag(visit, first, q, p):
    """token -> (weight, set of source labels), before hub dropping."""
    bag = {}

    def put(tok, src):
        if tok:
            w = TOKEN_SOURCE_WEIGHT[src] * q
            old = bag.get(tok)
            bag[tok] = (max(w, old[0]) if old else w, (old[1] if old else frozenset()) | {src})

    for m in visit.moments:
        dg = m.get("digest") or {}
        for t in dg.get("terms") or []:
            for w in _words(str(t), p):
                put(w, "terms")
        for t in dg.get("identifiers") or []:
            put(_ident_token(str(t), p), "identifiers")
        for line in dg.get("lines") or []:
            for w in _words(str(line), p):
                put(w, "lines")
    for w in _words(first.get("window"), p):
        put(w, "title")
    for ref in re.findall(r"#\d{1,6}\b", first.get("window") or ""):       # a bare #N in the title is an identifier
        put(ref, "identifiers")
    for src, val in (("target", first.get("target")), ("url", first.get("url"))):
        for w in _path_words(val, p):
            put(w, src)
    return bag


def _internal_shift(visit, p, rp):
    ms = [m for m in visit.moments if max(0.0, m["t_end"] - m["t_start"]) >= rp.internal_shift_min_dwell_s]
    if len(ms) < 2:
        return None

    def toks(m):
        dg = m.get("digest") or {}
        return ({str(t).casefold() for t in dg.get("terms") or []}, {str(t).casefold() for t in dg.get("identifiers") or []})

    (ta, ia), (tb, ib) = toks(ms[0]), toks(ms[-1])
    a, b = ta | ia, tb | ib
    if not a or not b:
        return None
    overlap = len(a & b) / len(a | b)
    new_ids = sorted(ib - ia)
    if overlap < p.shift_overlap_max or (new_ids and ia):
        return {"from_moment_id": ms[0]["id"], "to_moment_id": ms[-1]["id"], "overlap": overlap, "new_identifiers": new_ids}
    return None


@dataclass(frozen=True)
class Corpus:
    n: int                              # eligible visits: a non-empty bag after normalisation (before hub dropping)
    df: dict                            # token -> number of distinct visits whose bag holds it
    cls: dict                           # token -> hub | single | rare | common
    idf: dict                           # token -> idf in [0, 1]
    artifacts: dict                     # (type, key) -> {'visits', 'specificity', 'prior', 'f_pattern', 'f_volatility',
                                        #               'f_recurrence', 'sittings', 'band'}

    def to_dict(self):
        return {"n": self.n, "df": dict(sorted(self.df.items())), "cls": dict(sorted(self.cls.items())),
                "artifacts": {f"{t}|{k}": v for (t, k), v in sorted(self.artifacts.items())}}


@dataclass(frozen=True)
class FeatureSet:
    visits: tuple
    corpus: Corpus
    params: FeatureParams
    replay_params: ReplayParams
    report: dict                        # counts only

    def visit(self, visit_id):
        return next((v for v in self.visits if v.visit_id == visit_id), None)

    def to_dict(self):
        return {"visits": [v.to_dict() for v in self.visits], "corpus": self.corpus.to_dict(), "report": self.report}


# ---------------------------------------------------------------- similarity (a measurement, not a decision)

def content_similarity(a, b, corpus, p=FeatureParams(), exclude=frozenset()):
    """cos_idf, R and x_E between two visits (design 14.3). `exclude`: tokens removed from both bags first (the tokens
    of a lineage or artifact key, so one string never counts twice). Symmetric; sums are in sorted token order."""
    A = {t: w for t, w in a.bag if t not in exclude}
    B = {t: w for t, w in b.bag if t not in exclude}
    rare = sorted(t for t in set(A) & set(B) if corpus.cls.get(t) == "rare")
    cos = 0.0
    if len(A) >= p.min_bag_tokens and len(B) >= p.min_bag_tokens:
        i2 = lambda t: corpus.idf.get(t, 0.0) ** 2
        na = math.sqrt(math.fsum(i2(t) * A[t] ** 2 for t in sorted(A)))
        nb = math.sqrt(math.fsum(i2(t) * B[t] ** 2 for t in sorted(B)))
        if na > 0 and nb > 0:
            cos = math.fsum(i2(t) * A[t] * B[t] for t in sorted(set(A) & set(B))) / (na * nb)
    r = len(rare)
    x = min(1.0, max(0.0, (cos - p.cos_floor) / p.cos_span)) * min(1.0, r / p.rare_cap_r)
    return {"cos_idf": cos, "rare_shared": r, "rare_tokens": rare, "x_e": x}


# ---------------------------------------------------------------- the pipeline

def extract(prepared, params=None, replay_params=None):
    """Prepared -> FeatureSet. A pure function of the validated records and the two parameter sets."""
    p = params or FeatureParams()
    rp = replay_params or ReplayParams()
    pre = []
    for v in prepared.visits:
        first = v.moments[0]
        # a truncated visit (oldest moments dropped by retention) keeps its constant fields on every moment
        arts = artifacts_of(first, p)
        lines = [str(l) for m in v.moments for l in ((m.get("digest") or {}).get("lines") or [])]
        digest_empty = not any((m.get("digest") or {}).get(k) for m in v.moments for k in ("terms", "identifiers", "lines"))
        raw0 = _raw_bag(v, first, 1.0, p)
        q = _quality(v, len(raw0), digest_empty, p)
        raw = _raw_bag(v, first, q["q"], p)
        pre.append((v, first, arts, lines, q, raw))

    eligible = [x for x in pre if x[5]]
    n = len(eligible)
    df = {}
    for x in eligible:
        for t in x[5]:
            df[t] = df.get(t, 0) + 1
    cls = {t: token_class(c, n, p) for t, c in df.items()}
    idfs = {t: idf(c, n) for t, c in df.items()}

    feats = []
    for v, first, arts, lines, q, raw in pre:
        bag = tuple((t, raw[t][0]) for t in sorted(raw) if cls[t] != "hub")
        key_tokens = frozenset(t for t in raw if raw[t][1] & {"target", "url"} and cls[t] != "hub")
        for a in arts:
            if a["type"] == "file" and a.get("name"):
                key_tokens |= {t for t in (_ident_token(a["name"], p),) if t in raw and cls[t] != "hub"}
        idents = tuple(sorted(t for t in raw if "identifiers" in raw[t][1] and cls[t] != "hub"))
        dwell = math.fsum(max(0.0, m["t_end"] - m["t_start"]) for m in v.moments)
        known = first.get("kind") in KNOWN_KINDS
        need = rp.seed_dwell_s if known else rp.unknown_app_seed_dwell_s
        glance = dwell < rp.glance_dwell_s
        reasons = []
        if glance:
            reasons.append("glance")
        if dwell < need:
            reasons.append("short_dwell")
        if q["frames"] < p.min_frames:
            reasons.append("few_frames")
        if q["content"] == "empty":
            reasons.append("empty_digest")
        weak = q["content"] == "empty" and not any(a["id_bearing"] and not a["hub_pattern"] and a["type"] != "item" for a in arts)
        feats.append(VisitFeatures(
            visit_id=v.visit_id, t_start=v.t_start, t_end=v.t_end, dwell_s=dwell,
            moment_ids=tuple(m["id"] for m in v.moments), app=first.get("app"), bundle_id=first.get("bundle_id"),
            kind=first.get("kind"), known_kind=known, n_frames=q["frames"], artifacts=tuple(arts),
            namespaces=tuple(namespaces_of(first, arts)), branch=branch_of(first, lines),
            lineage=lineage_of(first, arts), bag=bag, key_tokens=key_tokens, identifiers=idents, quality=q,
            glance=glance, weak_evidence=weak, substance={"ok": not reasons, "reasons": reasons},
            internal_shift=_internal_shift(v, p, rp)))

    corpus0 = Corpus(n, df, cls, idfs, {})
    corpus = Corpus(n, df, cls, idfs, _specificity(feats, corpus0, p, rp))
    report = {"visits": len(feats), "eligible": n, "glance": sum(f.glance for f in feats),
              "weak_evidence": sum(f.weak_evidence for f in feats),
              "internal_shift": sum(f.internal_shift is not None for f in feats),
              "tokens": len(df), "hub_tokens": sum(c == "hub" for c in cls.values())}
    return FeatureSet(tuple(feats), corpus, p, rp, report)


def volatility_factor(mean_sim, p=FeatureParams()):
    """UNCALIBRATED HEURISTIC. f_vol = clamp(mean_sim / vol_ref, vol_floor, 1), where mean_sim is the mean pairwise cos_idf
    between the artifact's own visits (the first vol_max_visits by (t_start, visit_id)). An artifact whose own visits share
    little content (a feed, a mailbox, a reused tab) is container-like and is demoted. A single visit gives 1.0."""
    return min(1.0, max(p.vol_floor, mean_sim / p.vol_ref))


def sittings_of(visits, t_resume_s):
    """Number of sittings of one artifact: 1 + the number of times a visit starts MORE than t_resume_s after the latest
    end of the earlier visits. A gap of exactly t_resume_s stays inside the sitting. Differences only, so it is
    translation-invariant; with t_resume_s scaled with the timestamps it is scale-invariant. `visits` ordered by start."""
    n, end = 1, visits[0].t_end
    for f in visits[1:]:
        if f.t_start - end > t_resume_s:
            n += 1
        end = max(end, f.t_end)
    return n


def recurrence_factor(sittings):
    """UNCALIBRATED HEURISTIC. f_rec = 1 for 1 or 2 sittings, else 2 / sittings: an artifact seen in many separate
    sittings is a place you keep returning to rather than one piece of work. In (0, 1]."""
    return 1.0 if sittings <= 2 else 2.0 / sittings


def _specificity(feats, corpus, p, rp):
    """prior x breadth per artifact (13.3), from raw visit features only. breadth = pattern x volatility x recurrence,
    each a factor in (0, 1]. The volatility and recurrence definitions and every constant are uncalibrated heuristics
    (docs/adr/0004). Nothing here reads an assignment."""
    groups = {}
    for f in feats:
        for a in f.artifacts:
            groups.setdefault((a["type"], a["key"]), []).append((f, a))
    out = {}
    for k in sorted(groups):
        vs = sorted(groups[k], key=lambda x: (x[0].t_start, x[0].visit_id))
        a0 = vs[0][1]
        kind = vs[0][0].kind
        prior = PRIOR_BY_KIND.get(kind, PRIOR_OTHER) if a0["type"] == "item" else PRIOR_BY_TYPE.get(a0["type"], PRIOR_OTHER)
        f_pat = p.f_pattern_hub if a0["hub_pattern"] else 1.0
        fs = [x[0] for x in vs][:p.vol_max_visits]
        if len(fs) >= 2:
            sims = [content_similarity(fs[i], fs[j], corpus, p)["cos_idf"] for i in range(len(fs)) for j in range(i + 1, len(fs))]
            f_vol = volatility_factor(math.fsum(sims) / len(sims), p)
        else:
            f_vol = 1.0
        sittings = sittings_of([x[0] for x in vs], rp.t_resume_s)
        f_rec = recurrence_factor(sittings)
        spec = prior * f_pat * f_vol * f_rec
        band = "low" if spec < p.spec_low else "high" if spec >= p.spec_high else "mid"
        out[k] = {"visits": len(vs), "specificity": spec, "prior": prior, "f_pattern": f_pat, "f_volatility": f_vol,
                  "f_recurrence": f_rec, "sittings": sittings, "band": band}
    return out

"""LMemM - evidence between visits: what observable things two visits share, and what forbids linking them.

Step 2, PR 2B (docs/adr/0003-activity-inference-design.md). Pure, deterministic Python on a `FeatureSet`
(activity_features.py). This module records EVIDENCE only. It decides nothing: no score, no threshold on whether two
visits belong together, no assignment, no clustering. A shared artifact here is a fact about two visits, not an
instruction to merge them; PR 2C weighs the facts and applies the rules of the ADR.

An `Evidence` record
    type      shared_artifact   the same identity-only artifact (class I): item, URL, file, document id, search query,
                                conversation, chat
              shared_identity   the same PR / issue number (class H1). `detail.match` says how exact it is:
                                  exact           both sides carry the scoped identity (repository + number)
                                  unscoped_number one side is scoped, the other only has "#N" (`detail.scope_match` records
                                                  whether the other side's editor project equals the repository name)
                                  number_only     both sides only have "#N" (no repository known on either side)
              shared_lineage    the same commit (class L): a relationship, never proof of the same work
              shared_namespace  the same soft context (class N): domain, section, repo, project, branch
              shared_tokens     screen-derived words in common, only for pairs sharing at least one rare, non-generic token
              cannot_link       a restriction (class CL): x_pr (same repository, different PR / issue numbers) or x_branch
                                (comparable scopes, different non-integration branches; no dwell condition). `active` is False when the raw
                                override applies (a shared identity, or at least two shared rare tokens); the record is kept so
                                the reason is visible, and `EvidenceSet.cannot_link_pairs()` returns only the active ones.
    source / target   visit ids; `source` starts earlier (ties by visit id). Evidence is symmetric in meaning.
    category / key    the artifact or namespace kind, and its normalised identity
    cls       H1 | I | L | N | T | CL
    detail    read-only mapping of plain values (specificity prior, match kind, shared words, counts, ...)
    provenance  ids of the moments that carried it on each side, how it was observed, and the lower of the two qualities

Bounded work. A key shared by more than `FeatureParams.max_group` visits does not produce every pair: each visit is paired
with the next `max_group - 1` visits that carry it, and the key is listed in `EvidenceSet.truncated`.

Duplicates. Two records with the same (type, source, target, category, key) are one record whose provenance is the union.
Conflicts. `EvidenceSet.conflicts()` lists visit pairs that have both positive evidence (artifact, identity, lineage, content)
and an ACTIVE cannot-link; both stay in the set, the consumer decides.

Time. Evidence carries no timestamp. The only time-valued input is `cannot_link_min_dwell_s` (a declared parameter), compared
with a visit's dwell, so translation changes nothing and joint scaling of timestamps and parameters changes nothing.
"""

import json
import math
from dataclasses import dataclass
from types import MappingProxyType

import activity_features as af

SCHEMA = 1


@dataclass(frozen=True)
class Evidence:
    type: str
    source: str
    target: str
    category: str
    key: str
    cls: str
    active: bool
    detail: MappingProxyType
    source_moments: tuple
    target_moments: tuple
    via: tuple
    quality: float

    @property
    def pair(self):
        return frozenset((self.source, self.target))


@dataclass(frozen=True)
class EvidenceSet:
    items: tuple
    truncated: tuple
    visit_ids: tuple
    schema: int = SCHEMA

    def pair(self, a, b):
        want = frozenset((a, b))
        return tuple(e for e in self.items if e.pair == want)

    def of_type(self, type_):
        return tuple(e for e in self.items if e.type == type_)

    def cannot_link_pairs(self):
        return frozenset(e.pair for e in self.items if e.type == "cannot_link" and e.active)

    def conflicts(self):
        """[(pair, positive evidence types, cannot-link categories)] for pairs with both, in canonical order."""
        pos, neg = {}, {}
        for e in self.items:
            if e.type == "cannot_link":
                if e.active:
                    neg.setdefault(e.pair, set()).add(e.category)
            elif e.type != "shared_namespace":
                pos.setdefault(e.pair, set()).add(e.type)
        order = {v: i for i, v in enumerate(self.visit_ids)}
        keys = sorted(set(pos) & set(neg), key=lambda p: sorted(order[v] for v in p))
        return tuple((tuple(sorted(p, key=order.get)), tuple(sorted(pos[p])), tuple(sorted(neg[p]))) for p in keys)


# ---------------------------------------------------------------- building

class _Sink:
    def __init__(self, order):
        self.order, self.rows = order, {}

    def add(self, a, b, type_, category, key, cls, detail, a_moments, b_moments, via, quality, active=True):
        if a == b:
            return
        if self.order[a] > self.order[b]:
            a, b, a_moments, b_moments = b, a, b_moments, a_moments
        k = (type_, a, b, category, key)
        row = self.rows.get(k)
        if row is None:
            self.rows[k] = dict(cls=cls, detail=dict(detail), am=set(a_moments), bm=set(b_moments), via=set(via), q=quality, active=active)
        else:
            row["am"].update(a_moments)
            row["bm"].update(b_moments)
            row["via"].update(via)
            row["q"] = min(row["q"], quality)
            row["active"] = row["active"] and active


def _group_pairs(members, limit):
    """Pairs of positions in a list already in canonical order; beyond `limit` members, each pairs with its successors."""
    n = len(members)
    if n <= limit:
        return [(i, j) for i in range(n) for j in range(i + 1, n)], False
    return [(i, j) for i in range(n) for j in range(i + 1, min(n, i + limit))], True


def _plain_detail(d):
    out = {}
    for k, v in sorted(d.items()):
        out[k] = list(v) if isinstance(v, (tuple, set, frozenset)) else v
    return out


def build_evidence(feature_set, params, fparams=None):
    """FeatureSet -> EvidenceSet. See the module docstring: facts and restrictions, no decisions."""
    fp = fparams or af.FeatureParams()
    visits = feature_set.visits
    order = {v.visit_id: i for i, v in enumerate(visits)}
    by_id = {v.visit_id: v for v in visits}
    stats = feature_set.stats
    sink, truncated = _Sink(order), set()

    def q(a, b):
        return min(by_id[a].quality, by_id[b].quality)

    # -- artifacts: identity-only, H1 identities, lineage -------------------------------------------
    index = {}
    for v in visits:
        for a in v.artifacts:
            index.setdefault((a.kind, a.key), []).append((v, a))
    for (kind, key), members in sorted(index.items()):
        if len(members) < 2:
            continue
        pairs, cut = _group_pairs(members, fp.max_group)
        if cut:
            truncated.add(f"{kind}:{key}")
        for i, j in pairs:
            (va, aa), (vb, ab) = members[i], members[j]
            detail = {"specificity": aa.specificity}
            if kind in ("pr", "issue"):
                type_, cls, detail = "shared_identity", "H1", {**detail, "match": "exact", "scope": aa.scope}
            elif kind == "ref":
                type_, cls, detail = "shared_identity", "H1", {**detail, "match": "number_only"}
            elif kind == "commit":
                type_, cls = "shared_lineage", "L"
            else:
                type_, cls = "shared_artifact", "I"
            sink.add(va.visit_id, vb.visit_id, type_, kind, key, cls, detail, aa.moments, ab.moments,
                     set(aa.sources) | set(ab.sources), q(va.visit_id, vb.visit_id))

    # scoped PR / issue against an unscoped "#N" of the same number
    scoped, refs = {}, {}
    for v in visits:
        for a in v.artifacts:
            if a.kind in ("pr", "issue"):
                scoped.setdefault(int(a.key.rsplit("#", 1)[1]), []).append((v, a))
            elif a.kind == "ref":
                refs.setdefault(int(a.key.rsplit("#", 1)[1]), []).append((v, a))
    for number in sorted(set(scoped) & set(refs)):
        for va, aa in scoped[number]:
            for vb, ab in refs[number]:
                if va.visit_id == vb.visit_id or any(x.kind == aa.kind and x.key == aa.key for x in vb.artifacts):
                    continue
                project = next((n.key for n in vb.namespaces if n.kind == "project"), None)
                repo_name = aa.scope.rsplit("/", 1)[-1] if aa.scope else None
                sink.add(va.visit_id, vb.visit_id, "shared_identity", aa.kind, aa.key, "H1",
                         {"specificity": aa.specificity, "match": "unscoped_number", "scope": aa.scope,
                          "scope_match": "project_equals_repo" if project and repo_name and project == repo_name else "none"},
                         aa.moments, ab.moments, set(aa.sources) | set(ab.sources), q(va.visit_id, vb.visit_id))

    # -- namespaces: soft context -----------------------------------------------------------------
    nsx = {}
    for v in visits:
        for n in v.namespaces:
            nsx.setdefault((n.kind, n.key, n.scope or ""), []).append((v, n))
    for (kind, key, scope), members in sorted(nsx.items()):
        if len(members) < 2:
            continue
        pairs, cut = _group_pairs(members, fp.max_group)
        if cut:
            truncated.add(f"{kind}:{key}")
        for i, j in pairs:
            (va, na), (vb, nb) = members[i], members[j]
            detail = {"scope": scope or None}
            if kind == "branch":
                detail["integration"] = na.integration
            sink.add(va.visit_id, vb.visit_id, "shared_namespace", kind, key if kind != "branch" else f"{key}", "N", detail,
                     na.moments, nb.moments, set(na.via) | set(nb.via), q(va.visit_id, vb.visit_id))

    # -- content: pairs sharing at least one rare, non-generic token --------------------------------
    rare = {}
    for v in visits:
        rare[v.visit_id] = {t.text for t in v.tokens if not t.generic and stats.classes.get(t.text) == "rare"}
    holders = {}
    for v in visits:
        for t in rare[v.visit_id]:
            holders.setdefault(t, []).append(v)
    candidate = set()
    for t, vs in sorted(holders.items()):
        pairs, cut = _group_pairs(vs, fp.max_group)
        if cut:
            truncated.add(f"token:{t}")
        candidate.update((vs[i].visit_id, vs[j].visit_id) for i, j in pairs)
    bags = {v.visit_id: {t.text: t.weight * stats.idf.get(t.text, 0.0) for t in v.tokens if not t.generic} for v in visits}
    norms = {k: math.sqrt(sum(x * x for x in b.values())) for k, b in bags.items()}
    for a, b in sorted(candidate, key=lambda p: (order[p[0]], order[p[1]])):
        shared = sorted(set(bags[a]) & set(bags[b]))
        shared_rare = sorted(rare[a] & rare[b])
        dot = sum(bags[a][t] * bags[b][t] for t in shared)
        cos = dot / (norms[a] * norms[b]) if norms[a] > 0 and norms[b] > 0 else 0.0
        sink.add(a, b, "shared_tokens", "tokens", "", "T",
                 {"n_shared": len(shared), "n_shared_rare": len(shared_rare), "shared_rare": tuple(shared_rare[:12]),
                  "n_shared_common": sum(1 for t in shared if stats.classes.get(t) == "common"),
                  "cosine": round(cos, 9)},
                 (), (), ("digest",), q(a, b))

    # -- restrictions: x_pr and x_branch, with the raw override baked in as `active` -------------------
    def ids_of(v):
        return {int(a.key.rsplit("#", 1)[1]) for a in v.artifacts if a.kind in ("pr", "issue", "ref")}

    def override(a, b):
        if ids_of(by_id[a]) & ids_of(by_id[b]):
            return "shared_identity"
        if len(rare[a] & rare[b]) >= 2:
            return "shared_rare_tokens"
        return None

    long_enough = {v.visit_id: v.dwell_s >= fp.cannot_link_min_dwell_s for v in visits}
    pr_of = {}
    for v in visits:
        found = {}
        for a in v.artifacts:
            if a.kind in ("pr", "issue"):
                found.setdefault(a.scope, set()).add(int(a.key.rsplit("#", 1)[1]))
        pr_of[v.visit_id] = found
    repos = sorted({r for f in pr_of.values() for r in f})
    for repo in repos:
        group = [v for v in visits if repo in pr_of[v.visit_id] and len(pr_of[v.visit_id][repo]) == 1 and long_enough[v.visit_id]]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                va, vb = group[i], group[j]
                na, nb = next(iter(pr_of[va.visit_id][repo])), next(iter(pr_of[vb.visit_id][repo]))
                if na == nb:
                    continue
                why = override(va.visit_id, vb.visit_id)
                sink.add(va.visit_id, vb.visit_id, "cannot_link", "x_pr", repo, "CL",
                         {"repo": repo, "numbers": tuple(sorted((na, nb))), "override": why},
                         va.moments, vb.moments, ("url",), q(va.visit_id, vb.visit_id), active=why is None)

    def single_branch(v):
        names = {n.key for n in v.namespaces if n.kind == "branch"}
        if len(names) != 1:
            return None
        name = next(iter(names))
        return None if name in af.INTEGRATION_BRANCHES else name

    def comparable(na, nb):
        sa, sb = na.scope or "", nb.scope or ""
        if sa == sb and sa:
            return "same_scope"
        for repo_scope, project_scope in ((sa, sb), (sb, sa)):
            if repo_scope.startswith("repo:") and project_scope.startswith("project:") \
                    and repo_scope.rsplit("/", 1)[-1] == project_scope[len("project:"):]:
                return "project_equals_repo_name"
        return None

    cand = [v for v in visits if single_branch(v)]            # no dwell condition: only x_pr has one
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            va, vb = cand[i], cand[j]
            if single_branch(va) == single_branch(vb):
                continue
            scopes = {comparable(x, y) for x in va.namespaces for y in vb.namespaces if x.kind == "branch" and y.kind == "branch"} - {None}
            if not scopes:
                continue
            why = override(va.visit_id, vb.visit_id)
            moments_a = tuple(sorted({m for n in va.namespaces if n.kind == "branch" for m in n.moments}))
            moments_b = tuple(sorted({m for n in vb.namespaces if n.kind == "branch" for m in n.moments}))
            sink.add(va.visit_id, vb.visit_id, "cannot_link", "x_branch", "|".join(sorted((single_branch(va), single_branch(vb)))), "CL",
                     {"branches": tuple(sorted((single_branch(va), single_branch(vb)))), "scope_match": sorted(scopes)[0], "override": why},
                     moments_a, moments_b, ("url", "window"), q(va.visit_id, vb.visit_id), active=why is None)

    items = []
    for (type_, a, b, category, key), r in sorted(sink.rows.items(), key=lambda kv: (order[kv[0][1]], order[kv[0][2]], kv[0][0], kv[0][3], kv[0][4])):
        items.append(Evidence(type_, a, b, category, key, r["cls"], r["active"], MappingProxyType(_plain_detail(r["detail"])),
                              tuple(sorted(r["am"])), tuple(sorted(r["bm"])), tuple(sorted(r["via"])), round(r["q"], 6)))
    return EvidenceSet(tuple(items), tuple(sorted(truncated)), tuple(v.visit_id for v in visits))


def analyse(prepared, params, fparams=None):
    """Prepared -> (FeatureSet, EvidenceSet): the whole layer in one call."""
    fs = af.extract_features(prepared, params, fparams)
    return fs, build_evidence(fs, params, fparams)


def as_dict(evidence_set):
    return json.loads(json.dumps(af._plain(evidence_set), sort_keys=True))

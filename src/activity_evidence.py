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
              distinct_identity a FACT: two visits carry different PR / issue numbers in one repository (class H1), or different
                                non-integration branches in one scope (class N). It is support-side information, produced for
                                every such pair whether or not a restriction is enforced.
              cannot_link       a restriction (class CL): x_pr (same repository, different PR / issue numbers, each visit with
                                the declared dwell) or x_branch (two REPOSITORY-BACKED branches from code-host URLs, same
                                repository, both non-integration). `active` is False when the raw override applies (a shared
                                identity, or at least two shared rare tokens) or when `x_pr_restricts` is off; the record is kept
                                so the reason is visible, and `EvidenceSet.cannot_link_pairs()` returns only the active ones.
                                A branch read from an editor window title is workspace-derived and never produces a restriction.
    source / target   visit ids; `source` starts earlier (ties by visit id). Evidence is symmetric in meaning.
    category / key    the artifact or namespace kind, and its normalised identity
    cls       H1 | I | L | N | T | CL
    detail    read-only mapping of plain values (specificity prior, match kind, shared words, counts, ...)
    provenance  ids of the moments that carried it on each side, how it was observed, and the lower of the two qualities

Bounded work, for SUPPORT evidence only. A key shared by more than `FeatureParams.max_group` visits does not produce every
pair: each visit is paired with the next `max_group - 1` visits that carry it, and the key is listed in
`EvidenceSet.truncated`. RESTRICTIONS are never limited this way (see `_restrictions`): all pairs are enumerated, or the
build raises ConstraintOverflowError; `EvidenceSet.constraints_complete` and `require_complete_constraints()` make the
guarantee checkable by the consumer.

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


class ConstraintOverflowError(Exception):
    """Too many candidate restriction pairs to enumerate completely. The build FAILS instead of dropping any: a missing
    cannot-link would let a later step put two visits that must be apart into one activity."""


class ConstraintsIncompleteError(Exception):
    """A consumer was handed an EvidenceSet whose restrictions are not known to be complete."""


@dataclass(frozen=True)
class EvidenceSet:
    items: tuple
    truncated: tuple                 # SUPPORT keys that were limited to a successor window; never restrictions
    visit_ids: tuple
    constraints_complete: bool = True
    constraint_pairs_considered: int = 0
    schema: int = SCHEMA

    def require_complete_constraints(self):
        """PR 2C calls this before assigning anything: an incomplete restriction set must stop the run (fail closed)."""
        if not self.constraints_complete:
            raise ConstraintsIncompleteError("the cannot-link set is not known to be complete; no visit may be assigned from it")
        return self

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
            elif e.type not in ("shared_namespace", "distinct_identity"):          # soft context and a difference are not support
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


# ---------------------------------------------------------------- restrictions (never truncated)

def _restrictions(visits, by_id, rare, fp, sink, q):
    """Restrictions and the distinct-identity facts that accompany them. Returns the number of pairs enumerated.

    Completeness guarantee. Every pair that can carry a restriction is enumerated, with no group-size cutoff and no
    successor window (`max_group` applies to support evidence only). If the number of pairs to enumerate would exceed
    `max_restriction_pairs`, the build raises ConstraintOverflowError BEFORE producing anything; an EvidenceSet therefore
    either holds every required cannot-link or does not exist. `EvidenceSet.require_complete_constraints()` is the
    consumer-side check.

    Sources (what may generate what):
      PR / issue numbers   scoped identity from a code-host URL (repository + number)
                           -> `distinct_identity` (always, for different numbers in one repository) and the `x_pr` restriction
                              (dwell condition, raw override; switchable by `x_pr_restricts`)
      branch from URL      repository-backed (`.../tree/<ref>` under a known code host; scope `repo:<host/owner/repo>`)
                           -> `distinct_identity` and the `x_branch` restriction (same repository, different non-integration branches)
      branch from a window title   workspace-derived (scope `project:<name>`); a project / workspace name is not a globally
                           unique repository identity -> `distinct_identity` (support) ONLY, never a restriction
      unscoped "#N", domains, sections, projects   never restrictions
    """
    def ids_of(v):
        return {int(a.key.rsplit("#", 1)[1]) for a in v.artifacts if a.kind in ("pr", "issue", "ref")}

    def override(a, b):
        if ids_of(by_id[a]) & ids_of(by_id[b]):
            return "shared_identity"
        if len(rare[a] & rare[b]) >= 2:
            return "shared_rare_tokens"
        return None

    def check(total, label, size):
        if total > fp.max_restriction_pairs:
            raise ConstraintOverflowError(f"{label}: a group of {size} visits needs {total} restriction pairs, more than "
                                          f"max_restriction_pairs={fp.max_restriction_pairs}; refusing to drop any")

    # PR / issue identities
    pr_of = {}
    for v in visits:
        found = {}
        for a in v.artifacts:
            if a.kind in ("pr", "issue"):
                found.setdefault(a.scope, set()).add(int(a.key.rsplit("#", 1)[1]))
        pr_of[v.visit_id] = {repo: next(iter(nums)) for repo, nums in found.items() if len(nums) == 1}      # exactly one number
    considered = 0
    repos = sorted({r for f in pr_of.values() for r in f})
    for repo in repos:
        group = [v for v in visits if repo in pr_of[v.visit_id]]
        pairs = len(group) * (len(group) - 1) // 2
        check(considered + pairs, f"x_pr {repo}", len(group))
        considered += pairs
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                va, vb = group[i], group[j]
                na, nb = pr_of[va.visit_id][repo], pr_of[vb.visit_id][repo]
                if na == nb:
                    continue
                numbers = tuple(sorted((na, nb)))
                sink.add(va.visit_id, vb.visit_id, "distinct_identity", "pr", repo, "H1", {"repo": repo, "numbers": numbers},
                         va.moments, vb.moments, ("url",), q(va.visit_id, vb.visit_id))
                if min(va.dwell_s, vb.dwell_s) < fp.cannot_link_min_dwell_s:
                    continue
                why = override(va.visit_id, vb.visit_id)
                inactive = "x_pr_not_a_constraint" if not fp.x_pr_restricts else None
                sink.add(va.visit_id, vb.visit_id, "cannot_link", "x_pr", repo, "CL",
                         {"repo": repo, "numbers": numbers, "override": why, "inactive_reason": inactive},
                         va.moments, vb.moments, ("url",), q(va.visit_id, vb.visit_id), active=why is None and inactive is None)

    # branches: one observed, non-integration branch per visit and scope
    def one_branch(v, prefix):
        names = {(n.scope, n.key) for n in v.namespaces if n.kind == "branch" and (n.scope or "").startswith(prefix)}
        if len(names) != 1:
            return None
        scope, name = next(iter(names))
        return None if name in af.INTEGRATION_BRANCHES else (scope, name)

    def branch_moments(v, scope):
        return tuple(sorted({m for n in v.namespaces if n.kind == "branch" and n.scope == scope for m in n.moments}))

    for prefix, via, restrict in (("repo:", ("url",), True), ("project:", ("window",), False)):
        by_scope = {}
        for v in visits:
            ob = one_branch(v, prefix)
            if ob:
                by_scope.setdefault(ob[0], []).append((v, ob[1]))
        for scope in sorted(by_scope):
            group = by_scope[scope]
            pairs = len(group) * (len(group) - 1) // 2
            check(considered + pairs, f"branch {scope}", len(group))
            considered += pairs
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    (va, ba), (vb, bb) = group[i], group[j]
                    if ba == bb:
                        continue
                    names = tuple(sorted((ba, bb)))
                    ma, mb = branch_moments(va, scope), branch_moments(vb, scope)
                    sink.add(va.visit_id, vb.visit_id, "distinct_identity", "branch", "|".join(names), "N",
                             {"scope": scope, "branches": names, "source": "url" if restrict else "window",
                              "restriction": "x_branch" if restrict else "none (workspace-derived branch is not a repository identity)"},
                             ma, mb, via, q(va.visit_id, vb.visit_id))
                    if restrict:
                        why = override(va.visit_id, vb.visit_id)
                        sink.add(va.visit_id, vb.visit_id, "cannot_link", "x_branch", "|".join(names), "CL",
                                 {"scope": scope, "branches": names, "override": why, "inactive_reason": None},
                                 ma, mb, via, q(va.visit_id, vb.visit_id), active=why is None)
    return considered


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

    # -- restrictions and the facts that go with them: ALWAYS complete, never limited by max_group ----------
    considered = _restrictions(visits, by_id, rare, fp, sink, q)

    items = []
    for (type_, a, b, category, key), r in sorted(sink.rows.items(), key=lambda kv: (order[kv[0][1]], order[kv[0][2]], kv[0][0], kv[0][3], kv[0][4])):
        items.append(Evidence(type_, a, b, category, key, r["cls"], r["active"], MappingProxyType(_plain_detail(r["detail"])),
                              tuple(sorted(r["am"])), tuple(sorted(r["bm"])), tuple(sorted(r["via"])), round(r["q"], 6)))
    return EvidenceSet(tuple(items), tuple(sorted(truncated)), tuple(v.visit_id for v in visits), True, considered)


def analyse(prepared, params, fparams=None):
    """Prepared -> (FeatureSet, EvidenceSet): the whole layer in one call."""
    fs = af.extract_features(prepared, params, fparams)
    return fs, build_evidence(fs, params, fparams)


def as_dict(evidence_set):
    return json.loads(json.dumps(af._plain(evidence_set), sort_keys=True))

"""LMemM - deterministic evidence between visits for activity inference (Step 2, PR 2B). Pure Python, offline.

    FeatureSet (activity_features.extract)  ->  build_evidence()  ->  EvidenceSet

Evidence is a MEASUREMENT about a pair of visits: what they share, how specific that is, what restricts its use. It is
never a decision. A shared artifact is evidence, not a merge instruction; nothing here scores, ranks, assigns, clusters
or reads an assignment, and no tier (hard or soft) is chosen: that is PR 2C's scoring rule. The design's rules that
limit what a kind of evidence may do alone (13.4, 12.2) are carried as `restrictions` so PR 2C can apply them without
re-deriving them.

An Evidence item:
    id          "ev-" + hash of its canonical content (same content, same id: duplicates collapse)
    type        same_item | same_artifact | same_pr_issue | same_file_name | version_lineage | same_branch |
                same_workspace_branch | shared_identifiers | shared_terms | same_locator   (polarity "support")
                different_branch | file_in_different_folders                       (polarity "conflict": a cannot-link)
                different_pr_issue | different_workspace_branch                    (polarity "contrast": distinct
                artifacts or observations, NOT a cannot-link; one Activity may span several PRs)
    family      artifact | exact | lexical | locator                                   (design section 4)
    polarity    support | conflict | contrast
    source, target   visit ids, source is the earlier by (t_start, visit_id); evidence is symmetric in meaning
    subject     what supports it: an artifact key, a namespace, a branch, a lineage key, or tokens
    strength    measurements: specificity and band (artifacts), idf and class (tokens), cos_idf / R / x_E (content)
    restrictions    what the design says this kind may NOT do alone (strings, see RESTRICTIONS)
    provenance  fields it came from, the quality factor q of both sides, and gap_s (earlier end to later start)
    lifted, lifted_by   a conflict lifted by a direct shared PR/issue identity (12.2/14.1); lifted conflicts are not
                cannot-links but stay visible

Cannot-link completeness. The cannot-links that constrain assignment are NOT the Evidence items. They live in
`EvidenceSet.constraints`, a ConstraintStore built from per-visit observations in one pass with no size cutoff, and
queried by rule (`is_cannot_link`, `reasons`, `pairs`). Evidence items for constraints are explanations; a group that is
too large to list pair by pair is reported in `report["constraint_explanations_omitted"]` but its constraints still
hold. A store that could not be built completely has `complete == False`, and `EvidenceSet.require_complete()` raises
ConstraintsIncompleteError: PR 2C must call it before it assigns anything.

Which sources may produce what (design 12.2, 14.1):
    repository-backed branch (a git-host URL names host/owner/repo)   same_branch support; different_branch cannot-link
    workspace-derived branch ('(branch)' on an editor workspace name) same_workspace_branch / different_workspace_branch,
                                                                      soft: a workspace name is not a repository identity
    branch with no repository (terminal)                              nothing pairs
    file name in two known, different folders                         file_in_different_folders cannot-link (N1)
    different PR / issue numbers                                      different_pr_issue contrast only

Time. gap_s is a difference of timestamps (translation cancels) and scales with the time parameters; every other field is
independent of time, so translating changes nothing and scaling changes only gap_s, by exactly k.
"""

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType

import activity_features as af

RESTRICTIONS = {
    "version_lineage": ("cannot_assign_alone", "needs_content_corroboration", "cannot_override_cannot_link"),
    "same_pr_issue": ("needs_corroboration_other_than_time",),
    "same_artifact": ("needs_purpose_check", "needs_corroboration_other_than_time"),
    "same_item": ("needs_purpose_check",),
    "same_branch": ("cannot_assign_alone", "cannot_tip_margin_alone"),
    "same_workspace_branch": ("cannot_assign_alone", "cannot_tip_margin_alone", "workspace_name_not_unique"),
    "same_file_name": ("folder_unverified", "cannot_assign_alone"),
    "same_locator": ("cannot_assign_alone", "never_bare_domain"),
    "shared_identifiers": (),
    "shared_terms": ("needs_second_family",),
}
FAMILY = {"same_item": "artifact", "same_artifact": "artifact", "same_pr_issue": "artifact", "same_file_name": "artifact",
          "version_lineage": "exact", "same_branch": "exact", "shared_identifiers": "lexical", "shared_terms": "lexical",
          "same_locator": "locator", "different_branch": "exact", "different_pr_issue": "artifact",
          "file_in_different_folders": "artifact", "same_workspace_branch": "exact",
          "different_workspace_branch": "exact"}
CONFLICTS = frozenset({"different_branch", "file_in_different_folders"})      # cannot-links
CONTRASTS = frozenset({"different_pr_issue", "different_workspace_branch"})   # distinct, never a cannot-link
RULES = ("different_branch", "file_in_different_folders")


@dataclass(frozen=True)
class EvidenceParams:
    """Dimensionless limits, placeholders. A group of visits larger than max_group_visits is a hub in practice and emits no
    pair SUPPORT or CONTRAST evidence; it is counted in EvidenceSet.skipped, never silently dropped. The limit never
    applies to cannot-links: those are held complete in the ConstraintStore whatever the group size."""
    max_group_visits: int = 60


class ConstraintsIncompleteError(RuntimeError):
    """Raised by EvidenceSet.require_complete(): the cannot-links are not complete, so nothing may be assigned."""


@dataclass(frozen=True)
class ConstraintStore:
    """The immutable cannot-link set of 14.1, held per visit instead of per pair so that no size cutoff can drop a
    constraint. Two visits are cannot-linked when a rule in RULES holds between them and no direct shared PR/issue
    identity lifts it. Built from raw features only; reads no assignment."""
    complete: bool
    rules: tuple
    branch_of: object              # visit_id -> (repo, branch)             repository-backed branches only
    file_of: object                # visit_id -> (name, folder)             known folders only
    prs: object                    # visit_id -> frozenset of PR / issue artifact keys
    order: object                  # visit_id -> position in canonical order

    def reasons(self, a, b):
        """Every rule that separates a and b: [{'rule', 'subject', 'lifted', 'lifted_by'}], sorted."""
        out = []
        ba, bb = self.branch_of.get(a), self.branch_of.get(b)
        if ba and bb and ba[0] == bb[0] and ba[1] != bb[1]:
            out.append(("different_branch", {"repo": ba[0], "branches": sorted((ba[1], bb[1]))}))
        fa, fb = self.file_of.get(a), self.file_of.get(b)
        if fa and fb and fa[0] == fb[0] and fa[1] != fb[1]:
            out.append(("file_in_different_folders", {"name": fa[0], "folders": sorted((fa[1], fb[1]))}))
        shared = sorted(self.prs.get(a, frozenset()) & self.prs.get(b, frozenset()))
        return [{"rule": r, "subject": subj, "lifted": bool(shared), "lifted_by": shared[0] if shared else None}
                for r, subj in out]

    def is_cannot_link(self, a, b):
        return any(not r["lifted"] for r in self.reasons(a, b))

    def pairs(self):
        """Sorted (source, target) pairs that are cannot-linked. Lazy; use is_cannot_link for point queries."""
        seen = set()
        groups = {}
        for v, (repo, _) in self.branch_of.items():
            groups.setdefault(("b", repo), []).append(v)
        for v, (name, _) in self.file_of.items():
            groups.setdefault(("f", name), []).append(v)
        for k in sorted(groups):
            ids = sorted(groups[k], key=self.order.get)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if (ids[i], ids[j]) not in seen and self.is_cannot_link(ids[i], ids[j]):
                        seen.add((ids[i], ids[j]))
        return iter(sorted(seen, key=lambda ab: (self.order[ab[0]], self.order[ab[1]])))

    def count_observations(self):
        return {"branch": len(self.branch_of), "file": len(self.file_of)}


def build_constraints(fs):
    """FeatureSet -> ConstraintStore, one pass over the visits, no cutoff. `complete` is True only if every visit was
    examined for every rule in RULES."""
    order = {f.visit_id: i for i, f in enumerate(fs.visits)}
    branch_of, file_of, prs = {}, {}, {}
    for f in fs.visits:
        if f.branch and f.branch.get("scope") == "repo":
            branch_of[f.visit_id] = (f.branch["repo"], f.branch["name"])
        for a in f.artifacts:
            if a["type"] == "file" and a.get("folder_known"):
                file_of[f.visit_id] = (a["name"], a["folder"])
        keys = frozenset(a["key"] for a in f.artifacts if a["type"] in ("pr", "issue"))
        if keys:
            prs[f.visit_id] = keys
    return ConstraintStore(len(order) == len(fs.visits), RULES, MappingProxyType(branch_of), MappingProxyType(file_of),
                           MappingProxyType(prs), MappingProxyType(order))


@dataclass(frozen=True)
class Evidence:
    id: str
    type: str
    family: str
    polarity: str
    source: str
    target: str
    subject: dict
    strength: dict
    restrictions: tuple
    provenance: dict
    lifted: bool = False
    lifted_by: object = None

    def pair(self):
        return (self.source, self.target)

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["restrictions"] = list(self.restrictions)
        return d


@dataclass(frozen=True)
class EvidenceSet:
    items: tuple                        # Evidence, sorted by (source order, target order, type, subject)
    skipped: dict                       # support/contrast groups over max_group_visits -> number of visits
    report: dict
    constraints: object                 # ConstraintStore: the complete cannot-links
    skipped_members: object = None      # label -> visit ids of each skipped group (who the omitted pair evidence concerns)

    def for_pair(self, a, b):
        key = tuple(sorted((a, b)))
        return tuple(e for e in self.items if tuple(sorted(e.pair())) == key)

    def by_type(self, type_):
        return tuple(e for e in self.items if e.type == type_)

    def require_complete(self):
        """PR 2C calls this before it assigns anything. Fails closed."""
        if not self.constraints.complete:
            raise ConstraintsIncompleteError("cannot-links are incomplete: do not assign")
        return self.constraints

    @property
    def support_complete(self):
        """False when some groups were too large to list pair evidence for (see `skipped`); constraints are unaffected."""
        return not self.skipped

    def cannot_links(self):
        """The cannot-link pairs as frozensets, from the ConstraintStore (never from the listed items)."""
        return frozenset(frozenset(p) for p in self.require_complete().pairs())

    def conflicting_pairs(self):
        """Pairs that carry both support and a cannot-link rule (kept explicit; PR 2C decides what that means)."""
        sup = {frozenset(e.pair()) for e in self.items if e.polarity == "support"}
        rules = {frozenset(p) for p in self.constraints.pairs()} | {frozenset(e.pair()) for e in self.items if e.polarity == "conflict"}
        return frozenset(sup & rules)                                 # includes conflicts that were lifted

    def to_dict(self):
        return {"items": [e.to_dict() for e in self.items], "skipped": dict(sorted(self.skipped.items())),
                "report": self.report}


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ev_id(type_, source, target, subject):
    h = hashlib.sha256(canonical_json([type_, source, target, subject]).encode("utf-8")).hexdigest()[:12]
    return "ev-" + h


def build_evidence(fs, eparams=None):
    """FeatureSet -> EvidenceSet. Candidate pairs come from inverted indexes (shared artifact, namespace, lineage, branch,
    identifier, rare token), so cost follows the evidence, not N squared. Output order and ids are deterministic."""
    ep = eparams or EvidenceParams()
    p, corpus = fs.params, fs.corpus
    feats = {f.visit_id: f for f in fs.visits}
    order = {f.visit_id: i for i, f in enumerate(fs.visits)}          # visits are sorted by (t_start, visit_id)
    out, skipped, omitted, skipped_members = {}, {}, {}, {}
    store = build_constraints(fs)

    def emit(type_, a, b, subject, strength, fields, lifted=False, lifted_by=None):
        if order[a] > order[b]:
            a, b = b, a
        fa, fb = feats[a], feats[b]
        eid = _ev_id(type_, a, b, subject)
        if eid in out:
            return
        gap = fb.t_start - fa.t_end
        out[eid] = Evidence(
            id=eid, type=type_, family=FAMILY[type_], polarity="conflict" if type_ in CONFLICTS else "contrast" if type_ in CONTRASTS else "support",
            source=a, target=b, subject=subject, strength=strength, restrictions=RESTRICTIONS.get(type_, ()),
            provenance={"fields": sorted(fields), "q_source": fa.quality["q"], "q_target": fb.quality["q"], "gap_s": gap},
            lifted=lifted, lifted_by=lifted_by)

    def groups(index, label, explanation=False):
        """Groups worth listing pair by pair. Too large: support and contrast groups go to `skipped`; for a cannot-link
        rule (explanation=True) only the pair LISTING is omitted, because the ConstraintStore already holds the constraint."""
        for k in sorted(index):
            ids = sorted(set(index[k]), key=order.get)
            if len(ids) < 2:
                continue
            if len(ids) > ep.max_group_visits:
                (omitted if explanation else skipped)[f"{label}:{k}"] = len(ids)
                if not explanation:
                    skipped_members[f"{label}:{k}"] = tuple(ids)
                continue
            yield k, ids

    def pairs(ids):
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                yield ids[i], ids[j]

    spec = corpus.artifacts

    # ---- artifacts: item, document/page/file/chat, PR and issue identity
    by_art = {}
    for f in fs.visits:
        for a in f.artifacts:
            by_art.setdefault((a["type"], a["key"]), []).append(f.visit_id)
    for (t, key), ids in groups(by_art, "artifact"):
        s = spec[(t, key)]
        a0 = next(a for a in feats[ids[0]].artifacts if (a["type"], a["key"]) == (t, key))
        strength = {"specificity": s["specificity"], "band": s["band"], "id_bearing": a0["id_bearing"],
                    "hub_pattern": a0["hub_pattern"], "sittings": s["sittings"]}
        for x, y in pairs(ids):
            fields = set(a0["fields"])
            if t == "item":
                emit("same_item", x, y, {"artifact": key}, strength, fields)
            elif t in ("pr", "issue"):
                emit("same_pr_issue", x, y, {"artifact_type": t, "artifact": key}, strength, fields)
            else:
                emit("same_artifact", x, y, {"artifact_type": t, "artifact": key}, strength, fields)

    # ---- files: same name, folder not both known and equal -> weak evidence; both known and different -> conflict
    by_name = {}
    for f in fs.visits:
        for a in f.artifacts:
            if a["type"] == "file":
                by_name.setdefault(a["name"], []).append(f.visit_id)
    for name, ids in groups(by_name, "file_name", explanation=True):
        for x, y in pairs(ids):
            fa = next(a for a in feats[x].artifacts if a["type"] == "file" and a["name"] == name)
            fb = next(a for a in feats[y].artifacts if a["type"] == "file" and a["name"] == name)
            if fa["key"] == fb["key"]:
                continue                                              # same artifact: already evidence
            if fa["folder_known"] and fb["folder_known"]:
                emit("file_in_different_folders", x, y, {"name": name, "folders": sorted((fa["folder"], fb["folder"]))},
                     {}, {"target", "window"})
            else:
                emit("same_file_name", x, y, {"name": name}, {"folder_known": [fa["folder_known"], fb["folder_known"]]},
                     {"target"})

    # ---- namespaces: locator / repo / folder prefix shared by different pages
    by_ns = {}
    for f in fs.visits:
        for t, v in f.namespaces:
            if t != "domain":                                         # a bare domain is never evidence
                by_ns.setdefault((t, v), []).append(f.visit_id)
    for (t, v), ids in groups(by_ns, "namespace"):
        for x, y in pairs(ids):
            same_art = {(a["type"], a["key"]) for a in feats[x].artifacts if a["type"] != "item"} & \
                       {(a["type"], a["key"]) for a in feats[y].artifacts if a["type"] != "item"}
            if same_art:
                continue                                              # the same page: artifact evidence, not locator
            emit("same_locator", x, y, {"namespace": t, "value": v}, {}, {"url" if t != "folder" else "window"})

    # ---- git. Repository-backed branches: support and cannot-link. Workspace-derived: soft only. PR/issue: contrast only.
    by_branch, by_ws = {}, {}
    for f in fs.visits:
        br = f.branch
        if br and br["scope"] == "repo":
            by_branch.setdefault(br["repo"], []).append(f.visit_id)
        elif br and br["scope"] == "workspace":
            by_ws.setdefault(br["workspace"], []).append(f.visit_id)
    for repo, ids in groups(by_branch, "repo_branch", explanation=True):
        for x, y in pairs(ids):
            bx, by_ = feats[x].branch["name"], feats[y].branch["name"]
            if bx == by_:
                emit("same_branch", x, y, {"repo": repo, "branch": bx}, {}, {"window", "url"})
            else:
                emit("different_branch", x, y, {"repo": repo, "branches": sorted((bx, by_))}, {}, {"window", "url"})
    for ws, ids in groups(by_ws, "workspace_branch"):
        for x, y in pairs(ids):
            bx, by_ = feats[x].branch["name"], feats[y].branch["name"]
            if bx == by_:
                emit("same_workspace_branch", x, y, {"workspace": ws, "branch": bx}, {}, {"window"})
            else:
                emit("different_workspace_branch", x, y, {"workspace": ws, "branches": sorted((bx, by_))}, {}, {"window"})
    by_repo_pr = {}
    for f in fs.visits:
        for a in f.artifacts:
            if a["type"] in ("pr", "issue"):
                by_repo_pr.setdefault(a["repo"], []).append(f.visit_id)
    for repo, ids in groups(by_repo_pr, "repo_pr"):
        for x, y in pairs(ids):
            kx = {(a["type"], a["number"]) for a in feats[x].artifacts if a["type"] in ("pr", "issue") and a["repo"] == repo}
            ky = {(a["type"], a["number"]) for a in feats[y].artifacts if a["type"] in ("pr", "issue") and a["repo"] == repo}
            if kx and ky and not (kx & ky):
                emit("different_pr_issue", x, y, {"repo": repo, "refs": sorted(f"{t}#{n}" for t, n in kx | ky)}, {},
                     {"url", "window"})

    # ---- version lineage: same stem, ext and scope, different names; corroboration is measured, never decided
    by_lin = {}
    for f in fs.visits:
        if f.lineage:
            L = f.lineage
            by_lin.setdefault((L["scope"], L["stem"], L["ext"]), []).append(f.visit_id)
    for (scope, stem, ext), ids in groups(by_lin, "lineage"):
        for x, y in pairs(ids):
            if feats[x].lineage["name"] == feats[y].lineage["name"]:
                continue                                              # same name: the same artifact, not a version
            lin_tokens = frozenset(af._words(stem, p)) | frozenset(af._words(feats[x].lineage["name"], p)) | \
                frozenset(af._words(feats[y].lineage["name"], p)) | feats[x].key_tokens | feats[y].key_tokens | \
                frozenset(af._words(scope.replace("|", " ").replace(".", " "), p))     # app and folder words too
            corr = af.content_similarity(feats[x], feats[y], corpus, p, exclude=lin_tokens)
            emit("version_lineage", x, y, {"stem": stem, "ext": ext, "scope": scope,
                                           "names": sorted((feats[x].lineage["name"], feats[y].lineage["name"]))},
                 {"corroboration": {"cos_idf": corr["cos_idf"], "rare_shared": corr["rare_shared"], "x_e": corr["x_e"]},
                  "excluded_tokens": sorted(lin_tokens)}, {"target", "window"})

    # ---- lexical: shared specific identifiers, and shared rare terms with the content measurement
    by_ident = {}
    for f in fs.visits:
        for t in f.identifiers:
            by_ident.setdefault(t, []).append(f.visit_id)
    shared_ids = {}
    for t, ids in groups(by_ident, "identifier"):
        for x, y in pairs(ids):
            shared_ids.setdefault((x, y), []).append(t)
    for (x, y), toks in sorted(shared_ids.items(), key=lambda kv: (order[kv[0][0]], order[kv[0][1]])):
        items = [{"token": t, "df": corpus.df[t], "class": corpus.cls[t], "idf": corpus.idf[t],
                  "in_key": t in feats[x].key_tokens or t in feats[y].key_tokens} for t in sorted(toks)]
        emit("shared_identifiers", x, y, {"identifiers": [i["token"] for i in items]}, {"items": items, "count": len(items)},
             {"digest.identifiers"})
    by_rare = {}
    for f in fs.visits:
        for t, _ in f.bag:
            if corpus.cls.get(t) == "rare":
                by_rare.setdefault(t, []).append(f.visit_id)
    cand = set()
    for t, ids in groups(by_rare, "rare_token"):
        cand.update(pairs(ids))
    for x, y in sorted(cand, key=lambda xy: (order[xy[0]], order[xy[1]])):
        c = af.content_similarity(feats[x], feats[y], corpus, p)
        items = [{"token": t, "df": corpus.df[t], "idf": corpus.idf[t],
                  "in_key": t in feats[x].key_tokens or t in feats[y].key_tokens} for t in c["rare_tokens"]]
        emit("shared_terms", x, y, {"tokens": c["rare_tokens"]},
             {"cos_idf": c["cos_idf"], "rare_shared": c["rare_shared"], "x_e": c["x_e"], "items": items},
             {"digest.terms", "digest.identifiers", "digest.lines", "window", "target", "url"})

    # ---- a direct shared PR/issue identity lifts a listed conflict; decided from the complete store, not from listed items
    final = {}
    for eid, e in out.items():
        if e.polarity == "conflict":
            lift = [r for r in store.reasons(e.source, e.target) if r["rule"] == e.type and r["lifted"]]
            if lift:
                e = Evidence(**{**{k: getattr(e, k) for k in e.__dataclass_fields__}, "lifted": True,
                                "lifted_by": lift[0]["lifted_by"]})
        final[eid] = e
    items = tuple(sorted(final.values(), key=lambda e: (order[e.source], order[e.target], e.type, canonical_json(e.subject))))
    counts = {}
    for e in items:
        counts[e.type] = counts.get(e.type, 0) + 1
    n_links = sum(1 for _ in store.pairs())
    return EvidenceSet(items, skipped, {
        "evidence": len(items), "by_type": dict(sorted(counts.items())), "cannot_links": n_links,
        "constraints_complete": store.complete, "constraint_rules": list(store.rules),
        "constraint_observations": store.count_observations(),
        "constraint_explanations_omitted": dict(sorted(omitted.items())),
        "support_complete": not skipped, "skipped_groups": len(skipped)}, store,
        {k: v for k, v in sorted(skipped_members.items())})


def analyse(prepared, params=None, replay_params=None, eparams=None):
    """Prepared -> (FeatureSet, EvidenceSet). The only entry PR 2C needs."""
    fs = af.extract(prepared, params, replay_params)
    return fs, build_evidence(fs, eparams)

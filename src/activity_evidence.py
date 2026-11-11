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
                shared_identifiers | shared_terms | same_locator                     (polarity "support")
                different_branch | different_pr_issue | file_in_different_folders    (polarity "conflict", a cannot-link)
    family      artifact | exact | lexical | locator                                   (design section 4)
    polarity    support | conflict
    source, target   visit ids, source is the earlier by (t_start, visit_id); evidence is symmetric in meaning
    subject     what supports it: an artifact key, a namespace, a branch, a lineage key, or tokens
    strength    measurements: specificity and band (artifacts), idf and class (tokens), cos_idf / R / x_E (content)
    restrictions    what the design says this kind may NOT do alone (strings, see RESTRICTIONS)
    provenance  fields it came from, the quality factor q of both sides, and gap_s (earlier end to later start)
    lifted, lifted_by   a conflict lifted by a direct shared PR/issue identity (12.2/14.1); lifted conflicts are not
                cannot-links but stay visible

Time. gap_s is a difference of timestamps (translation cancels) and scales with the time parameters; every other field is
independent of time, so translating changes nothing and scaling changes only gap_s, by exactly k.
"""

import hashlib
import json
from dataclasses import dataclass

import activity_features as af

RESTRICTIONS = {
    "version_lineage": ("cannot_assign_alone", "needs_content_corroboration", "cannot_override_cannot_link"),
    "same_pr_issue": ("needs_corroboration_other_than_time",),
    "same_artifact": ("needs_purpose_check", "needs_corroboration_other_than_time"),
    "same_item": ("needs_purpose_check",),
    "same_branch": ("cannot_assign_alone", "cannot_tip_margin_alone"),
    "same_file_name": ("folder_unverified", "cannot_assign_alone"),
    "same_locator": ("cannot_assign_alone", "never_bare_domain"),
    "shared_identifiers": (),
    "shared_terms": ("needs_second_family",),
}
FAMILY = {"same_item": "artifact", "same_artifact": "artifact", "same_pr_issue": "artifact", "same_file_name": "artifact",
          "version_lineage": "exact", "same_branch": "exact", "shared_identifiers": "lexical", "shared_terms": "lexical",
          "same_locator": "locator", "different_branch": "exact", "different_pr_issue": "artifact",
          "file_in_different_folders": "artifact"}
CONFLICTS = frozenset({"different_branch", "different_pr_issue", "file_in_different_folders"})


@dataclass(frozen=True)
class EvidenceParams:
    """Dimensionless limits, placeholders. A group of visits larger than max_group_visits is a hub in practice and emits
    no pair evidence; it is counted in EvidenceSet.skipped, never silently dropped."""
    max_group_visits: int = 60


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
    skipped: dict                       # group label -> number of visits, for groups over max_group_visits
    report: dict

    def for_pair(self, a, b):
        key = tuple(sorted((a, b)))
        return tuple(e for e in self.items if tuple(sorted(e.pair())) == key)

    def by_type(self, type_):
        return tuple(e for e in self.items if e.type == type_)

    def cannot_links(self):
        """The immutable constraint store (14.1): frozenset pairs from conflicts that were not lifted."""
        return frozenset(frozenset(e.pair()) for e in self.items if e.polarity == "conflict" and not e.lifted)

    def conflicting_pairs(self):
        """Pairs that carry both support and a conflict (kept explicit; PR 2C decides what that means)."""
        sup = {frozenset(e.pair()) for e in self.items if e.polarity == "support"}
        con = {frozenset(e.pair()) for e in self.items if e.polarity == "conflict"}
        return frozenset(sup & con)

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
    out, skipped = {}, {}

    def emit(type_, a, b, subject, strength, fields, lifted=False, lifted_by=None):
        if order[a] > order[b]:
            a, b = b, a
        fa, fb = feats[a], feats[b]
        eid = _ev_id(type_, a, b, subject)
        if eid in out:
            return
        gap = fb.t_start - fa.t_end
        out[eid] = Evidence(
            id=eid, type=type_, family=FAMILY[type_], polarity="conflict" if type_ in CONFLICTS else "support",
            source=a, target=b, subject=subject, strength=strength, restrictions=RESTRICTIONS.get(type_, ()),
            provenance={"fields": sorted(fields), "q_source": fa.quality["q"], "q_target": fb.quality["q"], "gap_s": gap},
            lifted=lifted, lifted_by=lifted_by)

    def groups(index, label):
        for k in sorted(index):
            ids = sorted(set(index[k]), key=order.get)
            if len(ids) < 2:
                continue
            if len(ids) > ep.max_group_visits:
                skipped[f"{label}:{k}"] = len(ids)
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
    for name, ids in groups(by_name, "file_name"):
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

    # ---- git: branch and PR/issue conflicts (14.1 constraint store, from raw features only)
    by_branch = {}
    for f in fs.visits:
        if f.branch and f.branch["repo"]:
            by_branch.setdefault(f.branch["repo"], []).append(f.visit_id)
    for repo, ids in groups(by_branch, "repo_branch"):
        for x, y in pairs(ids):
            bx, by_ = feats[x].branch["name"], feats[y].branch["name"]
            if bx == by_:
                emit("same_branch", x, y, {"repo": repo, "branch": bx}, {}, {"window", "url"})
            else:
                emit("different_branch", x, y, {"repo": repo, "branches": sorted((bx, by_))}, {}, {"window", "url"})
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

    # ---- a direct shared PR/issue identity lifts conflicts between the same two visits (14.1)
    pr_pairs = {}
    for e in out.values():
        if e.type == "same_pr_issue":
            pr_pairs.setdefault(frozenset(e.pair()), e.subject["artifact"])
    final = {}
    for eid, e in out.items():
        if e.polarity == "conflict" and frozenset(e.pair()) in pr_pairs:
            e = Evidence(**{**{k: getattr(e, k) for k in e.__dataclass_fields__}, "lifted": True,
                            "lifted_by": pr_pairs[frozenset(e.pair())]})
        final[eid] = e
    items = tuple(sorted(final.values(), key=lambda e: (order[e.source], order[e.target], e.type, canonical_json(e.subject))))
    counts = {}
    for e in items:
        counts[e.type] = counts.get(e.type, 0) + 1
    return EvidenceSet(items, skipped, {"evidence": len(items), "by_type": dict(sorted(counts.items())),
                                        "cannot_links": len(EvidenceSet(items, {}, {}).cannot_links()),
                                        "skipped_groups": len(skipped)})


def analyse(prepared, params=None, replay_params=None, eparams=None):
    """Prepared -> (FeatureSet, EvidenceSet). The only entry PR 2C needs."""
    fs = af.extract(prepared, params, replay_params)
    return fs, build_evidence(fs, eparams)

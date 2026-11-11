"""LMemM - deterministic, offline activity inference (Step 2, PR 2C). Pure Python: no model, no CPU judge, no I/O.

    Prepared -> activity_evidence.analyse() -> (FeatureSet, EvidenceSet) -> score -> assign (3 passes) -> Result

`infer(prepared, params)` is the interface of activity_contract; `activity_contract.replay(infer, records, params)` is the
records-in entry. Everything here is a function of the evidence; no assignment is ever read back into a feature, and
specificity is the raw-feature number computed in PR 2B.

THE SCORING POLICY IS ADDITIVE AND PROVISIONAL. All weights and thresholds live in ScoringConfig below, are placeholders
chosen to be conservative and readable, and are UNCALIBRATED: they must be tuned on real labelled sessions, not on the
synthetic fixtures (which expose contradictions, nothing more). The three decision thresholds are the design's: soft
assignment 4.0, weak-evidence floor 2.0, minimum winning margin 2.0. Seconds-valued rules come from ReplayParams.

Score of a visit V against a candidate activity A (members earlier than V):
    E   evidence  = sum over evidence FAMILIES (artifact, exact, lexical, locator) of the strongest contribution any
        member offers in that family, plus a contrast penalty. Correlated signals of one family are not added.
    C   context   = time / neighbour / app adjacency to A's members, decayed; only in Pass 2; capped; never evidence.
    S = E + C
Rules (each is enforced in code and covered by a test):
    - cannot-links are checked first (EvidenceSet.require_complete() then the ConstraintStore); a blocked candidate is
      never assigned to and never competes in a margin.
    - an assignment needs S >= 4.0, E >= 2.0 (weak floor), >= 2 independent families, and at least one PRIMARY family
      (an artifact, identifier or corroborated lineage link; same branch, locator, file name without a verified folder
      and shared terms alone are never primary), and a winning margin >= 2.0 over the next candidate, measured on TIP
      evidence only (same branch, workspace branch, locator and context never tip a margin).
    - 2.0 <= S below the bar, or a rule failing, is `tentative`; a margin too small is `ambiguous`.
    - different PR / issue numbers are CONTRAST: a penalty unless a member also shares the PR; never a prohibition.
Three passes: 1 anchors (evidence only, no time, no neighbours); 2 context (earlier assignments only); seeds; 3
consolidation (evidence only, mutual best, margin, cannot-links rechecked after every merge). Assignments are additive.

Incomplete support evidence (EvidenceSet.support_complete is False): the omitted pair listings are NOT evidence of
absence. A visit that belongs to an omitted group may still use explicitly listed positive evidence, but whenever the
omitted pairs could change the decision (a competing activity it might also belong to, a seed or a "no candidate"
outcome) it abstains: `ambiguous` with reason `shared_artifact`. No bound is changed and no omitted pair is enumerated.

Incomplete cannot-links: ConstraintsIncompleteError propagates and no result exists.
"""

import math
from dataclasses import dataclass

import activity_contract as ac
import activity_evidence as ev
import activity_features as af

EPS = 1e-9


@dataclass(frozen=True)
class ScoringConfig:
    """PROVISIONAL, UNCALIBRATED. Dimensionless. Nothing else in this module holds a weight or a threshold."""
    assign_threshold: float = 4.0            # soft assignment threshold (design 4-5)
    weak_floor: float = 2.0                  # evidence below this is treated as absent; [floor, threshold) is tentative
    min_margin: float = 2.0                  # minimum winning margin over the next candidate (tip evidence only)
    # artifact family (times the factor of the artifact's specificity BAND from PR 2B; the continuous number is not used
    # because, on a small corpus, its breadth factors make it vanish for an artifact that is plainly the same page)
    band_high: float = 1.0
    band_mid: float = 0.75
    band_low: float = 0.5
    w_same_pr_issue: float = 4.0
    w_same_artifact: float = 3.0
    w_same_item: float = 2.5
    w_same_file_name: float = 1.0            # folder unverified: never primary
    # exact family
    w_version_lineage: float = 3.0           # times x_E (content corroboration); never primary without corroboration
    w_same_branch: float = 1.0               # soft, never primary, never tips a margin
    w_same_workspace_branch: float = 0.5     # weaker still
    # locator family
    w_same_locator: float = 1.0              # never primary, never tips a margin; a bare domain is not produced at all
    # lexical family
    w_identifier_each: float = 1.0
    w_identifier_cap: float = 3.0
    w_shared_terms: float = 3.0              # times x_E; needs a second family, so never primary
    # contrast
    contrast_pr_issue: float = -2.0
    contrast_workspace_branch: float = -0.5
    # context (Pass 2 only); the cap is below the assignment threshold so context can never decide alone
    ctx_adjacent: float = 2.0
    ctx_neighbour: float = 1.0
    ctx_same_app: float = 0.5
    ctx_cap: float = 2.5
    # independence
    min_family_value: float = 0.5            # a family counts as an independent family from this contribution
    min_families: int = 2
    # reporting only
    confidence_full: float = 8.0
    shift_confidence_factor: float = 0.5


# ---------------------------------------------------------------- one evidence item -> (family, value, primary, tip)

def _contribution(e, cfg):
    t, s = e.type, e.strength
    spec = {"high": cfg.band_high, "mid": cfg.band_mid}.get(s.get("band"), cfg.band_low) if isinstance(s, dict) else 0.0
    if t == "same_pr_issue":
        return "artifact", cfg.w_same_pr_issue * spec, True, True
    if t == "same_artifact":
        return "artifact", cfg.w_same_artifact * spec, True, True
    if t == "same_item":
        return "artifact", cfg.w_same_item * spec, True, True
    if t == "same_file_name":
        return "artifact", cfg.w_same_file_name, False, True
    if t == "version_lineage":
        return "exact", cfg.w_version_lineage * s["corroboration"]["x_e"], s["corroboration"]["x_e"] > 0, True
    if t == "same_branch":
        return "exact", cfg.w_same_branch, False, False
    if t == "same_workspace_branch":
        return "exact", cfg.w_same_workspace_branch, False, False
    if t == "same_locator":
        return "locator", cfg.w_same_locator, False, False
    if t == "shared_identifiers":
        return "lexical", min(cfg.w_identifier_cap, cfg.w_identifier_each * s["count"]), True, True
    if t == "shared_terms":
        return "lexical", cfg.w_shared_terms * s["x_e"], False, True
    return None


def _contrast(e, cfg):
    return {"different_pr_issue": cfg.contrast_pr_issue, "different_workspace_branch": cfg.contrast_workspace_branch}.get(e.type)


class _Profile:
    """What the listed evidence says about one pair of visits."""
    __slots__ = ("fams", "contrast", "pr_link", "purpose_conflict", "ev_ids")

    def __init__(self):
        self.fams = {}              # family -> [value, tip_value, primary_value]
        self.contrast = 0.0
        self.pr_link = False
        self.purpose_conflict = False
        self.ev_ids = []


class _Score:
    """Evidence of a visit against a unit, from the profiles of its member pairs."""
    __slots__ = ("E", "tip", "families", "primary", "contrast", "purpose_conflict", "ev_ids", "types")

    def __init__(self, E, tip, families, primary, contrast, purpose_conflict, ev_ids):
        self.E, self.tip, self.families, self.primary = E, tip, families, primary
        self.contrast, self.purpose_conflict, self.ev_ids = contrast, purpose_conflict, ev_ids


def _combine(profiles, cfg):
    fams, contrast, pr_link, pc, ids = {}, 0.0, False, False, []
    for p in profiles:
        for f, (v, tv, pv) in p.fams.items():
            cur = fams.setdefault(f, [0.0, 0.0, 0.0])
            cur[0], cur[1], cur[2] = max(cur[0], v), max(cur[1], tv), max(cur[2], pv)
        contrast = min(contrast, p.contrast)
        pr_link = pr_link or p.pr_link
        pc = pc or p.purpose_conflict
        ids.extend(p.ev_ids)
    penalty = 0.0 if pr_link else contrast
    E = max(0.0, sum(v[0] for v in fams.values()) + penalty)
    tip = max(0.0, sum(v[1] for v in fams.values()) + penalty)
    names = sorted(f for f, v in fams.items() if v[0] >= cfg.min_family_value)
    primary = any(v[2] >= cfg.min_family_value for v in fams.values())
    return _Score(E, tip, names, primary, penalty, pc, sorted(set(ids)))


# ---------------------------------------------------------------- the engine

class _Engine:
    def __init__(self, fs, es, params, cfg):
        self.fs, self.es, self.rp, self.cfg = fs, es, params, cfg
        self.visits = fs.visits
        self.f = {v.visit_id: v for v in fs.visits}
        self.order = {v.visit_id: i for i, v in enumerate(fs.visits)}
        self.store = es.require_complete()                       # fail closed before anything is decided
        self.eligible = [v.visit_id for v in fs.visits if not v.glance and v.substance["ok"]]
        self.elig = set(self.eligible)
        self.pair_ev = {}
        for e in es.items:
            if e.polarity in ("support", "contrast"):
                self.pair_ev.setdefault((e.source, e.target), []).append(e)
        self.prof = {}
        self.shares = {}                                          # visit -> visits sharing an omitted (skipped) group
        for members in (es.skipped_members or {}).values():
            for v in members:
                self.shares.setdefault(v, set()).update(m for m in members if m != v)
        self.uid_of, self.members, self.assigned = {}, {}, set()
        for v in self.eligible:
            self.uid_of[v] = v
            self.members[v] = [v]
        self.reason = {}                                          # visit -> decision record for non-assigned visits
        self.decided_by = {}

    # ------------------------------------------------------------ pair evidence
    def profile(self, a, b):
        if self.order[a] > self.order[b]:
            a, b = b, a
        key = (a, b)
        if key in self.prof:
            return self.prof[key]
        p = _Profile()
        evs = sorted(self.pair_ev.get(key, ()), key=lambda e: (e.type, e.id))
        corroborated = any(e.type in ("shared_identifiers", "shared_terms") for e in evs)
        for e in evs:
            c = _contrast(e, self.cfg)
            if c is not None:
                p.contrast = min(p.contrast, c)
                p.ev_ids.append(e.id)
                continue
            x = _contribution(e, self.cfg)
            if x is None:
                continue
            fam, val, primary, tip = x
            if e.type in ("same_item", "same_artifact") and not corroborated and self._purposes_differ(a, b):
                p.purpose_conflict = True
                continue                                          # same thing, different purposes: not a link
            if e.type == "same_pr_issue":
                p.pr_link = True
            cur = p.fams.setdefault(fam, [0.0, 0.0, 0.0])
            cur[0] = max(cur[0], val)
            cur[1] = max(cur[1], val if tip else 0.0)
            cur[2] = max(cur[2], val if primary else 0.0)
            p.ev_ids.append(e.id)
        self.prof[key] = p
        return p

    def _purposes_differ(self, a, b):
        fa, fb = self.f[a], self.f[b]
        mb = self.fs.params.min_bag_tokens
        if fa.weak_evidence or fb.weak_evidence or len(fa.bag) < mb or len(fb.bag) < mb:
            return False                                          # too little content to compare: no claim either way
        c = af.content_similarity(fa, fb, self.fs.corpus, self.fs.params)
        return c["cos_idf"] < self.fs.params.cos_floor and not c["rare_shared"]

    def score(self, v, unit_members):
        return _combine([self.profile(v, m) for m in unit_members if self._has(v, m)], self.cfg)

    def _has(self, a, b):
        key = (a, b) if self.order[a] < self.order[b] else (b, a)
        return key in self.pair_ev

    # ------------------------------------------------------------ context (Pass 2 only)
    def context(self, v, unit_members):
        fv, cfg, rp = self.f[v], self.cfg, self.rp
        last_end = max(self.f[m].t_end for m in unit_members)
        gap = max(0.0, fv.t_start - last_end)
        if gap > rp.candidate_horizon_s:
            return 0.0, gap
        decay = 0.5 ** (gap / rp.decay_half_life_s)
        c = 0.0
        if gap <= rp.t_resume_s:
            c += cfg.ctx_adjacent * decay
        near = [u for u in self.eligible if self.order[u] < self.order[v] and fv.t_start - self.f[u].t_end <= rp.neighbour_window_s]
        if near:
            inside = [u for u in near if u in set(unit_members)]
            c += cfg.ctx_neighbour * decay * len(inside) / len(near)
            if any(self.f[u].app == fv.app for u in inside):
                c += cfg.ctx_same_app * decay
        return min(cfg.ctx_cap, c), gap

    # ------------------------------------------------------------ constraints
    def blocked(self, v, unit_members):
        return any(self.store.is_cannot_link(v, m) for m in unit_members)

    def uncertain_units(self, v, units):
        """Units (uids) that contain a visit sharing an omitted support group with v: omitted pairs may concern them."""
        share = self.shares.get(v, ())
        return {u for u in units if any(m in share for m in self.members[u])}

    # ------------------------------------------------------------ one decision
    def evaluate(self, v, use_context, only_assigned):
        cfg = self.cfg
        earlier = [u for u in sorted(set(self.uid_of[x] for x in self.eligible if self.order[x] < self.order[v] and x in self.uid_of),
                                     key=lambda u: self.order[self.members[u][0]])]
        units = [u for u in earlier if (u in self.assigned or not only_assigned)]
        cands, blocked = [], []
        for u in units:
            mem = self.members[u]
            s = self.score(v, mem)
            C, gap = self.context(v, mem) if use_context else (0.0, None)
            if s.E < cfg.weak_floor - EPS and not s.purpose_conflict:
                continue
            row = {"uid": u, "E": s.E, "tip": s.tip, "C": C, "S": s.E + C, "score": s}
            if self.blocked(v, mem):
                blocked.append(row)
            elif s.purpose_conflict and s.E < cfg.weak_floor - EPS:
                row["purpose"] = True
                blocked.append(row)
            else:
                cands.append(row)
        cands.sort(key=lambda r: (-r["S"], -r["tip"], self.order[self.members[r["uid"]][0]]))
        return {"units": units, "cands": cands, "blocked": blocked}

    def decide(self, v, use_context, only_assigned):
        """-> ('assign', uid, info) or ('abstain', state, reason, info)."""
        cfg = self.cfg
        d = self.evaluate(v, use_context, only_assigned)
        cands, blocked = d["cands"], d["blocked"]
        info = {"cands": cands[:3]}
        top_blocked = max((b for b in blocked if not b.get("purpose")), key=lambda r: r["S"], default=None)
        if not cands:
            if any(b.get("purpose") for b in blocked):
                return ("abstain", ac.UNASSIGNED, "purpose_conflict", info)
            if top_blocked is not None and top_blocked["S"] + EPS >= cfg.assign_threshold:
                return ("abstain", ac.UNASSIGNED, "cannot_link", info)
            return ("none", None, None, info)
        best = cands[0]
        s = best["score"]
        others = [c["tip"] for c in cands[1:]] + [b["tip"] for b in ()]
        margin = best["tip"] - (max(others) if others else 0.0)
        enough = best["S"] + EPS >= cfg.assign_threshold
        fam_ok = len(s.families) >= cfg.min_families and s.primary
        if enough and fam_ok:
            if margin + EPS < cfg.min_margin:
                return ("abstain", ac.AMBIGUOUS, "margin_too_small", info)
            rivals = self.uncertain_units(v, [u for u in d["units"] if u != best["uid"]])
            if rivals:                                            # omitted pairs could favour another activity
                return ("abstain", ac.AMBIGUOUS, "shared_artifact", info)
            return ("assign", best["uid"], info)
        fams = set(s.families)
        if enough and not fam_ok:
            why = "artifact_only" if fams == {"artifact"} else "lineage_only" if fams == {"exact"} else "insufficient_families"
        elif fams == {"artifact"}:
            why = "artifact_only"
        elif fams == {"exact"}:
            why = "lineage_only"
        else:
            why = "insufficient_families"
        if top_blocked is not None and top_blocked["S"] > best["S"] + EPS and top_blocked["S"] + EPS >= cfg.assign_threshold:
            return ("abstain", ac.UNASSIGNED, "cannot_link", info)
        return ("abstain", ac.TENTATIVE, why, info)

    # ------------------------------------------------------------ merging
    def join(self, v, uid):
        old = self.uid_of[v]
        self.members[uid] = sorted(set(self.members[uid]) | set(self.members.pop(old)), key=self.order.get)
        for m in self.members[uid]:
            self.uid_of[m] = uid
        self.assigned.add(uid)
        self.assigned.discard(old)

    def seed_ok(self, v):
        fv, rp = self.f[v], self.rp
        return fv.dwell_s + EPS >= (rp.seed_dwell_s if fv.known_kind else rp.unknown_app_seed_dwell_s)

    # ------------------------------------------------------------ the passes
    def pass1(self):
        for v in self.eligible:
            kind = self.decide(v, False, False)
            if kind[0] == "assign":
                self.assign_into(v, kind[1], kind[2], "anchor")
            else:
                self.reason[v] = kind

    def assign_into(self, v, uid, info, how):
        row = next(c for c in info["cands"] if c["uid"] == uid)
        rec = (how, row["S"], row["score"].families, row["score"].ev_ids, info)
        for m in self.members[uid]:
            self.decided_by.setdefault(m, ("anchor", row["S"], row["score"].families, row["score"].ev_ids, info))
        self.decided_by[v] = rec
        self.join(v, uid)

    def pass2(self):
        """One sweep in canonical order, using earlier assignments only. A visit attaches to an earlier assigned
        activity, abstains, or (no candidate and enough dwell) becomes the seed of a new activity that later visits may
        join. A seed is never withdrawn or re-assigned. If omitted pair evidence could concern the visit it abstains
        instead of seeding: missing evidence is never taken as 'nothing to link to'."""
        for v in self.eligible:
            if self.uid_of[v] in self.assigned:
                continue
            kind = self.decide(v, True, True)
            prior = self.reason.get(v)
            if kind[0] == "assign":
                self.assign_into(v, kind[1], kind[2], "context")
                self.reason.pop(v, None)
                continue
            if kind[0] == "none":
                if prior is not None and prior[0] == "abstain":
                    continue                                      # pass 1 already recorded why (e.g. margin too small)
                info = kind[3]
                if any(m in self.elig and self.order[m] < self.order[v] for m in self.shares.get(v, ())):
                    self.reason[v] = ("abstain", ac.AMBIGUOUS, "shared_artifact", info)
                elif self.seed_ok(v):
                    self.assigned.add(self.uid_of[v])
                    self.decided_by[v] = ("seed", 0.0, [], [], info)
                    self.reason.pop(v, None)
                else:
                    self.reason[v] = kind
            else:
                self.reason[v] = kind

    def pass3(self):
        cfg = self.cfg
        while True:
            units = sorted((u for u in self.assigned), key=lambda u: self.order[self.members[u][0]])
            groups = {}
            for (a, b), evs in self.pair_ev.items():
                ua, ub = self.uid_of.get(a), self.uid_of.get(b)
                if ua is None or ub is None or ua == ub or ua not in self.assigned or ub not in self.assigned:
                    continue
                groups.setdefault(tuple(sorted((ua, ub), key=lambda u: self.order[self.members[u][0]])), []).append(self.profile(a, b))
            scored = {k: _combine(v, cfg) for k, v in groups.items()}
            ok = {}
            for (ua, ub), s in scored.items():
                if self.cross_blocked(ua, ub):
                    continue
                ok[(ua, ub)] = s
            best_of = {}
            for (ua, ub), s in ok.items():
                for x, y in ((ua, ub), (ub, ua)):
                    best_of.setdefault(x, []).append((s.tip, y))
            merges = []
            for (ua, ub), s in ok.items():
                if s.E + EPS < cfg.assign_threshold or len(s.families) < cfg.min_families or not s.primary:
                    continue
                if any(self.uncertain_units_pair(ua, ub, units)):
                    continue
                good = True
                for x, y in ((ua, ub), (ub, ua)):
                    rivals = [t for t, z in best_of[x] if z != y]
                    if s.tip - (max(rivals) if rivals else 0.0) + EPS < cfg.min_margin:
                        good = False
                if good:
                    merges.append((-s.E, self.order[self.members[ua][0]], self.order[self.members[ub][0]], ua, ub))
            if not merges:
                return
            _, _, _, ua, ub = sorted(merges)[0]
            keep = ua
            self.members[keep] = sorted(self.members[keep] + self.members.pop(ub), key=self.order.get)
            for m in self.members[keep]:
                self.uid_of[m] = keep
            self.assigned.discard(ub)
            assert not self.cross_blocked_within(keep), "cannot-link inside a merged activity"

    def uncertain_units_pair(self, ua, ub, units):
        outside = [u for u in units if u not in (ua, ub)]
        mem = set(self.members[ua]) | set(self.members[ub])
        share = set()
        for m in mem:
            share |= self.shares.get(m, set())
        return [u for u in outside if any(x in share for x in self.members[u])]

    def cross_blocked(self, ua, ub):
        return any(self.store.is_cannot_link(a, b) for a in self.members[ua] for b in self.members[ub])

    def cross_blocked_within(self, uid):
        m = self.members[uid]
        return any(self.store.is_cannot_link(m[i], m[j]) for i in range(len(m)) for j in range(i + 1, len(m)))


# ---------------------------------------------------------------- the result

def infer(prepared, params, config=None):
    """The contract interface: Prepared -> result."""
    fs, es = ev.analyse(prepared, None, params)
    return infer_evidence(fs, es, params, config)


def infer_evidence(fs, es, params, config=None):
    """FeatureSet + EvidenceSet -> result. Raises ConstraintsIncompleteError before deciding anything if the cannot-links
    are incomplete."""
    cfg = config or ScoringConfig()
    en = _Engine(fs, es, params, cfg)
    en.pass1()
    en.pass2()
    en.pass3()
    return _result(en)


def _result(en):
    cfg, fs, rp = en.cfg, en.fs, en.rp
    starts = {v.visit_id: v.t_start for v in fs.visits}
    final = {}                                                    # unit -> activity id
    for u in sorted(en.assigned):
        final[u] = ac.activity_id(ac.earliest_member(starts, en.members[u]))
    acts = [{"id": final[u], "members": sorted(en.members[u])}
            for u in sorted(en.assigned, key=lambda u: en.order[en.members[u][0]])]

    def cand_rows(info):
        out = []
        for c in (info or {}).get("cands", [])[:3]:
            if c["uid"] in final:
                out.append({"activity_id": final[c["uid"]], "score": round(c["S"], 3), "families": list(c["score"].families)})
        return out

    rows = []
    for v in fs.visits:
        vid = v.visit_id
        shift = None
        if v.internal_shift:
            shift = {"from_moment_id": v.internal_shift["from_moment_id"], "to_moment_id": v.internal_shift["to_moment_id"]}
        factor = cfg.shift_confidence_factor if shift else 1.0
        row = {"visit_id": vid, "state": ac.UNASSIGNED, "activity_id": None, "reason": "no_substance", "candidates": [],
               "confidence": 0.0, "internal_shift": shift, "evidence": [], "explanation": ["no substance: glance or too little content"]}
        if vid in en.elig:
            u = en.uid_of[vid]
            if u in en.assigned:
                how, S, fams, ev_ids, info = en.decided_by[vid]
                seed = how == "seed"
                row.update(state=ac.ASSIGNED, activity_id=final[u], reason="seed" if seed else None, candidates=cand_rows(info),
                           confidence=round(factor * (0.0 if seed else min(1.0, S / cfg.confidence_full)), 3), evidence=ev_ids,
                           explanation=[f"{how}: score {S:.2f}, families {'+'.join(fams) or 'none'}"])
            else:
                rec = en.reason.get(vid)
                if rec is None or rec[0] == "none":
                    state, why, info = ac.UNASSIGNED, "no_candidate", (rec[3] if rec else {"cands": []})
                    if any(v.t_start - en.f[x].t_end <= rp.t_resume_s for x in en.eligible if en.order[x] < en.order[vid]):
                        why = "adjacency_only"             # near other work in time, with no evidence of sharing it
                else:
                    _, state, why, info = rec
                row.update(state=state, reason=why, candidates=cand_rows(info), explanation=[f"{state}: {why}"],
                           confidence=round(factor * min(1.0, (info["cands"][0]["S"] / cfg.confidence_full) if info.get("cands") else 0.0), 3))
        rows.append(row)
    return {"visits": rows, "activities": acts,
            "relations": {"support_complete": en.es.support_complete, "constraints_complete": True,
                          "support_omitted_groups": len(en.es.skipped)}}


def report_status(result, prepared, params):
    """REPORTING ONLY: {activity_id: candidate | active | paused | dormant}, read from a finished result and the
    reporting horizons. It is a separate function on purpose: the result never carries a status, so no assignment can
    depend on one. candidate = a single-visit activity; otherwise by the idle time before the latest visit end."""
    end = {v.visit_id: v.t_end for v in prepared.visits}
    now = max(end.values(), default=0.0)
    out = {}
    for a in result["activities"]:
        idle = now - max(end[m] for m in a["members"])
        out[a["id"]] = ("candidate" if len(a["members"]) == 1 else "active" if idle <= params.status_active_s
                        else "paused" if idle <= params.status_paused_s else "dormant")
    return out

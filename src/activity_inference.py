"""LMemM - deterministic activity inference (Step 2, PR 2C). Pure Python, offline, no model, no I/O, no clock.

    Prepared -> activity_evidence.analyse() -> (FeatureSet, EvidenceSet) -> infer_from() -> Result

Public entry for records: activity_contract.replay(infer, records, params). `infer(prepared, params)` is the inference.

Order of work (design 3, 5, 12.4, 14.1)
  Gate        EvidenceSet.require_complete() first: if the cannot-links are incomplete nothing is assigned (the error is
              raised, no result exists).
  Pass 1      conservative ANCHORS. Cannot-links are checked first. A pair becomes an anchor only on non-temporal evidence of
              two independent sources or a design-hard identifier rule (see anchor_rule). No time, no neighbour, no decay.
  Pass 2      visits in canonical order. Each unassigned visit is scored against the activities as they are, using only
              members that start before it (plus Pass 1 anchors that already contain them). Time (decay, T_resume, handoff)
              and neighbour context enter here. Assignments are only added, never withdrawn. A visit with nothing at the weak
              floor becomes a seed (a new activity) when it has substance.
  Pass 3      consolidation with look-ahead: retry unplaced visits against the final activities, then merge activities under
              the same evidence, margin and cannot-link rules. The cannot-links are rechecked after every merge.

Scoring (design 4, 5; additive, per the owner's decision). Every number lives in ScoringConfig. The THRESHOLDS are the
design's (assign 4.0, weak floor 2.0, margin 2.0, at least 2 families). The WEIGHTS are PROVISIONAL and UNCALIBRATED. A
candidate's score is a sum over FAMILIES (artifact, lexical, exact; a handoff is carried by the lexical family), each family contributing the maximum over its
signals and over the activity's members, so correlated signals of one family and many members of one activity never add up.
Contextual evidence (same branch, same workspace branch, same file name without a verified folder, same locator, unverified
lineage) adds at most `contextual_cap`, can never establish an assignment, and is excluded from the margin test.
Contrast (different PR or issue, different workspace branch) and age beyond the horizon subtract. A missing pair is never a
negative: absence of evidence scores zero and changes nothing.

Incomplete support evidence. When EvidenceSet.support_complete is False, a visit that belongs to a skipped group can still be
assigned on explicitly available positive evidence that meets every rule alone, but it never becomes a seed and never wins on
a margin, because a missing pair listing could belong to either side. Those cases abstain: state `ambiguous`, reason
`insufficient_families`, with `incomplete_support` in the explanation.

Not implemented because the evidence layer produces nothing for them: S5 (search query echoed; typed text is not stored) and S6
(interaction coherence; interaction is not read), N4 (disjoint identifiers), and dynamic artifact promiscuity (an artifact seen
in two activities); the last would make specificity depend on assignments, which is forbidden. Specificity bands, hub patterns
and recurrence stand in for it.

Time. Gaps are differences of timestamps; decay uses gap / half-life, so translation cancels and joint scaling cancels.
"""

import math
from dataclasses import dataclass, field

import activity_contract as ac
import activity_evidence as ev
import activity_features as af


class ConstraintViolation(RuntimeError):
    """A final activity contains a cannot-linked pair, or a pass withdrew an assignment. Never expected."""


# ---------------------------------------------------------------- the scoring configuration (centralised)

@dataclass(frozen=True)
class ScoringConfig:
    """All scoring numbers. THRESHOLDS: design sections 4 and 5. WEIGHTS: PROVISIONAL AND UNCALIBRATED (owner decision,
    PR 2C): conservative starting points chosen so that an exact fact outranks a shared identifier, which outranks lexical and
    locator hints, and so that no soft family reaches the assign threshold alone. Retune only against real labels."""
    # design thresholds
    assign_threshold: float = 4.0
    weak_floor: float = 2.0
    margin: float = 2.0
    min_families: int = 2
    # hard tier (each one assigns alone when nothing is against it): the weight is the family's score
    w_h1_lineage: float = 6.0            # version chain, content-corroborated
    w_h2_artifact: float = 6.0           # specific artifact within T_resume, not demoted
    w_h3_identifiers: float = 5.0        # at least ident_hard_min rare shared identifiers
    w_h3_extra: float = 0.5              # per further rare identifier
    w_h3_extra_cap: float = 1.0
    w_h4_handoff: float = 5.0            # switch within handoff_s plus a shared specific token
    ident_hard_min: int = 2
    handoff_rare_terms: int = 3
    # soft tier
    w_s1_artifact: float = 3.0           # same artifact but demoted (long gap, hub, low specificity, purpose); below assign alone
    w_s2_terms: float = 3.0              # times x_E of the shared rare terms
    w_s3_locator: float = 1.0            # contextual
    w_s4_ident_rare: float = 2.0         # one shared rare identifier
    w_s4_ident_common: float = 0.5
    # contextual (counted up to the cap, never alone, not in the margin)
    w_same_branch: float = 0.5
    w_same_workspace_branch: float = 0.25
    w_same_file_name: float = 0.5
    w_lineage_uncorroborated: float = 1.0
    contextual_cap: float = 1.0
    # negative
    w_diff_pr: float = -1.0              # contrast: two distinct PRs or issues
    w_diff_workspace_branch: float = -0.5
    w_age: float = -1.0                  # N5: older than the candidate horizon, no hard evidence
    # corroboration and gates (x_E scale of 14.1)
    anchor_corroboration_x: float = 0.5  # Pass 1: artifact or lineage link needs E >= this, key tokens removed
    h2_min_overlap_x: float = 0.3        # Pass 2: weak overlap demotes H2 below this
    lineage_corroboration_x: float = 0.5
    family_min: float = 1.0              # a family counts toward min_families only at this weight
    member_shift_weight: float = 0.5     # lexical weight of a member with internal_shift


SCORING = ScoringConfig()

CONTEXTUAL = frozenset({"same_branch", "same_workspace_branch", "same_file_name", "same_locator"})
ARTIFACT_LINKS = frozenset({"same_item", "same_artifact", "same_pr_issue"})
LEVELS = ("low", "medium", "high")


# ---------------------------------------------------------------- run context

class _Ctx:
    def __init__(self, fs, es, cfg):
        self.fs, self.es, self.cfg = fs, es, cfg
        self.rp = fs.replay_params
        self.feats = {f.visit_id: f for f in fs.visits}
        self.order = {f.visit_id: i for i, f in enumerate(fs.visits)}
        self.ids = [f.visit_id for f in fs.visits]
        self.store = es.constraints
        self.pair_ev, self.partners = {}, {}
        for e in es.items:
            self.pair_ev.setdefault((e.source, e.target), []).append(e)
            self.partners.setdefault(e.source, set()).add(e.target)
            self.partners.setdefault(e.target, set()).add(e.source)
        self.affected = set()
        for members in es.skipped_visits.values():
            self.affected |= set(members)
        self._content = {}
        self.contested = set()
        self.anchor_adj = {}
        self.constrained = sorted(set(self.store.branch_of) | set(self.store.file_of), key=self.order.get)

    def gap(self, a, b):
        x, y = sorted((a, b), key=self.order.get)
        return max(0.0, self.feats[y].t_start - self.feats[x].t_end)

    def content(self, a, b, exclude):
        key = (a, b, exclude) if self.order[a] < self.order[b] else (b, a, exclude)
        if key not in self._content:
            self._content[key] = af.content_similarity(self.feats[key[0]], self.feats[key[1]], self.fs.corpus,
                                                       self.fs.params, exclude)
        return self._content[key]

    def cannot(self, a, b):
        return self.store.is_cannot_link(a, b)


# ---------------------------------------------------------------- pair signals

@dataclass
class Sig:
    fam: dict = field(default_factory=dict)          # family -> (weight, rule)
    ctx: dict = field(default_factory=dict)          # contextual type -> weight
    pen: dict = field(default_factory=dict)          # name -> negative weight
    hard: set = field(default_factory=set)           # rules: H1..H4
    locator: float = 0.0
    demotions: set = field(default_factory=set)
    evidence: set = field(default_factory=set)

    def put(self, family, weight, rule, hard=False):
        if weight > self.fam.get(family, (0.0, ""))[0]:
            self.fam[family] = (weight, rule)
        if hard:
            self.hard.add(rule)

    def absorb(self, other):
        for f, (w, r) in other.fam.items():
            if w > self.fam.get(f, (0.0, ""))[0]:
                self.fam[f] = (w, r)
        for t, w in other.ctx.items():
            self.ctx[t] = max(self.ctx.get(t, 0.0), w)
        for n, w in other.pen.items():
            self.pen[n] = min(self.pen.get(n, 0.0), w)
        self.hard |= other.hard
        self.locator = max(self.locator, other.locator)
        self.demotions |= other.demotions
        self.evidence |= other.evidence

    def substantive(self):
        return math.fsum(w for w, _ in self.fam.values())

    def contextual(self, cfg):
        return min(cfg.contextual_cap, math.fsum(self.ctx.values()) + self.locator) if (self.ctx or self.locator) else 0.0

    def total(self, cfg):
        return self.substantive() + self.contextual(cfg) + math.fsum(self.pen.values())

    def net_substantive(self):
        return self.substantive() + math.fsum(self.pen.values())

    def families(self, cfg):
        """The independent families: substantive ones only. The locator and every other contextual signal is not a family."""
        return {f for f, (w, _) in self.fam.items() if w >= cfg.family_min}


def _ref(e):
    """Provenance of one piece of evidence as 'type:source>target'. Never the evidence id: ids hash opaque item keys, which
    change when only app names change, and the outcome must not."""
    return f"{e.type}:{e.source}>{e.target}"


def anchor_rule(ctx, a, b):
    """Pass 1: (rule, score, evidence ids) when the pair is a conservative anchor, else None. Non-temporal: no gap, no
    neighbour, no decay. The cannot-link is checked by the caller first."""
    cfg = ctx.cfg
    evs = ctx.pair_ev.get(tuple(sorted((a, b), key=ctx.order.get)), ())
    if not evs:
        return None
    fa, fb = ctx.feats[a], ctx.feats[b]
    key_ex = fa.key_tokens | fb.key_tokens
    corr = ctx.content(a, b, key_ex)["x_e"]
    has_art = any(e.type in ARTIFACT_LINKS for e in evs)
    best = None

    def offer(rule, score, ids):
        nonlocal best
        if best is None or score > best[1]:
            best = (rule, score, sorted(ids))
    for e in evs:
        if e.polarity != "support":
            continue
        ref = _ref(e)
        if e.type == "shared_identifiers":
            n = sum(1 for i in e.strength["items"] if i["class"] == "rare" and not (has_art and i["in_key"]))
            if n >= cfg.ident_hard_min:
                offer("H3", cfg.w_h3_identifiers + min(cfg.w_h3_extra_cap, cfg.w_h3_extra * (n - cfg.ident_hard_min)), {ref})
        elif e.type in ARTIFACT_LINKS:
            if e.strength["band"] == "high" and not e.strength["hub_pattern"] and corr >= cfg.anchor_corroboration_x:
                offer("H2", cfg.w_h2_artifact + corr, {ref})
        elif e.type == "version_lineage":
            if e.strength["corroboration"]["x_e"] >= cfg.anchor_corroboration_x:
                offer("H1", cfg.w_h1_lineage + e.strength["corroboration"]["x_e"], {ref})
    return best


def pair_signal(ctx, subject, member, use_time=True):
    """What the evidence between `subject` (the visit being placed) and one `member` supports, as a Sig."""
    cfg, rp = ctx.cfg, ctx.rp
    a, b = sorted((subject, member), key=ctx.order.get)
    evs = ctx.pair_ev.get((a, b), ())
    sig = Sig()
    if not evs:
        return sig
    gap = ctx.gap(a, b)
    decay = 0.5 ** (gap / rp.decay_half_life_s) if use_time else 1.0
    shifted = ctx.feats[subject].internal_shift is not None
    mw = cfg.member_shift_weight if ctx.feats[member].internal_shift is not None else 1.0
    key_ex = ctx.feats[a].key_tokens | ctx.feats[b].key_tokens
    has_art = any(e.type in ARTIFACT_LINKS for e in evs)
    corr = ctx.content(a, b, key_ex)
    handoff = use_time and gap <= rp.handoff_s
    for e in evs:
        t = e.type
        if e.polarity in ("support", "contrast"):
            sig.evidence.add(_ref(e))
        if t in ARTIFACT_LINKS:
            specific = e.strength["band"] == "high" and not e.strength["hub_pattern"]
            why = set()
            if e.strength["hub_pattern"]:
                why.add("hub_pattern")
            elif not specific:
                why.add("low_specificity")
            if gap > rp.t_resume_s:
                why.add("long_gap")
            if shifted:
                why.add("purpose_conflict")
            if corr["x_e"] < cfg.h2_min_overlap_x:
                why.add("weak_overlap")
            if not why:
                sig.put("artifact", cfg.w_h2_artifact, "H2", hard=True)
            else:
                sig.demotions |= why
                sig.put("artifact", cfg.w_s1_artifact * decay, "S1")
        elif t == "shared_identifiers":
            items = [i for i in e.strength["items"] if not (has_art and i["in_key"])]
            n_rare = sum(1 for i in items if i["class"] == "rare")
            if n_rare >= cfg.ident_hard_min:
                sig.put("lexical", cfg.w_h3_identifiers + min(cfg.w_h3_extra_cap, cfg.w_h3_extra * (n_rare - cfg.ident_hard_min)),
                        "H3", hard=True)
            elif n_rare == 1:
                sig.put("lexical", cfg.w_s4_ident_rare * decay * mw, "S4")
            elif items:
                sig.put("lexical", cfg.w_s4_ident_common * decay * mw, "S4")
            if handoff and any(i["class"] == "rare" and not i["in_key"] for i in e.strength["items"]):
                sig.put("lexical", cfg.w_h4_handoff, "H4", hard=True)      # one family: a handoff is a lexical fact plus a short switch
        elif t == "shared_terms":
            x = corr["x_e"] if (has_art or any(q.type == "version_lineage" for q in evs)) else e.strength["x_e"]
            if x > 0:
                sig.put("lexical", cfg.w_s2_terms * x * decay * mw, "S2")
            if handoff and e.strength["rare_shared"] >= cfg.handoff_rare_terms:
                sig.put("lexical", cfg.w_h4_handoff, "H4", hard=True)      # one family: a handoff is a lexical fact plus a short switch
        elif t == "version_lineage":
            if e.strength["corroboration"]["x_e"] >= cfg.lineage_corroboration_x:
                sig.put("exact", cfg.w_h1_lineage, "H1", hard=True)
            else:
                sig.ctx[t] = cfg.w_lineage_uncorroborated * decay
                sig.demotions.add("lineage_only")
        elif t == "same_branch":
            sig.ctx[t] = cfg.w_same_branch * decay
        elif t == "same_workspace_branch":
            sig.ctx[t] = cfg.w_same_workspace_branch * decay
        elif t == "same_file_name":
            sig.ctx[t] = cfg.w_same_file_name * decay
        elif t == "same_locator":
            sig.locator = max(sig.locator, cfg.w_s3_locator * decay)
        elif t == "different_pr_issue":
            sig.pen[t] = cfg.w_diff_pr
        elif t == "different_workspace_branch":
            sig.pen[t] = cfg.w_diff_workspace_branch
    if use_time and gap > rp.candidate_horizon_s and not sig.hard:
        sig.pen["age"] = cfg.w_age
    return sig


# ---------------------------------------------------------------- state

class _State:
    def __init__(self, ctx):
        self.ctx = ctx
        self.act = {}                    # visit -> internal activity key
        self.members = {}                # key -> [visit ids] sorted by canonical order
        self.alias = {}                  # merged key -> surviving key
        self.info = {}                   # visit -> dict(tier, rule, families, score, margin, evidence, ...)
        self.rows = {}                   # visit -> row for visits that are not assigned
        self.next_key = 0

    def root(self, k):
        while k in self.alias:
            k = self.alias[k]
        return k

    def new(self, visit):
        k = self.next_key
        self.next_key += 1
        self.members[k] = [visit]
        self.act[visit] = k
        return k

    def add(self, k, visit):
        k = self.root(k)
        self.members[k] = sorted(self.members[k] + [visit], key=self.ctx.order.get)
        self.act[visit] = k
        self.rows.pop(visit, None)

    def merge(self, a, b):
        a, b = self.root(a), self.root(b)
        keep, drop = (a, b) if a < b else (b, a)
        self.members[keep] = sorted(self.members[keep] + self.members.pop(drop), key=self.ctx.order.get)
        self.alias[drop] = keep
        for v in self.members[keep]:
            self.act[v] = keep
        return keep

    def groups(self):
        return {k: list(m) for k, m in self.members.items()}

    def snapshot(self):
        return {v: self.root(k) for v, k in self.act.items()}


def _check(ctx, st, before, where):
    """Assignments are only added: every earlier group is inside one later group. And no group holds a cannot-linked pair."""
    after = st.snapshot()
    for v, k in before.items():
        if v not in after:
            raise ConstraintViolation(f"{where}: {v} was withdrawn")
    seen = {}
    for v, k in before.items():
        seen.setdefault(k, set()).add(after[v])
    for k, s in seen.items():
        if len(s) > 1:
            raise ConstraintViolation(f"{where}: an activity was split")
    for k, members in st.members.items():
        cons = [v for v in members if v in ctx.store.branch_of or v in ctx.store.file_of]
        for i in range(len(cons)):
            for j in range(i + 1, len(cons)):
                if ctx.cannot(cons[i], cons[j]):
                    raise ConstraintViolation(f"{where}: cannot-linked {cons[i]} and {cons[j]} share an activity")


# ---------------------------------------------------------------- pass 1

def _contested(ctx, anchors):
    """Visits that are anchored to two or more groups that are not otherwise linked (design N2: a visit strongly tied to
    different activities must not chain them). They stay out of Pass 1 and are decided, with a margin, in Pass 2."""
    adj = ctx.anchor_adj = {}
    for _, _, _, a, b, _ in anchors:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    out = set()
    for x in sorted(adj, key=ctx.order.get):
        nb = sorted(adj[x], key=ctx.order.get)
        if len(nb) < 2:
            continue
        reached, groups = set(), 0
        for start in nb:
            if start in reached:
                continue
            groups += 1
            stack = [start]
            reached.add(start)
            while stack and not set(nb) <= reached:
                for w in sorted(adj[stack.pop()] - {x}, key=ctx.order.get):
                    if w not in reached:
                        reached.add(w)
                        stack.append(w)
            if set(nb) <= reached:
                break
        if groups > 1 or not set(nb) <= reached:
            out.add(x)
    return out


def _pass1(ctx, st):
    cfg = ctx.cfg
    ok = {v for v in ctx.ids if ctx.feats[v].substance["ok"] and v not in ctx.affected}
    anchors = []
    for (a, b), evs in sorted(ctx.pair_ev.items(), key=lambda kv: (ctx.order[kv[0][0]], ctx.order[kv[0][1]])):
        if a not in ok or b not in ok:
            continue
        if ctx.cannot(a, b):
            continue                                                  # cannot-links first
        r = anchor_rule(ctx, a, b)
        if r:
            anchors.append((-r[1], ctx.order[a], ctx.order[b], a, b, r))
    anchors.sort()
    contested = ctx.contested = _contested(ctx, anchors)
    anchors = [a for a in anchors if a[3] not in contested and a[4] not in contested]
    comp = {}                                                         # visit -> set of members (shared object)
    edges = {}
    for _, _, _, a, b, (rule, score, ids) in anchors:
        ca, cb = comp.get(a, {a}), comp.get(b, {b})
        if ca is cb:
            continue
        if len(ca) >= 2 and len(cb) >= 2:
            continue                                                  # two established groups merge only in Pass 3
        if any(ctx.cannot(x, y) for x in ca for y in cb):
            continue                                                  # complete linkage
        merged = ca | cb
        for v in merged:
            comp[v] = merged
        edges.setdefault(a, []).append((rule, score, ids, b))
        edges.setdefault(b, []).append((rule, score, ids, a))
    done = set()
    for v in ctx.ids:
        c = comp.get(v)
        if not c or len(c) < 2 or id(c) in done:
            continue
        done.add(id(c))
        members = sorted(c, key=ctx.order.get)
        k = st.new(members[0])
        for m in members[1:]:
            st.add(k, m)
        for m in members:
            best = max(edges[m], key=lambda e: (e[1], e[0]))
            st.info[m] = {"tier": "hard", "rule": best[0], "families": [], "score": round(best[1], 6), "margin": None,
                          "evidence": best[2], "pass": 1}


# ---------------------------------------------------------------- decisions (Pass 2 and Pass 3)

@dataclass
class Cand:
    key: int
    sig: Sig
    members: list
    adjacent: bool = False
    cannot: bool = False

    def total(self, cfg):
        return self.sig.total(cfg)


def _candidates(ctx, st, v, past_only):
    cfg = ctx.cfg
    keys = {}
    for w in sorted(ctx.partners.get(v, ()), key=ctx.order.get):
        if w not in st.act or (past_only and ctx.order[w] > ctx.order[v]):
            continue
        keys.setdefault(st.root(st.act[w]), None)
    adj = None
    if past_only and ctx.order[v] > 0:
        for i in range(ctx.order[v] - 1, -1, -1):
            p = ctx.ids[i]
            if p in st.act:
                if ctx.gap(p, v) <= ctx.rp.neighbour_window_s:
                    adj = st.root(st.act[p])
                break
    if adj is not None:
        keys.setdefault(adj, None)
    out = []
    for k in sorted(keys):
        members = [m for m in st.members[k] if m != v and (not past_only or ctx.order[m] < ctx.order[v])]
        if not members:
            continue
        sig = Sig()
        cl = False
        for m in members:
            if ctx.cannot(v, m):
                cl = True
            sig.absorb(pair_signal(ctx, v, m, use_time=True))
        out.append(Cand(k, sig, members, adjacent=(k == adj), cannot=cl))
    return out


def _conflicting_neighbours(ctx, st, v, key):
    """12.3: both neighbours of v (in canonical order, within the neighbour window) are in one activity that is not `key`."""
    i = ctx.order[v]
    if i == 0 or i + 1 >= len(ctx.ids):
        return False
    p, n = ctx.ids[i - 1], ctx.ids[i + 1]
    if p not in st.act or n not in st.act:
        return False
    if ctx.gap(p, v) > ctx.rp.neighbour_window_s or ctx.gap(v, n) > ctx.rp.neighbour_window_s:
        return False
    kp, kn = st.root(st.act[p]), st.root(st.act[n])
    return kp == kn and kp != key


def _row_cands(ctx, st, cands, limit=3):
    out = []
    for c in sorted(cands, key=lambda c: (-c.total(ctx.cfg), c.key))[:limit]:
        out.append({"activity": c.key, "score": round(c.total(ctx.cfg), 6), "adjacent": c.adjacent, "cannot_link": c.cannot,
                    "families": sorted(c.sig.families(ctx.cfg))})
    return out


def _decide(ctx, st, v, past_only, allow_seed):
    """One decision for visit v. Returns a dict: kind assign|seed|row, plus the facts for the explanation."""
    cfg = ctx.cfg
    feat = ctx.feats[v]
    cands = _candidates(ctx, st, v, past_only)
    for c in cands:                                                   # 12.3: conflicting neighbour context demotes H2
        if "H2" in c.sig.hard and _conflicting_neighbours(ctx, st, v, c.key):
            c.sig.hard.discard("H2")
            c.sig.demotions.add("conflicting_neighbours")
            w, _ = c.sig.fam.get("artifact", (0.0, ""))
            c.sig.fam["artifact"] = (min(w, cfg.w_s1_artifact), "S1")
    contradiction = [c for c in cands if c.cannot and (c.sig.hard or c.total(cfg) > 0)]
    viable = [c for c in cands if not c.cannot]
    affected = v in ctx.affected
    base = {"candidates": _row_cands(ctx, st, cands), "incomplete_support": False, "contradiction": bool(contradiction)}

    def row(state, reason, **kw):
        d = dict(base, kind="row", state=state, reason=reason)
        d.update(kw)
        return d

    scored = sorted(viable, key=lambda c: (-c.total(cfg), c.key))
    best_positive = [c for c in scored if c.total(cfg) > 0 and (c.sig.fam or c.sig.hard)]
    if not best_positive:
        if contradiction:
            return row(ac.AMBIGUOUS, "cannot_link")
        adjacent = next((c for c in viable if c.adjacent), None)
        if not feat.substance["ok"]:
            if adjacent is not None:
                return row(ac.TENTATIVE, "adjacency_only")
            return row(ac.UNASSIGNED, "no_substance")
        if not allow_seed:
            return dict(base, kind="keep")                            # Pass 3 starts nothing: the Pass 2 outcome stands
        if affected:
            return row(ac.AMBIGUOUS, "insufficient_families", incomplete_support=True)
        return dict(base, kind="seed")
    top = best_positive[0]
    runner = next((c for c in scored if c is not top and c.total(cfg) > 0), None)
    sig = top.sig
    margin_total = top.total(cfg) - (runner.total(cfg) if runner else 0.0)
    margin_subst = sig.net_substantive() - (runner.sig.net_substantive() if runner else 0.0)
    anchors = sig.families(cfg)
    facts = {"key": top.key, "rule": "+".join(sorted(sig.hard)) or "soft", "families": sorted(anchors),
             "score": round(top.total(cfg), 6), "evidence": sorted(sig.evidence), "demotions": sorted(sig.demotions),
             "margin": round(margin_total, 6), "incomplete_support": False}

    def assign():
        if affected:        # a margin needs every rival listed: a missing runner-up is not an infinite margin, so abstain
            return row(ac.AMBIGUOUS, "insufficient_families", incomplete_support=True, facts=facts)
        return dict(base, kind="assign", tier="hard" if sig.hard else "soft", **facts)

    if sig.hard:
        # a hard signal establishes the candidate; the margin over a rival is substantive evidence only (contextual never tips it)
        if runner is None or (margin_total >= cfg.margin and margin_subst >= cfg.margin):
            return assign()
        return row(ac.AMBIGUOUS, "margin_too_small", facts=facts)
    total = top.total(cfg)
    # contextual evidence (locator, branch, file name) is never a family and never helps reach the assign threshold
    if sig.net_substantive() >= cfg.assign_threshold and len(anchors) >= cfg.min_families:
        if runner is None or (margin_total >= cfg.margin and margin_subst >= cfg.margin):
            return assign()
        return row(ac.AMBIGUOUS, "margin_too_small", facts=facts)
    if total < cfg.weak_floor:
        if not feat.substance["ok"]:
            adjacent = next((c for c in viable if c.adjacent), None)
            return row(ac.TENTATIVE, "adjacency_only", facts=facts) if adjacent is not None else row(ac.UNASSIGNED, "no_substance", facts=facts)
        if not allow_seed:
            return dict(base, kind="keep")
        if affected:
            return row(ac.AMBIGUOUS, "insufficient_families", incomplete_support=True, facts=facts)
        return dict(base, kind="seed")
    # between the weak floor and a full assignment
    near = [c for c in scored if c.total(cfg) >= cfg.weak_floor]
    if len(near) >= 2 and margin_total < cfg.margin:
        return row(ac.AMBIGUOUS, "margin_too_small", facts=facts)
    if anchors == {"artifact"} or (not anchors and not sig.fam):
        if sig.demotions & {"hub_pattern", "low_specificity"}:
            return row(ac.AMBIGUOUS, "shared_artifact", facts=facts)
        if "purpose_conflict" in sig.demotions:
            return row(ac.AMBIGUOUS, "purpose_conflict", facts=facts)
        return row(ac.TENTATIVE, "artifact_only", facts=facts)
    if not sig.fam and "lineage_only" in sig.demotions:
        return row(ac.TENTATIVE, "lineage_only", facts=facts)
    return row(ac.AMBIGUOUS, "insufficient_families", facts=facts)


def _apply(ctx, st, v, d):
    if d["kind"] == "assign":
        st.add(d["key"], v)
        st.info[v] = {"tier": d["tier"], "rule": d["rule"], "families": d["families"], "score": d["score"], "margin": d["margin"],
                      "evidence": d["evidence"], "demotions": d["demotions"], "contradiction": d["contradiction"]}
    elif d["kind"] == "seed":
        st.new(v)
        st.info[v] = {"tier": "seed", "rule": "seed", "families": [], "score": 0.0, "margin": None, "evidence": [],
                      "demotions": [], "contradiction": d["contradiction"]}
        st.rows.pop(v, None)
    else:
        st.rows[v] = d


def _pass2(ctx, st):
    for v in ctx.ids:
        if v in st.act:
            continue
        if v in ctx.contested:                                        # anchored to groups it would chain: wait for Pass 3
            st.rows[v] = {"kind": "row", "state": ac.AMBIGUOUS, "reason": "margin_too_small", "candidates": [],
                          "incomplete_support": False, "contradiction": False, "facts": {"rule": "contested", "evidence": [],
                                                                                         "demotions": []}}
            continue
        _apply(ctx, st, v, _decide(ctx, st, v, past_only=True, allow_seed=True))


# ---------------------------------------------------------------- pass 3

def _activity_pair(ctx, st, ka, kb):
    """Aggregated Sig between two activities (family maximum over member pairs) and whether any pair is cannot-linked."""
    sig, cl, links = Sig(), False, []
    for a in st.members[ka]:
        for b in st.members[kb]:
            if ctx.cannot(a, b):
                cl = True
            one = pair_signal(ctx, a, b, use_time=True)
            if one.fam:
                links.append((a, b))
            sig.absorb(one)
    return sig, cl, links


def _independent(ka_members, kb_members, links):
    """Two established activities (two or more members each) merge only on links that do not all pass through one visit:
    a greedy vertex-disjoint matching of at least two pairs. A single bridging visit is never enough (N2)."""
    if len(ka_members) < 2 or len(kb_members) < 2:
        return bool(links)
    used, n = set(), 0
    for a, b in links:
        if a not in used and b not in used:
            used |= {a, b}
            n += 1
    return n >= 2


def _merge_pass(ctx, st):
    cfg = ctx.cfg
    changed = False
    while True:
        keys = sorted(st.members)
        table = {}
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                if not (ctx.partners.keys() & set(st.members[ka])) and not (ctx.partners.keys() & set(st.members[kb])):
                    continue
                if not any(b in ctx.partners.get(a, ()) for a in st.members[ka] for b in st.members[kb]):
                    continue
                sig, cl, links = _activity_pair(ctx, st, ka, kb)
                if cl or not (sig.fam or sig.hard) or not _independent(st.members[ka], st.members[kb], links):
                    continue
                table[(ka, kb)] = sig
        best_for = {}
        for (ka, kb), sig in table.items():
            for me, other in ((ka, kb), (kb, ka)):
                best_for.setdefault(me, []).append((sig.total(cfg), sig.net_substantive(), other))
        cands = []
        for (ka, kb), sig in sorted(table.items()):
            anchors = sig.families(cfg)
            total, subst = sig.total(cfg), sig.net_substantive()
            if sig.hard:
                ok = True
            else:
                ok = subst >= cfg.assign_threshold and len(anchors) >= cfg.min_families
            if not ok:
                continue
            if any(v in ctx.affected for v in st.members[ka] + st.members[kb]):
                continue                  # an omitted pair listing could favour a rival: an uncontested merge is unprovable
            fine = True
            for me, other in ((ka, kb), (kb, ka)):
                rivals = [(t, u) for t, u, o in best_for[me] if o != other]
                if rivals and (total - max(t for t, _ in rivals) < cfg.margin or subst - max(u for _, u in rivals) < cfg.margin):
                    fine = False
            if fine:
                cands.append((-total, ka, kb, sig))
        if not cands:
            return changed
        cands.sort(key=lambda c: (c[0], c[1], c[2]))
        _, ka, kb, sig = cands[0]
        before = st.snapshot()
        keep = st.merge(ka, kb)
        _check(ctx, st, before, "pass 3 merge")
        changed = True
        for v in st.members[keep]:
            if v in st.info and st.info[v].get("tier") == "seed":
                st.info[v] = dict(st.info[v], merged=True)


def _pass3(ctx, st):
    for _ in range(len(ctx.ids) + 1):
        moved = False
        decisions = {}
        for v in ctx.ids:                                             # every decision is made on the same snapshot
            if v not in st.act:
                decisions[v] = _decide(ctx, st, v, past_only=False, allow_seed=False)
        for v, d in decisions.items():
            if d["kind"] == "assign" and v in ctx.contested and any(w in decisions for w in ctx.anchor_adj.get(v, ())
                                                                    if w in ctx.contested):
                d = decisions[v] = dict(d, kind="row", state=ac.AMBIGUOUS, reason="margin_too_small",
                                        facts={"rule": "contested", "evidence": [], "demotions": []})
        incoming = {}
        for v, d in decisions.items():
            if d["kind"] == "assign":
                incoming.setdefault(st.root(d["key"]), []).append(v)
        for k, vs in incoming.items():                                # two newcomers that are cannot-linked: neither goes in
            for i, a in enumerate(vs):
                if any(ctx.cannot(a, b) for j, b in enumerate(vs) if j != i):
                    decisions[a] = dict(decisions[a], kind="row", state=ac.AMBIGUOUS, reason="cannot_link",
                                        facts={"rule": None, "evidence": [], "demotions": []})
        for v, d in decisions.items():
            if d["kind"] == "assign":
                before = st.snapshot()
                _apply(ctx, st, v, d)
                _check(ctx, st, before, "pass 3 retry")
                moved = True
            elif d["kind"] == "row":
                st.rows[v] = d
        merged = _merge_pass(ctx, st)
        if not (moved or merged):
            return


# ---------------------------------------------------------------- result

def _confidence(ctx, st, v, info, size):
    shifted = ctx.feats[v].internal_shift is not None
    if info["tier"] == "seed":
        level = 0                                                     # a seed is a visit that opened an activity with no evidence
    elif info["tier"] == "hard":
        level = 2 if (info.get("margin") is None or info["margin"] >= ctx.cfg.margin) else 1
    else:
        level = 1
    reasons = [f"{info['tier']}:{info['rule']}"] if info["tier"] != "seed" else ["seed"]
    if info.get("margin") is not None:
        reasons.append(f"margin {info['margin']:.2f}")
    if shifted:
        level = max(0, level - 1)
        reasons.append("internal_shift")
    if info.get("contradiction"):
        reasons.append("contradiction_recorded")
    return {"level": LEVELS[level], "score": info["score"], "reasons": reasons}


def _status(ctx, members_end, now):
    age = now - members_end
    rp = ctx.rp
    return "active" if age <= rp.status_active_s else "paused" if age <= rp.status_paused_s else "dormant"


def _result(ctx, st):
    starts = {v: ctx.feats[v].t_start for v in ctx.ids}
    ids = {}
    for k, members in st.members.items():
        ids[k] = ac.activity_id(ac.earliest_member(starts, members))
    now = max((f.t_end for f in ctx.fs.visits), default=0.0)
    rows = []
    for v in ctx.ids:
        f = ctx.feats[v]
        flag = None if f.internal_shift is None else dict(f.internal_shift)
        if v in st.act:
            k = st.root(st.act[v])
            info = st.info.get(v) or {"tier": "soft", "rule": "soft", "families": [], "score": 0.0, "margin": None, "evidence": []}
            size = len(st.members[k])
            rows.append({"visit_id": v, "state": ac.ASSIGNED, "activity_id": ids[k], "reason": "seed" if size == 1 else None,
                         "candidates": [], "internal_shift": flag,
                         "confidence": _confidence(ctx, st, v, info, size),
                         "explanation": {"rule": info["rule"], "families": info.get("families", []), "evidence": info.get("evidence", []),
                                         "demotions": info.get("demotions", []), "score": info["score"], "pass": info.get("pass", 2)}})
        else:
            d = st.rows.get(v) or {"state": ac.UNASSIGNED, "reason": "no_candidate", "candidates": [], "incomplete_support": False}
            cands, seen = [], set()
            for c in d["candidates"]:
                kk = st.root(c["activity"]) if c["activity"] in st.members or c["activity"] in st.alias else None
                if kk is None or kk not in st.members or ids[kk] in seen:
                    continue
                seen.add(ids[kk])
                cands.append({"activity_id": ids[kk], "score": c["score"], "adjacent": c["adjacent"],
                              "cannot_link": c["cannot_link"], "families": c["families"]})
            facts = d.get("facts") or {}
            rows.append({"visit_id": v, "state": d["state"], "activity_id": None, "reason": d["reason"], "candidates": cands,
                         "internal_shift": flag, "confidence": None,
                         "explanation": {"rule": facts.get("rule"), "evidence": facts.get("evidence", []),
                                         "demotions": facts.get("demotions", []),
                                         "incomplete_support": bool(d.get("incomplete_support"))}})
    state_of = {r["visit_id"]: r for r in rows}
    acts = []
    by_act = {ids[k]: members for k, members in st.members.items()}
    for k, members in sorted(st.members.items(), key=lambda kv: ids[kv[0]]):
        end = max(ctx.feats[m].t_end for m in members)
        dwell = math.fsum(ctx.feats[m].dwell_s for m in members)
        status = "candidate" if len(members) == 1 and dwell < ctx.rp.seed_dwell_s else _status(ctx, end, now)
        lv = [LEVELS.index(state_of[m]["confidence"]["level"]) for m in members
              if len(members) == 1 or (st.info.get(m) or {}).get("tier") != "seed"] or [0]
        evid = sorted({e for m in members for e in (st.info.get(m) or {}).get("evidence", [])})
        acts.append({"id": ids[k], "members": sorted(members), "status": status,
                     "confidence": {"level": LEVELS[min(lv)], "members_low": sum(1 for x in lv if x == 0)},
                     "evidence": evid, "shares_artifact_with": []})
    # relations: shared artifacts, returns, interruptions (reporting only)
    act_of = {v: r["activity_id"] for v, r in state_of.items() if r["state"] == ac.ASSIGNED}
    share = {}
    for e in ctx.es.items:
        if e.type in ARTIFACT_LINKS and e.source in act_of and e.target in act_of and act_of[e.source] != act_of[e.target]:
            share.setdefault(act_of[e.source], set()).add(act_of[e.target])
            share.setdefault(act_of[e.target], set()).add(act_of[e.source])
    for a in acts:
        a["shares_artifact_with"] = sorted(share.get(a["id"], ()))
    returns, interruptions = [], []
    for i, v in enumerate(ctx.ids):
        if v not in act_of:
            continue
        prev = next((ctx.ids[j] for j in range(i - 1, -1, -1) if ctx.ids[j] in act_of and act_of[ctx.ids[j]] != act_of[v]), None)
        earlier = [m for m in by_act[act_of[v]] if ctx.order[m] < i]
        if earlier and prev is not None and ctx.order[prev] > max(ctx.order[m] for m in earlier):
            last = max(earlier, key=ctx.order.get)
            if ctx.gap(last, v) > ctx.rp.neighbour_window_s:
                returns.append({"visit_id": v, "activity_id": act_of[v], "earlier_visit_id": last})
        if ctx.feats[v].dwell_s <= ctx.rp.interruption_max_s and 0 < i < len(ctx.ids) - 1:
            p, n = ctx.ids[i - 1], ctx.ids[i + 1]
            if p in act_of and n in act_of and act_of[p] == act_of[n] != act_of[v]:
                interruptions.append({"visit_id": v, "activity_id": act_of[p], "by": act_of[v]})
    abstained = sum(1 for r in rows if r["explanation"].get("incomplete_support"))
    relations = {"returns": returns, "interruptions": interruptions,
                 "support_complete": ctx.es.support_complete, "abstained_for_incomplete_support": abstained}
    return {"visits": rows, "activities": acts, "relations": relations}


# ---------------------------------------------------------------- entry points

def infer_from(fs, es, scoring=None):
    """FeatureSet and EvidenceSet -> result. Raises ConstraintsIncompleteError before any assignment if the cannot-links are
    incomplete (fail closed)."""
    es.require_complete()
    cfg = scoring or SCORING
    ctx = _Ctx(fs, es, cfg)
    st = _State(ctx)
    _pass1(ctx, st)
    _check(ctx, st, {}, "pass 1")
    before = st.snapshot()
    _pass2(ctx, st)
    _check(ctx, st, before, "pass 2")
    before = st.snapshot()
    _pass3(ctx, st)
    _check(ctx, st, before, "pass 3")
    return _result(ctx, st)


def infer(prepared, params=None, scoring=None, feature_params=None, evidence_params=None):
    """Prepared (already validated by activity_input.prepare) -> result. A pure function of its arguments."""
    rp = params or ac.ReplayParams()
    fs, es = ev.analyse(prepared, feature_params, rp, evidence_params)
    return infer_from(fs, es, scoring)

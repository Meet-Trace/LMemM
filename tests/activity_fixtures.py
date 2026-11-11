"""Synthetic Moment fixtures with gold labels for the activity-inference work (Step 2, PR 2A).

TEST-ONLY. Every fixture is a list of Moment records in exactly the persisted shape (built with moments.Moment, so
the schema cannot drift), plus gold in the annotation-sheet format of activity_eval ({moment_id: set of labels},
empty set = noise, `unsure` ids, missing = unlabelled). Nothing here infers anything: the `must_*` and `expect_*`
fields are what later inference PRs are checked against.

Determinism. A fixture is a pure function of its seed. Structure and content use one random stream and OCR noise a
separate one, so the same seed at noise 0 and noise 0.4 has identical ids, times and visits; only digest words differ.

Noise model (letters only, as real digests are): a word is garbled (a letter dropped, doubled, swapped or turned into
l or i), a digest is emptied, or a glance is too short to read.
"""

import csv
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field

import activity_eval as ev
import moments

T0 = 1_800_000_000.0                      # a fixed start: fixtures never read the clock

GENERIC = ("meeting notes update review summary project report email draft schedule team plan status document weekly "
           "follow agenda inbox calendar message thanks reply forward attached comment").split()
TOPICS = {
    "paper": dict(words="attention softmax transformer encoder decoder gradient embedding perplexity tokenizer benchmark ablation "
                        "residual checkpoint optimizer scheduler warmup dropout convergence dataset".split(),
                  idents=["attention_layer.py", "train_loop.py", "paper_notes.md"], site="arxiv.org/abs/2601.00123"),
    "invoices": dict(words="invoice balance outstanding reminder payment supplier ledger receipt remittance overdue quarterly "
                           "reconcile statement creditor".split(),
                     idents=["invoice_summary.xlsx", "ledger_entries.csv"], site="books.example.com/invoices"),
    "trip": dict(words="itinerary flight hostel passport luggage museum boarding layover reservation neighborhood sightseeing "
                       "departure".split(),
                 idents=["itinerary_draft.docx"], site="trips.example.com/lisbon"),
    "hiring": dict(words="candidate interview resume recruiter offer salary referral onboarding headcount panel screening".split(),
                   idents=["hiring_plan.xlsx"], site="jobs.example.com/pipeline"),
    "budget": dict(words="budget forecast expense variance allocation projection spreadsheet capex payroll quarter".split(),
                   idents=["budget_model.xlsx"], site="finance.example.com/budget"),
    "retry": dict(words="retry backoff idempotent timeout webhook exponential jitter deadline circuit".split(),
                  idents=["retry_policy.py", "webhook_client.py"], site="github.com/acme/billing"),
    "pagination": dict(words="pagination cursor offset serializer endpoint pagesize ordering listing".split(),
                       idents=["list_view.py", "page_cursor.py"], site="github.com/acme/billing"),
    "docs": dict(words="readme tutorial changelog glossary diagram walkthrough screenshots".split(),
                 idents=["README.md", "CHANGELOG.md"], site="github.com/acme/billing"),
    "deck_wood": dict(words="lumber joist railing stain sanding cedar fasteners permit patio deck".split(),
                      idents=["deck_plan.pdf"], site="homeproject.example.com/deck"),
    "deck_pitch": dict(words="investor traction valuation slides narrative funding fundraising pitch deck".split(),
                       idents=["pitch_deck.pptx"], site="decks.example.com/pitch"),
    "taxes": dict(words="deduction filing refund withholding bracket schedule accountant estimated".split(),
                  idents=["tax_summary.pdf"], site="tax.example.com/filing"),
}

CATEGORIES = ("reading", "typing", "scrolling", "navigating")


# ---------------------------------------------------------------- items

@dataclass(frozen=True)
class Item:
    app: str
    bundle: str
    kind: str
    target: str
    window: str
    url: str = None

    @property
    def ref(self):
        return f"{self.app}|{self.kind}|{self.target}"

    @property
    def item_id(self):
        return "item-" + hashlib.sha1((self.ref + "|" + (self.url or "")).encode()).hexdigest()[:10]


def chrome(kind, target, window, url):
    return Item("Google Chrome", "com.google.Chrome", kind, target, window, url)


def code(file, project="workspace"):
    return Item("Visual Studio Code", "com.microsoft.VSCode", "code_file", file, f"{file} - {project}")


def terminal(title="zsh"):
    return Item("Terminal", "com.apple.Terminal", "terminal", "zsh", title)


def slack(channel="general"):
    return Item("Slack", "com.tinyspeck.slackmacgap", "chat", channel, f"{channel} - Slack")


def corrupt(word, rng):
    """Garble a word the way OCR does, keeping it letters only and at least 4 long."""
    if len(word) < 5:
        return word
    i = rng.randrange(1, len(word) - 1)
    op = rng.choice(("drop", "double", "swap", "ell"))
    w = list(word)
    if op == "drop":
        del w[i]
    elif op == "double":
        w.insert(i, w[i])
    elif op == "swap":
        w[i], w[i + 1] = w[i + 1], w[i]
    else:
        w[i] = rng.choice("li")
    out = "".join(w)
    return out if out != word and len(out) >= 4 else word


# ---------------------------------------------------------------- the fixture object

@dataclass
class Fixture:
    name: str
    seed: int
    records: list
    gold: dict
    unsure: set = field(default_factory=set)
    must_merge: list = field(default_factory=list)          # [(visit_id, visit_id)]
    must_not_merge: list = field(default_factory=list)
    cannot_link: list = field(default_factory=list)         # visit pairs the raw-feature rules must forbid
    expect_unassigned: list = field(default_factory=list)   # visit ids that must not end up assigned
    shared_artifacts: list = field(default_factory=list)    # item ids that serve more than one activity
    flagged_shift: list = field(default_factory=list)       # visits internal_shift should flag
    unflaggable_shift: list = field(default_factory=list)   # mixed-gold visits the design cannot flag
    returns: int = None                                     # expected number of returns to the main activity
    notes: str = ""

    def moments_of(self):
        out = {}
        for r in sorted(self.records, key=lambda r: (r["t_start"], r["seq"])):
            out.setdefault(r["visit_id"], []).append(r["id"])
        return out

    def visit_ids(self):
        return list(self.moments_of())

    def write_jsonl(self, directory):
        """Day files as the MomentStore writes them (owner-only). Returns the paths."""
        os.makedirs(directory, mode=0o700, exist_ok=True)
        days = {}
        for r in sorted(self.records, key=lambda r: (r["t_start"], r["seq"])):
            days.setdefault(time.strftime("%Y-%m-%d", time.localtime(r["t_start"])), []).append(r)
        paths = []
        for day, rows in sorted(days.items()):
            path = os.path.join(directory, day + ".jsonl")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
            paths.append(path)
        return paths

    def write_gold_sheet(self, directory):
        """units.csv in the annotation-sheet format with the gold filled in; activity_eval.load_sheet reads it back."""
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, "units.csv")
        units = ev.units_from_moments(self.records)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ev.SHEET_UNITS)
            w.writeheader()
            for u in units:
                g = self.gold.get(u["unit_id"])
                labels = sorted(g) if g else []
                w.writerow({"unit_id": u["unit_id"], "visit_id": u["visit_id"], "moment_id": u["moment_id"],
                            "start": "", "dur_s": round(u["dur_s"]), "app": u["app"] or "", "where": u.get("where") or "",
                            "item_id": u["item_id"] or "", "what": "",
                            "activity": "" if g is None else (labels[0] if labels else "-"),
                            "also": ";".join(labels[1:]),
                            "unsure": "Y" if u["unit_id"] in self.unsure else "", "note": ""})
        return path


# ---------------------------------------------------------------- the builder

class Builder:
    """Lays visits end to end on a clock and emits real Moment records. Structure and content come from `rng`,
    OCR noise from a separate stream, so noise never changes ids, times or visit structure."""

    def __init__(self, seed, noise=0.0, t0=T0, clock="v"):
        self.seed, self.noise, self.clock = seed, noise, clock
        self.rng = random.Random(seed)
        self.nrng = random.Random(seed * 7919 + 13)
        self.t = t0
        self.n = 0
        self.records, self.gold, self.unsure = [], {}, set()
        self.visits = []                                   # visit ids in order
        self.last = {}                                     # item_id -> {"visit_id", "ended_at", "visits"}
        self.prev_id = None
        self.prev_end = None

    def wait(self, seconds):
        self.t += seconds
        return self

    # ---- content
    def words(self, topic, n=8, generic=2, extra=()):
        pool = TOPICS[topic]["words"]
        picked = self.rng.sample(pool, min(n, len(pool))) + self.rng.sample(GENERIC, generic) + list(extra)
        garbled = []
        for w in picked:
            u = self.nrng.random()
            garbled.append(corrupt(w, self.nrng) if u < self.noise else w)
        self.nrng.random()
        return garbled

    def part(self, topic, dwell, cat="reading", n=8, generic=2, extra=(), idents=1, extra_idents=(), labels="__visit__",
             unsure=False, empty=False, frames=None, lines=True):
        """One moment's spec. `topic` None gives a thin generic digest."""
        if topic is None:
            terms, ids = self.rng.sample(GENERIC, max(0, min(n, 3))), []
        else:
            terms = self.words(topic, n, generic, extra)
            ids = (self.rng.sample(TOPICS[topic]["idents"], min(idents, len(TOPICS[topic]["idents"]))) if idents else []) + list(extra_idents)
        if empty or (self.noise and self.nrng.random() < self.noise / 4):
            terms, ids = [], []
        text = [" ".join(terms[:6])[:80]] if lines and len(terms) >= 3 and len(" ".join(terms[:6])) >= 12 else []
        return {"dwell": float(dwell), "cat": cat, "terms": terms, "idents": ids, "lines": text, "labels": labels,
                "unsure": unsure, "frames": frames}

    # ---- visits
    def visit(self, item, parts, labels=None, gap=2.0, trigger=None):
        """Append one visit made of `parts` (one moment each). `labels`: set of labels for every moment unless a part
        overrides; None leaves moments unlabelled; set() is noise. Returns the visit id."""
        if isinstance(parts, dict):
            parts = [parts]
        self.t += gap
        self.n += 1
        t0 = self.t
        vid = f"{self.clock}{int(t0)}-{self.n}"
        before = self.last.get(item.item_id)
        same = None
        if before:
            same = {"visit_id": before["visit_id"], "ended_at": before["ended_at"],
                    "gap_s": round(t0 - before["ended_at"], 1), "visits_before": before["visits"]}
        u, domain = moments.clean_url(item.url) if item.url else (None, None)
        prev_id, prev_end = self.prev_id, self.prev_end
        for seq, p in enumerate(parts):
            a, b = self.t, self.t + p["dwell"]
            dg_terms, dg_ids = p["terms"], p["idents"]
            digest = {"terms": list(dg_terms)[:12], "identifiers": list(dg_ids)[:8], "lines": list(p["lines"]),
                      "text_hash": hashlib.sha1(" ".join(sorted(dg_terms + dg_ids)).encode()).hexdigest()[:12] if (dg_terms or dg_ids) else None}
            frames = p["frames"] or max(1, int(p["dwell"] // 3))
            m = moments.Moment(
                id=f"{vid}.{seq}", visit_id=vid, seq=seq, t_start=a, t_end=b, item_id=item.item_id, app=item.app,
                bundle_id=item.bundle, window=item.window, url=u, domain=domain, kind=item.kind, target=item.target,
                ref=item.ref, n_frames=frames, obs_id=None,
                interaction={"category": p["cat"], "seconds": {p["cat"]: round(p["dwell"], 1)},
                             "ages": {"key": 2.0 if p["cat"] == "typing" else 30.0, "scroll": 5.0, "click": 8.0}},
                digest=digest, provenance={"ocr": frames},
                context={"prev_moment_id": prev_id if seq == 0 else f"{vid}.{seq - 1}",
                         "gap_s": round(a - prev_end, 1) if (seq == 0 and prev_end is not None) else (0.0 if seq else None),
                         "same_item_before": same, "trigger": trigger, "pinned": False},
                continued=False)
            self.records.append(m.to_dict())
            lab = labels if p["labels"] == "__visit__" else p["labels"]
            if lab is not None:
                self.gold[m.id] = set(lab)
            if p["unsure"]:
                self.unsure.add(m.id)
            self.t = b
            prev_id, prev_end = m.id, b
        self.prev_id, self.prev_end = prev_id, prev_end
        self.last[item.item_id] = {"visit_id": vid, "ended_at": self.t, "visits": (before["visits"] + 1) if before else 1}
        self.visits.append(vid)
        return vid

    def done(self, name, **kw):
        return Fixture(name, self.seed, self.records, self.gold, self.unsure, **kw)


def pairs(ids):
    return [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]


# ---------------------------------------------------------------- scenarios

def continuous_multiapp(seed=1, noise=0.0, rounds=3, t0=T0):
    """1 and 8. Paper -> chat -> editor -> terminal, repeated: one activity across four apps."""
    b = Builder(seed, noise, t0)
    paper = chrome("reading_page", "arxiv.org/abs/2601.00123", "Attention scaling study", "https://arxiv.org/abs/2601.00123")
    chat = Item("ChatGPT", "com.openai.chat", "chat", "c/42", "ChatGPT")
    editor, term = code("attention_layer.py", "paper-impl"), terminal("pytest attention_layer.py")
    core = ["attention", "softmax", "residual", "warmup", "perplexity"]
    ids, lab = [], {"paper-impl"}
    for _ in range(rounds):
        for item, cat, dwell in ((paper, "reading", 70), (chat, "typing", 90), (editor, "typing", 120), (term, "reading", 40)):
            ids.append(b.visit(item, b.part("paper", dwell, cat, n=6, extra=b.rng.sample(core, 3), extra_idents=["attention_layer.py"]), lab))
            b.wait(b.rng.choice((2, 5, 12)))
    return b.done("continuous_multiapp" + (f"_noise{int(noise * 100)}" if noise else ""),
                  must_merge=[(ids[i], ids[i + 1]) for i in range(len(ids) - 1)] + [(ids[0], ids[-1])],
                  notes="no app change is a boundary; every assigned visit needs two independent families")


def two_in_one_app(seed=2, rounds=6):
    """2. Chrome alternates between invoice pages and trip pages. App is not evidence."""
    b = Builder(seed)
    inv = [chrome("reading_page", f"books.example.com/invoices/{i}", f"Invoice {i}", f"https://books.example.com/invoices/{i}") for i in range(3)]
    trip = [chrome("reading_page", f"trips.example.com/lisbon/{i}", f"Lisbon {i}", f"https://trips.example.com/lisbon/{i}") for i in range(3)]
    a_ids, b_ids = [], []
    for r in range(rounds):
        a_ids.append(b.visit(inv[r % 3], b.part("invoices", 80, n=7), {"invoices"}))
        b_ids.append(b.visit(trip[r % 3], b.part("trip", 80, n=7), {"trip"}))
    return b.done("two_in_one_app", must_merge=pairs(a_ids) + pairs(b_ids),
                  must_not_merge=[(x, y) for x in a_ids for y in b_ids],
                  notes="renaming every app/bundle at random must not change anything")


def interruptions_returns(seed=3, stretches=4):
    """3. Work on one file, four short interruptions in other apps; the interruption is never absorbed."""
    b = Builder(seed)
    ed = code("retry_policy.py", "billing")
    noise_items = [slack("general"), chrome("mail", "mail.google.com/inbox", "Inbox", "https://mail.google.com/mail/u/0/"),
                   chrome("reading_page", "news.example.com/today", "Today", "https://news.example.com/today"),
                   Item("Calendar", "com.apple.iCal", "app_window", "week", "Calendar")]
    work, glances = [], []
    for s in range(stretches):
        work.append(b.visit(ed, b.part("retry", 170, "typing", n=7, extra_idents=["retry_policy.py"]), {"billing"}))
        if s < stretches - 1:
            glances.append(b.visit(noise_items[s % 4], b.part(None, 15, "reading", n=2, lines=False, frames=1), set()))
    return b.done("interruptions_returns", must_merge=pairs(work), expect_unassigned=glances, returns=stretches - 1,
                  notes="glances are no_substance; the main activity records each resume")


def long_gaps(seed=4, gap_s=3600.0):
    """4. A return after a long gap joins on shared specific content; unrelated work after the gap is new."""
    b = Builder(seed)
    ed, term = code("webhook_client.py", "billing"), terminal("pytest webhook_client.py")
    first = [b.visit(ed, b.part("retry", 200, "typing", n=7, extra_idents=["webhook_client.py"]), {"billing"}),
             b.visit(term, b.part("retry", 60, n=6, extra=["webhook", "backoff"]), {"billing"})]
    b.wait(gap_s)
    back = b.visit(ed, b.part("retry", 150, "typing", n=7, extra=["webhook", "backoff"], extra_idents=["webhook_client.py"]), {"billing"})
    other = b.visit(chrome("reading_page", "tax.example.com/filing", "Filing", "https://tax.example.com/filing"),
                    b.part("taxes", 120, n=7), {"taxes"})
    return b.done("long_gaps", must_merge=pairs(first + [back]), must_not_merge=[(x, other) for x in first + [back]],
                  notes="directional fixture for the time parameters: move gap_s across t_resume_s")


def shared_entities(seed=5):
    """5. Two unrelated activities that both mention the same person and company."""
    b = Builder(seed)
    sheet = chrome("document", "docs.example.com/cafe-budget", "Cafe budget", "https://docs.example.com/cafe-budget")
    plan = chrome("document", "docs.example.com/hiring-plan", "Hiring plan", "https://docs.example.com/hiring-plan")
    mail = chrome("mail", "mail.example.com/tara", "Mail from Tara", "https://mail.example.com/tara")
    bud, hire = [], []
    for r in range(3):
        bud.append(b.visit(sheet, b.part("budget", 90, "typing", n=6, extra=["tara", "harbour"]), {"cafe-budget"}))
        hire.append(b.visit(plan, b.part("hiring", 90, "typing", n=6, extra=["tara", "harbour"]), {"hiring"}))
        b.visit(mail, b.part(None, 25, n=3), set())
    return b.done("shared_entities", must_merge=pairs(bud) + pairs(hire), must_not_merge=[(x, y) for x in bud for y in hire],
                  notes="tara and harbour are shared and must not link the two activities")


def overlap_sweep(k, seed=6, fillers=12):
    """6. The same story seen in two apps sharing k rare words, inside a background of unrelated visits (for idf).
    Gold is left empty on purpose: the property is that the join happens at one k and stays joined above it."""
    b = Builder(seed)
    rare = ["gantry", "ferrite", "quorum", "lattice", "tessera", "umbrage", "vellum", "zephyr"][:k]
    topics = ["invoices", "trip", "hiring", "taxes", "deck_wood", "deck_pitch"]
    for i in range(fillers):
        t = topics[i % len(topics)]
        b.visit(chrome("reading_page", f"{t}.example.com/{i}", f"{t} {i}", f"https://{t}.example.com/{i}"), b.part(t, 60, n=6))
    v1 = b.visit(chrome("reading_page", "wiki.example.com/a", "Reading A", "https://wiki.example.com/a"),
                 b.part("deck_wood", 90, n=4, extra=rare, idents=0))
    v2 = b.visit(code("notes.txt", "scratch"), b.part("deck_pitch", 90, "typing", n=4, extra=rare, idents=0))
    f = b.done(f"overlap_sweep_{k}", must_merge=[], notes="gold intentionally empty; compare runs across k")
    f.gold = {}
    f.visit_pair = (v1, v2)
    return f


def ambiguous_moments(seed=7):
    """7. Thin, generic, unsure visits: abstaining is the right answer, and every one needs a reason."""
    b = Builder(seed)
    home = chrome("reading_page", "www.example.com", "Home", "https://www.example.com/")
    ids = []
    for i in range(6):
        ids.append(b.visit(home, b.part(None, 25, n=3, lines=False, unsure=True), {"misc"}))
    ids.append(b.visit(slack("random"), b.part(None, 20, n=2, lines=False, unsure=True), {"misc"}))
    f = b.done("ambiguous_moments", expect_unassigned=ids)
    return f


def noisy_variants(seed=1, levels=(0.0, 0.1, 0.2, 0.4)):
    """8. The continuous fixture at several OCR corruption levels. Same ids, times and visits at every level."""
    return {lv: continuous_multiapp(seed, lv) for lv in levels}


def shared_doc_two_purposes(seed=9, gap_s=5 * 3600.0):
    """9 (S1). One document, opened for a budget and, five hours later, for hiring."""
    b = Builder(seed)
    doc = chrome("document", "docs.google.com/document/d/q3plan", "Q3 plan", "https://docs.google.com/document/d/q3plan")
    sheet = chrome("document", "sheets.example.com/budget", "Budget", "https://sheets.example.com/budget")
    jobs = chrome("reading_page", "jobs.example.com/pipeline", "Pipeline", "https://jobs.example.com/pipeline")
    a = [b.visit(sheet, b.part("budget", 80, "typing", n=6), {"budget"}),
         b.visit(doc, b.part("budget", 120, "typing", n=6), {"budget"}),
         b.visit(sheet, b.part("budget", 60, n=6), {"budget"})]
    b.wait(gap_s)
    h = [b.visit(jobs, b.part("hiring", 80, n=6), {"hiring"}),
         b.visit(doc, b.part("hiring", 120, "typing", n=6), {"hiring"}),
         b.visit(jobs, b.part("hiring", 60, n=6), {"hiring"})]
    return b.done("shared_doc_two_purposes", must_merge=[(a[0], a[2]), (h[0], h[2])],
                  must_not_merge=[(a[1], h[1]), (a[0], h[0])], shared_artifacts=[doc.item_id],
                  notes="a shared document is a candidate link, not a verdict (12.3)")


def hub_between(seed=10):
    """9 (S2). An inbox visited between two activities never bridges them."""
    b = Builder(seed)
    inbox = chrome("mail", "mail.google.com/inbox", "Inbox", "https://mail.google.com/mail/u/0/#inbox")
    xs, ys, hubs = [], [], []
    for r in range(3):
        xs.append(b.visit(code("retry_policy.py", "billing"), b.part("retry", 100, "typing", n=6), {"billing"}))
        hubs.append(b.visit(inbox, b.part(None, 30, n=3, lines=False), set()))
        ys.append(b.visit(chrome("reading_page", f"trips.example.com/lisbon/{r}", f"Lisbon {r}", f"https://trips.example.com/lisbon/{r}"),
                          b.part("trip", 100, n=6), {"trip"}))
        hubs.append(b.visit(inbox, b.part(None, 30, n=3, lines=False), set()))
    return b.done("hub_between", must_merge=pairs(xs) + pairs(ys), must_not_merge=[(x, y) for x in xs for y in ys],
                  shared_artifacts=[inbox.item_id], expect_unassigned=hubs)


def same_doc_short_gap(seed=11):
    """9 (S3). The same document, a short gap, the same terms: one activity."""
    b = Builder(seed)
    doc = chrome("document", "docs.google.com/document/d/spec", "Spec", "https://docs.google.com/document/d/spec")
    ref = chrome("reading_page", "wiki.example.com/spec", "Wiki", "https://wiki.example.com/spec")
    x = [b.visit(doc, b.part("pagination", 100, "typing", n=7, extra=["cursor"]), {"spec"}),
         b.visit(ref, b.part("pagination", 40, n=7, extra=["cursor"]), {"spec"}),
         b.visit(doc, b.part("pagination", 100, "typing", n=7, extra=["cursor"]), {"spec"})]
    return b.done("same_doc_short_gap", must_merge=pairs(x))


def same_tab_different_file(seed=12):
    """9 (S4). One terminal tab reused for two projects: same item id, different work."""
    b = Builder(seed)
    tab = terminal("zsh")
    a = b.visit(tab, b.part("retry", 90, n=6, extra_idents=["retry_policy.py"]), {"billing"})
    b.wait(300)
    c = b.visit(tab, b.part("paper", 90, n=6, extra_idents=["train_loop.py"]), {"paper-impl"})
    return b.done("same_tab_different_file", must_not_merge=[(a, c)], shared_artifacts=[tab.item_id])


def _gh(path, title):
    return chrome("reading_page", f"github.com/acme/billing/{path}", title, f"https://github.com/acme/billing/{path}")


def branches_alternating(seed=13):
    """10 (B1). Two branches of one repo, two unrelated tasks, alternating: branch evidence separates them."""
    b = Builder(seed)
    t1, t2 = _gh("tree/feature-retry", "billing at feature-retry"), _gh("tree/fix-pagination", "billing at fix-pagination")
    e1, e2 = code("retry_policy.py", "billing (feature-retry)"), code("page_cursor.py", "billing (fix-pagination)")
    r_ids, p_ids = [], []
    for _ in range(3):
        r_ids += [b.visit(t1, b.part("retry", 50, n=5), {"retry"}), b.visit(e1, b.part("retry", 110, "typing", n=6), {"retry"})]
        p_ids += [b.visit(t2, b.part("pagination", 50, n=5), {"pagination"}), b.visit(e2, b.part("pagination", 110, "typing", n=6), {"pagination"})]
    return b.done("branches_alternating", must_merge=pairs(r_ids) + pairs(p_ids),
                  must_not_merge=[(x, y) for x in r_ids for y in p_ids], cannot_link=[(r_ids[0], p_ids[0]), (r_ids[1], p_ids[1])])


def same_branch_two_tasks(seed=14):
    """10 (B2). One non-integration branch used for two unrelated tasks, nothing else shared: not merged."""
    b = Builder(seed)
    page = _gh("tree/dev-wip", "billing at dev-wip")
    a = [b.visit(page, b.part("retry", 60, n=5), {"retry"}), b.visit(code("webhook_client.py", "billing (dev-wip)"), b.part("retry", 100, "typing", n=6), {"retry"})]
    b.wait(1800)
    c = [b.visit(page, b.part("docs", 60, n=5), {"docs"}), b.visit(code("README.md", "billing (dev-wip)"), b.part("docs", 100, "typing", n=6), {"docs"})]
    return b.done("same_branch_two_tasks", must_not_merge=[(x, y) for x in a for y in c], shared_artifacts=[page.item_id],
                  notes="same branch is soft context; it never assigns or merges alone")


def same_pr_three_pages(seed=15):
    """10 (B3). The same PR on a GitHub page, in the editor title and in the terminal: one activity."""
    b = Builder(seed)
    pr = _gh("pull/12", "Add retry backoff by dev - Pull Request #12")
    e = code("retry_policy.py", "billing - #12")
    t = terminal("gh pr checkout 12")
    ids = [b.visit(pr, b.part("retry", 80, n=6, extra_idents=["#12"]), {"pr12"}),
           b.visit(e, b.part("retry", 120, "typing", n=6, extra_idents=["#12"]), {"pr12"}),
           b.visit(t, b.part("retry", 40, n=5, extra_idents=["#12"]), {"pr12"})]
    return b.done("same_pr_three_pages", must_merge=pairs(ids))


def pr_numbers_differ(seed=16):
    """10 (B4). PR 12 and PR 15 in one repository: distinct work, kept apart (must_not_merge), but only as contrast evidence:
    different PR numbers are never a hard cannot-link (one Activity can span several PRs; ADR 0004)."""
    b = Builder(seed)
    p12, p15 = _gh("pull/12", "Add retry backoff - Pull Request #12"), _gh("pull/15", "Fix pagination order - Pull Request #15")
    a = [b.visit(p12, b.part("retry", 90, n=6, extra_idents=["#12"]), {"pr12"}), b.visit(p12, b.part("retry", 80, n=6, extra_idents=["#12"]), {"pr12"})]
    c = [b.visit(p15, b.part("pagination", 90, n=6, extra_idents=["#15"]), {"pr15"}), b.visit(p15, b.part("pagination", 80, n=6, extra_idents=["#15"]), {"pr15"})]
    return b.done("pr_numbers_differ", must_merge=[tuple(a), tuple(c)], must_not_merge=[(x, y) for x in a for y in c],
                  cannot_link=[])


def blob_slash_branch(seed=17):
    """10 (B5). A blob URL whose branch contains a slash cannot be split from the path: no branch, so no cannot-link."""
    b = Builder(seed)
    x = b.visit(_gh("blob/feature/retry/src/a.py", "a.py"), b.part("retry", 90, n=6), {"retry"})
    y = b.visit(_gh("blob/feature/pagination/src/b.py", "b.py"), b.part("pagination", 90, n=6), {"pagination"})
    return b.done("blob_slash_branch", must_not_merge=[(x, y)], cannot_link=[],
                  notes="no observed branch on either side: absence is never evidence for or against")


def internal_shift(seed=18):
    """11. A long visit that changes topic (flag it), category flips with one digest (do not), a change the digest never sees."""
    b = Builder(seed)
    doc = chrome("document", "docs.google.com/document/d/notes", "Notes", "https://docs.google.com/document/d/notes")
    shifted = b.visit(doc, [b.part("budget", 250, "typing", n=7, labels={"budget"}),
                            b.part("hiring", 350, "typing", n=7, labels={"hiring"})])
    same = b.part("retry", 130, "reading", n=7, labels={"billing"})
    flips = b.visit(code("retry_policy.py", "billing"), [same, dict(same, cat="typing", dwell=130.0), dict(same, cat="reading", dwell=140.0)])
    # the digest never changes although the gold does (the design cannot see it)
    flat = b.part("trip", 150, "reading", n=7, labels={"trip"})
    blind = b.visit(chrome("reading_page", "trips.example.com/long", "Long page", "https://trips.example.com/long"),
                    [flat, dict(flat, labels={"invoices"}, dwell=150.0, cat="scrolling")])
    return b.done("internal_shift", flagged_shift=[shifted], unflaggable_shift=[blind],
                  notes=f"category-flip-only visit {flips} must not be flagged")


BUILDERS = {f.__name__: f for f in (
    continuous_multiapp, two_in_one_app, interruptions_returns, long_gaps, shared_entities, ambiguous_moments,
    shared_doc_two_purposes, hub_between, same_doc_short_gap, same_tab_different_file, branches_alternating,
    same_branch_two_tasks, same_pr_three_pages, pr_numbers_differ, blob_slash_branch, internal_shift)}


def all_fixtures():
    """Every fixture once (the sweep and the noise levels are separate generators)."""
    out = [f() for f in BUILDERS.values()]
    out += [overlap_sweep(k) for k in (0, 2, 4)] + list(noisy_variants().values())[1:]
    return out

"""LMemM - a generator of long, labelled activity streams with the hard cases built in. Test data only.

Real labelled weeks are the true test, but there are none yet. This writes streams in the same shape as
tests/fixtures/project_grouping/sample_day.json (memory.json items with a "label", plus a "timeline"),
for any number of projects, so grouping can be measured at 100, 500 or 1,000 projects.

It was written from the hard cases in the architecture review, not from any grouping method:

    shared resources   a playbook or template used by 2 projects (labelled with both; the projects must
                       still stay apart)
    shared people      every colleague works on about 3 unrelated projects
    same client        about 1 in 10 projects is a second engagement with another project's client
    shared workspace   only 5 Slack workspaces and 3 GitHub organisations for everything
    renames            about 1 in 10 projects changes its code name halfway through
    two repos          about 1 in 10 software projects has a second repo
    no names           about 1 in 5 places never names its project (Untitled document, a DM, a Zoom call)
    interleaving       about 1 in 4 sittings mixes two projects; 1 in 3 has a short interruption
    distractors        an inbox, #general, news pages that mention a client, music

Everything is drawn from a seeded random generator, so a seed always gives the same stream. Tune on
some seeds and report on others; the "world" block says which project pairs share what, for scoring
the hard cases on their own.
"""

import random
from datetime import datetime, timedelta

SYL_A = "b br c d f g h k l m n p r s st t v w z".split()
SYL_V = "a e i o u ai ea io".split()
SYL_C = "n r l s x th nd rk lt m".split()
FIRST = ("Aarav Priya Rohan Meera Arjun Ishaan Kavya Ananya Vikram Neha Sara Daniel Emma Lucas Olivia Noah Mia "
         "Leo Zara Omar Lena Hugo Ines Tariq Yuki Kenji Amara Kofi Elena Mateo Sofia Felix Nora Ravi Divya").split()
LAST = ("Shah Iyer Mehta Raman Okafor Silva Novak Berg Tanaka Haddad Costa Fischer Moreau Rossi Khan Patel "
        "Kim Larsen Duarte Weber Sato Nair Bose Adeyemi Lund Varga Quinn Ortiz Hale Brandt").split()
INDUSTRY = "Logistics Health Foods Labs Capital Retail Energy Studio Mobility Analytics Pharma Freight".split()
WORKSTREAMS = [("pricing model", "pricing tier margin discount churn"), ("dashboard", "dashboard chart metric filter widget"),
               ("vendor onboarding", "vendor contract onboarding checklist compliance"),
               ("customer feedback", "feedback survey interview complaint rating"),
               ("launch plan", "launch campaign timeline press checklist"), ("hiring", "candidate interview offer role panel"),
               ("budget", "budget forecast spend quarter variance"), ("api migration", "endpoint migration schema token latency"),
               ("client workshop", "workshop agenda slides attendees recap"), ("contract renewal", "renewal clause term invoice notice"),
               ("market sizing", "market segment clinics adoption estimate"), ("mobile app", "screen login release build crash")]
VERBS = "check update review fix send share finalise draft compare move".split()
WORKSPACES = ["Northstar Partners", "Acme Collective", "Studio Nine", "Delta Advisory", "Bright Labs"]
GH_ORGS = ["brightlabs", "acmeco", "studio-nine"]
PERSONAL = [("Trip", "flights hotel itinerary visa packing"), ("Wedding", "caterer venue guests invitations mehendi"),
            ("Apartment move", "lease movers deposit furniture utilities")]


class _Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.used = set()

    def name(self):
        while True:
            n = "".join(self.r.choice(SYL_A) + self.r.choice(SYL_V) for _ in range(self.r.choice((1, 2)))) + self.r.choice(SYL_C)
            n = n.capitalize()
            if len(n) >= 4 and n not in self.used:
                self.used.add(n)
                return n

    def person(self):
        while True:
            p = f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"
            if p not in self.used:
                self.used.add(p)
                return p


def _world(g, n_projects):
    r = g.r
    people = [g.person() for _ in range(max(6, n_projects))]
    clients = []
    projects = []
    for i in range(n_projects):
        personal = r.random() < 0.06
        if not personal and clients and r.random() < 0.1:
            client = r.choice(clients)                            # second engagement, same client
        elif personal:
            client = None
        else:
            client = {"name": f"{g.name()} {r.choice(INDUSTRY)}"}
            client["domain"] = client["name"].split()[0].lower() + ".example"
            clients.append(client)
        code = g.name()
        software = not personal and r.random() < 0.35
        org = r.choice(GH_ORGS)
        repos = [f"{org}/{code.lower()}"] + ([f"{org}/{g.name().lower()}"] if software and r.random() < 0.1 else [])
        if personal:
            title, words = r.choice(PERSONAL)
            streams = [(title.lower(), words)]
        else:
            streams = r.sample(WORKSTREAMS, r.choice((1, 2, 2, 3)))
        projects.append({
            "id": f"p{i:04d}", "code": code, "label": (f"{title} {code}" if personal else code), "client": client,
            "personal": personal, "software": software, "repos": repos if software else [],
            "workspace": None if personal else r.choice(WORKSPACES), "ticket": code[:3].upper(),
            "people": r.sample(people, 0 if personal else r.choice((1, 2, 3))),
            "renamed_to": g.name() if not personal and r.random() < 0.1 else None,
            "streams": [{"name": s[0], "words": s[1].split(), "numbers": [r.randint(120, 98000) for _ in range(2)]}
                        for s in streams]})
    return projects, people


def _item(pid, app, kind, title, label, state=None, typed=(), received=(), read=(), screen=()):
    item = {"id": pid, "app": app, "kind": kind, "title": title, "doing": f'Working on "{title}"', "state": state or {},
            "activity": {"typing": {"seconds": 30 if typed else 0, "text": list(typed)},
                         "reading": {"seconds": 60, "text": list(read)}}, "label": label}
    if received:
        item["activity"]["receiving"] = {"seconds": 0, "text": list(received), "count": len(received)}
    if screen:
        item["content"] = {"version": 1, "excerpts": [{"id": f"x{i}", "text": t, "source": {"app": app}}
                                                      for i, t in enumerate(screen)]}
    return item


def _places(g, p, s_idx, phase):
    """The places of one workstream. phase 1 = after a rename (new code name in new places)."""
    r = g.r
    s = p["streams"][s_idx]
    code = p["renamed_to"] if phase and p["renamed_to"] else p["code"]
    client = p["client"]["name"] if p["client"] else None
    who = p["people"][0] if p["people"] else None
    w1, w2, w3 = (r.choice(s["words"]) for _ in range(3))
    n1, n2 = s["numbers"]
    label = [f"{p['label']}/{s['name']}"]
    key = f"{p['id']}-{s_idx}-{phase}"
    named = lambda: r.random() > 0.2                          # 1 in 5 places never names the project
    out = []
    if p["personal"]:
        out.append(_item(f"{key}-sheet", "Google Sheets", "spreadsheet", f"{s['name'].title()} {code} budget", label,
                         {"url": f"https://docs.google.com/spreadsheets/d/1{key}"}, typed=[f"{w1} {n1}"],
                         screen=[f"{w1.title()} | {n1}", f"{w2.title()} | {n2}"]))
        out.append(_item(f"{key}-mail", "Gmail", "email", f"Your {w2} confirmation {code.upper()}{n2}", label,
                         {"subject": f"Your {w2} confirmation"}, read=[f"{w2} for {code} on {n1 % 28 + 1} Dec", f"Ref {n2}"]))
        out.append(_item(f"{key}-page", "Safari", "page", f"{w3.title()} options - {code}", label,
                         {"url": f"https://www.example-travel.com/{code.lower()}"}, read=[f"{w3} near {code}", f"{n1} total"]))
        return out
    folder = f"~/Clients/{client.split()[0]}/{code}" if client else f"~/Projects/{code}"
    out.append(_item(f"{key}-doc", "Microsoft Word", "document",
                     f"{code} {s['name']} v{r.randint(1, 4)}.docx" if named() else "Untitled document", label,
                     {"file": f"{folder}/{code}_{s['name'].replace(' ', '_')}.docx"} if named() else {},
                     typed=[f"{w1.title()} for {client or code}: {n1}" if named() else f"{w1} {n1}"],
                     read=[f"{w2} {w3} {n2}"]))
    out.append(_item(f"{key}-sheet", "Microsoft Excel", "spreadsheet", f"{code}_{w1}.xlsx", label,
                     {"file": f"{folder}/{code}_{w1}.xlsx"}, typed=[f"{w2} {n2}"],
                     screen=[f"{w1.title()} {n1}", f"{w2.title()} {n2}", f"{w3} {r.randint(10, 99)}%"]))
    chan = f"#{code.lower()}-{s['name'].split()[0]}"
    out.append(_item(f"{key}-chan", "Slack", "chat", chan, label, {"chat": chan, "workspace": p["workspace"]},
                     typed=[f"{r.choice(VERBS)}ed the {w1}, now {n1}"],
                     received=[f"{who}: can we {r.choice(VERBS)} the {w2} before Friday?"] if who else []))
    if who:
        out.append(_item(f"{key}-dm", "Slack", "chat", who, label, {"chat": who, "workspace": p["workspace"]},
                         received=[f"{who}: the {w1} number should be {n1}, not {n1 + 7}"]))
    if client:
        contact = f"{who or 'Team'} <{(who or 'team').split()[0].lower()}@{p['client']['domain']}>"
        out.append(_item(f"{key}-mail", "Outlook", "email", f"Re: {client} {s['name']}", label,
                         {"subject": f"Re: {client} {s['name']}", "to": [contact]},
                         typed=[f"Attaching the {w2} update ({n2})"], received=[f"{(who or 'Team').split()[0]}: thanks, {w3} looks right"]))
    if p["software"]:
        repo = r.choice(p["repos"])
        out.append(_item(f"{key}-code", "Visual Studio Code", "code_file", f"{w1}_{s['name'].split()[0]}.py", label,
                         {"file": f"{w1}_{s['name'].split()[0]}.py", "project": repo.split("/")[1]},
                         typed=[f"def {r.choice(VERBS)}_{w1}_{w2}():", f"LIMIT = {n1}"]))
        out.append(_item(f"{key}-pr", "Safari", "page", f"{r.choice(VERBS)} {w1} {w2} · Pull Request #{n2 % 400}", label,
                         {"url": f"https://github.com/{repo}/pull/{n2 % 400}"}, read=[f"{w1}_{w2} changed", f"LIMIT = {n1}"]))
    if r.random() < 0.3:
        out.append(_item(f"{key}-zoom", "zoom.us", "meeting", "Zoom Meeting", label, {},
                         screen=[x for x in (who, f"{s['name']} sync") if x]))
    return out


def generate(n_projects=100, seed=1, sittings_per_project=(3, 6), sittings_per_day=6):
    """A labelled stream: {"items": {id: item}, "timeline": [...], "world": {...}}."""
    g = _Gen(seed)
    r = g.r
    projects, people = _world(g, n_projects)
    items, plan = {}, []
    for p in projects:
        n = r.randint(*sittings_per_project)
        for k in range(n):
            phase = 1 if p["renamed_to"] and k >= n // 2 else 0
            s_idx = r.randrange(len(p["streams"]))
            places = _places(g, p, s_idx, phase)
            for it in places:
                items.setdefault(it["id"], it)
            plan.append((p, [it["id"] for it in places]))
    # shared resources: a playbook used by 2 projects, labelled with both
    shared_pairs = []
    for i in range(max(1, n_projects // 10)):
        a, b = r.sample([p for p in projects if not p["personal"]], 2)
        both = r.random() < 0.5
        text = (f"Used for {a['client']['name']} and {b['client']['name']}" if both and a["client"] and b["client"]
                else "Value-based tiers and discount rules")
        pid = f"shared-{i:03d}"
        sa, sb = a["streams"][0]["name"], b["streams"][0]["name"]
        items[pid] = _item(pid, "Google Docs", "document", r.choice(["Pricing playbook (shared)", "Proposal template",
                                                                      "Onboarding checklist (shared)"]),
                           [f"{a['label']}/{sa}", f"{b['label']}/{sb}"],
                           {"url": f"https://docs.google.com/document/d/1{pid}"}, read=[text, "Tier rules"])
        shared_pairs.append((a["id"], b["id"]))
        for owner in (a, b):
            plan.append((owner, [pid] + [x for pp, ids in plan if pp is owner for x in ids][:2]))
    # distractors
    items["hub-inbox"] = _item("hub-inbox", "Outlook", "mailbox", "Inbox", [], {},
                               received=[f"Re: {p['client']['name']} {p['streams'][0]['name']}" for p in projects[:3] if p["client"]])
    items["hub-general"] = _item("hub-general", "Slack", "chat", "#general", [], {"chat": "#general", "workspace": WORKSPACES[0]},
                                 read=["Happy Monday everyone", "Lunch is here"])
    items["music"] = _item("music", "Safari", "page", "lofi radio – YouTube", [], {"url": "https://www.youtube.com/watch?v=lofi"})
    news = []
    for i, p in enumerate(r.sample([p for p in projects if p["client"]], min(5, n_projects // 10 or 1))):
        nid = f"news-{i}"
        items[nid] = _item(nid, "Safari", "page", f"{p['client']['name']} raises new funding", [],
                           {"url": f"https://news.example.com/{nid}"}, read=[f"{p['client']['name']} expands {p['streams'][0]['words'][0]} team"])
        news.append(nid)
    r.shuffle(plan)
    # lay sittings on days
    timeline, day0 = [], datetime(2026, 1, 5, 9, 0)
    for n, (p, ids) in enumerate(plan):
        day = day0 + timedelta(days=n // sittings_per_day)
        t = day + timedelta(minutes=(n % sittings_per_day) * 75)
        seq = [r.choice(ids) for _ in range(r.randint(3, 7))]
        seq = list(dict.fromkeys(ids[:2])) + seq
        if r.random() < 0.25 and n + 1 < len(plan):            # interleave a second project's places
            other = plan[(n + 1) % len(plan)][1]
            for k in range(1, len(seq), 3):
                seq.insert(k, r.choice(other))
        if r.random() < 0.33:                                    # a short interruption
            seq.insert(r.randrange(1, len(seq)), r.choice(["hub-inbox", "hub-general", "music"] + news))
        for iid in seq:
            short = iid.startswith(("hub-", "news-", "music"))
            end = t + timedelta(minutes=r.randint(1, 2) if short else r.randint(3, 9))
            timeline.append({"item": iid, "from": t.isoformat(), "to": end.isoformat()})
            t = end
    used = {v["item"] for v in timeline}
    items = {k: v for k, v in items.items() if k in used}
    same_client = [(a["id"], b["id"]) for i, a in enumerate(projects) for b in projects[i + 1:]
                   if a["client"] and a["client"] is b["client"]]
    shared_person = [(a["id"], b["id"]) for i, a in enumerate(projects) for b in projects[i + 1:]
                     if set(a["people"]) & set(b["people"])]
    return {"version": 1, "about": f"Synthetic stream, {n_projects} projects, seed {seed}. Test data only.",
            "items": items, "timeline": timeline,
            "world": {"labels": {p["id"]: p["label"] for p in projects},
                      "hard_pairs": {"shared resource": shared_pairs, "same client": same_client,
                                     "shared person": shared_person,
                                     "same workspace": [(a["id"], b["id"]) for i, a in enumerate(projects)
                                                        for b in projects[i + 1:i + 40]
                                                        if a["workspace"] and a["workspace"] == b["workspace"]]},
                      "renamed": [p["id"] for p in projects if p["renamed_to"]]}}


def hard_case_merges(doc, assigned):
    """For each kind of hard pair: how many project pairs ended up sharing a predicted group through
    their own places (the shared resource itself excluded). The share that merged is the failure rate."""
    labels = doc["world"]["labels"]
    owner = {}
    for iid, item in doc["items"].items():
        tops = {l.split("/")[0] for l in item["label"]}
        if len(tops) == 1:
            owner[iid] = next(iter(tops))
    groups = {}
    for iid, paths in assigned.items():
        if iid in owner:
            for path in paths:
                groups.setdefault(path.split("/")[0], set()).add(owner[iid])
    merged = set()
    for members in groups.values():
        members = sorted(members)
        merged.update((a, b) for i, a in enumerate(members) for b in members[i + 1:])
    out = {}
    for kind, pairs in doc["world"]["hard_pairs"].items():
        pairs = [tuple(sorted((labels[a], labels[b]))) for a, b in pairs if labels[a] != labels[b]]
        out[kind] = (sum(1 for pr in pairs if pr in merged), len(pairs))
    return out

"""LMemM - projects as persistent things that tasks are assigned to, not clusters that chain. Prototype.

Step 3 of the architecture review. The graph mode (project_reasoner_graph.py) joined two tasks into one
project whenever they shared enough rare words, transitively. On a generated stream of 100 projects that
chained everything into one project through Slack workspace names and colleagues' names. Here:

    1. Entities   Each place gives typed entities: names (copied by the model or the capitalised-word
                  rule, grounded in the card), repos, file folders, email domains, people, channel names,
                  numbers and plain words. Each type has a prior: how much it can say about which project
                  this is (a repo a lot, a colleague a little, a Slack workspace almost nothing).
    2. Tasks      Within a sitting, places join when they share enough entities, weighted by prior and
                  rarity. Workspace names and plain words alone cannot join two places.
    3. Assign     Tasks are taken in time order. Each is scored against the projects that already share
                  one of its entities (an inverted index, so never all projects). An entity's weight for a
                  project is its prior x the share of its uses that were in that project x a damping for how
                  many projects use it. The task joins the best project only if it scores high enough AND
                  clearly beats the runner-up; otherwise it starts a new project if it has two or more
                  places, or stays unfiled. Nothing is ever joined transitively.
    4. Used by    Each place is itself an entity, so going back to the same doc is strong evidence.
                  A place used by two projects (a shared playbook) is listed under both, but its weight
                  is split between them and damped, so it can never pull the two together on its own.
    5. Parts      Inside a project, tasks are assigned to sub-projects the same way, using every entity
                  including plain words and numbers.
"""

import math
import re
from collections import Counter, defaultdict

from project_reasoner import _texts, sittings
from project_reasoner_graph import Model, model_names, tokens
from project_reasoner_steps import HUB_KINDS

PRIOR = {"repo": 1.0, "folder": 0.8, "domain": 0.8, "name": 0.7, "channel": 0.6, "number": 0.6,
         "person": 0.4, "word": 0.15, "workspace": 0.05, "org": 0.1}
PROJECT_MIN_PRIOR = 0.3          # plain words and workspaces never decide a project
TASK_LINK = 4.0                  # evidence to join two places in one sitting
ACCEPT = 1.0                     # score to join an existing project
MARGIN = 0.5                     # ... and lead over the runner-up
LEAD = 3.0                       # ... and at least this many times the runner-up's score
SUB_ACCEPT = 1.0                 # score to join an existing sub-project
MAX_PROJECTS_PER_ENTITY = 5      # entities in more projects than this are not used to find candidates
HUB_ANCHORS = 4                  # an entity seen beside more distinct anchors than this identifies nothing
SKIP_FOLDERS = {"~", "users", "desktop", "documents", "downloads", "home", "clients", "projects", ""}
FREE_MAIL = {"gmail", "outlook", "yahoo", "icloud", "hotmail", "mail"}


def entities(item, names=()):
    """{key: prior} for one place. A key found by several routes keeps its highest prior."""
    out = {}

    def add(key, kind):
        key = " ".join(str(key).casefold().split()).strip("#").strip()
        if len(key) >= 3 and PRIOR[kind] > out.get(key, 0):
            out[key] = PRIOR[kind]

    state = item.get("state") or {}
    if isinstance(state.get("workspace"), str):
        add(state["workspace"], "workspace")
    if isinstance(state.get("project"), str):
        add(state["project"], "repo")
    chat = state.get("chat")
    if isinstance(chat, str):
        if chat.startswith("#"):
            for part in re.split(r"[-_\s]+", chat.lstrip("#")):
                if part.casefold() not in {"general", "random"}:
                    add(part, "channel")
        else:
            add(chat, "person")
    path = state.get("file")
    if isinstance(path, str) and "/" in path:
        for part in path.split("/")[:-1]:
            if part.casefold() not in SKIP_FOLDERS:
                add(part, "folder")
    url = state.get("url")
    if isinstance(url, str):
        m = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)", url)
        if m:
            add(m.group(1), "org")
            add(m.group(2), "repo")
    for key in ("to", "from"):
        for person in state.get(key) if isinstance(state.get(key), list) else [state.get(key)]:
            m = re.match(r"\s*([^<]+?)\s*<[^@]+@([^>]+)>", person) if isinstance(person, str) else None
            if m:
                add(m.group(1), "person")
                domain = m.group(2).split(".")[0]
                if domain.casefold() not in FREE_MAIL:
                    add(domain, "domain")
    texts = _texts(item)
    for line in texts["received"]:                       # "Meera Shah: can we ..." is a person speaking
        m = re.match(r"\s*([A-Z][\w'-]+(?: [A-Z][\w'-]+){0,2}):", line)
        if m:
            add(m.group(1), "person")
    weak = set().union(*(tokens(k) for k, v in out.items() if v <= PRIOR["person"])) if out else set()
    for n in names:
        for w in tokens(n):
            if not w.isdigit() and w not in weak:        # a workspace or a person stays what it is
                add(w, "name")
    lines = [item.get("title") or ""] + [l for f in ("typed", "received", "notes", "read", "screen") for l in texts[f]]
    for w in tokens(" ".join(lines)):
        add(w, "number" if w.replace(".", "").isdigit() else "word")
    return out


class Projects:
    """Persistent projects with per-entity counts, and the inverted index from entity to projects."""

    def __init__(self):
        self.counts = defaultdict(Counter)        # entity -> Counter(project -> tasks using it)
        self.names = {}                           # project -> Counter of anchor names
        self.subs = defaultdict(list)             # project -> [Counter(entity -> count)]

    def weight(self, key, prior, project):
        c = self.counts.get(key)
        if not c or project not in c:
            return 0.0
        share = c[project] / sum(c.values())
        return prior * share / math.log2(1 + len(c))

    def candidates(self, ents):
        found = set()
        for key, prior in ents.items():
            c = self.counts.get(key)
            if prior >= PROJECT_MIN_PRIOR and c and len(c) <= MAX_PROJECTS_PER_ENTITY:
                found.update(c)
        return found

    def score(self, ents, project):
        hits = [(self.weight(k, p, project), k) for k, p in ents.items() if p >= PROJECT_MIN_PRIOR]
        hits = sorted((h for h in hits if h[0] > 0), reverse=True)
        return sum(w for w, _ in hits), [k for _, k in hits[:5]]

    def add(self, project, ents, name_keys):
        for key, prior in ents.items():
            if prior >= PROJECT_MIN_PRIOR:
                self.counts[key][project] += 1
        self.names.setdefault(project, Counter()).update(name_keys)


def _task_weight(prior, adjacent):
    """Between two places visited one straight after the other, shared words and numbers are the echo
    of reading here and typing there, so they count fully; a colleague's name counts half; a workspace
    or organisation not at all. Between places further apart in the sitting, where another project may
    have been open in between, each entity counts only by its prior."""
    if prior <= PRIOR["org"]:
        return 0.0
    if not adjacent:
        return prior
    return 0.5 if prior == PRIOR["person"] else 1.0


def _conflict(a, b):
    """Two places that each carry anchors (repo, folder, email domain) but share no anchor or name, not
    even as part of a longer one ("kestrel" in "kestrelhealth"), say they belong to different projects;
    shared topic words must not join them."""
    anchors_a = [k for k, v in a.items() if v >= PRIOR["folder"]]
    anchors_b = [k for k, v in b.items() if v >= PRIOR["folder"]]
    if not anchors_a or not anchors_b:
        return False
    strong_a = [k for k, v in a.items() if v >= PRIOR["name"]]
    strong_b = [k for k, v in b.items() if v >= PRIOR["name"]]
    return not any(x in y or y in x for x in strong_a for y in strong_b)


def _switches(timeline):
    """Pairs of places visited one straight after the other."""
    order = [v["item"] for v in sorted(timeline, key=lambda v: v["from"])]
    return {frozenset(pair) for pair in zip(order, order[1:]) if pair[0] != pair[1]}


def _sub_score(profile, ents):
    total = sum(profile.values()) or 1
    return sum(prior * min(1.0, profile[k] / max(1, total / 20)) for k, prior in ents.items() if k in profile)


def run(items, timeline, model=None):
    """Group a stream. `model(system, user, schema) -> dict` copies names, or None to use only what the
    app knows. Returns (assigned {id: [paths]}, projects {path: summary}, log)."""
    log = []
    ask = Model(model, log)
    places = [i for i in items if items[i].get("kind") not in HUB_KINDS]
    ents = {}
    for p in places:
        found = model_names(ask, items[p], log) if model is not None else []
        ents[p] = entities(items[p], found)
    # hubs: an entity seen beside many different anchors (repos, folders, email domains) points to no
    # single project, however concentrated it looks (a code keyword, a sentence opener, a colleague).
    # A place with no anchor of its own (a call, a chat) borrows the anchors of its sitting.
    anchors = {p: {k for k, prior in ents[p].items() if prior >= PRIOR["folder"]} for p in places}
    beside = defaultdict(set)
    for sitting in sittings(timeline):
        around = set().union(*(anchors.get(p, set()) for p in sitting))
        for p in sitting:
            if p in ents:
                for k in ents[p]:
                    beside[k].update((anchors[p] or around) - {k})
    hubs = set()
    for p in places:
        for k, prior in ents[p].items():
            if prior >= PROJECT_MIN_PRIOR and prior < PRIOR["folder"] and len(beside[k]) > HUB_ANCHORS:
                ents[p][k] = PRIOR["word"]
                hubs.add(k)
    if hubs:
        log.append({"step": "hub entities", "entities": sorted(hubs)[:50], "count": len(hubs)})
    df = Counter(k for p in places for k in ents[p])
    n = max(len(places), 1)
    idf = {k: math.log(n / c) for k, c in df.items()}

    # tasks: places linked inside a sitting
    tasks, next_to = [], _switches(timeline)
    for sitting in sittings(timeline):
        here = [p for p in sitting if p in ents]
        up = {p: p for p in here}

        def find(x):
            while up[x] != x:
                up[x] = up[up[x]]
                x = up[x]
            return x
        for i, a in enumerate(here):
            for b in here[i + 1:]:
                shared = ents[a].keys() & ents[b].keys()
                if _conflict(ents[a], ents[b]):
                    continue
                adj = frozenset((a, b)) in next_to
                ev = sum(_task_weight(min(ents[a][k], ents[b][k]), adj) * idf[k] for k in shared)
                if ev >= TASK_LINK:
                    up[find(a)] = find(b)
        groups = defaultdict(list)
        for p in here:
            groups[find(p)].append(p)
        tasks.extend(groups.values())

    reg = Projects()
    task_project, task_sub, made = {}, {}, 0
    for t_idx, task in enumerate(tasks):
        te = {f"place:{p}": PRIOR["repo"] for p in task}       # the places themselves are evidence too
        for p in task:
            for k, prior in ents[p].items():
                te[k] = max(te.get(k, 0), prior)
        cands = reg.candidates(te)
        scored = sorted(((reg.score(te, c), c) for c in cands), key=lambda x: -x[0][0])
        best = scored[0] if scored else ((0.0, []), None)
        second = scored[1][0][0] if len(scored) > 1 else 0.0
        (s, why), proj = best
        if proj is not None and s >= ACCEPT and s - second >= MARGIN and s >= LEAD * second:
            decision = f"joined {proj} ({s:.2f}, next {second:.2f}): {', '.join(why)}"
        elif len(task) >= 2:
            made += 1
            proj = f"P{made}"
            decision = f"new project {proj}" + (f" (best {best[1]} only {s:.2f}, next {second:.2f})" if best[1] else "")
        else:
            proj = None
            decision = "not filed: one place and no clear project" + (f" (best {s:.2f})" if best[1] else "")
        log.append({"step": "assign", "task": task, "decision": decision})
        if proj is None:
            continue
        names = [k for k, prior in te.items() if prior >= 0.6 and not k.startswith("place:")]
        reg.add(proj, te, names)
        task_project[t_idx] = proj
        # sub-project inside the project
        subs = reg.subs[proj]
        sub_scores = [(_sub_score(prof, te), i) for i, prof in enumerate(subs)]
        sbest = max(sub_scores, default=(0.0, None))
        if sbest[1] is not None and sbest[0] >= SUB_ACCEPT:
            subs[sbest[1]].update(te.keys())
            task_sub[t_idx] = sbest[1]
        else:
            subs.append(Counter(te.keys()))
            task_sub[t_idx] = len(subs) - 1

    # names and paths
    def label(proj):
        c = reg.names.get(proj) or Counter()
        top = [k for k, _ in c.most_common(3)]
        return (top[0] if top else proj).title()
    labels = {p: label(p) for p in set(task_project.values())}
    assigned, projects = defaultdict(set), {}
    multi = Counter(task_project.values())
    for t_idx, proj in task_project.items():
        nsubs = len(reg.subs[proj])
        path = f"{labels[proj]} [{proj}]" + (f"/part {task_sub[t_idx] + 1}" if nsubs > 1 else "")
        projects.setdefault(path, f"{multi[proj]} tasks")
        for p in tasks[t_idx]:
            assigned[p].add(path)
    log.append({"step": "calls", "decision": f"{ask.calls} model calls, {len(labels)} projects, {len(tasks)} tasks"})
    return {i: sorted(v) for i, v in assigned.items()}, projects, log

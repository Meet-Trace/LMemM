"""LMemM - the project finder's AI method: local models read what you did and decide what belongs together.

project_finder.py calls `find` while you are idle (config.FINDER_METHOD = "ai"). It runs the AI-first
pipeline (project_reasoner_ai.py) over your unfiled things of the last days and plans the same offers the
rules method did: a new project, or things for one of yours. Nothing is filed without your answer.

Who decides what
    code    picks the recent unfiled things, splits the day into sittings, retrieves the few projects a
            task is worth asking about, caches every answer, keeps to the call budget, and turns the result
            into offers. It never decides that two things belong together.
    models  the narrator (qwen3.5:0.8b) says what you were doing in each window; the judge (lmemm-judge,
            Qwen3-Reranker-0.6B taught on written examples) says whether a window is a general place,
            whether two windows are the same piece of work, and whether a task belongs to a project; the
            embedding model (EmbeddingGemma) only narrows which projects the judge is asked about.

How it stays light and careful on an 8 GB Mac
    - Every request asks Ollama for the CPU only (num_gpu 0, config.FINDER_THREADS threads). Measured 2026-11-08:
      Ollama's "mlx" runner, which loads the judge and the narrator, ignores that and uses the GPU; only the
      embedding model obeys it. So this is a request, not a guarantee, until the models are run another way.
    - Every answer is cached on disk by its question (finder_cache.json beside memory.json), so a thing is
      read once and a pair is judged once; a look makes at most config.FINDER_CALLS new calls and the next
      look carries on from the cache.
    - The narrator is unloaded after each sentence (keep_alive 0). Ollama gave a different sentence for the
      same window when its prompt cache was warm; a cold start gives the same one every time.
    - Your projects take part as seeds, each shown by its latest things; a task is judged against only
      config.FINDER_CANDIDATES of them, so hundreds of projects cost the same as five. A project none of
      whose things was used in the look-back days (14) is not a seed: things are not added to old projects.
    - A task the judge says yes to for two projects is filed in neither (a shared resource), and the
      rules for offering a new project still hold: it must recur in separate sittings.
    - A join needs a second opinion: the judge reads the task's windows and the project's latest ones as they
      are, without the narrator's sentences, and that mean answer must also be yes (config.FINDER_CONFIRM).
      Two views that depend on different text must agree before anything joins a project. This is what stops a
      project description that went wrong from pulling in another project's task: on the Mac a second run
      wrote "Northwind and LMemM" into the Northwind description and merged four LMemM windows into it.
    - Project names are written after grouping, only to show you; a name never joins or splits groups.
"""

import json
import os
import threading
from collections import Counter
from datetime import datetime

import config
import project_finder as finder
import project_reasoner_ai as ai
import projects
import tiny_scorer as ts

CACHE_NAME = "finder_cache.json"
CACHE_KEEP = 20000          # answers kept on disk; the oldest go first
SEED_LATEST = 6             # things of one of your projects the models read
URL = config.FINDER_URL

_lock = threading.Lock()
_retriever = None           # embedding vectors are kept in memory between looks
_last_log = []              # the last look's steps, for explain() (lmemm.py look-for-projects --explain)


def cache_path():
    return os.path.join(config.paths().memory_dir, CACHE_NAME)


def load_cache():
    try:
        with open(cache_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    """Write the answers atomically, keeping the newest CACHE_KEEP. Never raises."""
    try:
        keys = list(cache)[-CACHE_KEEP:]
        os.makedirs(config.paths().memory_dir, exist_ok=True)
        tmp = cache_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({k: cache[k] for k in keys}, fh, ensure_ascii=False)
        os.replace(tmp, cache_path())
    except OSError:
        pass


def unavailable(url=URL):
    """Why this method cannot look now, or None: Ollama is not running, or lacks a model it needs (the
    embedding model is optional: without it retrieval compares shared words)."""
    try:
        have = ts.installed(url)
    except OSError:
        return f"Ollama is not running at {url}"
    needed = [config.FINDER_NARRATOR, config.FINDER_JUDGE] + ([config.FINDER_WORK_JUDGE] if work_model() else [])
    missing = [m for m in dict.fromkeys(needed) if m not in have]
    return f"Ollama lacks {', '.join(missing)}" if missing else None


def work_model():
    """The model that answers only "is this deliberate work?", when that is a model of its own (and the
    question is on); otherwise the judge answers it too."""
    other = config.FINDER_WORTH and config.FINDER_WORK_JUDGE and config.FINDER_WORK_JUDGE != config.FINDER_JUDGE
    return config.FINDER_WORK_JUDGE if other else None


def models(cache, budget, url=URL):
    """(judge, narrator, retriever, work_judge or None), asked for the CPU through Ollama (see the module note: the mlx runner may use the GPU)."""
    global _retriever
    cpu = ts.cpu_only(config.FINDER_THREADS)
    judge = ai.Judge(ts.RerankScorer(config.FINDER_JUDGE, url, timeout=300, extra=cpu, keep_alive="2m"),
                     cache, name=config.FINDER_JUDGE, budget=budget)
    narrator = ai.Narrator(ts.ChatScorer(config.FINDER_NARRATOR, url, timeout=300, extra=cpu, keep_alive=0),
                           cache, name=f"cold={config.FINDER_NARRATOR}", budget=budget)
    if _retriever is None or _retriever.k != config.FINDER_CANDIDATES:
        embed = None
        if config.FINDER_EMBED and config.FINDER_EMBED in ts.installed(url):
            embed = ts.Embedder(config.FINDER_EMBED, url, timeout=300, extra=cpu, keep_alive="2m")
        _retriever = ai.Retriever(embed, k=config.FINDER_CANDIDATES)
    work = work_model()
    work_judge = ai.Judge(ts.RerankScorer(work, url, timeout=300, extra=cpu, keep_alive=0),
                          cache, name=work, budget=budget) if work else None      # one question a group: not kept loaded
    return judge, narrator, _retriever, work_judge


def seeds(reg, items, exclude, active=None):
    """Your projects as the pipeline sees them: each one's latest things (none of `exclude`). With `active`
    (the things used in the look-back days) a project takes part only if one of its things is among them: a
    project you stopped working on is not a place new things are added to (archive it to be sure)."""
    out = []
    with projects.indexed(reg, items):
        for pid in reg["projects"]:
            if projects.hidden(reg, pid):
                continue
            every = [i["id"] for i in projects.members(reg, items, pid, deep=False) if i.get("id") in items]
            if active is not None and not any(i in active for i in every):
                continue
            mine = [i for i in every if i not in exclude][:SEED_LATEST]
            if mine:
                out.append({"id": pid, "members": list(reversed(mine))})       # oldest first, like a project
    return out


def plan(reg, items, summary):
    """The offers for what the models found, strongest first, in project_finder.plan's shape:
    [(strength, "items", (pid, ids, reason))] for your projects, [(strength, "project", (name, ids, reason))]
    for new ones. Groups are never joined by name, and a thing the models put in two groups is offered in
    neither, like a shared resource. The narrator names a group afresh each look, so a group most of whose
    things you already dismissed is not offered again under a new name."""
    out, names = [], Counter()
    groups = Counter(i for p in summary.values() for i in set(p["added"] if p.get("seed") else p["members"]))
    twice = {i for i, n in groups.items() if n > 1}     # put in two groups (seen in two sittings): offered in neither
    for path, p in summary.items():
        if p.get("seed"):
            pid = p["seed"]
            loose = [i for i in p["added"] if i in items and i not in twice and projects.allowed(items[i], pid)]
            if loose and projects.exists(reg, pid):
                out.append((1000 + len(loose), "items", (pid, loose, "The model judged them part of this project")))
            continue
        if p.get("idle"):
            continue                      # the judge said this is idle looking around, not work to keep track of
        ids = [i for i in p["members"] if i in items and i not in twice]
        if sum(1 for i in ids if items[i].get("declined_projects")) * 2 >= len(ids):
            continue                      # you dismissed most of these together already, whatever name it was given now
        if p["tasks"] >= finder.NEW_MIN_TASKS and len(ids) >= finder.NEW_MIN_THINGS:
            name = path.rsplit(" [", 1)[0]
            names[projects.key(name)] += 1
            if names[projects.key(name)] > 1:                # two groups, one name: shown apart, never joined
                name = f"{name} ({names[projects.key(name)]})"
            why = f"The model judged these one piece of work, in {p['tasks']} separate sittings"
            out.append((p["tasks"] * 2 + len(ids), "project", (name, ids, why)))
    return sorted(out, key=lambda x: -x[0])


def find(reg, items, visits, now=None, days=finder.LOOKBACK_DAYS, url=URL):
    """Run the AI method over your unfiled things of the last `days` days and plan what to offer.
    Reads only. Returns [] when Ollama or a model is missing or the look ran out of calls (it goes on next
    time); the log says which."""
    now = now or datetime.now()
    why_not = unavailable(url)
    if why_not:
        finder.record("look", method="ai", skipped=why_not)
        return []
    things, timeline = finder.recent(items, visits, now, days)
    loose = {i: t for i, t in things.items() if not finder._filed(reg, items[i])}
    if len(loose) < finder.NEW_MIN_THINGS:
        return []
    library = {i: {k: v for k, v in it.items() if k not in finder.SKIP_FIELDS} for i, it in items.items()}
    with _lock:                                   # one look at a time, so two never fight over the cache
        cache, budget = load_cache(), ai.Budget(config.FINDER_CALLS)
        judge, narrator, retriever, work_judge = models(cache, budget, url)

        def calls():
            return judge.calls + narrator.calls + (work_judge.calls if work_judge else 0)

        t0 = datetime.now()
        try:
            _, summary, log = ai.run(loose, timeline, judge, narrator, rule="raw", seeds=seeds(reg, items, set(loose), set(things)),
                                     library=library, retrieve=retriever, margin=config.FINDER_MARGIN,
                                     abstain_shared=True, confirm=config.FINDER_CONFIRM or None,
                                     use_description=config.FINDER_DESCRIBE, worth=config.FINDER_WORTH,
                                     work_judge=work_judge)
        except ai.OutOfBudget:
            finder.record("look", method="ai", things=len(loose), paused=True, calls=calls(),
                          seconds=round((datetime.now() - t0).total_seconds()))
            return []
        finally:
            save_cache(cache)
            for m in (config.FINDER_JUDGE, work_model(), config.FINDER_EMBED):
                if m:
                    ts.unload(m, url)
    _last_log[:] = log
    end = next(e for e in log if e["step"] == "projects")
    finder.record("look", method="ai", things=len(loose), calls=calls(),
                  seconds=round((datetime.now() - t0).total_seconds()),
                  projects={k: v["members"] for k, v in summary.items()},
                  shared=end["shared"], not_filed=end["not_filed"],
                  why=[{k: e[k] for k in ("task", "decision", "scores", "retrieved", "confirmed") if k in e}
                       for e in log if e["step"] == "assign"])
    return plan(reg, items, summary)


def explain(items, planned, log=None):
    """Lines saying why each offer was made, for lmemm.py look-for-projects --explain: for the tasks behind it,
    what the narrator wrote about each thing, how the judge scored the projects it was asked about, and the
    second opinion (without the narrator's sentences) on a join; then the groups dropped as idle looking around."""
    log = _last_log if log is None else log
    said = {e["id"]: e["said"] for e in log if e["step"] == "understand"}
    pairs = {}                                           # how strongly the judge said two windows are one piece of work
    for e in log:
        if e["step"] == "relate":
            for key, score in e["pairs"].items():
                pairs[frozenset(key.split(" + "))] = score
    lines = []
    for _, kind, (target, ids, _) in planned:
        lines.append(f"new project “{target}”" if kind == "project" else f"add to {target}")
        for e in log:
            if e["step"] != "assign" or not set(e["task"]) & set(ids):
                continue
            lines.append(f"  {e['decision']}; judge {e['scores']}"
                         + (f"; second opinion {e['confirmed']}" if "confirmed" in e else ""))
            for i in e["task"]:
                it = items.get(i) or {}
                lines.append(f"    {it.get('app') or '?'} · {it.get('title') or i}: {said.get(i, '(no sentence)')}")
            shown = [(a, b, pairs[frozenset((a, b))]) for n, a in enumerate(e["task"]) for b in e["task"][n + 1:]
                     if frozenset((a, b)) in pairs]
            for a, b, score in sorted(shown, key=lambda x: x[2])[:6]:                # the weakest links first
                name = lambda i: (items.get(i) or {}).get("title") or i               # noqa: E731
                lines.append(f"    joined with {score:+.1f}: {name(a)}  ~  {name(b)}")
    end = next((e for e in log if e["step"] == "projects"), {"projects": {}})
    idle = sorted(((p["work"], p["members"]) for p in end["projects"].values() if p.get("idle")), reverse=True)
    if idle:                                          # the ones the work question dropped, nearest the line first
        lines.append("left out as idle looking around, not work to keep track of (work score, the things):")
        for work, members in idle:
            titles = " · ".join(str((items.get(i) or {}).get("title") or i) for i in members[:4])
            lines.append(f"  {work:+.1f}: {titles}" + (f" (+{len(members) - 4} more)" if len(members) > 4 else ""))
    return lines


def counts(log):
    """How many things the look filed, left out as general, shared or unfiled (for the CLI)."""
    end = next((e for e in log if e["step"] == "projects"), {"projects": {}, "shared": [], "not_filed": []})
    general = sum(1 for e in log if e["step"] == "general" and e["score"] > 0)
    return Counter(projects=len(end["projects"]), shared=len(end["shared"]), unfiled=len(end["not_filed"]),
                   general=general)

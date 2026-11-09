"""LMemM - exact links: facts the computer can check without judging. Beta; config.FINDER_EXACT_LINKS turns them off.

A link says that two windows are the SAME THING seen again, never that two different things belong to one project.
Two kinds, both checked from names alone:

    versions   files in one folder with the same extension whose names differ only by a version marker
               (Pitch_v1.key, Pitch_v2.key, Pitch_final.key, "Pitch final (1).key", "Copy of Pitch.key").
               The same name in two folders is two files; a copy in another folder is not linked.
    branch     windows that name the same git branch of the same repository as the branch they are on: a terminal
               title or prompt, a git checkout / switch / push, "Switched to branch", or a pull request page's
               "X into Y". A branch only mentioned in output (a stash message names the branch you left) does not
               count, and a window that names two different branches is not linked at all.

What the finder does with them (project_reasoner_ai.run, links=True): two linked windows in one sitting start as one
task, and the model that judges the task is told the link in plain words. The model still decides which project a
task belongs to; a link never files anything.
"""

import posixpath
import re
from collections import defaultdict
from urllib.parse import urlparse

from project_reasoner import _texts

TERMINALS = {"terminal", "iterm", "iterm2", "warp", "ghostty", "kitty", "alacritty", "hyper", "wezterm"}
DEFAULT_BRANCHES = {"main", "master", "develop", "dev", "trunk", "head", "origin", "upstream", "stable", "production",
                    "prod", "release"}
NAME = r"[A-Za-z0-9_][A-Za-z0-9_.\-/]*"
COMMANDS = (re.compile(rf"\bgit\s+(?:checkout|switch)\s+(?:-[bBcC]\s+)?({NAME})"),
            re.compile(rf"\bgit\s+push\s+(?:(?:-u|--set-upstream|-f|--force-with-lease)\s+)*\S+\s+({NAME})"))
OUTPUT = (re.compile(rf"Switched to (?:a new )?branch ['‘\"]?({NAME})"), re.compile(rf"^On branch ({NAME})\s*$", re.M))
TITLE = (re.compile(rf"git:\(({NAME})\)"), re.compile(rf"\(({NAME})\)"), re.compile(rf"\[({NAME})\]"))
PULL = (re.compile(rf"(?:^|\s)({NAME}) into ({NAME})\b"), re.compile(rf"\binto\s+({NAME})\s+from\s+({NAME})"))
NOT_BRANCH = {"merged", "merge", "commits", "commit", "changes", "pull", "request", "it", "them"}
HOSTS = ("github.", "gitlab.", "bitbucket.")


# ---------------------------------------------------------------- versions

def stem(name):
    """(stem, extension) of a file name with the version markers taken off, lower case and spaces for separators:
    Harbour_Proposal_final_FINAL (1).docx -> ("harbour proposal", "docx")."""
    base, dot, ext = name.rpartition(".")
    if not dot:
        base, ext = name, ""
    s = re.sub(r"[\s_\-]+", " ", base.casefold()).strip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"^copy of ", "", s)
        s = re.sub(r" ?\(\d+\)$", "", s)
        s = re.sub(r" (?:copy|final|draft|latest)(?: ?\d+)?$", "", s)
        s = re.sub(r" (?:v|ver|version) ?\d+(?:\.\d+)*$", "", s)
        s = re.sub(r" (?:rev|revision) ?\d+$", "", s)
    return s.strip(), ext.casefold()


def _file_chains(items):
    groups = defaultdict(list)
    for i, it in items.items():
        path = (it.get("state") or {}).get("file")
        if not isinstance(path, str) or "/" not in path:
            continue
        folder, name = posixpath.split(path.rstrip("/"))
        s, ext = stem(name)
        if len(s) >= 3:
            groups[(folder, s, ext)].append(i)
    out = []
    for (folder, s, ext), ids in groups.items():
        if len(ids) >= 2:
            titles = ", ".join(str(items[i].get("title") or i) for i in ids)
            out.append({"kind": "versions", "key": f"{folder}/{s}.{ext}", "ids": ids,
                        "note": f"These are versions of one file (same folder, same name apart from the version): {titles}."})
    return out


# ---------------------------------------------------------------- branches

def _valid(name, strict):
    name = name.strip(".,;:'\"")
    if not re.fullmatch(NAME, name) or name.casefold() in DEFAULT_BRANCHES or name.casefold() in NOT_BRANCH:
        return None
    if re.fullmatch(r"[0-9a-f]{7,40}", name) or name.isdigit() or len(name) < 2:
        return None
    return name if not strict or any(c in name for c in "/-_") else None


def _repo(item):
    state = item.get("state") or {}
    cwd = state.get("cwd")
    if isinstance(cwd, str) and cwd.strip("/"):
        return posixpath.basename(cwd.rstrip("/")).casefold()
    url = state.get("url")
    if isinstance(url, str):
        u = urlparse(url)
        parts = [p for p in u.path.split("/") if p]
        if any(h in (u.netloc or "") for h in HOSTS) and len(parts) >= 2:
            return re.sub(r"\.git$", "", parts[1]).casefold()
    return None


def _branches(item):
    """The git branch names a window is on, from what it shows of its own branch only."""
    app, kind, found = str(item.get("app") or "").casefold(), item.get("kind"), set()
    texts = _texts(item)
    if kind == "terminal" or app in TERMINALS:
        for rx in TITLE:
            found |= {n for n in (_valid(m, True) for m in rx.findall(str(item.get("title") or ""))) if n}
        for line in texts["typed"]:
            for rx in COMMANDS:
                found |= {n for n in (_valid(m, False) for m in rx.findall(line)) if n}
        for line in texts["screen"] + texts["read"]:
            for rx in OUTPUT:
                found |= {n for n in (_valid(m, False) for m in rx.findall(line)) if n}
    url = urlparse(str((item.get("state") or {}).get("url") or ""))
    if any(h in url.netloc for h in HOSTS) and re.search(r"/(?:pull|merge_requests|compare)/", url.path):
        for line in [str(item.get("title") or "")] + texts["read"] + texts["screen"]:
            for k, rx in enumerate(PULL):
                for m in rx.findall(line):
                    found |= {n for n in (_valid(m[1] if k else m[0], True),) if n}
    return found


def _branch_links(items):
    groups = defaultdict(list)
    for i, it in items.items():
        found, repo = _branches(it), _repo(it)
        if len(found) == 1 and repo:                                 # two branches in one window: not linked
            groups[(repo, next(iter(found)))].append(i)
    out = []
    for (repo, branch), ids in groups.items():
        if len(ids) >= 2:
            titles = ", ".join(str(items[i].get("title") or i) for i in ids)
            out.append({"kind": "branch", "key": f"{repo}@{branch}", "ids": ids,
                        "note": f"These name the same git branch ({branch}) of the same repository ({repo}): {titles}."})
    return out


# ---------------------------------------------------------------- all

def bundles(items):
    """The links among `items` ({id: item}) as [{"kind", "key", "ids", "note"}], ids oldest first. No model."""
    order = {i: k for k, i in enumerate(sorted(items, key=lambda i: str(items[i].get("first_seen") or "")))}
    out = _file_chains(items) + _branch_links(items)
    for b in out:
        b["ids"].sort(key=order.get)
    return sorted(out, key=lambda b: (b["kind"], b["key"]))

"""A small GitHub REST client (standard library only) and an in-memory fake for tests."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")

LABEL_COLORS = {
    "factory": "5319e7",
    "factory:waiting": "fbca04",
    "factory:running": "1d76db",
    "factory:done": "0e8a16",
    "factory:stopped": "b60205",
    "factory:proposed": "fef2c0",
    "factory:log": "ededed",
}


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            raise GitHubError("GITHUB_TOKEN is not set")

    # -- transport -----------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None, *, full: bool = False):
        url = path if path.startswith("http") else f"{API}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "software-factory",
        })
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    payload = json.loads(raw) if raw else None
                    return (payload, resp.headers) if full else payload
            except urllib.error.HTTPError as exc:
                if exc.code in (502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                detail = exc.read().decode(errors="replace")[:500]
                raise GitHubError(f"{method} {path} -> {exc.code}: {detail}") from None
            except urllib.error.URLError as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise GitHubError(f"{method} {path}: {exc.reason}") from None

    def _paged(self, path: str) -> list:
        items, url = [], f"{API}{path}{'&' if '?' in path else '?'}per_page=100"
        while url:
            page, headers = self._request("GET", url, full=True)
            items.extend(page or [])
            url = None
            for part in (headers.get("Link") or "").split(","):
                if 'rel="next"' in part:
                    url = part[part.index("<") + 1:part.index(">")]
        return items

    def _r(self, path: str) -> str:
        return f"/repos/{self.repo}{path}"

    # -- issues --------------------------------------------------------------
    def issue(self, number: int) -> dict:
        return self._request("GET", self._r(f"/issues/{number}"))

    def issues(self, label: str, state: str = "all") -> list[dict]:
        q = urllib.parse.urlencode({"labels": label, "state": state})
        return [i for i in self._paged(self._r(f"/issues?{q}")) if "pull_request" not in i]

    def comments(self, number: int) -> list[dict]:
        return self._paged(self._r(f"/issues/{number}/comments"))

    def comment(self, number: int, body: str) -> dict:
        return self._request("POST", self._r(f"/issues/{number}/comments"), {"body": body})

    def edit_comment(self, comment_id: int, body: str) -> dict:
        return self._request("PATCH", self._r(f"/issues/comments/{comment_id}"), {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        for label in labels:
            self.ensure_label(label)
        self._request("POST", self._r(f"/issues/{number}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        try:
            self._request("DELETE", self._r(f"/issues/{number}/labels/{urllib.parse.quote(label, safe='')}"))
        except GitHubError as exc:
            if "404" not in str(exc):
                raise

    _known_labels: set | None = None

    def ensure_label(self, label: str) -> None:
        if self._known_labels is None:
            self._known_labels = {l["name"] for l in self._paged(self._r("/labels"))}
        if label in self._known_labels:
            return
        color = LABEL_COLORS.get(label, "c5def5")
        try:
            self._request("POST", self._r("/labels"), {"name": label, "color": color,
                                                         "description": "Software factory"})
        except GitHubError as exc:
            if "already_exists" not in str(exc):
                raise
        self._known_labels.add(label)

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        for label in labels:
            self.ensure_label(label)
        return self._request("POST", self._r("/issues"), {"title": title, "body": body, "labels": labels})

    def close_issue(self, number: int, reason: str = "not_planned") -> None:
        self._request("PATCH", self._r(f"/issues/{number}"), {"state": "closed", "state_reason": reason})

    # -- pull requests -------------------------------------------------------
    def find_pr(self, branch: str) -> dict | None:
        owner = self.repo.split("/")[0]
        q = urllib.parse.urlencode({"head": f"{owner}:{branch}", "state": "open"})
        prs = self._request("GET", self._r(f"/pulls?{q}"))
        return prs[0] if prs else None

    def create_pr(self, branch: str, base: str, title: str, body: str) -> dict:
        return self._request("POST", self._r("/pulls"), {
            "head": branch, "base": base, "title": title, "body": body, "draft": True})

    def mark_ready(self, pr: dict) -> None:
        if not pr.get("draft"):
            return
        query = ("mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id})"
                 " { pullRequest { number } } }")
        self._request("POST", f"{API}/graphql", {"query": query, "variables": {"id": pr["node_id"]}})


def _dispatch(self, workflow: str, ref: str, inputs: dict) -> None:
    self._request("POST", self._r(f"/actions/workflows/{workflow}/dispatches"),
                  {"ref": ref, "inputs": {k: str(v) for k, v in inputs.items()}})


GitHub.dispatch = _dispatch


class FakeGitHub:
    """In-memory stand-in with the same methods, for tests and dry runs."""

    def __init__(self, repo: str = "owner/repo"):
        self.repo = repo
        self._issues: dict[int, dict] = {}
        self._comments: dict[int, list[dict]] = {}
        self.prs: dict[str, dict] = {}
        self._next = 1000

    def add_issue(self, number: int, title: str, body: str = "", author: str = "AvniDhir",
                  labels: tuple[str, ...] = ("factory",)) -> dict:
        issue = {"number": number, "title": title, "body": body, "state": "open",
                 "user": {"login": author}, "labels": [{"name": l} for l in labels],
                 "html_url": f"https://github.com/{self.repo}/issues/{number}",
                 "created_at": "2026-11-22T09:00:00Z", "updated_at": "2026-11-22T09:00:00Z"}
        self._issues[number] = issue
        self._comments[number] = []
        return issue

    def labels(self, number: int) -> set[str]:
        return {l["name"] for l in self._issues[number]["labels"]}

    def issue(self, number):
        return self._issues[number]

    def issues(self, label, state="all"):
        return [i for i in self._issues.values() if label in self.labels(i["number"])]

    def comments(self, number):
        return list(self._comments[number])

    def comment(self, number, body, author="github-actions[bot]"):
        self._next += 1
        c = {"id": self._next, "body": body, "user": {"login": author},
             "created_at": "2026-11-22T09:00:00Z"}
        self._comments[number].append(c)
        return c

    def edit_comment(self, comment_id, body):
        for comments in self._comments.values():
            for c in comments:
                if c["id"] == comment_id:
                    c["body"] = body
                    return c
        raise GitHubError("no such comment")

    def add_labels(self, number, labels):
        have = self.labels(number)
        self._issues[number]["labels"] += [{"name": l} for l in labels if l not in have]

    def remove_label(self, number, label):
        self._issues[number]["labels"] = [l for l in self._issues[number]["labels"] if l["name"] != label]

    def ensure_label(self, label):
        pass

    def create_issue(self, title, body, labels, author="github-actions[bot]"):
        number = max(self._issues, default=0) + 1
        return self.add_issue(number, title, body, author=author, labels=tuple(labels))

    def close_issue(self, number, reason="not_planned"):
        self._issues[number]["state"] = "closed"

    def find_pr(self, branch):
        return self.prs.get(branch)

    def create_pr(self, branch, base, title, body):
        self._next += 1
        pr = {"number": self._next, "html_url": f"https://github.com/{self.repo}/pull/{self._next}",
              "draft": True, "node_id": "x", "title": title, "body": body, "head": {"ref": branch}}
        self.prs[branch] = pr
        return pr

    def mark_ready(self, pr):
        pr["draft"] = False

    dispatched: list | None = None

    def dispatch(self, workflow, ref, inputs):
        self.dispatched = (self.dispatched or []) + [(workflow, ref, dict(inputs))]

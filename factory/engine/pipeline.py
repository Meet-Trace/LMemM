"""The orchestrator: moves a factory issue through the declared pipeline."""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from . import records, runner
from .config import Agent, Factory
from .github import GitHubError

WAITING, RUNNING, DONE, STOPPED = "factory:waiting", "factory:running", "factory:done", "factory:stopped"
PROPOSED, LOG = "factory:proposed", "factory:log"
STATUS_LABELS = (WAITING, RUNNING, DONE, STOPPED, PROPOSED)
PROPOSAL_MARKER = "<!-- factory-proposal -->"
LOG_TITLE = "Factory: overnight scout log"
DAILY_PAUSE = "⏸️ The factory reached today's budget"
BOTS = ("github-actions[bot]",)
MAX_DIFF_CHARS = 80_000
UPDATE_EVERY_S = 15
STALE_RUN_S = 4 * 3600


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48].rstrip("-") or "feature"


class Workspace:
    """The git checkout agents work in. One feature branch per issue."""

    def __init__(self, path: Path | str, base: str):
        self.path, self.base = Path(path), base
        self.branch: str | None = None

    def git(self, *args: str, check: bool = True) -> str:
        out = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True)
        if check and out.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()[:400]}")
        return out.stdout

    def remote_has(self, branch: str) -> bool:
        return bool(self.git("ls-remote", "--heads", "origin", branch).strip())

    def prepare(self, branch: str, create: bool) -> bool:
        """Check out the issue branch (creating it from base if allowed).

        Returns True when the checkout is the feature branch, False when it is
        the base branch (an agent before any branch exists, such as triage).
        """
        self.git("fetch", "--quiet", "origin", self.base)
        self.git("reset", "--quiet", "--hard")
        self.git("clean", "-fdq")
        if self.remote_has(branch):
            self.git("fetch", "--quiet", "origin", branch)
            self.git("checkout", "--quiet", "-B", branch, f"origin/{branch}")
            self.branch = branch
        elif create:
            self.git("checkout", "--quiet", "-B", branch, f"origin/{self.base}")
            self.branch = branch
        else:
            self.git("checkout", "--quiet", "--detach", f"origin/{self.base}")
            self.branch = None
        return self.branch is not None

    def diff(self) -> str:
        if not self.branch:
            return ""
        return self.git("diff", f"origin/{self.base}...HEAD", check=False)

    def discard(self) -> None:
        self.git("reset", "--quiet", "--hard")
        self.git("clean", "-fdq")

    def commit_and_push(self, message: str) -> bool:
        """Commit everything the agent changed and push. False if nothing changed."""
        if not self.branch:
            self.discard()
            return False
        self.git("add", "-A")
        if self.git("status", "--porcelain").strip():
            self.git("commit", "--quiet", "-m", message)
        ahead = self.git("rev-list", "--count", f"origin/{self.base}..HEAD").strip()
        if ahead == "0":
            return False
        self.git("push", "--quiet", "-u", "origin", self.branch)
        return True


class Orchestrator:
    def __init__(self, factory: Factory, gh, workspace: Workspace,
                 run_agent: Callable[..., runner.AgentResult] = runner.run,
                 run_url: str | None = None, log: Callable[[str], None] = print):
        self.f, self.gh, self.ws = factory, gh, workspace
        self.run_agent, self.run_url, self.log = run_agent, run_url, log

    # -- state ---------------------------------------------------------------
    def labels(self, issue: dict) -> set[str]:
        return {l["name"] for l in issue.get("labels", [])}

    def current_stage(self, labels: set[str]) -> str | None:
        for stage in self.f.stage_names:
            if f"factory:{stage}" in labels:
                return stage
        return None

    def set_stage(self, number: int, labels: set[str], stage: str | None, status: str | None) -> set[str]:
        wanted = set()
        if stage:
            wanted.add(f"factory:{stage}")
        if status:
            wanted.add(status)
        managed = {f"factory:{s}" for s in self.f.stage_names} | set(STATUS_LABELS)
        for label in sorted((labels & managed) - wanted):
            self.gh.remove_label(number, label)
        if wanted - labels:
            self.gh.add_labels(number, sorted(wanted - labels))
        return (labels - managed) | wanted

    def branch(self, number: int) -> str:
        return f"{self.f.project.get('branch_prefix', 'factory/issue-')}{number}"

    def allowed(self, login: str | None) -> bool:
        return not self.f.allowed_users or (login or "").lower() in self.f.allowed_users

    def allowed_issue(self, issue: dict) -> bool:
        """Filed by an allowed person, or proposed by the factory's own scout.

        A scout proposal only enters the pipeline once someone with permission
        to label issues adds the factory label (or auto_build is on).
        """
        login = issue["user"]["login"]
        return self.allowed(login) or (login in BOTS and PROPOSAL_MARKER in (issue.get("body") or ""))

    # -- spending ------------------------------------------------------------
    def spent_today(self, today: str | None = None) -> float:
        today = today or records.now_iso()[:10]
        total = 0.0
        seen = set()
        for label in (self.f.label, LOG):
            for issue in self.gh.issues(label, state="all"):
                if issue["number"] in seen or (issue.get("updated_at") or "")[:10] < today:
                    continue
                seen.add(issue["number"])
                for r in records.runs_from_comments(self.gh.comments(issue["number"])):
                    if (r.get("started") or "")[:10] == today:
                        total += float(r.get("cost_usd") or 0)
        return total

    def over_daily_budget(self) -> float | None:
        cap = float(self.f.budgets.get("per_day_usd", 0) or 0)
        if not cap:
            return None
        spent = self.spent_today()
        return spent if spent >= cap else None

    # -- context for an agent ------------------------------------------------
    def build_task(self, agent: Agent, issue: dict, comments: list[dict], runs: list[dict]) -> str:
        parts = [f"# Feature request #{issue['number']}: {issue['title']}",
                 f"Requested by @{issue['user']['login']}. You are the **{agent.title}** stage."]
        if "issue" in agent.context:
            parts += ["## Request", (issue.get("body") or "").strip() or "_No description._"]
        if "comments" in agent.context:
            # Only people allowed to drive the factory: a stranger's comment on a
            # public issue must never become instructions for an agent.
            human = [c for c in comments if not records.parse_comment(c.get("body", ""))
                     and c["user"]["login"] not in BOTS and self.allowed(c["user"]["login"])]
            if human:
                parts.append("## Comments from people")
                parts += [f"**@{c['user']['login']}** ({c.get('created_at', '')}):\n{c['body'].strip()}"
                          for c in human]
        if "reports" in agent.context:
            latest: dict[str, dict] = {}
            for r in runs:
                if r.get("status") == "succeeded":
                    latest.pop(r["agent"], None)
                    latest[r["agent"]] = r
            if latest:
                parts.append("## Reports from earlier agents (oldest first)")
                parts += [f"### {r['agent']} → {r.get('decision')}\n{r.get('report', '')}"
                          for r in latest.values()]
        if "diff" in agent.context:
            diff = self.ws.diff()
            if diff:
                note = ""
                if len(diff) > MAX_DIFF_CHARS:
                    note = (f"\n\n(The diff is {len(diff):,} characters; only the first "
                            f"{MAX_DIFF_CHARS:,} are shown. Run `git diff origin/{self.ws.base}...HEAD` for the rest.)")
                    diff = diff[:MAX_DIFF_CHARS]
                parts += [f"## Changes on this branch vs {self.ws.base}", f"```diff\n{diff}\n```{note}"]
        parts.append(f"Do the {agent.title} work now, then return your structured result.")
        return "\n\n".join(parts)

    def prompt_values(self, issue: dict) -> dict[str, str]:
        p = self.f.project
        return {
            "spec_dir": p.get("spec_dir", "docs/specs"), "date": date.today().isoformat(),
            "slug": slugify(issue["title"]), "test_command": p.get("test_command", ""),
            "setup_command": p.get("setup_command", ""), "base_branch": self.ws.base,
            "issue_number": str(issue["number"]), "branch": self.branch(issue["number"]),
        }

    # -- running -------------------------------------------------------------
    def run_stage(self, stage: str, issue: dict, comments: list[dict], runs: list[dict]) -> tuple[records.RunRecord, runner.AgentResult]:
        agent = self.f.agent_for(stage)
        number = issue["number"]
        on_branch = self.ws.prepare(self.branch(number), create=agent.workspace == "write")
        record = records.RunRecord(id=uuid.uuid4().hex[:12], issue=number, stage=stage,
                                   agent=agent.name, model=agent.model, run_url=self.run_url)
        comment = self.gh.comment(number, records.render_comment(record, agent.title, agent.icon))
        record.comment_id = comment["id"]
        last = [0.0]

        def on_activity(line: str) -> None:
            record.add_activity(line)
            if time.monotonic() - last[0] >= UPDATE_EVERY_S:
                last[0] = time.monotonic()
                try:
                    self.gh.edit_comment(record.comment_id, records.render_comment(record, agent.title, agent.icon))
                except Exception as exc:  # status updates are best effort
                    self.log(f"could not update status: {exc}")

        self.log(f"#{number}: running {agent.name} ({agent.model}) on {'branch' if on_branch else 'base'}")
        task = self.build_task(agent, issue, comments, runs)
        result = self.run_agent(agent, task, self.prompt_values(issue), self.ws.path, on_activity)
        record.cost_usd, record.turns, record.duration_s = result.cost_usd, result.turns, round(result.duration_s, 1)
        record.decision, record.summary, record.error = result.decision, result.summary, result.error
        if result.ok and agent.workspace == "write":
            try:
                if self.ws.commit_and_push(f"factory({agent.name}): {result.summary[:60] or stage} (#{number})"):
                    pr = self.ensure_pr(issue)
                    record.pr = pr["number"] if pr else None
            except RuntimeError as exc:
                record.error = f"Could not push the branch: {exc}"
        else:
            self.ws.discard()  # read agents, and failed runs, leave nothing behind
        record.status = "succeeded" if result.ok and not record.error else "failed"
        return record, result

    def finish(self, record: records.RunRecord, result: runner.AgentResult) -> None:
        agent = self.f.agents[record.agent]
        record.finished = records.now_iso()
        self.gh.edit_comment(record.comment_id, records.render_comment(record, agent.title, agent.icon, result.report))

    def ensure_pr(self, issue: dict) -> dict | None:
        branch = self.branch(issue["number"])
        pr = self.gh.find_pr(branch)
        if pr:
            return pr
        body = (f"Built by the software factory for #{issue['number']}.\n\n"
                f"Closes #{issue['number']}\n\nEach agent's report is on the issue.")
        try:
            return self.gh.create_pr(branch, self.ws.base, issue["title"], body)
        except GitHubError as exc:
            # Usually "GitHub Actions is not permitted to create pull requests"
            # (an org or repo setting). The branch is pushed; deliver() links it.
            self.log(f"#{issue['number']}: could not open a pull request: {exc}")
            return None

    def compare_url(self, number: int) -> str:
        return (f"https://github.com/{self.gh.repo}/compare/{self.ws.base}...{self.branch(number)}"
                "?expand=1")

    # -- the loop ------------------------------------------------------------
    def advance(self, number: int, start: str | None = None) -> str:
        """Run stages for one issue until it waits, stops, finishes or hits a limit.

        Returns the final state: a stage name (handed off), 'wait', 'stop',
        'done' or 'skipped'.
        """
        issue = self.gh.issue(number)
        labels = self.labels(issue)
        if self.f.label not in labels or issue.get("state") != "open":
            return "skipped"
        if not self.allowed_issue(issue):
            self.log(f"#{number}: author not in intake.allowed_users; ignoring")
            return "skipped"
        spent_today = self.over_daily_budget()
        if spent_today is not None:
            last = (self.gh.comments(number) or [{}])[-1].get("body", "")
            if not last.startswith(DAILY_PAUSE):
                self.gh.comment(number, f"{DAILY_PAUSE} (${spent_today:.2f} of ${self.f.budgets['per_day_usd']:g}, "
                                        "`budgets.per_day_usd`). It picks this up again tomorrow (UTC).")
            return "paused"
        stage = start or self.current_stage(labels) or self.f.first_stage
        max_loops = int(self.f.policy("max_rework_loops", 3))
        budget = float(self.f.budgets.get("per_issue_usd", 0) or 0)

        for _ in range(int(self.f.policy("max_stages_per_job", 8))):
            comments = self.gh.comments(number)
            runs = records.runs_from_comments(comments)
            self.close_stale(runs)
            spent = sum(float(r.get("cost_usd") or 0) for r in runs)
            if budget and spent >= budget:
                self.set_stage(number, labels, stage, WAITING)
                self.gh.comment(number, f"⏸️ The factory paused: this request has used ${spent:.2f} of its "
                                        f"${budget:g} budget (`budgets.per_issue_usd`). Reply to continue anyway.")
                return "wait"
            labels = self.set_stage(number, labels, stage, RUNNING)
            record, result = self.run_stage(stage, issue, comments, runs)
            if record.status != "succeeded":
                record.next = "wait"
                self.finish(record, result)
                self.set_stage(number, labels, stage, WAITING)
                self.gh.comment(number, f"⏸️ The **{stage}** stage failed ({record.error}). "
                                        f"Reply `/factory retry` to run it again.")
                return "wait"
            target = self.f.stage(stage).on[result.decision]
            if target == "implement" and stage != "implement" and self.rework_count(runs) >= max_loops:
                record.next = "wait"
                self.finish(record, result)
                self.set_stage(number, labels, stage, WAITING)
                self.gh.comment(number, f"⏸️ Work has gone back to implement {max_loops} times "
                                        f"(`policies.max_rework_loops`). Reply with guidance, or `/factory run implement`.")
                return "wait"
            record.next = target
            self.finish(record, result)
            if target == "wait":
                self.set_stage(number, labels, stage, WAITING)
                return "wait"
            if target == "stop":
                self.set_stage(number, labels, None, STOPPED)
                self.gh.close_issue(number)
                return "stop"
            if target == "done":
                self.set_stage(number, labels, None, DONE)
                self.deliver(issue, runs + [{"agent": record.agent, "cost_usd": record.cost_usd}])
                return "done"
            stage = target
            labels = self.set_stage(number, labels, stage, None)
        return stage  # handed off: the caller dispatches a fresh job

    def rework_count(self, runs: list[dict]) -> int:
        return sum(1 for r in runs if r.get("next") == "implement" and r.get("stage") != "spec"
                   and r.get("stage") != "implement")

    def close_stale(self, runs: list[dict]) -> None:
        for r in runs:
            if r.get("status") == "running" and r.get("comment_id"):
                agent = self.f.agents.get(r["agent"])
                rec = records.RunRecord(**{k: v for k, v in r.items() if k in records.RunRecord.__dataclass_fields__})
                rec.status, rec.error, rec.finished = "failed", "Interrupted before it finished", records.now_iso()
                r.update(status="failed")
                self.gh.edit_comment(r["comment_id"], records.render_comment(
                    rec, agent.title if agent else r["agent"], agent.icon if agent else "🤖", r.get("report", "")))

    def deliver(self, issue: dict, runs: list[dict]) -> None:
        pr = self.gh.find_pr(self.branch(issue["number"]))
        total = sum(float(r.get("cost_usd") or 0) for r in runs)
        if pr:
            try:
                self.gh.mark_ready(pr)
            except GitHubError as exc:
                self.log(f"could not mark the pull request ready: {exc}")
            msg = (f"✅ **Ready for you to test:** {pr['html_url']}\n\nThe verify agent's report above "
                   f"has the steps. Total agent cost: ${total:.2f}.")
        elif self.ws.remote_has(self.branch(issue["number"])):
            msg = (f"✅ **Ready for you to test** on branch `{self.branch(issue['number'])}`. "
                   f"GitHub did not let the factory open the pull request, so "
                   f"[open it with one click]({self.compare_url(issue['number'])}). "
                   f"The verify agent's report above has the steps. Total agent cost: ${total:.2f}.")
        else:
            msg = f"✅ The factory finished this request. Total agent cost: ${total:.2f}."
        login = issue["user"]["login"]
        self.gh.comment(issue["number"], f"@{login} {msg}")

    # -- events --------------------------------------------------------------
    def handle_comment(self, number: int, comment: dict) -> str:
        """A person commented on a factory issue: resume, or run a command."""
        login = comment["user"]["login"]
        body = (comment.get("body") or "").strip()
        if login in BOTS or login.endswith("[bot]") or records.parse_comment(body) or not self.allowed(login):
            return "skipped"
        issue = self.gh.issue(number)
        labels = self.labels(issue)
        if self.f.label not in labels:
            return "skipped"
        match = re.match(r"^/factory\s+(\w+)(?:\s+([\w-]+))?", body)
        if match:
            cmd, arg = match.group(1), match.group(2)
            if cmd == "stop":
                self.set_stage(number, labels, None, STOPPED)
                self.gh.comment(number, "🛑 Stopped. Reply `/factory run <stage>` to restart.")
                return "stop"
            if cmd == "retry":
                return self.advance(number)
            if cmd == "status":
                self.gh.comment(number, self.status_text(issue))
                return "skipped"
            if cmd == "run" and arg in self.f.stage_names:
                self.set_stage(number, labels, arg, None)
                return self.advance(number, start=arg)
            self.gh.comment(number, "Commands: `/factory status`, `/factory retry`, `/factory run <stage>`, `/factory stop`. "
                                    f"Stages: {', '.join(self.f.stage_names)}.")
            return "skipped"
        if WAITING in labels and issue.get("state") == "open":
            return self.advance(number)
        return "skipped"

    def status_text(self, issue: dict) -> str:
        labels = self.labels(issue)
        stage = self.current_stage(labels)
        state = next((l.split(":", 1)[1] for l in (RUNNING, WAITING, DONE, STOPPED) if l in labels), "queued")
        runs = records.runs_from_comments(self.gh.comments(issue["number"]))
        spent = sum(float(r.get("cost_usd") or 0) for r in runs)
        lines = [f"**Status:** {state}" + (f" at **{stage}**" if stage else ""),
                 f"**Runs so far:** {len(runs)} · ${spent:.2f}"]
        if runs:
            last = runs[-1]
            lines.append(f"**Last:** {last['agent']} → `{last.get('decision') or last.get('status')}`"
                         + (f": {last['summary']}" if last.get("summary") else ""))
        pr = self.gh.find_pr(self.branch(issue["number"]))
        if pr:
            lines.append(f"**Pull request:** {pr['html_url']}")
        return "\n".join(lines)

    def sweep(self, now: float | None = None) -> list[int]:
        """Issues that should be running but are not (new, handed off or orphaned).

        Oldest first, and never more than policies.max_parallel_requests at once.
        """
        now = now or time.time()
        due, busy = [], 0
        for issue in sorted(self.gh.issues(self.f.label, state="open"), key=lambda i: i["number"]):
            labels = self.labels(issue)
            if labels & {WAITING, DONE, STOPPED} or not self.allowed_issue(issue):
                continue
            if RUNNING in labels:
                updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
                if now - updated.timestamp() < STALE_RUN_S:
                    busy += 1
                    continue
            due.append(issue["number"])
        room = max(0, int(self.f.policy("max_parallel_requests", 3)) - busy)
        return due[:room]

    # -- overnight scout -----------------------------------------------------
    def scout(self) -> list[int]:
        """Run the scout agent: it reads the codebase and proposes new work.

        Proposals become issues labelled factory:proposed. You approve one by
        adding the factory label; with overnight.auto_build they start at once.
        Returns the new issue numbers.
        """
        name = self.f.overnight.get("scout")
        if not name:
            return []
        if self.over_daily_budget() is not None:
            self.log("scout: daily budget reached; skipping")
            return []
        agent = self.f.agents[name]
        open_issues = self.gh.issues(self.f.label, state="open") + self.gh.issues(PROPOSED, state="open")
        log = next((i for i in self.gh.issues(LOG, state="open")), None) or self.gh.create_issue(
            LOG_TITLE, "Each overnight scout run is recorded here. Proposals are filed as their own issues "
                       "with the `factory:proposed` label.", [LOG])
        self.ws.prepare("__scout__", create=False)
        limit = int(self.f.overnight.get("max_proposals", 3))
        auto = bool(self.f.overnight.get("auto_build", False))
        existing = "\n".join(f"- #{i['number']} {i['title']}" for i in open_issues) or "- (none)"
        task = (f"# Overnight scout run\n\nPropose at most {limit} new feature requests for this repository.\n\n"
                f"## Already open (do not repeat these)\n{existing}")
        record = records.RunRecord(id=uuid.uuid4().hex[:12], issue=log["number"], stage="scout",
                                   agent=agent.name, model=agent.model, run_url=self.run_url)
        comment = self.gh.comment(log["number"], records.render_comment(record, agent.title, agent.icon))
        record.comment_id = comment["id"]
        result = self.run_agent(agent, task, self.prompt_values({"title": "scout", "number": 0}),
                                self.ws.path, record.add_activity)
        self.ws.discard()
        record.cost_usd, record.turns, record.duration_s = result.cost_usd, result.turns, round(result.duration_s, 1)
        record.decision, record.summary, record.error = result.decision, result.summary, result.error
        record.status = "succeeded" if result.ok else "failed"
        taken = {i["title"].strip().lower() for i in open_issues}
        created = []
        for p in result.proposals if result.ok else []:
            title = str(p["title"]).strip()[:120]
            if not title or title.lower() in taken or len(created) >= limit:
                continue
            body = (f"{PROPOSAL_MARKER}\n{str(p.get('body', '')).strip()}\n\n**Why:** {str(p.get('why', '')).strip()}\n\n"
                    f"**Size:** {p.get('size', '?')} · proposed by the scout agent overnight.\n\n"
                    + ("The factory starts building this automatically (`overnight.auto_build`)."
                       if auto else "To build it, add the `factory` label. To drop it, close this issue."))
            issue = self.gh.create_issue(title, body, [PROPOSED] + ([self.f.label] if auto else []))
            created.append(issue["number"])
            taken.add(title.lower())
        record.pr = None
        record.next = "auto_build" if auto and created else ("proposed" if created else None)
        listing = "\n".join(f"- #{n}" for n in created)
        self.finish(record, runner.AgentResult(result.decision, result.summary,
                                                (result.report + ("\n\n**Filed:**\n" + listing if created else "")).strip(),
                                                result.cost_usd, result.turns, result.duration_s, result.error))
        return created

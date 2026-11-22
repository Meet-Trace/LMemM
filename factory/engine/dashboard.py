"""Build the factory dashboard: every agent, what it is doing, and every run."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from . import records
from .config import Factory
from .pipeline import DONE, LOG, PROPOSED, RUNNING, STOPPED, WAITING

TEMPLATE = Path(__file__).with_name("dashboard.html")


def _agent(f: Factory, a, stage) -> dict:
    return {
            "stage": stage.stage if stage else None, "name": a.name, "title": a.title, "color": a.color,
            "description": a.description, "model": a.model, "effort": a.effort,
            "tools": list(a.tools), "disallowed_tools": list(a.disallowed_tools),
            "workspace": a.workspace, "budget_usd": a.budget_usd, "context": list(a.context),
            "decisions": [{"name": k, "meaning": v, "next": stage.on.get(k) if stage else None}
                          for k, v in a.decisions.items()],
            "prompt": a.prompt, "file": f"factory/{a.source.relative_to(f.root)}" if a.source else "",
            "prompt_file": f"factory/{a.prompt_path.relative_to(f.root)}",
        }


def agents_data(f: Factory) -> list[dict]:
    return [_agent(f, f.agents[s.agent], s) for s in f.pipeline]


def overnight_data(f: Factory) -> dict | None:
    name = f.overnight.get("scout")
    if not name or name not in f.agents:
        return None
    return {"scout": _agent(f, f.agents[name], None), "max_proposals": f.overnight.get("max_proposals", 3),
            "auto_build": bool(f.overnight.get("auto_build", False)),
            "per_day_usd": f.budgets.get("per_day_usd"), "schedule": f.overnight.get("schedule", "")}


def issue_state(f: Factory, issue: dict) -> tuple[str | None, str]:
    labels = {l["name"] for l in issue.get("labels", [])}
    stage = next((s for s in f.stage_names if f"factory:{s}" in labels), None)
    if PROPOSED in labels and f.label not in labels and issue.get("state") == "open":
        return None, "proposed"
    if DONE in labels:
        return None, "done"
    if STOPPED in labels or issue.get("state") == "closed":
        return stage, "stopped"
    if WAITING in labels:
        return stage, "waiting"
    if RUNNING in labels:
        return stage, "running"
    return stage or f.first_stage, "queued"


def collect(f: Factory, gh, repo: str) -> dict:
    """Read every factory issue and its run records from GitHub."""
    issues, runs = [], []
    for log in gh.issues(LOG):
        for r in records.runs_from_comments(gh.comments(log["number"])):
            r.pop("report", None)
            r["issue_title"] = "Overnight scout"
            runs.append(r)
    found = {i["number"]: i for i in gh.issues(f.label)}
    for i in gh.issues(PROPOSED, state="open"):
        found.setdefault(i["number"], i)
    for issue in sorted(found.values(), key=lambda i: i["number"], reverse=True):
        stage, state = issue_state(f, issue)
        issue_runs = records.runs_from_comments(gh.comments(issue["number"]))
        for r in issue_runs:
            r.pop("report", None)
            r["issue_title"] = issue["title"]
        runs += issue_runs
        issues.append({
            "number": issue["number"], "title": issue["title"], "url": issue["html_url"],
            "author": issue["user"]["login"], "stage": stage, "state": state,
            "created": issue.get("created_at"), "updated": issue.get("updated_at"),
            "pr": next((r.get("pr") for r in reversed(issue_runs) if r.get("pr")), None),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in issue_runs), 2),
            "tokens": sum(sum((r.get("tokens") or {}).values()) for r in issue_runs),
        })
    runs.sort(key=lambda r: r.get("started") or "", reverse=True)
    return {"repo": repo, "generated": records.now_iso(), "agents": agents_data(f), "overnight": overnight_data(f),
            "issues": issues, "runs": runs, "budget_per_issue": f.budgets.get("per_issue_usd"),
            "budget_per_day": f.budgets.get("per_day_usd")}


def render(data: dict, standalone: bool = True) -> str:
    """The dashboard page. standalone=True adds the document wrapper (for Pages)."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__FACTORY_DATA__*/null", payload)
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + page + '\n</body>\n</html>\n')


def sample_tokens(usd: float) -> dict:
    """Plausible example token counts for a run that cost `usd`."""
    return {"input": int(usd * 6000), "output": int(usd * 9000),
            "cache_read": int(usd * 260000), "cache_write": int(usd * 30000)}


def sample(f: Factory) -> dict:
    """Realistic example data, clearly marked, for previews and tests."""
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def at(minutes_ago: float) -> str:
        return (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")

    plan = [
        (41, "Show today's top three projects in the menu bar", "done", None, [
            ("triage", "accept", "spec", 300, 2.1, 0.21), ("spec", "done", "implement", 290, 4.8, 0.74),
            ("implement", "done", "review", 280, 19.5, 3.92), ("review", "changes_requested", "implement", 258, 6.2, 1.10),
            ("implement", "done", "review", 250, 8.4, 1.65), ("review", "approve", "verify", 240, 5.1, 0.88),
            ("verify", "pass", "done", 232, 11.7, 0.94)]),
        (44, "Export a project's resume as Markdown", "running", "implement", [
            ("triage", "accept", "spec", 38, 1.8, 0.18), ("spec", "done", "implement", 34, 5.5, 0.81)]),
        (45, "Let me pause capture for one app from the pill", "waiting", "triage", [
            ("triage", "needs_info", "wait", 22, 2.4, 0.26)]),
        (43, "Sync memories to my iPhone", "stopped", "triage", [
            ("triage", "reject", "stop", 900, 1.2, 0.12)]),
        (46, "Keyboard shortcut to copy the current context", "queued", "triage", []),
    ]
    issues, runs = [], []
    for number, title, state, stage, steps in plan:
        agents = {a.name: a for a in f.agents.values()}
        cost = 0.0
        for i, (st, decision, nxt, ago, minutes, usd) in enumerate(steps):
            a = agents.get(f.stage(st).agent) if st in f.stage_names else None
            if not a:
                continue
            runs.append({"id": f"ex{number}{i}", "issue": number, "issue_title": title, "stage": st,
                         "agent": a.name, "model": a.model, "status": "succeeded", "decision": decision,
                         "next": nxt, "started": at(ago), "finished": at(ago - minutes),
                         "duration_s": minutes * 60, "cost_usd": usd, "turns": int(minutes * 3),
                         "tokens": sample_tokens(usd),
                         "summary": f"Example {a.title.lower()} run.", "pr": 52 if st != "triage" and number == 41 else None,
                         "activity": [], "run_url": None})
            cost += usd
        if state == "running":
            a = f.agent_for(stage)
            runs.append({"id": f"ex{number}r", "issue": number, "issue_title": title, "stage": stage,
                         "agent": a.name, "model": a.model, "status": "running", "decision": None,
                         "next": None, "started": at(12), "finished": None, "duration_s": 0,
                         "cost_usd": 0, "turns": 0, "summary": "", "pr": None, "run_url": None,
                         "activity": ["Reading `docs/specs/2026-11-22-export-a-projects-resume-as-markdown.md`",
                                      "Reading `src/project_actions.py`", "Editing `src/project_actions.py`",
                                      "Editing `tests/test_project_actions.py`",
                                      "Running: Run the project action tests"]})
        issues.append({"number": number, "title": title, "url": "#", "author": "AvniDhir",
                       "stage": stage, "state": state, "created": at(400), "updated": at(5),
                       "pr": 52 if number == 41 else None, "cost_usd": round(cost, 2)})
    runs.sort(key=lambda r: r["started"], reverse=True)
    if f.overnight.get("scout") in f.agents:
        s = f.agents[f.overnight["scout"]]
        runs.append({"id": "exs", "issue": 40, "issue_title": "Overnight scout", "stage": "scout", "agent": s.name,
                     "model": s.model, "status": "succeeded", "decision": "proposed", "next": "proposed",
                     "started": at(560), "finished": at(552), "duration_s": 480, "cost_usd": 0.64, "turns": 20,
                     "tokens": sample_tokens(0.64),
                     "summary": "Example scout run.", "pr": None, "activity": [], "run_url": None})
        issues.append({"number": 47, "title": "Show a weekly summary of time spent per project", "url": "#",
                       "author": "github-actions[bot]", "stage": None, "state": "proposed", "created": at(552),
                       "updated": at(552), "pr": None, "cost_usd": 0})
    runs.sort(key=lambda r: r["started"], reverse=True)
    return {"repo": "Meet-Trace/LMemM", "generated": records.now_iso(), "sample": True,
            "agents": agents_data(f), "overnight": overnight_data(f), "issues": issues, "runs": runs,
            "budget_per_issue": f.budgets.get("per_issue_usd"), "budget_per_day": f.budgets.get("per_day_usd")}

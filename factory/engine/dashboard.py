"""Build the factory dashboard: every agent, what it is doing, and every run."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from . import records
from .config import Factory
from .pipeline import DONE, RUNNING, STOPPED, WAITING

TEMPLATE = Path(__file__).with_name("dashboard.html")


def agents_data(f: Factory) -> list[dict]:
    out = []
    for stage in f.pipeline:
        a = f.agents[stage.agent]
        prompt = a.prompt
        out.append({
            "stage": stage.stage, "name": a.name, "title": a.title, "color": a.color,
            "description": a.description, "model": a.model, "effort": a.effort,
            "tools": list(a.tools), "disallowed_tools": list(a.disallowed_tools),
            "workspace": a.workspace, "budget_usd": a.budget_usd, "context": list(a.context),
            "decisions": [{"name": k, "meaning": v, "next": stage.on.get(k)} for k, v in a.decisions.items()],
            "prompt": prompt, "file": f"factory/{a.source.relative_to(f.root)}" if a.source else "",
            "prompt_file": f"factory/{a.prompt_path.relative_to(f.root)}",
        })
    return out


def issue_state(f: Factory, issue: dict) -> tuple[str | None, str]:
    labels = {l["name"] for l in issue.get("labels", [])}
    stage = next((s for s in f.stage_names if f"factory:{s}" in labels), None)
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
    for issue in sorted(gh.issues(f.label), key=lambda i: i["number"], reverse=True):
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
        })
    runs.sort(key=lambda r: r.get("started") or "", reverse=True)
    return {"repo": repo, "generated": records.now_iso(), "agents": agents_data(f),
            "issues": issues, "runs": runs, "budget_per_issue": f.budgets.get("per_issue_usd")}


def render(data: dict, standalone: bool = True) -> str:
    """The dashboard page. standalone=True adds the document wrapper (for Pages)."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__FACTORY_DATA__*/null", payload)
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + page + '\n</body>\n</html>\n')


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
    return {"repo": "Meet-Trace/LMemM", "generated": records.now_iso(), "sample": True,
            "agents": agents_data(f), "issues": issues, "runs": runs,
            "budget_per_issue": f.budgets.get("per_issue_usd")}

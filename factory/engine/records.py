"""Run records: what each agent did on each run, stored on the GitHub issue.

Every agent run is one issue comment. The comment shows people a readable
status; a hidden JSON marker at its top holds the machine-readable record, so
the issue itself is the run history and the dashboard needs no database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re

MARKER_RE = re.compile(r"<!--\s*factory-run\s+(\{.*?\})\s*-->", re.S)
STATUS_TEXT = {
    "running": "running",
    "succeeded": "finished",
    "failed": "failed",
}
MAX_ACTIVITY = 12


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    id: str
    issue: int
    stage: str
    agent: str
    model: str
    status: str = "running"          # running | succeeded | failed
    started: str = field(default_factory=now_iso)
    finished: str | None = None
    decision: str | None = None
    next: str | None = None          # the stage or special target chosen
    summary: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    run_url: str | None = None
    pr: int | None = None
    error: str | None = None
    activity: list[str] = field(default_factory=list)
    comment_id: int | None = None

    def add_activity(self, line: str) -> None:
        line = " ".join(line.split())[:160]
        if line and (not self.activity or self.activity[-1] != line):
            self.activity.append(line)
            del self.activity[:-MAX_ACTIVITY]

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("comment_id", None)
        # `--` would end the HTML comment early.
        return json.dumps(data, ensure_ascii=False, sort_keys=True).replace("--", "\\u002d\\u002d")


def render_comment(record: RunRecord, agent_title: str, icon: str, report: str = "") -> str:
    """The issue comment for a run: hidden record, then what people read."""
    status = STATUS_TEXT.get(record.status, record.status)
    head = f"### {icon} {agent_title} agent · {status}"
    meta = [f"stage `{record.stage}`", f"model `{record.model}`"]
    if record.status != "running":
        meta.append(f"{record.duration_s / 60:.1f} min")
        meta.append(f"${record.cost_usd:.2f}")
    if record.run_url:
        meta.append(f"[logs]({record.run_url})")
    lines = [f"<!-- factory-run {record.to_json()} -->", head, "", " · ".join(meta), ""]
    if record.status == "running":
        lines.append("**Doing now**")
        lines += [f"- {a}" for a in record.activity[-6:]] or ["- starting up"]
    else:
        if record.decision:
            nxt = f" → **{record.next}**" if record.next else ""
            lines.append(f"**Decision:** `{record.decision}`{nxt}")
        if record.summary:
            lines += ["", record.summary]
        if record.error:
            lines += ["", f"> ⚠️ {record.error}"]
        if report:
            lines += ["", report.strip()]
        if record.activity:
            lines += ["", "<details><summary>Steps taken</summary>", ""]
            lines += [f"- {a}" for a in record.activity]
            lines += ["", "</details>"]
    return "\n".join(lines).strip() + "\n"


def parse_comment(body: str) -> dict | None:
    """Return the run record stored in a comment, or None."""
    match = MARKER_RE.search(body or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and "agent" in data else None


def report_from_comment(body: str) -> str:
    """The human-readable part of a finished run comment (used as context)."""
    body = MARKER_RE.sub("", body or "")
    body = re.split(r"\n<details><summary>Steps taken</summary>", body)[0]
    return body.strip()


def runs_from_comments(comments: list[dict]) -> list[dict]:
    """Every run record in a list of GitHub comments, oldest first."""
    runs = []
    for comment in comments:
        record = parse_comment(comment.get("body", ""))
        if record:
            record["comment_id"] = comment.get("id")
            record["report"] = report_from_comment(comment.get("body", ""))
            runs.append(record)
    return runs

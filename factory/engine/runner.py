"""Run one agent: Claude Code in headless mode, configured from the agent's YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Callable

from .config import Agent

OUTPUT_CONTRACT = """\

## How your work is used

You are one stage of an automated pipeline. Nobody answers questions while you
run; ask them through your decision and report instead. When you finish, return
the structured result:

- `decision`: exactly one of:
{decisions}
- `summary`: one plain sentence saying what you concluded or did.
{proposals}- `report`: Markdown for the issue thread. The requester and the next agents
  read it. Lead with the outcome; keep it under about 400 words unless a spec
  or a list of problems needs more.
"""


@dataclass
class AgentResult:
    decision: str | None
    summary: str
    report: str
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    error: str | None = None
    proposals: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.decision is not None


def fill(template: str, values: dict[str, str]) -> str:
    """Replace {name} for known names only, so other braces in prompts survive."""
    return re.sub(r"\{([a-z_]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


PROPOSAL_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "body", "size", "why"],
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "size": {"type": "string", "enum": ["S", "M", "L"]},
            "why": {"type": "string"},
        },
    },
}


def output_schema(agent: Agent) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "summary", "report"],
        "properties": {
            "decision": {"type": "string", "enum": list(agent.decisions)},
            "summary": {"type": "string"},
            "report": {"type": "string"},
        },
    }
    if agent.proposes:
        schema["required"].append("proposals")
        schema["properties"]["proposals"] = PROPOSAL_SCHEMA
    return schema


def system_prompt(agent: Agent, values: dict[str, str]) -> str:
    decisions = "\n".join(f"  - `{k}`: {v}" for k, v in agent.decisions.items())
    proposals = ("- `proposals`: the feature requests you propose, each with a short `title`, a `body` "
                 "written like a good feature request, a `size` (S, M or L) and `why` it matters.\n"
                 if agent.proposes else "")
    return fill(agent.prompt, values).rstrip() + "\n" + OUTPUT_CONTRACT.format(decisions=decisions, proposals=proposals)


def command(agent: Agent, task: str, values: dict[str, str], claude: str = "claude") -> list[str]:
    cmd = [
        claude, "-p", task,
        "--output-format", "stream-json", "--verbose",
        "--model", agent.model,
        "--effort", agent.effort,
        "--max-budget-usd", f"{agent.budget_usd:g}",
        "--permission-mode", "dontAsk",
        "--append-system-prompt", system_prompt(agent, values),
        "--json-schema", json.dumps(output_schema(agent)),
        "--no-session-persistence",
        "--tools", ",".join(agent.builtin_tools) or "",
    ]
    if agent.tools:
        cmd += ["--allowed-tools", ",".join(agent.tools)]
    if agent.disallowed_tools:
        cmd += ["--disallowed-tools", ",".join(agent.disallowed_tools)]
    return cmd


def describe_tool(name: str, args: dict) -> str:
    """A short human line for a tool call, shown as 'what the agent is doing'."""
    path = args.get("file_path") or args.get("path") or ""
    if path:
        path = os.path.relpath(path) if os.path.isabs(path) else path
    if name == "Read":
        return f"Reading `{path}`"
    if name in ("Edit", "Write", "MultiEdit"):
        return f"Editing `{path}`"
    if name == "Bash":
        cmd = (args.get("description") or args.get("command") or "").strip().splitlines()
        return f"Running: {cmd[0][:100]}" if cmd else "Running a command"
    if name == "Grep":
        return f"Searching for `{str(args.get('pattern', ''))[:60]}`"
    if name == "Glob":
        return f"Looking for files `{args.get('pattern', '')}`"
    return f"Using {name}"


def _parse_structured(event: dict) -> dict | None:
    data = event.get("structured_output")
    if isinstance(data, dict):
        return data
    text = event.get("result")
    if isinstance(text, str):
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


# The orchestrator talks to GitHub; agents never get its token.
HIDDEN_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")


def agent_env() -> dict[str, str]:
    # A missing Actions secret arrives as an empty string; drop it so Claude Code
    # falls back to the other credential (API key or subscription token).
    return {k: v for k, v in os.environ.items()
            if k not in HIDDEN_ENV and not (k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN") and not v)}


def run(agent: Agent, task: str, values: dict[str, str], cwd: Path,
        on_activity: Callable[[str], None] | None = None,
        claude: str | None = None, timeout_s: int = 3 * 3600) -> AgentResult:
    """Run the agent to completion, reporting each step through on_activity."""
    claude = claude or os.environ.get("FACTORY_CLAUDE", "claude")
    started = time.monotonic()
    cmd = command(agent, task, values, claude)
    # stderr goes to a file so a chatty process can never block on a full pipe.
    errfile = tempfile.TemporaryFile(mode="w+")
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=errfile,
                                text=True, bufsize=1, env=agent_env())
    except OSError as exc:
        errfile.close()
        return AgentResult(None, "", "", error=f"could not start Claude Code: {exc}")
    final: dict | None = None
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and on_activity:
                    on_activity(describe_tool(block.get("name", ""), block.get("input") or {}))
        elif event.get("type") == "result":
            final = event
        if time.monotonic() - started > timeout_s:
            proc.kill()
            break
    proc.stdout.close()
    code = proc.wait()
    errfile.seek(0)
    stderr = errfile.read()
    errfile.close()
    duration = time.monotonic() - started
    if not final:
        tail = stderr.strip().splitlines()[-3:]
        return AgentResult(None, "", "", duration_s=duration,
                           error=f"Claude Code exited ({code}) without a result. {' '.join(tail)}".strip())
    cost = float(final.get("total_cost_usd") or 0.0)
    turns = int(final.get("num_turns") or 0)
    structured = _parse_structured(final)
    if final.get("is_error") or final.get("subtype") not in (None, "success"):
        reason = final.get("subtype") or "error"
        if reason == "error_max_budget_usd":
            reason = f"hit this agent's ${agent.budget_usd:g} budget"
        return AgentResult(None, "", "", cost, turns, duration, error=f"Run ended early: {reason}")
    if not structured or structured.get("decision") not in agent.decisions:
        return AgentResult(None, "", str(final.get("result") or ""), cost, turns, duration,
                           error="The agent did not return a valid decision")
    proposals = [p for p in structured.get("proposals") or [] if isinstance(p, dict) and p.get("title")]
    return AgentResult(structured["decision"], str(structured.get("summary", "")).strip(),
                       str(structured.get("report", "")).strip(), cost, turns, duration, proposals=proposals)

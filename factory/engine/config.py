"""Load and validate the factory's declarative config (factory.yaml + agents/)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import yaml

FACTORY_DIR = Path(__file__).resolve().parents[1]
SPECIAL_TARGETS = ("wait", "stop", "done")
# Stage names that would clash with the factory's own status labels.
RESERVED = SPECIAL_TARGETS + ("waiting", "running", "stopped")
CONTEXT_KINDS = ("issue", "comments", "reports", "diff")
WORKSPACES = ("read", "write")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


class ConfigError(ValueError):
    """The factory config is invalid. The message lists every problem found."""


@dataclass(frozen=True)
class Agent:
    name: str
    title: str
    description: str
    model: str
    prompt_path: Path
    tools: tuple[str, ...]
    decisions: dict[str, str]
    icon: str = "🤖"
    color: str = "#64748b"
    effort: str = "medium"
    workspace: str = "read"
    disallowed_tools: tuple[str, ...] = ()
    context: tuple[str, ...] = ("issue",)
    budget_usd: float = 2.0
    proposes: bool = False   # returns new feature proposals (the overnight scout)
    source: Path | None = None

    @property
    def prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    @property
    def builtin_tools(self) -> tuple[str, ...]:
        """Tool names without permission patterns: `Bash(git:*)` -> `Bash`."""
        names = []
        for tool in self.tools:
            base = tool.split("(", 1)[0]
            if base not in names:
                names.append(base)
        return tuple(names)


@dataclass(frozen=True)
class Stage:
    stage: str
    agent: str
    on: dict[str, str]


@dataclass(frozen=True)
class Factory:
    root: Path
    label: str
    allowed_users: tuple[str, ...]
    project: dict
    runner: str
    budgets: dict
    policies: dict
    pipeline: tuple[Stage, ...]
    agents: dict[str, Agent] = field(default_factory=dict)
    overnight: dict = field(default_factory=dict)

    def stage(self, name: str) -> Stage:
        for stage in self.pipeline:
            if stage.stage == name:
                return stage
        raise KeyError(name)

    def agent_for(self, stage: str) -> Agent:
        return self.agents[self.stage(stage).agent]

    @property
    def first_stage(self) -> str:
        return self.pipeline[0].stage

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(s.stage for s in self.pipeline)

    def policy(self, key: str, default):
        return self.policies.get(key, default)


def _load_yaml(path: Path, problems: list[str]) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        problems.append(f"{path.name}: cannot read: {exc}")
        return {}
    if not isinstance(data, dict):
        problems.append(f"{path.name}: must be a mapping")
        return {}
    return data


def _load_agent(path: Path, root: Path, problems: list[str]) -> Agent | None:
    data = _load_yaml(path, problems)
    if not data:
        return None
    where = f"agents/{path.name}"
    start = len(problems)
    name = data.get("name", path.stem)
    if not isinstance(name, str) or not NAME_RE.match(name):
        problems.append(f"{where}: name must be lowercase letters, digits or dashes")
    elif name != path.stem:
        problems.append(f"{where}: name '{name}' must match the file name '{path.stem}'")
    for key in ("title", "description", "model", "prompt"):
        if not isinstance(data.get(key), str) or not data.get(key).strip():
            problems.append(f"{where}: '{key}' is required")
    tools = data.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        problems.append(f"{where}: 'tools' must be a list of tool names")
        tools = []
    disallowed = data.get("disallowed_tools", [])
    if not isinstance(disallowed, list):
        problems.append(f"{where}: 'disallowed_tools' must be a list")
        disallowed = []
    decisions = data.get("decisions")
    if not isinstance(decisions, dict) or not decisions:
        problems.append(f"{where}: 'decisions' must map each decision to a description")
        decisions = {}
    for key in decisions:
        if not isinstance(key, str) or not re.match(r"^[a-z][a-z0-9_]*$", key):
            problems.append(f"{where}: decision '{key}' must be snake_case")
    effort = data.get("effort", "medium")
    if effort not in EFFORTS:
        problems.append(f"{where}: effort must be one of {', '.join(EFFORTS)}")
    workspace = data.get("workspace", "read")
    if workspace not in WORKSPACES:
        problems.append(f"{where}: workspace must be 'read' or 'write'")
    if workspace == "read" and {"Write", "Edit"} & {t.split("(")[0] for t in tools}:
        problems.append(f"{where}: a read workspace cannot have Write or Edit tools")
    context = data.get("context", ["issue"])
    if not isinstance(context, list) or any(c not in CONTEXT_KINDS for c in context):
        problems.append(f"{where}: context items must be from {', '.join(CONTEXT_KINDS)}")
        context = ["issue"]
    budget = data.get("budget_usd", 2.0)
    if not isinstance(budget, (int, float)) or budget <= 0:
        problems.append(f"{where}: budget_usd must be a positive number")
        budget = 2.0
    prompt_path = root / str(data.get("prompt", ""))
    if data.get("prompt") and not prompt_path.is_file():
        problems.append(f"{where}: prompt file '{data.get('prompt')}' not found")
    if len(problems) > start:
        return None
    return Agent(
        name=name, title=data["title"], description=" ".join(data["description"].split()),
        model=data["model"], prompt_path=prompt_path, tools=tuple(tools),
        decisions={k: " ".join(str(v).split()) for k, v in decisions.items()},
        icon=str(data.get("icon", "🤖")), color=str(data.get("color", "#64748b")),
        effort=effort, workspace=workspace, disallowed_tools=tuple(disallowed),
        context=tuple(context), budget_usd=float(budget), source=path,
        proposes=bool(data.get("proposes", False)),
    )


def _routes(item: dict) -> dict[str, str]:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    raw = item.get("on", item.get(True)) or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def load(root: Path | str = FACTORY_DIR) -> Factory:
    """Load factory.yaml and every agent; raise ConfigError listing all problems."""
    root = Path(root)
    problems: list[str] = []
    main = _load_yaml(root / "factory.yaml", problems)
    agents: dict[str, Agent] = {}
    for path in sorted((root / "agents").glob("*.yaml")):
        agent = _load_agent(path, root, problems)
        if agent:
            agents[agent.name] = agent

    intake = main.get("intake") or {}
    stages: list[Stage] = []
    raw = main.get("pipeline") or []
    if not isinstance(raw, list) or not raw:
        problems.append("factory.yaml: 'pipeline' must list at least one stage")
        raw = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(f"factory.yaml: pipeline item {i + 1} must be a mapping")
            continue
        stages.append(Stage(stage=str(item.get("stage", "")), agent=str(item.get("agent", "")),
                            on=_routes(item)))
    names = [s.stage for s in stages]
    for s in stages:
        if not NAME_RE.match(s.stage):
            problems.append(f"pipeline: stage name '{s.stage}' must be lowercase letters, digits or dashes")
        if s.stage in RESERVED:
            problems.append(f"pipeline: '{s.stage}' is reserved and cannot be a stage name")
        if names.count(s.stage) > 1:
            problems.append(f"pipeline: stage '{s.stage}' appears more than once")
        agent = agents.get(s.agent)
        if agent is None:
            problems.append(f"pipeline: stage '{s.stage}' uses agent '{s.agent}', which has no valid agents/{s.agent}.yaml")
            continue
        for decision, target in s.on.items():
            if decision not in agent.decisions:
                problems.append(f"pipeline: stage '{s.stage}' maps '{decision}', which agent '{agent.name}' never returns")
            if target not in SPECIAL_TARGETS and target not in names:
                problems.append(f"pipeline: stage '{s.stage}' sends '{decision}' to unknown stage '{target}'")
        for decision in agent.decisions:
            if decision not in s.on:
                problems.append(f"pipeline: stage '{s.stage}' does not say where '{decision}' goes")
    if stages and not any(t == "done" for s in stages for t in s.on.values()):
        problems.append("pipeline: no stage ever reaches 'done'")
    # Every stage must be reachable from the first one.
    if stages and not problems:
        seen, todo = set(), [stages[0].stage]
        while todo:
            cur = todo.pop()
            if cur in seen or cur in SPECIAL_TARGETS:
                continue
            seen.add(cur)
            todo.extend(next(s for s in stages if s.stage == cur).on.values())
        for s in stages:
            if s.stage not in seen:
                problems.append(f"pipeline: stage '{s.stage}' can never be reached")

    overnight = dict(main.get("overnight") or {})
    scout = overnight.get("scout")
    if scout:
        if scout not in agents:
            problems.append(f"overnight: scout agent '{scout}' has no valid agents/{scout}.yaml")
        elif not agents[scout].proposes:
            problems.append(f"overnight: agent '{scout}' needs 'proposes: true' to act as the scout")
        if not isinstance(overnight.get("max_proposals", 3), int) or overnight.get("max_proposals", 3) < 1:
            problems.append("overnight: max_proposals must be a whole number of at least 1")

    if problems:
        raise ConfigError("\n".join(problems))
    allowed = intake.get("allowed_users") or []
    return Factory(
        root=root, label=str(intake.get("label", "factory")),
        allowed_users=tuple(str(u).lower() for u in allowed),
        project=dict(main.get("project") or {}), runner=str(main.get("runner", "ubuntu-latest")),
        budgets=dict(main.get("budgets") or {}), policies=dict(main.get("policies") or {}),
        pipeline=tuple(stages), agents=agents, overnight=overnight,
    )


AGENT_TEMPLATE = """\
# {title}: describe what this agent is for.
name: {name}
title: {title}
icon: "🤖"
color: "#64748b"
description: >
  One or two sentences on what this agent does. The dashboard shows this.

model: claude-sonnet-5-5
effort: medium
prompt: prompts/{name}.md
tools: [Read, Grep, Glob]
workspace: read        # read, or write to let it change files on the branch
budget_usd: 2

context: [issue, comments, reports]

decisions:
  done: Finished; move on.
  needs_info: Needs an answer from the requester.
"""

PROMPT_TEMPLATE = """\
You are the {name} agent of a software factory for this repository.

Describe the job, the steps and what a good report contains.
"""


def scaffold_agent(name: str, root: Path | str = FACTORY_DIR) -> list[Path]:
    """Create agents/<name>.yaml and prompts/<name>.md from templates."""
    if not NAME_RE.match(name) or name in RESERVED:
        raise ConfigError(f"'{name}' is not a valid agent name")
    root = Path(root)
    agent_path, prompt_path = root / "agents" / f"{name}.yaml", root / "prompts" / f"{name}.md"
    for path in (agent_path, prompt_path):
        if path.exists():
            raise ConfigError(f"{path.relative_to(root)} already exists")
    title = name.replace("-", " ").capitalize()
    agent_path.write_text(AGENT_TEMPLATE.format(name=name, title=title), encoding="utf-8")
    prompt_path.write_text(PROMPT_TEMPLATE.format(name=name), encoding="utf-8")
    return [agent_path, prompt_path]

"""Command line for the factory: python -m factory <command>."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import config, dashboard, pipeline
from .github import FakeGitHub, GitHub

WORKFLOW = "factory.yml"


def _factory() -> config.Factory:
    return config.load(os.environ.get("FACTORY_DIR", config.FACTORY_DIR))


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("GITHUB_REPOSITORY is not set (owner/name)")
    return repo


def _run_url() -> str | None:
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{_repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return None


def _orchestrator(f: config.Factory, gh) -> pipeline.Orchestrator:
    ws = pipeline.Workspace(os.environ.get("FACTORY_WORKSPACE", "."), f.project.get("base_branch", "main"))
    return pipeline.Orchestrator(f, gh, ws, run_url=_run_url())


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


def _handoff(gh, f, number: int, state: str) -> None:
    """A job stopped mid-pipeline (stage limit): continue in a fresh workflow run."""
    if state in f.stage_names:
        ref = os.environ.get("GITHUB_REF_NAME") or f.project.get("base_branch", "main")
        gh.dispatch(WORKFLOW, ref, {"issue": number, "stage": state})


def cmd_validate(args) -> int:
    try:
        f = _factory()
    except config.ConfigError as exc:
        print("The factory config has problems:\n")
        for line in str(exc).splitlines():
            print(f"  - {line}")
        return 1
    print(f"OK: {len(f.agents)} agents, pipeline {' -> '.join(f.stage_names)}")
    return 0


def cmd_agents(args) -> int:
    f = _factory()
    for stage in f.pipeline:
        a = f.agents[stage.agent]
        routes = ", ".join(f"{k}->{v}" for k, v in stage.on.items())
        print(f"{stage.stage:<12} {a.title:<16} {a.model:<20} {a.workspace:<5} ${a.budget_usd:<5g} {routes}")
    unused = sorted(set(f.agents) - {s.agent for s in f.pipeline})
    if unused:
        print(f"\nDefined but not in the pipeline: {', '.join(unused)}")
    return 0


def cmd_new_agent(args) -> int:
    try:
        paths = config.scaffold_agent(args.name, os.environ.get("FACTORY_DIR", config.FACTORY_DIR))
    except config.ConfigError as exc:
        print(exc)
        return 1
    for p in paths:
        print(f"created {p}")
    print("Add a stage for it to the pipeline in factory/factory.yaml, then run: python -m factory validate")
    return 0


def cmd_runner(args) -> int:
    print(_factory().runner)
    return 0


def cmd_handle(args) -> int:
    """Handle the GitHub Actions event that started this workflow run."""
    f = _factory()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()) if os.environ.get("GITHUB_EVENT_PATH") else {}
    gh = GitHub(_repo())
    orch = _orchestrator(f, gh)

    if event_name == "schedule" or (event_name == "workflow_dispatch" and not (event.get("inputs") or {}).get("issue")):
        due = orch.sweep()
        for number in due:
            gh.dispatch(WORKFLOW, os.environ.get("GITHUB_REF_NAME", "main"), {"issue": number, "stage": ""})
        print(f"sweep: dispatched {due or 'nothing'}")
        return 0

    number, start, comment = None, None, None
    if event_name == "issues" and (event.get("label") or {}).get("name") == f.label:
        number = event["issue"]["number"]
    elif event_name == "issue_comment" and "pull_request" not in event.get("issue", {}):
        number, comment = event["issue"]["number"], event["comment"]
    elif event_name == "workflow_dispatch":
        inputs = event.get("inputs") or {}
        number, start = int(inputs["issue"]), (inputs.get("stage") or None)
    if number is None:
        print(f"nothing to do for {event_name}")
        return 0
    if not _has_credentials():
        if comment is None or not comment["user"]["login"].endswith("[bot]"):
            gh.comment(number, "⚠️ The factory cannot run yet: add a `CLAUDE_CODE_OAUTH_TOKEN` secret (your Claude "
                               "subscription, from `claude setup-token`) or an `ANTHROPIC_API_KEY` secret under "
                               "Settings → Secrets and variables → Actions, then reply `/factory retry`.")
        return 1
    state = orch.handle_comment(number, comment) if comment else orch.advance(number, start=start)
    print(f"#{number}: {state}")
    _handoff(gh, f, number, state)
    return 0


def cmd_scout(args) -> int:
    """Overnight: the scout proposes new requests; auto-built ones start now."""
    f = _factory()
    if not f.overnight.get("scout"):
        print("no scout configured (overnight.scout in factory.yaml)")
        return 0
    if not _has_credentials():
        print("no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY; skipping the scout")
        return 1
    gh = GitHub(_repo())
    created = _orchestrator(f, gh).scout()
    print(f"scout: proposed {created or 'nothing'}")
    if f.overnight.get("auto_build"):
        ref = os.environ.get("GITHUB_REF_NAME") or f.project.get("base_branch", "main")
        for number in created:
            gh.dispatch(WORKFLOW, ref, {"issue": number, "stage": ""})
    return 0


def cmd_advance(args) -> int:
    f = _factory()
    gh = GitHub(_repo())
    state = _orchestrator(f, gh).advance(args.issue, start=args.stage)
    print(f"#{args.issue}: {state}")
    return 0


def cmd_dashboard(args) -> int:
    f = _factory()
    data = dashboard.sample(f) if args.sample else dashboard.collect(f, GitHub(_repo()), _repo())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(dashboard.render(data, standalone=not args.fragment), encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out / 'index.html'} ({len(data['runs'])} runs, {len(data['issues'])} requests)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m factory", description="The software factory.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check factory.yaml and every agent").set_defaults(fn=cmd_validate)
    sub.add_parser("agents", help="list the pipeline and its agents").set_defaults(fn=cmd_agents)
    s = sub.add_parser("new-agent", help="create agents/<name>.yaml and prompts/<name>.md")
    s.add_argument("name")
    s.set_defaults(fn=cmd_new_agent)
    sub.add_parser("runner", help="print the Actions runner label").set_defaults(fn=cmd_runner)
    sub.add_parser("handle", help="handle the current GitHub Actions event").set_defaults(fn=cmd_handle)
    sub.add_parser("scout", help="run the overnight scout now").set_defaults(fn=cmd_scout)
    s = sub.add_parser("advance", help="run one issue through the pipeline now")
    s.add_argument("issue", type=int)
    s.add_argument("--stage")
    s.set_defaults(fn=cmd_advance)
    s = sub.add_parser("dashboard", help="build the dashboard page")
    s.add_argument("--out", default="_site")
    s.add_argument("--sample", action="store_true", help="use example data instead of GitHub")
    s.add_argument("--fragment", action="store_true", help="omit the <html> wrapper")
    s.set_defaults(fn=cmd_dashboard)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

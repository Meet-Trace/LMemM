#!/usr/bin/env python3
"""
LMemM - the one command.

    lmemm.py                          a menu of everything below - start here
    lmemm.py start [--every N] [--no-widget] [--input-events --input-app com.microsoft.VSCode]
                                      watch and remember (Ctrl-C to stop)
    lmemm.py memory [N] [--content] [--events]
                                      what's remembered + the latest session's timeline
    lmemm.py notes [--all] [PROJECT]  pending edits (your ⌃⌥N notes) by project
    lmemm.py notes done ID… | notes reopen ID…
    lmemm.py suggest [NAME] [ID…]     offer things as one project (stands in for the model)
    lmemm.py suggest-for PROJECT [ID…]
                                      offer things for an existing project (the three latest not in it, or the ids)
    lmemm.py look-for-projects [--dry-run [--days N] [--method ai|assign] [--calls N]]
                                      look for projects in the last two weeks now and suggest them
                                      (--dry-run: only print what would be suggested; files and offers nothing.
                                      The ai method still caches the models' answers and logs the look)
    lmemm.py projects [--all] | projects new|rename|move|archive|restore|merge|delete …
                                      look at and shape the project tree (any depth)
    lmemm.py context [SESSION] [--days N]
                                      a clean export for handing to an AI: what you did
                                      and why, grouped by project, with no operational detail
    lmemm.py trail start|show|places|status|pause|resume|forget|probe
                                      the event trail: what you are doing, from app events (see docs/specs)
    lmemm.py status | pause | resume | open  control the running tracker or open its window
    lmemm.py pin                      force-save the current screen
    lmemm.py note                     open the note window (same as ⌃⌥N)
    lmemm.py delete-session ID (--dry-run | --confirm ID)
    lmemm.py delete-all (--dry-run | --confirm)
                                      remove everything LMemM has kept, on this Mac
    lmemm.py setup [--again]          first-run setup, then LMemM starts (also runs once before `start`)

Any of these also works by number from the menu (`lmemm.py`), which prompts for the
same arguments shown above when an action takes them.

How it fits together: see docs/architecture.md.
"""

import argparse
import json
import os
import shlex
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import config  # noqa: E402
import context as context_mod  # noqa: E402
import notes  # noqa: E402
import store  # noqa: E402
import tracker  # noqa: E402

USAGE = ("usage: lmemm.py [menu] | start [--every N] [--input-events --input-app APP]"
         " | memory [N] [--content] [--events]"
         " | notes [--all] [PROJECT] | notes done|reopen ID…"
         " | projects [--all] | projects new|rename|move|archive|restore|merge|delete …"
         " | context [SESSION] [--days N]"
         " | trail … | status | pause | resume | pin | note | delete-session ID (--dry-run | --confirm ID)"
         " | delete-all (--dry-run | --confirm) | setup [--again] [--no-start]")


# ---------------------------------------------------------------- memory

def show_memory(n, show_content=False, show_events=False):
    """What's remembered (one line per thing), then the latest session's timeline."""
    try:
        items = sorted(store.load_items().values(), key=lambda i: i["last_seen"], reverse=True)
        if not items and not show_events:
            raise ValueError
    except (OSError, ValueError):
        sys.exit("nothing remembered yet. Run:  python3 lmemm.py")
    print("remembered (most recent first)\n")
    for i in items[:n]:
        print(f"{i['last_seen'][11:19]}  {i['seconds']:5d}s  x{i['visits']:<2}  "
              f"{(i.get('mostly') or '-'):9}  {i['app'][:14]:14}  {i['doing']}")
        acts = i.get("activity", {})
        split = "  ".join(f"{c} {a['seconds']}s" for c, a in acts.items() if a["seconds"])
        if split:
            print(f"{'':30}{split}")
        typed = acts.get("typing", {}).get("text")
        if typed:
            print(f"{'':30}typed: {typed[-1][:80]}")
        for note_entry in i.get("notes", [])[-3:]:
            mark = "done" if notes.is_done(i, note_entry) else "open"
            print(f"{'':30}your note ({mark}, {notes.note_id(note_entry)}): \"{note_entry['text'][:80]}\"")
        if show_content:
            show_excerpts(i)
    sessions = sorted(Path(config.paths().sessions_dir).glob("*.json"))
    if sessions:
        doc = json.loads(sessions[-1].read_text())
        print(f"\nlatest session {doc['session']}\n")
        for e in doc["timeline"][-n:]:
            elapsed = e.get("for") or store.duration(e.get("seconds", 0))
            print(f"{e['from']}  {elapsed:>7}  {(e.get('mostly') or '-'):9}  {e['app'][:14]:14}  {e['doing']}")
    if show_events:
        show_input_events(n)
    print(f"\n{config.paths().items_file}")


def show_excerpts(item):
    excerpts = item.get("content", {}).get("excerpts", [])
    if not excerpts:
        print("  No content excerpt retained for this item.")
    for excerpt in excerpts[-3:]:
        source = excerpt["source"]
        origin = source.get("url") or source.get("window") or source.get("app") or "unknown"
        print(f"  Observed {excerpt['first_seen']} (last {excerpt['last_seen']}) — {origin}")
        text = excerpt["text"]
        print("    " + text[:600].replace("\n", "\n    ") + ("…" if len(text) > 600 else ""))
        for quote in excerpt.get("decision_quotes", []):
            print(f"    Decision quote (unverified): {quote}")


def show_input_events(n):
    from input_store import prune_corpus
    root = Path(config.paths().memory_dir)
    if tracker.running_pid() is None:     # while running, the tracker owns pruning
        prune_corpus(root)
    for path in sorted((root / "inputs").glob("*.json"))[-1:]:
        doc = json.loads(path.read_text())
        cutoff = datetime.now(timezone.utc) - timedelta(hours=min(doc.get("retention_hours", 24), 24))
        events = [e for e in doc["events"] if datetime.fromisoformat(e["end_utc"]) >= cutoff]
        print(f"\ninput session {doc['session']}: {len(events)} events; {doc['status']}; "
              f"expired: {doc.get('expired', 0)}; capacity dropped: {doc.get('dropped_capacity', 0)}")
        navigation = [e for e in events if e["kind"] == "navigation_shortcut"]
        for action in ("tab_switch", "app_switch"):
            for direction in ("forward", "backward"):
                steps = sum(e["payload"]["count"] for e in navigation
                            if e["payload"]["action"] == action and e["payload"]["direction"] == direction)
                if steps:
                    print(f"  {action} {direction}: {steps} observed shortcut steps (not window distance)")
        for event in events[-n:]:
            print(f"  {event['start_utc']}  {event['kind'].replace('_', ' ')}: {event['payload']}")
            if event.get("observed_result"):
                print(f"    observed result: {event['observed_result']} — {event['observed_transition']}")
            if event.get("after_capture"):
                print(f"    after: {event['after_capture']}  item: {event.get('item', 'pending OCR')}")
            if event.get("before_capture"):
                print(f"    before: {event['before_capture']} ({event['before_age_seconds']}s old)")


# ---------------------------------------------------------------- commands

def cmd_start(args):
    from input_monitor import SUPPORTED_INPUT_APPS
    parser = argparse.ArgumentParser(prog="lmemm.py")
    parser.add_argument("--every", type=int, default=config.EVERY)
    parser.add_argument("--input-events", action="store_true")
    parser.add_argument("--input-app", action="append", default=[])
    parser.add_argument("--input-retention-hours", type=float, default=24)
    parser.add_argument("--no-widget", action="store_true", help="don't show the on-screen pill")
    parser.add_argument("--no-trail", action="store_true", help="don't run the accessibility event trail")
    parser.add_argument("--no-setup", action="store_true", help="skip first-run setup (it asks for permissions itself)")
    opts = parser.parse_args(args)
    if opts.every <= 0 or not 0 < opts.input_retention_hours <= 24:
        parser.error("positive capture interval and input retention of at most 24 hours required")
    if opts.input_events != bool(opts.input_app) or not set(opts.input_app) <= SUPPORTED_INPUT_APPS:
        parser.error("input monitoring requires --input-events --input-app com.microsoft.VSCode")
    if not opts.no_setup and not first_run_setup():
        sys.exit("Setup isn't finished. Run LMemM again to pick up where you left off.")
    tracker.Tracker(every=opts.every, input_apps=set(opts.input_app) or None,
                    input_retention_hours=opts.input_retention_hours, show_widget=not opts.no_widget,
                    trail=not opts.no_trail).run()


def first_run_setup(again=False):
    """Show first-run setup if it has not been finished (or when asked again). True when
    LMemM can go on; False when the window was closed part-way (it resumes next time)."""
    import onboarding
    path = config.paths().onboarding_file
    state = onboarding.load_state(path)
    if again:
        state.update(completed=False, step="welcome")
        onboarding.save_state(path, state)
    elif not onboarding.needs_setup(state):
        return True
    import onboarding_ui
    return onboarding_ui.run()


def cmd_setup(args):
    if not set(args) <= {"--again", "--no-start"}:
        sys.exit("usage: lmemm.py setup [--again] [--no-start]")
    if not first_run_setup(again="--again" in args):
        sys.exit("Setup isn't finished. Run  python3 lmemm.py setup  to pick up where you left off.")
    if "--no-start" in args:
        print("Setup is done.")
        return
    cmd_start(["--no-setup"])                      # setup ends with the pill on screen, not a closed app


def cmd_delete_all(args):
    import forget
    parser = argparse.ArgumentParser(prog="lmemm.py delete-all")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--confirm", action="store_true")
    opts = parser.parse_args(args)
    data_dir = config.paths().data_dir
    try:
        if opts.dry_run:
            result = forget.plan(data_dir)
            print(json.dumps(result, indent=2))
            print("\nNothing was deleted. Run with --confirm to delete all of this.")
        else:
            result = forget.delete_all(data_dir, tracker.running_pid())
            print(f"deleted {result['files']} file(s) from {data_dir}. Next start runs setup again.")
    except ValueError as error:
        sys.exit(str(error))


def cmd_memory(args):
    counts = [a for a in args if a not in {"--content", "--events"}]
    try:
        if len(counts) > 1:
            raise ValueError
        count = int(counts[0]) if counts else 20
        if count < 1:
            raise ValueError
    except ValueError:
        sys.exit("usage: lmemm.py memory [N] [--content] [--events]")
    show_memory(count, show_content="--content" in args, show_events="--events" in args)


def cmd_suggest(args):
    """Offer a project on the pill. With no ids, the four most recent things."""
    name = args[0] if args else "Project"
    if not tracker.send_control("suggest", name=name, ids=args[1:], reason="Opened together"):
        sys.exit("LMemM isn't running.")
    print(f"offered '{name}' on the pill.")


def cmd_suggest_for(args):
    """Offer things for a project on the pill's window. With no ids, the three latest things not in it."""
    if not args:
        sys.exit("usage: lmemm.py suggest-for PROJECT [ID…]")
    import projects
    try:
        pid = projects.resolve(projects.load(), args[0])          # say now if there is no such project
    except ValueError as error:
        sys.exit(str(error))
    if not tracker.send_control("suggest_for", project=args[0], ids=args[1:], reason="Looks like it belongs here"):
        sys.exit("LMemM isn't running.")
    print(f"offered things for '{projects.get(projects.load(), pid)['name']}'. Open LMemM and look on that project's page.")


def cmd_look_for_projects(args):
    """Project finder now (project_finder.py): what you did lately, offered as projects. Never files anything."""
    if "--dry-run" in args:
        import project_finder
        import projects
        import real_days
        memory_dir = config.paths().memory_dir
        try:
            items = real_days.read_items(memory_dir)
            days = int(args[args.index("--days") + 1]) if "--days" in args else project_finder.LOOKBACK_DAYS
            method = args[args.index("--method") + 1] if "--method" in args else config.FINDER_METHOD
            if "--calls" in args:
                config.FINDER_CALLS = int(args[args.index("--calls") + 1])
            planned = project_finder.find(projects.load(), items, real_days.read_visits(memory_dir), days=days,
                                          method=method)
        except (OSError, ValueError, IndexError) as error:
            sys.exit(f"Could not look: {error}")
        print(f"Looked at the last {days} days in {memory_dir} ({method} method)")
        last = (project_finder.read_log() or [{}])[-1]
        if method == "ai" and last.get("event") == "look":
            if last.get("skipped"):
                sys.exit(f"Skipped: {last['skipped']}. See scripts/install_judge.py.")
            print(f"{last.get('things', 0)} unfiled things, {last.get('calls', 0)} new model calls, "
                  f"{last.get('seconds', 0)} s" + (" (paused at the call budget: run it again to go on)"
                                                   if last.get("paused") else ""))
            if not last.get("paused"):
                print(f"{len(last.get('projects') or {})} groups, {len(last.get('shared') or [])} shared, "
                      f"{len(last.get('not_filed') or [])} things left unfiled")
        if not planned:
            print("Nothing to suggest yet: no group of things came up together in two separate sittings.")
        for _, kind, (target, ids, why) in planned:
            what = f"new project “{target}”" if kind == "project" else f"add to “{projects.get(projects.load(), target)['name']}”"
            print(f"{what}: {why}")
            for i in ids:
                print(f"    {items[i].get('app') or '?'} · {items[i].get('title') or i}")
        return
    if not tracker.send_control("look_for_projects"):
        sys.exit("LMemM isn't running. `lmemm.py look-for-projects --dry-run` works without it.")
    print("Looking now. Suggestions appear on the pill and at the top of the window.")


def cmd_notes(args):
    """The project view of pending edits, or mark some done / open again."""
    if args and args[0] in {"done", "reopen"}:
        ids = args[1:]
        if not ids:
            sys.exit("usage: lmemm.py notes done|reopen ID…")
        done = args[0] == "done"
        if tracker.send_control("notes_done", ids=ids, done=done):
            print(f"sent to the running tracker: {len(ids)} note(s) → {'done' if done else 'open'}")
            return
        items = store.load_items()
        found = notes.set_done(items, ids, done=done)
        missing = sorted(set(ids) - set(found))
        if found:
            record_offline_change(items)
            store.save_memory(items)
        print(f"{len(found)} note(s) marked {'done' if done else 'open'}"
              + (f"; not found: {', '.join(missing)}" if missing else ""))
        return
    include_done = "--all" in args
    wanted = " ".join(a for a in args if a != "--all").lower()
    view = notes.pending_view(store.load_items(), include_done=include_done)
    projects = [p for p in view["projects"] if wanted in p["project"].lower()]
    if not projects:
        print("no pending edits" + (f" in '{wanted}'" if wanted else "") + ". Press ⌃⌥N while LMemM runs to add one.")
        return
    total = sum(p["open"] for p in projects)
    print(f"{total} pending edit{'s' * (total != 1)}\n")
    for p in projects:
        print(f"{p['project']}  ({p['open']} open)")
        for entry in p["items"]:
            print(f"  {entry['app'][:14]:14}  {entry['what'][:70]}")
            for n in entry["notes"]:
                state = f"done {n['done']}" if "done" in n else n["at"]
                print(f"      {n['id']}  {'✓' if 'done' in n else '•'} {n['text'][:90]}   ({state})")
        print()
    print("mark one done:  lmemm.py notes done <id>")


def record_offline_change(items):
    """Changing memory while the tracker is stopped: keep session provenance consistent
    (otherwise later session deletion refuses, seeing an unexplained change)."""
    from input_store import InputStore
    memory_dir = Path(config.paths().memory_dir)
    if not (memory_dir / "contributions" / "baseline.json").exists():
        return
    before = store.load_items()
    evidence = InputStore("notes-" + str(time.time_ns()), memory_dir)
    evidence.initialize_baseline(before)
    evidence.checkpoint(items, [], [])
    evidence.close()


def cmd_control(action):
    pid = tracker.running_pid()
    if pid is None:
        sys.exit("tracker is not running")
    if action == "status":
        try:
            status = json.loads(Path(config.paths().status_file).read_text())
            if status["pid"] != pid:
                raise ValueError
            print(json.dumps(status, indent=2))
        except (OSError, ValueError, KeyError):
            sys.exit("running tracker has no current status")
    else:
        tracker.send_control(action)
        print(f"{action} requested; use status to check acknowledgement")


def cmd_context(args):
    parser = argparse.ArgumentParser(prog="lmemm.py context")
    parser.add_argument("session", nargs="?")
    parser.add_argument("--days", type=float)
    opts = parser.parse_args(args)
    if opts.session and opts.days is not None:
        parser.error("give a session or --days, not both")
    try:
        doc, path = context_mod.export(session_id=opts.session, days=opts.days)
    except ValueError as error:
        sys.exit(str(error))
    print(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"\n{doc['things']} things, {doc['notes_open']} open notes -> {path}", file=sys.stderr)


def cmd_delete_session(args):
    from input_store import delete_session, plan_session_deletion
    parser = argparse.ArgumentParser(prog="lmemm.py delete-session")
    parser.add_argument("session")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--confirm", metavar="SESSION")
    opts = parser.parse_args(args)
    if opts.confirm and opts.confirm != opts.session:
        parser.error("--confirm must match the session ID")
    p = config.paths()
    paths = {"data_dir": p.data_dir, "memory_dir": Path(p.memory_dir), "pidfile": p.pidfile}
    try:
        result = plan_session_deletion(opts.session, paths) if opts.dry_run else delete_session(opts.session, paths)
        print(json.dumps(result, indent=2))
    except ValueError as error:
        sys.exit(str(error))


# ---------------------------------------------------------------- the menu

class MenuItem:
    def __init__(self, key, label, hint, hint_example, action):
        self.key, self.label, self.hint, self.hint_example, self.action = key, label, hint, hint_example, action


def menu_status(args):
    cmd_control("status")


def menu_pause_resume(args):
    """One option that toggles: pause if running and not paused, resume if paused."""
    pid = tracker.running_pid()
    if pid is None:
        sys.exit("tracker is not running")
    try:
        paused = json.loads(Path(config.paths().status_file).read_text()).get("paused", False)
    except (OSError, ValueError, KeyError):
        paused = False
    cmd_control("resume" if paused else "pause")


def menu_note(args):
    tracker.note()


def menu_pin(args):
    tracker.pin()


MENU = [
    MenuItem("1", "Start watching", "capture + remember what you do; Ctrl-C stops",
             "--every 10, --no-widget, --input-events --input-app com.microsoft.VSCode", cmd_start),
    MenuItem("2", "See what's remembered", "memory.json: what you did, notes, activity",
             "a count, --content, --events", cmd_memory),
    MenuItem("3", "Pending edits", "your ⌃⌥N notes, grouped by project",
             "--all, a project name, or 'done <id>' / 'reopen <id>'", cmd_notes),
    MenuItem("4", "Export context for an AI", "a clean summary - just what you did and why",
             "a session id, or --days 2", cmd_context),
    MenuItem("5", "Status", "is a session running, paused, what it's costing", None, menu_status),
    MenuItem("6", "Pause / resume", "toggle a session that's already running", None, menu_pause_resume),
    MenuItem("7", "Dictate a note now", "same as pressing ⌃⌥N", None, menu_note),
    MenuItem("8", "Force-save the current screen", "same as the pin hotkey", None, menu_pin),
    MenuItem("9", "Delete a session's data", "review with --dry-run first, then --confirm",
             "SESSION --dry-run", cmd_delete_session),
]


def menu_text():
    lines = ["", "LMemM", "─────"]
    for item in MENU:
        lines.append(f" {item.key}  {item.label:<28} {item.hint}")
    lines.append(" 0  Quit")
    lines.append("")
    return "\n".join(lines)


def interactive_menu(read=input, write=print):
    """The front door: `lmemm.py` with no arguments. Pick a number, optionally add the
    same flags the command-line form takes, Enter for the common case. 0 or Ctrl-D/Ctrl-C
    to leave. Every action runs in this same process and returns you to the menu."""
    write(menu_text())
    while True:
        try:
            choice = read("> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            return
        if not choice:
            continue
        if choice in ("0", "q", "quit", "exit"):
            return
        item = next((m for m in MENU if m.key == choice), None)
        if item is None:
            write(f"'{choice}' isn't one of the options above - type a number, 0 to quit.")
            continue
        args = []
        if item.hint_example:
            try:
                raw = read(f"  {item.label} ({item.hint_example}) - Enter for the plain version: ").strip()
            except (EOFError, KeyboardInterrupt):
                write("")
                continue
            if raw:
                args = shlex.split(raw)
        write("")
        try:
            item.action(args)
        except SystemExit as exc:
            if exc.code:
                write(str(exc.code))
        except KeyboardInterrupt:
            write("")
        write(menu_text())


def cmd_projects(args):
    import projects_cli

    def save(items):
        record_offline_change(items)
        store.save_memory(items)
    try:
        projects_cli.run(args, running=tracker.running_pid() is not None, save_items=save)
    except ValueError as error:
        sys.exit(str(error))


def main():
    args = sys.argv[1:]
    if not args:
        interactive_menu()
        return
    cmd = args[0] if not args[0].startswith("-") else "start"
    rest = args[1:] if args[0] == cmd else args
    if cmd == "menu":
        interactive_menu()
    elif cmd == "start":
        cmd_start(rest)
    elif cmd == "memory":
        cmd_memory(rest)
    elif cmd == "notes":
        cmd_notes(rest)
    elif cmd == "suggest":
        cmd_suggest(rest)
    elif cmd == "suggest-for":
        cmd_suggest_for(rest)
    elif cmd == "look-for-projects":
        cmd_look_for_projects(rest)
    elif cmd == "projects":
        cmd_projects(rest)
    elif cmd in {"status", "pause", "resume", "open"}:
        cmd_control(cmd)
    elif cmd == "trail":
        import trail_cli
        trail_cli.main(sys.argv[2:])
    elif cmd == "pin":
        tracker.pin()
    elif cmd == "note":
        tracker.note()
    elif cmd == "delete-session":
        cmd_delete_session(rest)
    elif cmd == "delete-all":
        cmd_delete_all(rest)
    elif cmd == "setup":
        cmd_setup(rest)
    elif cmd == "context":
        cmd_context(rest)
    else:
        sys.exit(USAGE)


if __name__ == "__main__":
    main()

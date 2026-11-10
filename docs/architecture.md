# Architecture

Normal tracker: one process, two threads, plain files. The experimental pilot is a separate opt-in process.

```
 macOS ──▶ macos.py ──▶ tracker.py (main thread) ──queue──▶ tracker.py (resolver thread)
           front window,     when to capture:                 resolver.py   OCR + layout
           browser tab,      switches, timer, pins,           understand.py what you're doing
           screenshot,       notes, pause, idle                activity.py   typing/reading/…
           input counters                                      identity.py   which thing
                                                               memory_content.py excerpts
  ⌃⌥N ──▶ dictation.py ──▶ notes.py ◀──────────────────────────┘
                                │                         store.py ──▶ data/memory/*.json
  opt-in ─▶ input_monitor.py ─▶ input_events.py ─▶ input_store.py ──▶ data/memory/inputs, contributions
```

## Modules

| Module | One job | Talks to macOS? |
|---|---|---|
| `config.py` | tunables, privacy skip-lists, data paths (`config.paths()`, `config.use_paths()` for tests) | no |
| `macos.py` | front window, browser URL, displays, screenshot, idle/input counters, pointer, notifications, workspace events | **yes** |
| `tracker.py` | orchestration: schedule captures, run the resolver thread, keep the timeline, apply controls, resurface notes | via `macos` |
| `resolver.py` | screenshot → objects with text, kind, box, entities (Apple Vision) | Vision |
| `understand.py` | objects + URL/window → app, action, target, details (per-app rules) | no |
| `activity.py` | two frames of one thing → changed regions, scroll, activity category | no |
| `identity.py` | which memory item a screen is: same thing vs. new thing | no |
| `observation.py` | one record both readers write (Observation) + conservative merge of the app's text with OCR + the in-memory log they meet in ([ADR 0001](adr/0001-unified-observation.md)) | no |
| `memory_content.py` | bounded excerpts + unverified decision quotes, built from the merged lines | no |
| `notes.py` | notes: record/attach, ids, done-state, resurfacing rule, project view | no |
| `store.py` | load/save `.index.json`, readable `memory.json` / `pending.json`, sessions | no |
| `context.py` | distills memory into a clean export for an AI: notes + real content, no operational detail | no |
| `dictation.py` + `native/listen/` | ⌃⌥N hotkey (Carbon), note window, on-device speech helper app | **yes** |
| `widget.py` | the on-screen pill and its Left / Plan card (non-activating panels) | **yes** |
| `input_monitor.py` | opt-in listen-only event tap + privacy gate | **yes** |
| `input_events.py` | input summaries, ordering, context boundaries | no |
| `input_store.py` | input files, capture links, provenance, session deletion | no |
| `trail_ax.py` | bounded accessibility-tree read into a Snapshot (title, URL, focus label, headings, selected rows, text) | **yes** (`LiveNode` only) |
| `trail_place.py` | Snapshot + URL -> which place (chat, AI chat, email, doc, page, file), with confidence; settle logic | no |
| `trail_engine.py` | when to look (Scheduler), what may be looked at (Gate), what to log (Engine) | no |
| `trail_store.py` | the event trail on disk: JSONL per day, 48 h retention, forget | no |
| `trail_mac.py` | AX observers, workspace notifications, listen-only event tap, screenshot fallback | **yes** |
| `trail_cli.py` | `lmemm.py trail ...` | no (`start`, `probe` use `trail_mac`) |
| `lmemm.py` | CLI: the interactive menu, and one direct command per menu item | no |

Rules that keep this cohesive:

- Only `macos.py`, `dictation.py`, `widget.py`, `input_monitor.py`, `resolver.py`, `trail_mac.py` (and `LiveNode` in `trail_ax.py`) touch macOS APIs,
  so everything else is testable with plain data.
- Nothing imports `tracker` or `lmemm`. Dependencies point inward toward `config`.
- Paths come from `config.paths()` at call time, never module globals. Tests point
  everything at a temp directory with `config.use_paths(dir)`.
- `.index.json` is the source of truth. Readable files are derived from it on every save.
  New fields are additive (schema 2).

## A screenshot's life

1. **Trigger** (`tracker.tick`, ~4×/s): a workspace notification, a title/window change
   (one CGWindowList call), the timer, a pin, a note, or input activity. Triggers wait
   `SETTLE` s so a burst of switches becomes one capture.
2. **Capture** (`tracker.capture`): skip-list check, grab the window's display **in
   memory** (`macos.grab`, ~25 ms, a `macos.Frame`; falls back to `screencapture` if the OS
   API is unavailable), re-check that the front window didn't change, enqueue. Nothing is
   written to disk.
3. **Resolve** (resolver thread, `tracker.handle`): if the pixels didn't change since the
   last frame of the same window, reuse its OCR. Otherwise `tracker.read`: **fast OCR**
   for a changed later frame of the same window, **accurate OCR** for the first look,
   notes, pins, and any fast pass that finds under `THIN_RATIO` of the previous line count.
   Then `understand.describe`. OCR runs outside the memory lock, so saving a note never
   waits for Vision.
4. **Remember** (`tracker.remember`, under the lock):
   `identity.resolve_item` → `activity.classify` → update the item → extend or start a
   timeline event → **resurface** open notes on a new visit → attach notes waiting for
   this frame → if the frame became the thing's screenshot, write it as a thumbnail
   (`keep_thumbnail`) → `save()`. A redundant frame is simply released.

## Cost control

| Mechanism | Where | Effect |
|---|---|---|
| timer back-off | `Tracker.current_interval`, `idle_steps` | 5 → 10 → 20 → 30 s (15 s in chats) while frames are identical and you give no input; any trigger or input resets it |
| in-memory capture | `macos.grab`, `Frame` | no subprocesses, no JPEG for frames that aren't kept |
| fast OCR on continuation | `Tracker.read`, `resolver.resolve_frame(fast=…)` | ~10× less OCR CPU; thin results are redone accurately |
| thumbnails + retention | `Tracker.keep_thumbnail`, `retention.py` | 480 px / ~15 KB per thing, deleted after `SCREENSHOT_DAYS` unless pinned or an open note |
| batched writes | `Tracker.save(force=…)`, `maintain()` | memory files at most every `SAVE_EVERY` s; notes, ticks and shutdown write at once |
| self-measurement | `Tracker.cost_report`, `sample_resources` | CPU, memory, OCR counts and timings in `status` and the session summary; warns on high memory or sustained CPU |

All the knobs are in `config.py`.

## Notes as pending edits

- `notes.record` stores a note on the item its capture resolved to. Until then it's
  `pending` in the session file.
- A note never changes after it's written. Done-state lives in `item.notes_done`
  (`{note id: when}`), because provenance (`input_store`) matches notes by value.
  Note ids are `sha1(at|text)`, so old notes get them without migration.
- Reminders fire only when you **come back**. When a timeline stretch starts, the
  tracker checks the previous stretch. If it was this same thing, split only by the note
  window, the card, a pause or a lock, that's not a return. `notes.due_for_resurfacing`
  then needs open notes, and either no reminder within `RESURFACE_COOLDOWN` or a note
  added since the last reminder.
- `notes.project_of` derives the project. `notes.pending_view` builds the grouped view
  for both the CLI and `pending.json`.
- `lmemm.py notes done` goes through the running tracker's control file (applied under
  its lock). If the tracker is stopped, it writes `.index.json` directly and records a
  provenance contribution so session deletion stays consistent.

## The pill (widget.py)

- It runs inside the tracker process, on the main thread. Its panels are borderless and
  non-activating, at status-window level, on all Spaces, so clicking them never takes
  focus from the app you're in.
- `Tracker.widget_card()` hands it `notes.card(items, current item)` for the last thing
  you were on. `notes.card_view` / `notes.pill_summary` (plain data, unit-tested in
  `tests/test_card_view.py`) turn that into what is drawn: the count on the pill, and the card's
  "here" and "project" views. `widget.py` only draws it. Ticking a note calls `Tracker.widget_tick`,
  which runs `notes.set_done` and saves under the lock; the note stays struck through for under
  a second before leaving the card.
- `Tracker.widget_heard` feeds the pill the words from the open ⌃⌥N note window, and the
  "Add a note…" row sets `note_request`, so the pill and the hotkey share one path.
- `tick()` refreshes the pill's count about once a second, and animates it on every tick. While the card is open, it skips
  capture, so the card never gets OCR'd into memory.

## Context export (context.py)

`memory.json` and the session files are the tracker's own working data (every visit,
trigger, activity second — it needs all of it to decide identity and timing). `context.py`
builds a separate, much smaller document for anything that should only see *what you did
and why*:

- `distill(item)`: one thing → `{app, what, doing, when, notes, content, details}`, or
  `None` if it has neither a note nor kept content — nothing to tell an AI, so it's left
  out entirely. `when` collapses first/last/visits into one sentence.
- `_content(item)`: excerpts, deduplicated after stripping UI chrome (menu bars etc. that
  slipped past `memory_content`'s own filter) with `_clean`.
- `by_project` / `for_session` / `since`: group distilled things by `notes.project_of`,
  scoped to one session's timeline or a day range.
- `export()` / `lmemm.py context`: builds the document, writes it to
  `data/memory/context/<session or range>.json`, and returns it for printing.

No screenshots, item ids, triggers, per-visit timing or activity-category seconds appear
anywhere in the output.

## Talking to a running tracker

`.lmemm.pid` names the process. `lmemm.py pause|resume|notes done` write a PID-scoped
`.lmemm.control.json`, which the tracker applies on its next tick. `status` reads
`.lmemm.status.json`. `pin` and `note` use SIGUSR1 / SIGUSR2.

## The menu (lmemm.py)

`lmemm.py` with no arguments calls `interactive_menu()` instead of starting the tracker
(any explicit subcommand, including `start`, still goes straight there - this only
changes the bare-invocation default). `MENU` is one list of `MenuItem(key, label, hint,
hint_example, action)`; `action` is the same `cmd_*` function the direct command uses, so
there is exactly one implementation of each command's argument parsing, used by both the
CLI and the menu. Picking an item with a `hint_example` prompts once for an optional
argument string, `shlex.split` into the same `args` list `cmd_*` already expects.

The loop calls the action, catches `SystemExit` (several `cmd_*` functions call
`sys.exit` on bad input or "nothing yet") and `KeyboardInterrupt` so one mistake or an
empty state doesn't end the session, then redraws the menu. `read`/`write` are
parameters (default `input`/`print`) so tests drive it without a real terminal.

## Extending

| You want to… | Change |
|---|---|
| teach it a new app or site | a rule in `understand.py` (`describe`), plus a `KINDS` entry |
| give a thing a reliable identity | set `stable_identity` in its `understand` rule (see Google Docs) |
| change capture timing or skip-lists | `config.py` |
| group pending edits differently | `notes.project_of` |
| store something new per thing | set it in `tracker.update_item`; show it in `store.readable` |

## Testing

`./scripts/test.sh` runs the integrated capture and semantic suite: real Vision OCR on generated
images, identity, content, migration, notes/resurfacing/project view, the CLI, input
events with fake native data, retention and deletion recovery. No test uses the real
microphone, input tap or your data.

## Experimental semantic boundary

The feature branch adds a separate `semantic_memory/` package and SQLite database.
Its controlled flow is source envelope + access policy → source-owned text → bounded
episode → validated local quote selection → claims/relationships → scoped project
context and citations. The legacy tracker does not feed it. Session-deletion recovery
cleans a semantic database when one exists; there is no implicit legacy import.

The local runtime is an explicitly started isolated Ollama process on a fixed loopback
endpoint. Models select numbered source quotes/categories; host code maps evidence
IDs and exact project-path references. Inference cannot execute tools or grant access.
Automatic runtime startup and MCP remain unimplemented. The separate VS Code live adapter is described below. See the
[semantic handover](docs/semantic-project-memory-status.md) for contracts and limits
and the [benchmark index](docs/benchmarks/project-memory/README.md) for measured status.

## Live semantic pilot

`extensions/lmemm-source` reads bounded visible editor ranges using VS Code APIs.
Connect opens a transient tab with a random client challenge. `live_focus.py`
checks secure-input state, focused AX element/owner/window and a unique CG geometry
match; registration also requires the native title challenge. A client cannot
silently move its binding to another native window.

`live_bridge.py` validates private files, token/session/revision, sequence/freshness,
canonical approved roots/files and exclusions. No document contents are read by
Python. Heartbeats are metadata only; unchanged text is deduplicated. Missing/stale
clients expire. Denied focus, disconnect, pause and system lifecycle boundaries
advance the persisted cancellation barrier before cancelling the local extractor.

`live_pilot.py` owns intake, SQLite episodes, scheduled retention and one background
worker. Conservative SQLite page caps reserve journal/reclaim space within 250MiB;
there are at most eight queued episodes/128KiB queued text. `live_project_memory.py`
provides standalone controls, read-only dated project recall and offline deletion.
Quotes are observed content, not completed-work claims. Experimental model output
retains source attribution and tentative status. Actual native extension-host
acceptance and RAM/power are pending; see the [runbook](docs/live-semantic-pilot.md).

# LMemM — Large Memory Model (prototype)

A memory layer for your Mac. LMemM watches what you work on and keeps **one memory
entry per thing** — an email draft, a doc, a chat, a code file, a page — with what
you did there (typing, reading, receiving, focus) and what it was about. Press
**⌃⌥N** anywhere to say a note about the thing in front of you; it becomes a
**pending edit** that comes back when you reopen that thing.

Everything runs on your Mac: capture, OCR (Apple Vision), activity rules and speech
recognition. Normal tracking uses no model or server. The experimental semantic
benchmark is separate and remains disabled in capture.

## Setup (once)

macOS only, with **Python 3.10 or newer** (the pinned MCP dependency requires it).
Check `python3 --version` before creating the environment; an older system Python
will not install this project's full dependencies. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Project Memory's semantic search also requires a separately installed **Ollama**
runtime and its explicitly downloaded local embedding model; pip does not install
them. Standard **Visual Studio Code** plus LMemM's source extension is required for
visible editor capture. See the [Project Memory setup runbook](docs/hybrid-memory-runbook.md)
for enrollment, model installation and connecting the extension. Keyword recall
of retained notes works without the embedding model.

Then give the app you launch it from (Terminal, iTerm, VS Code…) these permissions in
**System Settings → Privacy & Security**, and quit/reopen that app:

| Permission | Needed for |
|---|---|
| **Screen Recording** | screenshots and window titles (required) |
| **Automation** | reading the browser tab's URL (macOS asks the first time) |
| **Microphone**, **Speech Recognition** | voice notes; asked for "LMemM Listen" the first time you press ⌃⌥N |
| **Accessibility** | knowing exactly which page or chat you are on, instantly (the pill is slower without it); also the opt-in input timeline |
| **Input Monitoring**, **Accessibility** | the event trail (clicks, typing bursts, shortcuts, window and chat names); also the opt-in input timeline |

The speech helper is built with clang (Xcode Command Line Tools) on first run.

### First run

The first time you start LMemM a small window walks you through it: welcome, your first name
(and, if you like, what you mostly work on), the three macOS switches with their live status,
a try-it for **⌃⌥N**, and done. Every permission can be skipped and fixed later; only the name is
required. Screen Recording can be limited to **only some apps**; LMemM is then off in every other
app. Your answers are saved as the `user` block of `data/memory/memory.json`, on this Mac only.

- Closing the window part-way is fine: the next start resumes on the same screen.
- Turning Screen Recording on needs LMemM to restart; the window offers a Restart button and
  resumes where it was.
- `python3 lmemm.py setup --again` shows it again; `--no-setup` skips it.
- `python3 lmemm.py delete-all --dry-run` / `--confirm` removes everything LMemM has kept.

Rules, states and what is tested: `docs/specs/2026-10-09-first-run-onboarding.md`.

### As an app (LMemM.app)

```bash
./build_app.sh
open dist/LMemM.app
```

Builds a menu-bar app (no Dock icon) with py2app in alias mode: it runs this repository's code and keeps
`data/` here, so `git pull` updates it. Grant Screen Recording and the other permissions to **LMemM**
itself. `./build_app.sh --standalone` bundles the code (data then lives inside the bundle; not for daily use yet).

## Use

```bash
python3 lmemm.py
```

That's the one command. With nothing after it, LMemM opens a menu:

```
LMemM
─────
 1  Start watching               capture + remember what you do; Ctrl-C stops
 2  See what's remembered        memory.json: what you did, notes, activity
 3  Pending edits                your ⌃⌥N notes, grouped by project
 4  Export context for an AI     a clean summary - just what you did and why
 5  Status                       is a session running, paused, what it's costing
 6  Pause / resume               toggle a session that's already running
 7  Dictate a note now           same as pressing ⌃⌥N
 8  Force-save the current screen same as the pin hotkey
 9  Delete a session's data      review with --dry-run first, then --confirm
 0  Quit
```

Pick a number. Anything with options asks for them on the next line, in the same
form as the command-line flags below — press Enter for the plain version. An
action runs and drops you back at the menu, so you can start watching, stop it with
Ctrl-C, then check `memory` or `notes` without leaving. 0, Ctrl-C or Ctrl-D to quit.

Every item is also a direct command, for scripts and muscle memory:

| Command | What it does |
|---|---|
| `lmemm.py [start] [--every N] [--no-widget]` | watch and remember; N s is the fastest timer on one window (default 5; it slows itself down when nothing changes) |
| `lmemm.py memory [N] [--content] [--events]` | latest N things; `--content` adds kept excerpts, `--events` the input timeline |
| `lmemm.py notes [--all] [PROJECT]` | open notes grouped project → thing; `--all` includes done ones |
| `lmemm.py notes done ID…` / `notes reopen ID…` | mark notes done / open again |
| `lmemm.py suggest-for PROJECT [ID…]` | offer things for an existing project in the window (Add / Not here) |
| `lmemm.py suggest [NAME] [ID…]` | offer things as one project on the pill (stands in for the model until it exists) |
| `lmemm.py context [SESSION] [--days N]` | a clean, de-noised export for an AI (below) |
| `lmemm.py setup [--again] [--no-start]` | first-run setup, then LMemM starts and the pill appears (`--no-start` stops after setup; also runs once before `start`) |
| `lmemm.py delete-all --dry-run` / `--confirm` | remove everything LMemM has kept on this Mac |
| `lmemm.py status` / `pause` / `resume` | the running tracker; pause stops all capture |
| `lmemm.py pin` | force-save the current screen |
| `lmemm.py note` | open the note window from a terminal (same as ⌃⌥N) |
| `lmemm.py delete-session ID --dry-run` / `--confirm ID` | remove one session's evidence |
| `./run.sh start` / `stop` / `log` / `status` | the same tracker, in the background |

While it runs you see one line per change in what you're doing:

```
LMemM is watching  ·  captures on app/tab switches, else every 5s  ·  pauses after 60s idle
⌃⌥N dictate a note  ·  Ctrl-C stop

10:02:13  Google Docs     Working on "Q3 plan"
10:02:41  Google Docs     note saved
10:03:05  Gmail           Looking through the inbox (1,792 unread)
10:09:12  Google Docs     Working on "Q3 plan"  (back to it)
10:09:12                    📝 1 pending edit: add a pricing table
```

## Notes and pending edits (⌃⌥N)

Press **⌃⌥N** in any app. A small card opens above the pill for what's in front ("Note
for Pricing › Q3 plan") and it is **already listening**: talk, and your words appear as
you speak. Click in to type or fix a word. **Return** saves, **Esc** cancels; on save the
pill says "Saved to Q3 plan" and the count rises. The app you were in stays in front.

- The note goes on the memory entry for that thing, kept apart from what LMemM
  observed. If OCR hasn't caught up yet, it waits for that screen; it never lands on
  the previous thing.
- Every note is a **pending edit** until you mark it done.
- **Resurfacing:** the next time you come back to that thing (after being somewhere
  else), LMemM shows a macOS notification and a terminal line. You never get one right
  after dictating. After that it reminds you at most every 10 minutes per thing, unless
  you've added a note since the last reminder.
- **Project view:** `lmemm.py notes` lists open edits by project: a code file's project
  folder, a document, `Email`, `Chats`, `AI chats`, a website, or the app. The same view
  is written to `data/memory/pending.json`.

```
2 pending edits

LMemM  (1 open)
  VS Code         tracker.py
      n-8c21d0  • split capture out of tracker   (2026-10-06 10:01)

Q3 plan  (1 open)
  Google Docs     Q3 plan
      n-3f9a1c  • add a pricing table   (2026-10-06 10:02)
```

## The pill

A thin pill sits at the bottom of your screen, like Wispr Flow. It never takes focus,
follows light/dark, and shows over every app, Space and full-screen window.

- Nothing waiting: it is nearly invisible.
- Notes waiting on the thing you're on: it shows a number. Hover to see the first one.
- While you dictate a note (⌃⌥N): the note card shows the waveform and the words; the pill stays quiet.
- Right after a note is saved: a check and "Saved to <thing>".
- Something wrong, as a mark on the pill (hover for its name; click for the card): a slashed eye,
  **Screen access is off** (red; the card opens System Settings); a lock, **Private window**
  (nothing is remembered from there); a slashed microphone, **Mic is off** (red; shown when
  there are no notes to count).
- **Pause** is on the card: a plain "Pause LMemM" row at the bottom, then 1 hour, Until tomorrow
  (8:00 AM) or Until I resume, each showing when it ends. While paused the pill shows one pause
  mark and nothing else; its card says "Paused", when it ends, and has **Resume**. When you step
  away (idle) the same mark shows, with no Resume because it ends by itself. ⌃⌥N while paused opens
  that card. `lmemm.py pause` / `resume` show the same.
- Nothing to show, on hover or with the card open: a pencil, **No notes yet**; a check,
  **All caught up**; a folder, **Start a project**, when you have none yet.

Click it for a small card about what you're on. It lists that thing's open notes: click
one to tick it off: it stays crossed out for a moment (click again to take it back), then folds
away, and the pill's count drops at once. Finishing the last one shows **All caught up** once;
that card still has "Add a note…". A **Done** row lists this thing's finished notes by day (click a
ring to reopen one), and **Undo** appears for 6 s after ticking. "Add a note…" opens the dictation window. If the
project has notes on other things, "N more in <project>" shows them grouped by thing,
with finished notes behind "Done". Click the pill again or anywhere else to close it.
Start with `--no-widget` to hide it.

## What it remembers

**When it captures:** on app, tab and window switches (after ~1 s to settle), on a timer
while you stay on one window, and on input bursts if the input timeline is on. The timer
runs every 5 s while you're active and **backs off to 10, 20, then 30 s** (15 s in chats)
while nothing changes and you give no input; any switch, key, scroll or click brings it
back to 5 s. It pauses when you're idle 60 s, locked, asleep or paused, and never captures
password managers, private/incognito windows, or banking, payment and login pages
(`config.py`).

**What a capture costs:** the screen is grabbed inside the program (~25 ms) and lives in
memory. A screen with no pixel change skips OCR. A changed screen of the window you were
already on gets fast OCR (~10× cheaper); the first look at a window, notes and pins get
accurate OCR, and so does a fast pass that finds suspiciously little text. Nothing is
written to disk for a frame that isn't kept. A kept frame becomes the thing's
**thumbnail** (480 px, ~15 KB), and thumbnails are deleted after 7 days unless the thing
is pinned or has an open note. Memory files are written at most every 5 s while running.
`lmemm.py status` shows what it's costing: CPU, memory, captures, OCR counts and timings.

**What it works out per screen:**

- **What you're doing** — per-app rules: Gmail (inbox, reading, composing, writing),
  chats, AI assistants, editors, docs, search, YouTube, any page or app.
- **Which thing it is** — a Google Doc by its id; otherwise by place plus what you
  typed there. Two chats in one WhatsApp tab are two things; a second email in the same
  compose slot is a new thing; a draft that gets saved or renamed stays the same thing.
- **What happened since its last screen** — from the changed pixels and three system
  counters (seconds since the last key press, scroll and click, never which key):

| Activity | Means |
|---|---|
| **typing** | you pressed keys and text was edited in place |
| **reading** | you scrolled, or nothing changed while you stayed |
| **receiving** | new text appeared with no input from you |
| **focus** | you clicked or pointed and that area changed |

**Where it goes** (`data/`, never committed):

| File | Contents |
|---|---|
| `memory/memory.json` | **the one memory file** - every thing, both readable (what, doing, project, notes, content, activity, time, screenshot) and the full internal record the tracker resumes from. Back this one up. |
| `memory/sessions/<id>.json` | one session's timeline: when you were on what, and what was resurfaced |
| `memory/context/<id>.json` | the clean AI-facing export for one session or date range (below), only written when you ask for one |
| `memory/inputs/`, `memory/contributions/` | input timeline and per-session evidence - only appear if you turn on the opt-in input timeline (see below) |
| `<ts>.jpg` / `<ts>.json` | the one thumbnail kept per thing, and its capture metadata (deleted after 7 days; see above) |

Pending edits (open notes by project) aren't a file - `notes` command and the pill
compute that view live from `memory.json`. `.index.json` and `pending.json` from
earlier versions are gone: the first run after upgrading folds `.index.json` into
`memory.json` and removes both.

One entry looks like:

```json
{
 "id": "document-1a2b3c4d", "app": "Google Docs", "project": "Q3 plan",
 "what": "Q3 plan", "doing": "Working on \"Q3 plan\"",
 "your_notes": [{"id": "n-3f9a1c", "at": "2026-10-06 10:02", "text": "add a pricing table", "status": "open"}],
 "mostly": "typing",
 "activity": {"typing": {"time": "2m 10s", "text": ["Pricing", "Three tiers: …"]}, "reading": {"time": "40s"}},
 "time": {"total": "2m 50s", "visits": 3, "first": "2026-10-06 10:01", "last": "2026-10-06 10:09"},
 "screenshot": "20261006-100912.jpg"
}
```

## Context export: handing this to an AI

`memory.json` and the session files are LMemM's own working data — every visit,
trigger and activity second, kept because the tracker needs them. That's the wrong
thing to hand an AI. `lmemm.py context` distills it down to what you did and why:

```bash
python3 lmemm.py context              # the latest session
python3 lmemm.py context SESSION_ID   # one session by id
python3 lmemm.py context --days 2     # everything touched in the last 2 days
```

Grouped by project, newest first. Each thing has only its app, title, a one-line
status, **when** as a plain span ("6 Oct, 23:49–23:51 (7 visits)") instead of a
timeline, your notes with open/done, and the real content it saw — UI chrome like
menu bars stripped out, deduplicated. A thing with no note and no real content isn't
included at all:

```json
{"project": "Q3 plan", "things": [
  {"app": "Google Docs", "what": "Q3 plan", "doing": "Working on \"Q3 plan\"",
   "when": "6 Oct, 23:49–23:51 (7 visits)",
   "notes": [{"text": "add a pricing table", "status": "open"}],
   "content": ["Pricing section goes here, three tiers ..."]}
]}
```

No screenshots, ids, triggers, per-visit timing or activity seconds. Saved to
`data/memory/context/<session or range>.json`, and also printed to stdout, so you can
pipe it straight to another tool.

## Event trail: knowing what you are doing from app events

While LMemM runs it also keeps an **event trail**: app switches, window and tab changes, which chat or
page you are on, typing bursts (counts, never the keys), clicks, shortcuts and the text apps expose through
accessibility. No screenshots needed; the screenshot read is only a fallback for apps that expose almost nothing.
Raw events stay on this Mac for 48 hours, then are deleted. `--no-trail` turns it off.

```bash
python3 lmemm.py trail show 40        # the last events, readable
python3 lmemm.py trail places --hours 2   # where you were, one line per visit
python3 lmemm.py trail status         # permissions, what a read costs
python3 lmemm.py trail pause | resume
python3 lmemm.py trail forget --last 10 | --app NAME | --all
python3 lmemm.py trail probe          # save an app's accessibility tree, to teach LMemM a new app
```
How chats are told apart, what is and isn't recorded: [docs/specs/2026-10-09-event-trail.md](docs/specs/2026-10-09-event-trail.md).

## Input timeline (opt-in, VS Code only)

```bash
python3 lmemm.py --input-events --input-app com.microsoft.VSCode
```

Records timestamped keyboard **bursts and counts** (never characters), coarse cursor
regions, clicks, scrolls and Ctrl/Cmd+Tab steps, linked to screenshots and memory
entries. It is kept for at most 24 h. Details, the privacy model, and how session
deletion works are in [docs/input-timeline.md](docs/input-timeline.md).

## Privacy, briefly

- Nothing leaves the Mac. Files are plaintext JSON/JPEG under `data/`, which git ignores.
- Captures are whole displays, kept in memory and OCR'd. Only a small thumbnail of the
  frame that became a thing's screenshot is written to disk, and it expires after 7 days
  unless the thing is pinned or has an open note. The skip-lists use foreground checks,
  so other windows on screen can still be captured. (The opt-in input timeline keeps its
  own full-size frames for at most 24 h.)
- Voice: on-device recognition only; the helper runs only while the note window is
  open and keeps no audio.

## Layout

```
lmemm.py        entry point (the menu and every command)
run.sh          run it detached in the background
src/            the Python modules (see docs/architecture.md)
native/listen/  Objective-C speech helper, built on first use
tests/          unit tests
scripts/        dev helpers (test.sh)
docs/           architecture, specs, plans, history
```

## Develop

```bash
./scripts/test.sh      # includes native OCR and semantic-memory tests
```

To score how well things are grouped into projects (step 0 of automatic projects), run
`python3 scripts/eval_project_grouping.py`. It compares groupings against a labelled day
(an invented one by default, `tests/fixtures/project_grouping/sample_day.json`) by pairwise
precision and recall. Pass `--day` for a day you labelled yourself; keep real days out of the repo.
`python3 scripts/reason_projects.py --model qwen2.5:7b` lets a local Ollama model group the same
day sitting by sitting (`src/project_reasoner.py`) and scores it the same way. Prototype only: it
never touches your memory or projects.

[docs/architecture.md](docs/architecture.md) covers how the modules fit and where to extend it.
[docs/](docs/) has specs, plans and the development history.

## Project Memory MVP

The implementation is now on `main`. Each project has **Overview** and **Memory**
tabs. Enable Memory for one workspace to retain new approved visible VS Code
excerpts and intentional project notes. Search combines keyword matching with
local MiniLM embeddings; decisions, reasons and tasks come from explicit notes
and link to exact retained sources. Inspect a source to distinguish its snapshot
from today's file. No repository crawl or historical import is performed.

The supervised private service supports pause/resume, deletion, scoped revocable
agent access and an official SDK read-only MCP adapter. Local model installation
and disclosure to Codex require separate explicit setup. Previewing context does
not send it to an agent.

Start from this checkout with `.venv/bin/python lmemm.py start`, then open a
project → **Memory** → **Enable Memory**. Follow the
[setup and testing runbook](docs/hybrid-memory-runbook.md).

Independent review findings were addressed with regression tests. Frozen synthetic
held-out recall was 20/20 at five results; the 10k-chunk synthetic warm-search
probe measured 0.251 seconds p95. These are limited measurements, not launch
acceptance. Original shutdown/sleep/lock testing, the paired 30-minute native
resource workload, real extension enrollment and three external testers remain
pending. See [current context](docs/current-context.md) and
[acceptance evidence](docs/benchmarks/project-memory/hybrid-mvp/acceptance.md).

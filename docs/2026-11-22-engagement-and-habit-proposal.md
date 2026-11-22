# Engagement and habit proposal for Trace

**Date:** 22 November 2026 · **Issue:** #75

Status: proposed scope, not an approved implementation plan. No feature work is authorized by this
document alone. Each P1 item still needs its own spec.

Trace is the product name for this repository, which the code and older docs call LMemM.

The question is how Trace becomes something people return to every day because it is useful, not
because it pesters them. Trace's advantage over notes apps, task managers and second brains is that
it already watches the work, quietly and on the Mac. It keeps one memory entry per thing and brings
notes back when you return to that thing. Over weeks, it can see how a person's work and interests
change. Every feature below is built on that advantage. None of them asks the user to start
organising their life inside Trace.

## Principles

Every feature is checked against these rules.

- **Almost invisible.** It appears where the user already is: the pill, the project page, the
  Resume tab, or the ⌃⌥N card. It is triggered when the user *comes back* to something, never on a
  timer. New features add no macOS notifications. The existing note resurfacing notification stays
  as it is. At most one unprompted thing shows per return, and nothing shows while the user is
  dictating.
- **On the Mac.** Capture, storage and every derived digest stay on this Mac. Nothing is synced or
  sent anywhere.
- **No model needed.** Normal tracking uses no model. Where a feature can use one, it is optional,
  runs through the user's own local Ollama, and its output is labelled *generated*, never presented
  as evidence.
- **Evidence or nothing.** Every claim about past work opens the retained evidence behind it (the
  Launch 1 rule). When the evidence is gone, the claim is gone too.
- **The user's boundaries hold.** Pause, private and incognito windows, the skip-lists, "only these
  apps" (R11 in `src/rules.py`), retention limits and deletion all apply. Anything derived from
  deleted data is deleted with it.
- **The work is the reward.** No points, streaks, badges or scores. The reward is getting back into
  the work faster, or finding something the user had forgotten they knew.
- **Refusals stick.** "Not now", "Let go" and "Not here" are permanent for the thing they answer, as
  with suggestions S2 and R14 (`src/suggestions.py`, `src/projects.py`).

## What Trace can already observe

| Capability | Owned by | Real limits today |
|---|---|---|
| One memory entry per thing: app, title, what you did (typing, reading, receiving, focus), visits, first and last seen, project | `src/tracker.py`, `src/store.py`, `memory.json` | Long-lived. Kept text is the latest 12 excerpts per thing (`src/memory_content.py`), so older wording on a thing is overwritten. |
| Session timelines: when you were on what, and what was resurfaced | `src/store.py`, `memory/sessions/` | Long-lived. Only one thing per stretch; no topic digest. |
| Notes from ⌃⌥N as pending edits, open and done, by project | `src/notes.py` | Long-lived. Each note is attached to one *thing*, never to a project or an app. |
| Resurfacing on return | `notes.due_for_resurfacing`, `src/tracker.py` | Only when coming back to a thing with open notes. At most once every 600 s per thing (`RESURFACE_COOLDOWN`). Uses a macOS notification and a terminal line. |
| Pill and card: count, peek of the first note, ticking off, pause | `src/widget.py` | Shows only the thing in front and its project. |
| Project page: "Pick up where you left off" (P2), Needs you (P5), search (P6) | `src/page_model.py` | Read-only views. Search matches names, titles and open notes, not kept text. |
| Project suggestions from recurrence (H2), refusals that stick (H5) | `src/project_finder.py`, `src/suggestions.py` | Runs while idle, at most every 30 min (`FIND_EVERY`), over a 14-day window, with at most 3 suggestions waiting. Every offer and answer goes to `grouping_log.jsonl`. |
| Moments: digest of terms and identifiers per stretch of a visit | `src/moments.py`, `src/moment_store.py` | Kept 48 h (`MOMENT_RETENTION_HOURS`). |
| Event trail: app, window, chat and page changes, typing bursts | `src/trail_store.py`, [event trail spec](specs/2026-10-09-event-trail.md) | Kept 48 h (`TRAIL_RETENTION_HOURS`). **Not yet fed into memory.** |
| Thumbnails | `src/retention.py` | Kept 7 days (`SCREENSHOT_DAYS`) unless pinned or holding an open note. |
| Window summaries by a local model | `src/screen_summary.py` | Optional, through Ollama, and shown on the thing page only. |
| Context export | `src/context.py` (`lmemm.py context`) | Command-line only. Distilled by project. |
| Project Memory: hybrid keyword and vector search, explicit decisions, reasons and tasks, Resume tab, Related sources, Copy context, explicit Chrome saves | `semantic_memory/`, `src/memory_pane.py`, [Launch 1 design](superpowers/specs/2026-10-20-launch-one-design.md) | **Semantic sources are limited to VS Code excerpts, notes and pasted excerpts, and explicit browser saves**, for enrolled projects only. Launch 1 is on `main`, but release acceptance is still pending (`docs/current-context.md`). |

Launch 1 risk applies to every feature below that builds on Resume, decisions, Related sources or
Copy context. If pilot acceptance changes those pieces, the features change with them.

## Features

### 1. Park and pick up

- **Goals:** 1 daily engagement, 2 habit loops, 4 emotional attachment
- **Description:** A deliberate "where I stopped and what's next" note for a project, written in a
  few seconds at the end of a sitting. The next time the user comes back to that project, Trace
  opens with it before anything else.
- **User Experience:** Discovered on the ⌃⌥N card as a quiet second action, "Park here". It is
  also offered once in the pill card the first time a user leaves a project with open notes late in
  the day. The user speaks one sentence: "Next: wire the pricing table into the export." On the
  first return to any thing in that project, the pill peek shows the parked line, and the project
  page shows it at the top of "Pick up where you left off" (P2). One tap marks it picked up.
  Empty state: no parked note means P2 behaves exactly as today. While paused, nothing is parked
  and nothing shows.
- **Core Value:** The hardest part of resuming is the first five minutes of "where was I?". A
  sentence from the user's past self removes most of it, in their own words.
- **Engagement Mechanism:** Trigger: returning to the project. Action: read one line. Reward:
  immediate momentum. Return: users who see their own note waiting start parking on purpose, and
  the habit forms around the work, not around Trace.
- **Builds on:** `src/notes.py` (a note with a `park` intent), `notes.due_for_resurfacing`,
  `page_model` P2, the pill peek in `src/widget.py`.
- **Needs:** A note kind that targets a project rather than one thing, and a "first return to this
  project since parking" rule. Nothing new is captured.
- **Privacy:** It is a note the user dictated, held under the same rules as any note. Pause blocks
  ⌃⌥N as it does today. Deleting the project, the session or everything removes it.
- **Implementation Complexity:** Low
- **Priority:** P1, because it is the cheapest way to make the first moment of every day useful.

### 2. Return briefing

- **Goals:** 1 daily engagement, 3 unexpected value
- **Description:** When the user comes back to a thing after several days away, the pill shows
  "Last time here": the date, how long they spent, the open note if there is one, and the last kept
  excerpt even when there is no note.
- **User Experience:** It is not announced. The pill shows a small "last time" mark instead of
  being nearly invisible, and hovering opens the peek. "Open" goes to the thing page. "Not for
  this" stops briefings on that thing for good. It triggers only after a gap of at least
  `BRIEFING_GAP_DAYS` (a new setting, say 3), once per return. Empty state: a thing seen for the
  first time, or with no kept text, shows nothing. Unwatched apps and private windows have no entry,
  so they never brief.
- **Core Value:** It replaces "what was this doc again?" with a two-second glance, for things the
  user did not think to note.
- **Engagement Mechanism:** Trigger: returning after a gap. Reward: remembered context the user
  didn't ask for. Return: the more often Trace is right about a return, the more the user trusts it
  to hold the things they never wrote down.
- **Builds on:** item `time` and `last_seen`, the excerpts in `src/memory_content.py`, the pill
  peek in `src/widget.py`, and the cooldown logic in `notes.due_for_resurfacing`.
- **Needs:** A gap rule, a peek state in the pill, and a refusal flag per thing. Nothing new is
  captured.
- **Privacy:** It shows only what is already on the thing page. Excerpts that the skip-lists
  dropped were never kept. It respects pause (no capture means no "return"). Deletion removes the
  entry, so it removes the briefing too.
- **Implementation Complexity:** Low
- **Priority:** P1, because it gives value without the user doing anything, using data Trace
  already keeps.

### 3. Loose ends

- **Goals:** 2 habit loops, 3 unexpected value, 6 retention
- **Description:** A calm review, inside the window, of what has quietly gone stale. It lists open
  notes older than two weeks and things the user kept coming back to (many visits over several
  days) but never noted or filed. Each row has Keep, Done or Let go. Let go is permanent.
- **User Experience:** A "Loose ends" row on Home, under Needs you (P5), showing a count only when
  there is something to review. It is never pushed to the pill. A review takes under a minute, and
  each answer says what it did and offers Undo (S4). Empty state: "Nothing has gone stale", with no
  count shown.
- **Core Value:** Unfinished intentions stop nagging in the background. The user either finishes
  them or decides to let them go, and both feel like progress.
- **Engagement Mechanism:** Trigger: opening the window, which the user already does. Reward: a
  shorter, more honest list. Return: the list stays trustworthy, so Needs you stays worth opening.
- **Builds on:** `notes.open_notes`, `page_model.needs_you` (P5), item `time.visits`, `first`
  and `last`, and the answer pattern in `src/suggestions.py`.
- **Needs:** A "let go" note status that drops out of every view but is kept for Undo, and a rule
  for "recurring but un-noted". Nothing new is captured.
- **Privacy:** Built only from memory entries and notes. Excluded apps and windows never appear.
  Let go hides a note but does not delete it. Deletion commands still remove it for real.
- **Implementation Complexity:** Low
- **Priority:** P1, because it keeps the core pending-edit loop from rotting, and that loop is what
  everything else depends on.

### 4. Context-bound reminders

- **Goals:** 1 daily engagement, 2 habit loops
- **Description:** The product idea's Natural Reminders, bound to a context instead of a clock:
  "Remind me when I'm next in Kestrel", "next time I open Figma", "next time I'm in the chat with
  Rahul".
- **User Experience:** Said through ⌃⌥N in plain words. The card shows how it understood the phrase
  ("When you're next in: Kestrel") before saving, and the user can change it. The reminder appears
  on the pill peek the first time the context matches, then behaves like any open note. Empty state:
  if the phrase names nothing Trace knows, the card says so and saves an ordinary note on the
  current thing instead.
- **Core Value:** Most reminders are really about a situation, not a time ([product
  idea](product-idea.md), "Remind me when I start working on this project"). Firing at the right
  moment is what makes a reminder feel like memory instead of an alarm.
- **Engagement Mechanism:** Trigger: entering the context. Reward: a thought delivered exactly
  when it is useful. Return: users start "posting" thoughts forward to places they know they'll go.
- **Builds on:** `src/notes.py` targets, project names in `src/projects.py`, app identity in
  `src/apps.py`, chat identity from the tracker, and resurfacing on return.
- **Needs:** Notes targeted at a project, an app or a chat; a deterministic phrase-to-target
  matcher (no model); and resurfacing on entering a context, with the same 600 s cooldown.
- **Privacy:** A reminder cannot target an unwatched app (R11). The card says so instead. It never
  fires in a private window or while paused. Deleting the target project leaves the reminder on
  "Unfiled" rather than silently losing it.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it is high value, but it changes what a note can be attached to and
  needs its own spec.

### 5. Related from before

- **Goals:** 3 unexpected value, 5 exploration
- **Description:** When the user saves a note, an excerpt or a browser selection to a Project
  Memory project, Trace shows one older source from the same project that hybrid search ranks as
  related. It is labelled "related", never as a dependency or a contradiction.
- **User Experience:** It appears in the save confirmation ("Saved to Kestrel · Related: your 3 Oct
  note on pgvector limits") and in the Resume tab's existing Related sources. Two answers:
  "Open", or "Not related", which is permanent for that pair. Empty state: no match above the
  threshold shows nothing, not a weak guess.
- **Core Value:** The user meets their own earlier thinking at the moment they are adding to it.
- **Engagement Mechanism:** Trigger: an explicit save. Reward: a connection the user didn't
  remember. Return: saving becomes more rewarding than not saving.
- **Builds on:** hybrid search (`semantic_memory/search.py`), Related sources and Resume
  (`semantic_memory/resume.py`), and explicit saves (`semantic_memory/intentional_sources.py`) from
  [Launch 1](superpowers/specs/2026-10-20-launch-one-design.md). It extends Related sources; it
  doesn't replace it.
- **Needs:** A post-save query with a threshold and a per-pair refusal. It works only for enrolled
  projects and the current semantic sources. Launch 1 release acceptance is a risk.
- **Privacy:** Project-scoped: never across projects, following Launch 1 isolation. Paused Project
  Memory means no saves and no matches. Deleted sources leave search and stop matching.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it depends on Launch 1 pieces and the pilot proving people save.

### 6. Circling back

- **Goals:** 3 unexpected value, 5 exploration
- **Description:** The project finder's recurrence rule (H2) applied to topics instead of projects.
  It notices when the same subject comes up across separate sittings and apps, for example "You've
  come back to 'pgvector' in 4 sittings across VS Code, Chrome and Slack this week", and offers to
  make it a project or a note.
- **User Experience:** It appears as an ordinary suggestion in the window and on the pill, with the
  existing answers: Create, or Dismiss (permanent, S2). It counts toward the same limit of at most
  3 waiting suggestions, and is looked for at the same idle cadence (at most every 30 min). Empty
  state: nothing recurs, nothing is offered.
- **Core Value:** It names an interest before the user has noticed they have one, which is the
  moment a loose curiosity can become a project.
- **Engagement Mechanism:** Trigger: Trace notices a recurrence while the user is idle. Reward: a
  small "huh, that's true". Return: accepted topics become projects, and projects get Resume.
- **Builds on:** `src/project_finder.py` (H2, H4, H5), `src/suggestions.py`, `moments.digest_of`
  for terms, and `grouping_log.jsonl` for labels.
- **Needs:** **Longer retention of a small term digest.** Moments last 48 h, and the finder looks
  back 14 days, so a per-sitting term digest (terms only, no sentences) must be kept for 14 days. The
  retention sweep and deletion paths must cover it.
- **Privacy:** Digests come from text already kept, after the secret filter (M3). Pause, private
  windows and unwatched apps produce no moments, so they produce no digests. `delete-session` and
  `delete-all` must remove the digest store.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it is distinctive and uses the finder's machinery, but needs a new
  retained store.

### 7. Week in work

- **Goals:** 2 habit loops, 4 emotional attachment, 5 exploration
- **Description:** A weekly page built from memory without a model: projects touched and for how
  long, notes finished, decisions recorded, things started, and what drifted (open notes that saw no
  visit). A local model can optionally write two sentences of prose, labelled generated.
- **User Experience:** A "This week" entry on Home, filled in from Friday afternoon onwards. It is
  never pushed. Every number opens its evidence: a project, a thing, a note. Previous weeks stay
  browsable. Empty state: a first week, or a week mostly paused, says "Not much to look back on yet"
  and shows whatever exists.
- **Core Value:** A felt sense of progress for work that rarely produces a visible artefact, and a
  calm place to decide what next week is for.
- **Engagement Mechanism:** Trigger: the end of the week, but the user opens it. Reward: seeing the
  week's shape and finishing it deliberately. Return: Week in work leads to Park and pick up, which
  leads to Monday's first return.
- **Builds on:** session timelines (`memory/sessions/`, `src/store.py`), item time and visits,
  done notes (`notes.done_view`), Launch 1 decisions for enrolled projects, and
  `src/screen_summary.py` (an optional narrator).
- **Needs:** A weekly aggregation over sessions. Nothing new is captured. A model is optional.
  Decisions depend on Launch 1, whose acceptance is pending.
- **Privacy:** Only what is already kept. Paused time is shown as paused, not guessed. Deleted
  sessions leave the week, and the page recomputes rather than caching text. Model prose runs only
  in the user's local Ollama.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it is the strongest attachment feature that needs no new data, but it
  is a new page.

### 8. Decision revisit

- **Goals:** 2 habit loops, 6 retention
- **Description:** Decisions recorded in a project, with their reasons, come back when the user
  returns to that project after a long gap: "On 3 Oct you chose SQLite because it needs no server.
  Still true?" Answers: Keep, Revise, or Not now.
- **User Experience:** It is a row on the Resume tab, never on the pill. At most one decision
  shows per return, and only after a gap of 3 weeks or more since the decision was last seen. Revise
  opens a note prefilled with the decision so the user can record what changed. Not now is
  permanent for that decision until it is edited. Empty state: no decisions, or none old enough,
  means no row.
- **Core Value:** Decisions decay silently. Revisiting the *why* at the right moment prevents
  re-arguing settled questions and catches ones that should be reopened.
- **Engagement Mechanism:** Trigger: returning to the project. Reward: confidence, or a timely
  correction. Return: the decision log becomes something the user maintains because it pays off.
- **Builds on:** explicit decisions and reasons (`semantic_memory/relationships.py`), the Resume
  tab (`semantic_memory/resume.py`) from the [resume
  proposal](2026-10-20-project-resume-beta-proposal.md) and [Launch
  1](superpowers/specs/2026-10-20-launch-one-design.md).
- **Needs:** A "last reviewed" timestamp per decision and a gap rule. It depends on Launch 1
  decisions, whose release acceptance is pending.
- **Privacy:** Decisions come only from explicit notes, never inferred. Project Memory pause and
  deletion apply. A revised decision replaces its old claim, as Launch 1 already requires.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it is valuable once people record decisions, which the pilot must show
  first.

### 9. Ask your past self

- **Goals:** 3 unexpected value, 5 exploration, 6 retention
- **Description:** Ask in natural language: "What was that idea about pricing last week?", "When did
  I last talk to Rahul about the launch?" The answer is a list of cited things, notes and excerpts.
  A local model can optionally write a one-line answer, labelled generated.
- **User Experience:** Asked from the window's search field, or with ⌃⌥N by starting with "What" or
  "When". Results are evidence first, with any generated line underneath, marked as such. Empty
  state: "Nothing kept matches", with a hint about what Trace keeps (and that paused time, private
  windows and unwatched apps are not in it).
- **Core Value:** The product idea's Memory Search and "What was I doing?" Search is not recall,
  but recall needs a way to ask.
- **Engagement Mechanism:** Trigger: the user's own question. Reward: an answer with proof.
  Return: each good answer makes Trace the first place to look.
- **Builds on:** `page_model.search` (P6) for names, titles and notes; hybrid search in
  `semantic_memory/search.py` for enrolled projects; `src/screen_summary.py` for optional prose.
- **Needs:** **Semantic intake beyond current sources.** Today, only enrolled Project Memory sources
  are embedded. Answering across all things needs every item's kept excerpts indexed, plus
  date and person parsing, and an optional local model.
- **Privacy:** Indexes only what memory already keeps, on the Mac, and every deletion path must
  reach the index. Model use is optional, local through Ollama, and labelled generated.
- **Implementation Complexity:** High
- **Priority:** P2, because it has the highest long-term pull, but needs the broader index first.
  Ship a keyword version over kept excerpts sooner.

### 10. Brief my AI

- **Goals:** 1 daily engagement, 6 retention
- **Description:** One action copies a clean briefing of "this project" or "today" for any AI chat,
  so the user's assistant never starts from zero.
- **User Experience:** "Copy briefing" in the pill card when the user is in an AI chat (Trace
  already knows the "AI chats" place), and on every project page. It copies the `context.py`
  distillation, or Copy context for enrolled projects, and says "Copied: Kestrel, 6 things, 3 open
  notes". Empty state: "Nothing to brief yet", if nothing has a note or kept content.
- **Core Value:** It uses the habit heavy AI users already have, many times a day, and makes Trace
  the source of their context.
- **Engagement Mechanism:** Trigger: opening an AI chat. Reward: a better first answer. Return:
  every AI conversation is a reason to touch Trace.
- **Builds on:** `src/context.py` (`lmemm.py context`), Copy context in `src/memory_pane.py` from
  [Launch 1](superpowers/specs/2026-10-20-launch-one-design.md), and the AI-chat detection the
  tracker already does.
- **Needs:** A pill card action and a clipboard write. Copy context is Launch 1. Release acceptance
  is pending, so the `context.py` path is the fallback.
- **Privacy:** Copying is explicit and nothing is sent. The user pastes. The briefing excludes
  screenshots and IDs as `context.py` already does, and never includes unwatched apps or private
  windows.
- **Implementation Complexity:** Low
- **Priority:** P1, because it is almost free to build and attaches Trace to a daily habit that
  already exists.

### 11. What Trace has learned

- **Goals:** 4 emotional attachment, 6 retention
- **Description:** A visible, editable page of what Trace has learned about how the user works:
  project names it uses, suggestions accepted and declined, things kept out of projects ("Not
  here"), the apps it watches, and the places it never looks.
- **User Experience:** In Settings and linked from every suggestion ("Why this?"). Each line can be
  undone: un-decline a name, let a thing back in, watch another app. Empty state: in the first days,
  "Trace hasn't learned anything about you yet. Answers to suggestions show up here."
- **Core Value:** Trust. The user sees that Trace is learning from *them*, and that it can be
  corrected. That makes it feel personal rather than opaque.
- **Engagement Mechanism:** Trigger: curiosity, or a surprising suggestion. Reward: control and a
  sense of being understood. Return: rarely, but it is what makes leaving feel like a loss, because
  the learning stays on this Mac and is the user's.
- **Builds on:** `grouping_log.jsonl` (H6), declined names and `not_in` (R14) in
  `src/projects.py` and `src/notes.py`, and watched apps in `src/apps.py` (R11).
- **Needs:** A page that reads these existing records and offers their undo actions. Nothing new
  is captured.
- **Privacy:** It shows only the user's own answers and settings. Removing a line is a real change,
  and `delete-all` removes the log.
- **Implementation Complexity:** Medium
- **Priority:** P2, because it builds trust ahead of the more ambitious features.

### 12. Interest timeline

- **Goals:** 4 emotional attachment, 5 exploration
- **Description:** A month-by-month view of projects and recurring topics: when each first
  appeared, when it peaked, when it faded. "You started reading about local embeddings in
  September; it became Kestrel in October."
- **User Experience:** A "Timeline" page in the window, opened on purpose. Each band opens the
  projects and things behind it. A topic can be hidden from the timeline permanently. Empty state:
  under a month of history, "Your timeline fills in as you work", showing projects only.
- **Core Value:** People rarely see how their own interests evolve. Seeing it is both useful (what
  am I actually spending myself on?) and personal.
- **Engagement Mechanism:** Trigger: curiosity, often after Week in work. Reward: self-knowledge.
  Return: the timeline gets richer every month, so it is worth more the longer Trace is kept.
- **Builds on:** project and item first and last seen, session timelines, and the topic digest from
  Circling back.
- **Needs:** **Long-term retention of topic digests** (months, not 48 h or 14 days), clustering of
  terms into topics, and a new visual page. A model is optional, for topic names only, labelled
  generated.
- **Privacy:** Digests hold terms, not sentences, and pass the moments secret filter. The
  user can hide a topic, delete a month, or turn the timeline off, and `delete-all` removes it.
- **Implementation Complexity:** High
- **Priority:** P3, because it needs months of new retained data before it shows anything.

### 13. Echoes

- **Goals:** 3 unexpected value, 6 retention
- **Description:** When new work resembles an older project, Trace points back: "You did something
  like this in March (Atlas onboarding). Here's what you decided and what you left open."
- **User Experience:** It appears on the project page of the *new* project, as one row under "Pick
  up where you left off". It is shown once per project and only when similarity is strong. Answers:
  Open, or Not similar (permanent). Never on the pill. Empty state: no strong match, no row.
- **Core Value:** It turns old projects into experience. Mistakes are not repeated, and old
  decisions are reused.
- **Engagement Mechanism:** Trigger: starting something new. Reward: a head start from the user's
  own past. Return: the longer the history, the more often Echoes fires usefully, so leaving Trace
  costs more.
- **Builds on:** projects and their things, Launch 1 decisions and Related sources, hybrid search,
  and the per-project digest from Circling back.
- **Needs:** **Cross-project similarity**, which Launch 1 isolation forbids by default, so it needs
  an explicit opt-in and its own design. Also long-lived project digests and semantic intake beyond
  current sources.
- **Privacy:** Cross-project matching is a privacy change: off by default, opt-in per project, and
  never available to an AI grant. Deleted projects stop echoing.
- **Implementation Complexity:** High
- **Priority:** P3, because it is ambitious and the payoff comes after a year of use.

### 14. Anniversaries

- **Goals:** 4 emotional attachment, 5 exploration
- **Description:** A quiet, occasional look back: "A year ago today you started Kestrel", "Six
  months since you parked the essay draft."
- **User Experience:** A single line on Home, only when the window is opened, at most once a week.
  It is never on the pill and never a notification. It opens the project or thing as it was, with
  the notes and kept text that remain. "Not interested" turns anniversaries off. Empty state: no
  line at all, which is the norm until there is history old enough.
- **Core Value:** It gives a sense of continuity with past work, and sometimes a nudge to finish
  something that mattered.
- **Engagement Mechanism:** Trigger: a date, but shown only on a visit the user chose. Reward: a
  small, personal moment. Return: occasional, but emotionally sticky.
- **Builds on:** project creation and item `time.first` in `memory.json`, and notes with their
  dates. Both are long-lived.
- **Needs:** History that is actually old (Trace keeps it, but no user has a year yet), and a rule
  for which dates are worth mentioning: projects with real time spent, or notes parked for a long
  time.
- **Privacy:** Only from kept, long-lived records. Deleted things never come back as
  anniversaries. Thumbnails older than 7 days are gone, so it shows text, not pictures.
- **Implementation Complexity:** Medium
- **Priority:** P3, because it is cheap to build but useless until history is months old.

**Coverage of the six goals**

| Goal | Features |
|---|---|
| 1 Daily engagement | Park and pick up, Return briefing, Context-bound reminders, Brief my AI |
| 2 Habit loops | Park and pick up, Loose ends, Context-bound reminders, Week in work, Decision revisit |
| 3 Unexpected value | Return briefing, Loose ends, Related from before, Circling back, Ask your past self, Echoes |
| 4 Emotional attachment | Park and pick up, Week in work, What Trace has learned, Interest timeline, Anniversaries |
| 5 Exploration | Related from before, Circling back, Week in work, Ask your past self, Interest timeline, Anniversaries |
| 6 Retention | Loose ends, Decision revisit, Ask your past self, Brief my AI, What Trace has learned, Echoes |

Complexity: Low for 4 features, Medium for 7 and High for 3. Project Resume, the Resume tab and the
[ambient canvas](ambient-canvas.md) are existing work. The features above extend them rather than
re-proposing them.

## Considered and rejected

- **Streaks.** They reward opening the app, not getting value from it, and punish holidays. Trace
  should be worth the most after a week away, not the least.
- **Points, levels or badges.** They turn memory into a game with Trace as the scorekeeper. The
  only reward should come from the work itself.
- **Daily push digest.** A scheduled notification is exactly the spam the request rules out, and it
  arrives when the user is *not* in the work. Week in work is opened, not pushed.
- **Leaderboards or social sharing.** Everything stays on the Mac. Comparing memory with other
  people also invites performance instead of honesty.
- **An "engagement" or "productivity" score.** A single number about the user's work would be
  judgemental and wrong, and it would make Trace feel like surveillance.
- **Mood or wellbeing tracker.** Inferring feelings from screen text is intrusive and unreliable,
  and it is outside what a working-memory tool should claim to know.
- **Automatic "you were distracted" nudges.** Judging how time was spent breaks the
  almost-invisible principle, and users would pause Trace to avoid it.

## Ranked shortlist

1. **Park and pick up.** Lowest cost, and it makes the first return of every day useful. It also
   reinforces the ⌃⌥N habit that every other feature relies on.
2. **Return briefing.** Value with zero effort, from data Trace already keeps. It is the clearest
   demonstration of "it remembers for you".
3. **Brief my AI.** Almost free, and it hooks onto a habit users already repeat many times a day.
4. **Loose ends.** It keeps pending edits trustworthy. Without it, Needs you becomes a guilt list
   and people stop opening it.
5. **Week in work.** The strongest attachment feature that needs no new data. It also leads
   naturally into Park and pick up.

## Suggested next steps

- **Spec Park and pick up first.** It is the smallest change to the note model (a project target),
  and Context-bound reminders later reuses the same change.
- **Then Return briefing and Brief my AI**, which can share one pill card change.
- **Measure in the pilot** the signal already proposed for Launch 1: at least three of four testers
  return voluntarily on another day. Also count, locally, how often a parked note or briefing is
  opened rather than ignored. Pilots stay on the Mac, and no results are claimed here.
- **Wait on pending work:** Related from before and Decision revisit wait for Launch 1 release
  acceptance (`docs/current-context.md`). Circling back, Interest timeline and Echoes wait for a
  decision on retaining term digests longer than 48 h. Ask your past self waits for semantic intake
  beyond the current sources. Echoes also needs an explicit decision on cross-project matching.

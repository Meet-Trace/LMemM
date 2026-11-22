# Engagement and habit proposal for Trace (docs only)

**Date:** 22 November 2026 · **Issue:** #75 · **Branch:** `factory/issue-75`

Status: spec for a **writing-only** change. No code, config or UI changes.

## Goal

The requester wants a structured product-strategy list of features that would make Trace (the repo
and older docs call it LMemM) somewhere people return to every day because it is useful, not because
of streaks, points or notification spam. Each feature should build on what Trace already does that
other tools can't: continuous on-device observation, contextual memory, resurfacing at the moment
you come back, and seeing how a person's work and interests change over time. The list should
cover six goals: daily engagement, habit loops, unexpected value, emotional attachment, exploration
and retention. It should mix ideas that can ship on today's code with ambitious ones, and each
feature must carry the requested fields plus an honest complexity and a priority.

## Behaviour

The reader opens `docs/README.md`, follows a new row to the proposal and reads it from top to
bottom. Nothing in the product changes.

The proposal reads in this order:

1. **Header.** Status line: "proposed scope, not an approved implementation plan" (the same wording
   as `docs/2026-10-20-project-resume-beta-proposal.md`). One sentence says Trace is the product
   name for this repo (LMemM).
2. **Principles** (5–8 bullets) that every feature is checked against:
   - It stays almost invisible: it appears where the user already is (pill, project page,
     Resume, ⌃⌥N) and appears when the user *comes back* to something, not on a timer.
   - It runs on the Mac and nothing leaves it.
   - Normal tracking uses no model. Any model use goes through the user's own local Ollama, is
     optional, and its output is labelled as generated, never as evidence.
   - Every claim about past work opens its retained evidence (the Launch 1 rule).
   - It respects pause, private windows, "only these apps" (R11), retention and deletion.
   - Its rewards come from the work itself, never from points, streaks or badges.
   - Refusals stick (R14 / suggestions S2).
3. **What Trace can already observe.** A short table of the capabilities features build on, with
   the module or doc that owns each and its real limits. It must list these limits:
   - The event trail and moments are kept 48 h (`TRAIL_*`, `MOMENT_RETENTION_HOURS`).
   - Thumbnails are kept 7 days (`SCREENSHOT_DAYS`).
   - Memory items and notes in `memory.json` are long-lived.
   - Resurfacing happens on return, at most every 600 s per thing (`RESURFACE_COOLDOWN`).
   - The trail is not yet fed into memory.
   - Semantic sources are limited to VS Code, notes and explicit browser saves.
   - Release acceptance is still pending (`docs/current-context.md`).
4. **Features.** 12–16 entries. Each is an `###` section with a field list (format in Design).
5. **Considered and rejected.** Streaks, points or badges, daily push digests, leaderboards or
   social sharing, an "engagement score", a mood or wellbeing tracker. Give one line of reasoning
   for each.
6. **Ranked shortlist.** The top 5 by impact × feasibility, each with one line on why it ranks there.
7. **Suggested next steps.** Which shortlist item to spec first, what to measure in the pilot
   (the voluntary next-day return from the Launch 1 acceptance), and which items wait on pending
   work.

Empty or error states don't apply to the document itself. Each feature's **User Experience** field
must describe that feature's own empty state, for example what shows when there is no history yet
or the user is paused.

## Design

**Files**

| File | Change | Why there |
|---|---|---|
| `docs/2026-11-22-engagement-and-habit-proposal.md` | **new** | Dated proposals live at `docs/` top level (`2026-10-16-main-review-and-direction.md`, `2026-10-20-project-resume-beta-proposal.md`). |
| `docs/README.md` | add one table row: `[2026-11-22-engagement-and-habit-proposal.md](2026-11-22-engagement-and-habit-proposal.md)` with "proposed engagement and habit features for Trace, ranked" | The docs index is the only place proposals are listed. |

**Per-feature format** (every field present, in this order):

- **Feature Name** (the `###` heading)
- **Goals:** which of goals 1–6 it serves
- **Description:** what it does
- **User Experience:** how the user discovers it, uses it, and what its empty state looks like
- **Core Value:** why someone would want it
- **Engagement Mechanism:** the trigger, what the user does, what they get, and why they return
- **Builds on:** the existing modules or docs it uses, e.g. `notes.due_for_resurfacing`,
  `page_model` P2/P5, `suggestions.py`, `project_finder.py`, `context.py`, `moments.py`, the trail,
  Resume and Related sources from Launch 1, hybrid search, `screen_summary.py`
- **Needs:** the new capability it requires, or "nothing new"
- **Privacy:** how it handles pause, private windows, unwatched apps and deletion, and whether it
  needs a model, more retention or a new surface
- **Implementation Complexity:** Low, Medium or High
- **Priority:** P1 (now), P2 (next) or P3 (later), with one clause of reasoning

**Feature set.** The implementer must cover at least these features. They may rename, merge,
split or add, but must keep every field above and stay within 12–16 entries:

| # | Working name | Gist | Expected complexity |
|---|---|---|---|
| 1 | Park and pick up | An end-of-day "what's next" note per project (⌃⌥N). The next morning's first return opens with it on the pill peek and the project page (P2). | Low |
| 2 | Return briefing | When you come back to a thing after days away, the pill shows "last time here": the open note plus the last kept excerpt, even with no note. | Low–Medium |
| 3 | Loose ends | A gentle review, inside the window, of stale open notes and repeatedly visited things with no note: Keep / Done / Let go, permanently. | Low |
| 4 | Context-bound reminders | "Remind me when I'm next in project X / app Y". The product-idea Natural Reminders, bound to a project or app instead of one thing. | Low–Medium |
| 5 | Related from before | Saving a note or excerpt shows one older related source from hybrid search, labelled "related". | Medium |
| 6 | Circling back | The project finder's recurrence rule (H2) applied to topics: "You've returned to 'pgvector' in 4 sittings across 3 apps". Offered as a suggestion. | Medium (moments last 48 h, so it needs a longer-lived term digest) |
| 7 | Week in work | A weekly page built from items and notes without a model: projects touched, decisions, finished, drifted. Optional local prose, labelled generated. | Medium |
| 8 | Decision revisit | Recorded decisions and reasons come back on return to their project after a gap: "Still true?" Keep / Revise. | Medium (depends on Launch 1 decisions) |
| 9 | Ask your past self | A natural-language recall question from the pill or window, answered with cited evidence. | Medium–High |
| 10 | Brief my AI | One action copies today's or this project's context for any AI chat (`context.py` / Copy context). | Low |
| 11 | What Trace has learned | A visible, editable page of accepted and declined suggestions, project names and app choices (`grouping_log.jsonl`, R14). | Medium |
| 12 | Interest timeline | Projects and topics by month, first-seen dates, how interests changed. | High (needs long-term digests) |
| 13 | Echoes | "You did something like this in March: here's what you decided", when new work resembles an old project. | High |
| 14 | Anniversaries | Quiet, in-window only: "A year ago you started Kestrel". | Medium (needs history that old) |

## Acceptance criteria

1. `docs/2026-11-22-engagement-and-habit-proposal.md` exists, and `docs/README.md` has one new row
   linking to it. The link resolves.
2. No file outside these two is changed (`git diff --stat main` shows exactly two files).
3. The header carries the "proposed scope, not an approved implementation plan" status. "Trace" is
   used throughout and LMemM is named once to connect the two.
4. The document has these sections in this order: Principles; What Trace can already observe;
   Features; Considered and rejected; Ranked shortlist; Suggested next steps.
5. It has 12–16 features, and each one has every field from the per-feature format, in order.
6. Every Implementation Complexity is exactly `Low`, `Medium` or `High`. Every Priority is `P1`,
   `P2` or `P3` and gives a reason.
7. Each of goals 1–6 is served by at least two features. At least three features are Low and at
   least two are High. A range in the seed table, such as "Low–Medium", must resolve to one value.
8. No feature is marked Low if it depends on unbuilt capability: the trail feeding memory, live
   semantic inference or semantic intake beyond the current sources, retention longer than the
   current limits, or a new data source. Features that rely on Launch 1 pieces say so. Their
   release acceptance, which `docs/current-context.md` lists as pending, is noted as a risk.
9. No feature uses streaks, points, badges, leaderboards or scheduled push notifications. Any
   feature that shows something unprompted names its surface (pill peek, project page, Resume,
   window) and its rate limit or trigger.
10. Every feature that uses a model says it is optional and local (Ollama) and that its output is
    labelled generated. Every feature that needs longer retention or a new data source says so
    under **Needs**.
11. Project Resume, the Resume tab and the ambient canvas are not presented as new. Where a feature
    extends them, it links to `docs/2026-10-20-project-resume-beta-proposal.md`,
    `docs/superpowers/specs/2026-10-20-launch-one-design.md` or `docs/ambient-canvas.md`.
12. Every `src/…` module and `docs/…` path the proposal names exists in the repo. Check with
    `ls` on each path found by `grep -o '\(src\|docs\)/[A-Za-z0-9_./-]*' <proposal>`.
13. The ranked shortlist has exactly five features, all drawn from the Features section.

## Tests

No automated tests are added or changed, because this is documentation only and the repo has no
docs tests. Verification is by reading against the acceptance criteria above, plus the path check
in criterion 12 and `./scripts/test.sh` still passing unchanged, which shows no code was touched.

## Out of scope

- Building, prototyping or configuring any of the proposed features.
- Changing `config.py` limits, retention, resurfacing cadence or any UI.
- Approving a roadmap. The proposal is input to a later decision, and each P1 item still needs its
  own spec.
- Renaming LMemM to Trace in code or in existing docs.
- Contacting pilot users or claiming any measured engagement results.

## Assumptions

- File name and date: `2026-11-22-engagement-and-habit-proposal.md`, today's date. Triage proposed
  `2026-11-XX`.
- Priority uses P1/P2/P3 (now/next/later) rather than High/Medium/Low, so it isn't confused with
  complexity. The request didn't fix a scale.
- 12–16 features is enough to cover six goals at two features each, with room for ambitious items,
  while staying readable.
- The seed list above is a floor for the implementer's judgement, not a fixed final list. Names
  and merges may change as long as the criteria hold.
- Launch 1 features (Resume tab, decisions and tasks, Related sources, Copy context, browser saves)
  are treated as present on `main` per the 2026-10-22 entry in `docs/current-context.md`, with
  release acceptance still pending. Features that rely on them must say so.

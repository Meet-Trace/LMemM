# ADR 0002: Moments and evaluation tooling (Step 2, PR 1)

Status: proposed. Scope: records, buffering, annotation sheets, metrics, baselines. No activity inference, no model calls, no UI, no project-finder change.

## Moment
One record per stretch of a visit in which nothing material changed (`src/moments.py`). It holds where (item, app, window, url without query/fragment), when (`t_start`, `t_end`), how (interaction category, seconds per category, ages of last key/scroll/click, never keys), and a digest (up to 12 terms, 8 identifiers, 3 quoted lines of at most 80 characters, a text hash; secrets removed with `trail_engine.SECRET`). It carries no activity, objective, status or confidence. Context holds `prev_moment_id`, `gap_s` and `same_item_before` (previous visit to the same item, gap, visits before). Contiguity is recorded, never used as membership.

## Visits and buffer rules
- New visit when the item changes (any duration) or more than 90 s pass with no frame.
- Inside a visit a frame extends the open moment. A new moment starts on a category change (after 10 s in the moment), a material text change (3 or more new terms or any new identifier, with a different text hash, and only when the new words persist into a second frame, so OCR noise cannot start a moment or enter a digest), or after 300 s.
- Separate visits are never merged, even with identical content. A return is a new visit linked by `same_item_before`.
- In memory: 30 minutes, 400 moments. On disk: `memory/moments/<day>.jsonl`, owner-only, 48 h retention sweep, partial forget by time or app, and `forget.delete_all` already removes the folder. Off by default (`MOMENT_LOG`); the tracker hook is read-only and swallows errors.

## Evaluation (`src/activity_eval.py`, `scripts/eval_activities.py`)
- **Unit.** The primary annotation unit is the moment. `units.csv` carries `unit_id` (= the moment id), `visit_id` and `moment_id`; a label may span many moments and many visits, and `also` marks concurrent work. Stored sessions and the invented days are visit-level, a legacy coarser view: they are marked as such, written to their own sheets, and never mixed with or compared to moment-level scores. `HOW_TO_LABEL.txt` is written beside each sheet.
- **Gold.** Blank = unlabelled (excluded, counted). `-` = noise. `unsure = Y` units are excluded from the primary scores and counted; inclusive numbers are reported separately. Objectives are optional context for later evaluation and are never required to score grouping.
- **Primary metrics.** Pairwise precision/recall/F1; B-cubed; fragmentation; `wrong_pair_share` (share of merged pairs that are wrong, 1 minus precision); `impure_clusters` (share of clusters holding any wrong pair); cross-app recall; switch correctness; return correctness; the sandwich test (A, short X, A) reporting both wrong absorption and wrong splitting. Dwell-weighted pairwise is supplementary.
- **B-cubed with overlapping labels.** A unit's class is every unit it shares a label with, plus itself, so classes can overlap; no partition then reaches 1.0, which is a documented limit, not a bug.
- **Returns.** A run of a label is a maximal stretch of consecutive units carrying it; a return is a run after the first. Concurrent labels do not break each other's runs. A unit returning to several labels is one event, correct if grouped with an earlier unit of any of them.
- Calibration (ECE) is implemented for later PRs. Objective accuracy, boundary error and evidence reconstruction need inference output and arrive with it.
Baselines (structure only): each visit, each item, sittings (300 s), each app, whole session, and an absorb-short-visits rule marked reference only.

## Visit memory
The buffer remembers each item's last visit only to link a return. That memory follows the store's retention window (the recorder passes the store's value, 48 h by default): entries whose last frame is older are pruned on every frame (at most once a minute) and by the retention sweep, and a return is never linked to a visit older than the window, even between prunes. A hard cap of 5,000 items (about 1 MB) guards against a stream of distinct items and drops the longest-unseen first; it only loses a "been here before" link. The open item is never pruned.

## Text hygiene
Terms are words only (no digits inside, at most 20 letters). A line holding a long unbroken run that mixes letters and digits, or flips case repeatedly, is dropped whole, because OCR garbles keys so that exact secret patterns stop matching. A text change counts only when at least 60% of the moment's known words are still present: re-reading a static page with a different OCR pass garbles the same pixels the same way every frame, so repetition alone does not prove a change. These are heuristics, checked live on TextEdit only.

## Pause and idle
When the tracker is paused, idle, or locked it calls `interrupt()`: the open moment is closed at its last frame (and written), no time is credited for the stop, and the next frame starts a new visit. A category flip or the length cap keeps the digest already established.

## Forget
Partial forget (`Recorder.forget`) clears memory first, the open moment included and without writing it, then disk, under the buffer's lock. Links from surviving moments (memory and disk) to a forgotten moment or visit are scrubbed, and visit memory is pruned by visit id. The next frame starts a new visit with no link to anything forgotten. `forget.delete_all` is unchanged.

## Constraints carried forward
Activities feed the existing project finder in Step 3 and do not duplicate it. Completion is user-set only. `uncertain` is a flag, not a status. Contiguity is a candidate signal that needs supporting evidence. Reuse the existing CPU judge and narrator before any larger model.

## Limits
`obs_id` is not filled yet. Invented days are the only labelled data until real sessions are labelled; with 2 to 3 sessions the numbers are indicative only.

# ADR 0002: Moments and evaluation tooling (Step 2, PR 1)

Status: proposed. Scope: records, buffering, annotation sheets, metrics, baselines. No activity inference, no model calls, no UI, no project-finder change.

## Moment
One record per stretch of a visit in which nothing material changed (`src/moments.py`). It holds where (item, app, window, url without query/fragment), when (`t_start`, `t_end`), how (interaction category, seconds per category, ages of last key/scroll/click, never keys), and a digest (up to 12 terms, 8 identifiers, 3 quoted lines of at most 80 characters, a text hash; secrets removed with `trail_engine.SECRET`). It carries no activity, objective, status or confidence. Context holds `prev_moment_id`, `gap_s` and `same_item_before` (previous visit to the same item, gap, visits before). Contiguity is recorded, never used as membership.

## Visits and buffer rules
- New visit when the item changes (any duration) or more than 90 s pass with no frame.
- Inside a visit a frame extends the open moment. A new moment starts on a category change (after 10 s in the moment), a material text change (3 or more new terms or any new identifier, with a different text hash), or after 300 s.
- Separate visits are never merged, even with identical content. A return is a new visit linked by `same_item_before`.
- In memory: 30 minutes, 400 moments. On disk: `memory/moments/<day>.jsonl`, owner-only, 48 h retention sweep, partial forget by time or app, and `forget.delete_all` already removes the folder. Off by default (`MOMENT_LOG`); the tracker hook is read-only and swallows errors.

## Evaluation (`src/activity_eval.py`, `scripts/eval_activities.py`)
Units are moments or visits. Gold is labelled by you in `units.csv`/`activities.csv` (blank = unlabelled and excluded, `-` = noise, `also` for a second label). Metrics: pairwise P/R/F1 (plain and dwell-weighted), B-cubed, fragmentation, wrong-merge rate, cross-app recall, switch correctness, return correctness, and a sandwich test (A, short X, A) reporting both wrong absorption and wrong splitting. Calibration (ECE) is implemented for later PRs. Objective accuracy, boundary error and evidence reconstruction need inference output and arrive with it.
Baselines (structure only): each visit, each item, sittings (300 s), each app, whole session, and an absorb-short-visits rule marked reference only.

## Constraints carried forward
Activities feed the existing project finder in Step 3 and do not duplicate it. Completion is user-set only. `uncertain` is a flag, not a status. Contiguity is a candidate signal that needs supporting evidence. Reuse the existing CPU judge and narrator before any larger model.

## Limits
`obs_id` is not filled yet. Invented days are the only labelled data until real sessions are labelled; with 2 to 3 sessions the numbers are indicative only.

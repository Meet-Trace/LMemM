
## Incremental passes

The processor keeps provisional Moments in memory between passes (`state` in `run_pass`), so a pass reads only new
observations instead of rebuilding every active Moment from the database. Each Moment keeps running sums, so adding a
member costs the same however long the Moment is. On the first pass after a start, and after any failed write, the
cache is dropped and rebuilt from the database, and observations that have features but no Moment are assigned again.

Growth revisions are throttled: a provisional Moment that only grew is written at most every `revision_every_s`
(600 s). New Moments, state changes, merges and splits are written at once. The stored revision of a growing Moment can
therefore lag by up to that long; the in-memory one is current. Only core members move a Moment's start and end.
Moments are still unbounded in length and stay undescribed until they stabilise.

## Activities

Activities live in the V2 store (migration 0007) and are built from stable Moments only, one at a time:
`python -m trace_v2.run --moments --activities-model <ollama model>`.

For each stable Moment the model states its purpose once; the purpose is embedded and kept (`activity_interpretations`).
A new Moment is compared only with the nearest already-assigned Moments inside the horizon (`neighbours`, `horizon_s`),
never with the whole history. Each compared pair is judged once and kept (`activity_verdicts`). The Moment joins an
Activity when at least `share` of the compared members of that Activity were judged SAME; otherwise it starts its own
(`forming`, becoming `active` at `min_moments`). A failed judge call defers the Moment to the next pass instead of
guessing. An Activity with no new Moment for `archive_after_s` is archived and never joined again.

Revisions are immutable and store only the Moments added plus the evidence for the join, so storage grows linearly.
Forgetting an observation deletes every Activity that contained its Moment and everything the model said about that
Moment; the surviving Moments are assigned again on the next pass.

Not done yet: splitting Activities, a confidence score, and a real description of each Moment (the default
`action` is the Moment's top words, not the vision-model description). Thresholds are untuned.

## Moment descriptions (vision)

`python -m trace_v2.run --moments --vision-model <ollama vision model> --activities-model <text model>`.

A background worker (`trace_v2/descriptions`) describes each stable Moment once: the normalised account of the Moment
(application, document structure, ordered observations, interactions) goes to the vision model together with up to four
of its screenshots. The answer is kept only when every piece of evidence cites an observation that exists; otherwise the
Moment is recorded as `insufficient`. Results are immutable rows in `moment_descriptions` (model, prompt version, status,
confidence, evidence, seconds) and are deleted when an observation of the Moment is forgotten.

The worker does one Moment per pass and waits while the Mac is on battery below 20% or has under 10% memory free
(`pmset`, `memory_pressure`; unknown readings never pause). A model that cannot be reached is retried three times, then
the Moment is recorded as `failed`. When `--vision-model` is set, Activities interpret only Moments that have an `ok`
description, and use it as the action text.

Untested on a Mac with a real model; the model call, image path and gate readings are covered by fakes only.

## Closing long Moments

A Moment no longer stays provisional for as long as work continues. When the next observation arrives it is closed
(stable) instead of joined if: the Moment is at least `min_length_s` (20 min) long and work paused for `pause_s` (2 min)
before the observation; or it reached `max_length_s` (60 min) and the gap is at least `cap_pause_s` (20 s); or it is past
`max_length_s + hard_slack_s` (70 min), whatever the gap. The observation then starts or joins another Moment, so
continuous work becomes a chain of Moments that Activities can link. Every Moment therefore gets described. All five
values are untuned guesses.

## Waiting for OCR

An observation whose screenshot is stored is not judged while its OCR is unfinished, for up to `ocr_wait_s` (5 min); after
that it is judged with what exists. If OCR is done but GSA-1 structure is missing it waits only `ready_wait_s` (45 s),
because structure is optional and may never arrive. Observations without a stored screenshot are never held.

### Merging Activities

When a new Moment qualifies for more than one Activity, the larger one is the candidate winner and each other one must
pass a cross-check: the `cross_pairs` (4) most similar member pairs across the two (at least `cross_min_pairs`, 2) are
judged, and at least `share` (75%) must be SAME; verdicts already stored count. A passing Activity is merged in one
transaction: the winner takes its Moments and the new one, and the loser gets a closing `merged` revision pointing at the
winner. If the cross-check fails the Moment simply joins the best Activity and the two stay separate. Splitting is not
automatic. Merged Activities are never joined again.

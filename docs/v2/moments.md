
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

Not done yet: merging and splitting Activities, a confidence score, and a real description of each Moment (the default
`action` is the Moment's top words, not the vision-model description). Thresholds are untuned.


## Incremental passes

The processor keeps provisional Moments in memory between passes (`state` in `run_pass`), so a pass reads only new
observations instead of rebuilding every active Moment from the database. Each Moment keeps running sums, so adding a
member costs the same however long the Moment is. On the first pass after a start, and after any failed write, the
cache is dropped and rebuilt from the database, and observations that have features but no Moment are assigned again.

Growth revisions are throttled: a provisional Moment that only grew is written at most every `revision_every_s`
(600 s). New Moments, state changes, merges and splits are written at once. The stored revision of a growing Moment can
therefore lag by up to that long; the in-memory one is current. Only core members move a Moment's start and end.
Moments are still unbounded in length and stay undescribed until they stabilise.

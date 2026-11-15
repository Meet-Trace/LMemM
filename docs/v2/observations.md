# Trace V2: the Observation layer

The foundation of Trace V2. It records, on a fixed schedule, one immutable Observation per capture and nothing
else. No Moments, Visits, Activities, Projects, models or clustering live here, and nothing here reads them.
Design decisions and trade-offs: [ADR 0006](../adr/0006-v2-observation-store.md).

## Where data lives
- Data directory: `TRACE_V2_DATA_DIR` if set, else `<repo>/data` (`trace_v2/paths.py`).
- `observations.sqlite3`: structured evidence and settings. `observation-assets/screenshots/`: image files.
- `check_data_dir` refuses the V1 archive (`~/Library/Application Support/Trace/Legacy-V1`) and any folder holding
  V1 files (`memory/memory.json`, `memory/moments`, `trail`). V2 never reads or writes V1 data.
- Known overlap: V1's code also defaults to `<repo>/data`. If V1 runs it writes V1 files there, and V2 will then
  refuse that folder rather than mix with it.

## Logical contract
```
Observation
  observation_id, timestamp
  context: {application, window, display}
  evidence: {screenshot, ocr, accessibility}        # ocr/accessibility = original (revision 1)
  interaction_events: [{event_id, seq, timestamp, type, payload}]
  metadata: {reader_versions, capture_version, capture_key, events_window_ns,
             extraction_status: {context, screenshot, ocr, accessibility, events},
             later_extractions: {ocr: [...], accessibility: [...]}}
```
`ObservationStore.get(id)` returns exactly this shape.

## Tables and relationships
```
observations 1 ── 1    screenshots              (status stored|failed; file metadata + sha256)
screenshots  1 ── 0..1 screenshot_deletions ─── retention_runs 1 ── 0..1 retention_run_finishes
observations 1 ── 2..* extractions              (reader ocr|accessibility, revision 1..n, supersedes)
observations 1 ── 0..* interaction_events       (PK observation_id + seq; event_id UUID)
settings (mutable)      settings_history (append-only)
schema_migrations       (version, file sha256)
```
Every table except `settings` and `schema_migrations` rejects UPDATE and DELETE.
Indexes: observations by time, by (app bundle id, time); extractions by (observation, reader, revision);
events by time. Schema: [`0001_observations.sql`](../../trace_v2/observations/migrations/0001_observations.sql).

## Ingestion (`store.ingest`)
validate -> duplicate check on `capture_key` -> write screenshot file (create-only, fsync, rename) ->
one transaction: observation + screenshot row + both reader results + all events -> commit.
A failure rolls back and removes the file just written. A crash between the file write and the commit leaves an
orphan file that `store.audit()` reports. Retrying the same `capture_key` with the same content returns the
existing Observation; different content raises `DuplicateObservation`.

## Capture (`pipeline.py`)
`build_draft` runs context, screenshot, OCR and accessibility as isolated steps. Any one can fail (recorded as
`failed`, `unavailable` or `skipped` with an error code) and the Observation is still stored. `ContinuousObserver`
captures every `interval_s` regardless of activity, skips and counts missed ticks, and keeps running when one
capture fails. Interaction events reach it through `events.EventBuffer`.

## Interaction events (`events.py`)
Types: `cursor_move`, `click`, `keypress`, `scroll`, each with an allow-listed payload; anything else is rejected
and counted. Keypresses keep phase, key class, modifiers and repeat only, and are dropped (counted) while secure
input is on. Events are stored one row each, in arrival order (`seq`), never summarised. Each event belongs to the
Observation whose capture time closes its interval.

## Screenshot retention (`retention.py`)
Policies: permanent (default), 5, 7, 14, 30 days, or a custom duration of at least one hour. `run(store, now_ns)`
removes expired screenshot files oldest first, records each in `screenshot_deletions` and audits the run.
Structured evidence is never touched. Dry runs record what would be deleted.

## Tests
`tests/test_v2_observation_{store,retention,migrations,pipeline}.py`, run with `./scripts/test.sh`.

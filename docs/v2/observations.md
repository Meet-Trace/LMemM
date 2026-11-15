# Trace V2: the Observation layer

The foundation of Trace V2. It records, on a fixed schedule, one immutable Observation per capture and nothing
else. No Moments, Visits, Activities, Projects, models or clustering live here, and nothing here reads them.
Design decisions and trade-offs: [ADR 0006](../adr/0006-v2-observation-store.md).

## Where data lives
- Data directory: `TRACE_V2_DATA_DIR` if set, else `~/Library/Application Support/Trace/V2` on macOS
  (`$XDG_DATA_HOME/trace/v2` elsewhere), from `trace_v2/paths.py`. Never `<repo>/data`, which is V1's default.
- `observations.sqlite3`: structured evidence and settings. `observation-assets/screenshots/`: image files.
- `check_data_dir` refuses the V1 archive (`~/Library/Application Support/Trace/Legacy-V1`), V1's data folders
  (`<repo>/data`, `~/Library/Application Support/LMemM`) and any folder holding V1 files (`memory/memory.json`,
  `memory/moments`, `trail`). V2 never reads or writes V1 data.

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
forget_requests 1 ── 0..* forget_tombstones (id only)   forget_requests 1 ── 0..1 forget_finishes
deletion_grants (transient, inside one forget transaction only)
settings (mutable)      settings_history (append-only)
schema_migrations       (version, file sha256)
```
Every table except `settings`, `schema_migrations` and `deletion_grants` rejects UPDATE. DELETE is refused on
every evidence row unless a forget request granted it in the same transaction.
Indexes: observations by time, by (app bundle id, time); extractions by (observation, reader, revision);
events by time. Schema: [`0001_observations.sql`](../../trace_v2/observations/migrations/0001_observations.sql).

## Ingestion (`store.ingest`)
validate -> duplicate check on `capture_key` -> write screenshot file (create-only, fsync, rename) ->
one transaction: observation + screenshot row + both reader results + all events -> commit.
A failure rolls back and removes the file just written. A crash between the file write and the commit leaves an
orphan file that `store.audit()` reports. Retrying the same `capture_key` with the same content returns the
existing Observation; different content raises `DuplicateObservation`.

## Privacy gates (`privacy.py`), before ingestion
Order: context -> gate -> screenshot and readers -> redaction -> store. A gated context is never captured.
- Withheld (an Observation with only time, reason and bundle id; no screenshot, reader output, title, URL or
  events): built-in excluded apps (password managers, Keychain) plus the user's list, private windows (adapter
  flag or title), built-in sensitive sites plus the user's patterns, and secure keyboard input (unknown = on).
- Redacted: API keys, GitHub/Slack/AWS tokens, JWTs, private keys, `password: x` assignments and Luhn-valid card
  numbers are replaced with `[REDACTED:kind]` in OCR, accessibility, window title and URL. Values of
  `AXSecureTextField` nodes are removed. If OCR or accessibility text held a secret, the screenshot is withheld
  (`secret_on_screen`), because the pixels show it. Counts are kept in `metadata.privacy.redactions`.
- Patterns are conservative: git SHAs, dates, phone numbers and ordinary words are left alone.
- Policy lives in settings (`privacy.save_policy`) with history.

## Forgetting (`forget.py`)
The only way structured evidence is deleted. `forget(store, selector)` takes observation ids, a time range, an app
(optionally within a time range) or everything, and supports dry runs. It records the request, grants deletion for
exactly the matched observations, deletes all their rows in one transaction, leaves an id-only tombstone (a
forgotten id can never be reused), then removes screenshot files and records completion. An interrupted file
removal is finished when the store next opens. `secure_delete` is on and the WAL is truncated afterwards;
`everything` also removes migration backups, and a partial forget lists them.

## Capture (`pipeline.py`)
`build_draft` runs context, screenshot, OCR and accessibility as isolated steps. Any one can fail (recorded as
`failed`, `unavailable` or `skipped` with an error code) and the Observation is still stored. `ContinuousObserver`
captures every `interval_s` regardless of activity, skips and counts missed ticks, keeps running when one
capture fails, and takes one final Observation when stopped. Interaction events reach it through `events.EventBuffer`.

## Interaction events (`events.py`)
Types: `cursor_move`, `click`, `keypress`, `scroll`, each with an allow-listed payload; anything else is rejected
and counted. Keypresses keep phase, key class, modifiers and repeat only, and are dropped (counted) while secure
input is on. Events are stored one row each, in arrival order (`seq`), never summarised. Each event belongs to the
Observation whose capture time closes its interval:
- windows are (previous capture, this capture]; the first window starts at the session start, and events stamped
  before it are rejected and counted;
- a late-delivered event goes to the next Observation and is counted `late`, its timestamp unchanged;
- a failed capture restores its events and does not advance the window, so nothing is lost;
- a wall clock stepping backwards never produces an inverted window (`clock_regression` is counted);
- a crash loses at most the events of the current interval, which were never on disk.

## Screenshot retention (`retention.py`)
Policies: permanent (default), 5, 7, 14, 30 days, or a custom duration of at least one hour. `run(store, now_ns)`
removes expired screenshot files oldest first, records each in `screenshot_deletions` and audits the run.
Structured evidence is never touched, and it has no automatic expiry: it is kept until the user forgets it.
Dry runs record what would be deleted.

## Tests
`tests/test_v2_observation_{store,retention,migrations,pipeline,privacy,forget}.py`, run with `./scripts/test.sh`.

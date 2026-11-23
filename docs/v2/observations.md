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
Gate order (first that applies is recorded): `context_unavailable` (fail closed), `screen_locked`, `excluded_app`,
`private_window`, `excluded_site`, `secure_input`. A locked screen is decided before secure input because the lock
screen itself turns secure keyboard entry on (PR E fixed an order that recorded a locked screen as `secure_input`).
- Withheld (an Observation with only time, reason and bundle id; no screenshot, reader output, title, URL or
  events): built-in excluded apps (password managers, Keychain) plus the user's list, private windows (adapter
  flag or title), built-in sensitive sites plus the user's patterns, secure keyboard input (unknown = on), a
  locked screen (`screen_locked`, nothing behind the lock is read) and a failed context read
  (`context_unavailable`: when the front app is unknown no other gate can judge it, so the capture fails closed).
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
and counted. `cursor_move` may carry `coalesced` (how many raw moves the stored sample stands for, 1..1,000,000) and
`final` (true: the position where the cursor stopped); the buffer itself never reduces anything, the sampling happens in
`trace_v2.inputs` before events reach it. Keypresses keep phase, key class, modifiers and repeat only, and are dropped (counted) while secure
input is on. Events are stored one row each, in arrival order (`seq`); a trailing cursor sample can carry a timestamp up to one sample interval later than a click or key that arrived just before it (the cursor stayed put, so the position is still true). Each event belongs to the
Observation whose capture time closes its interval:
- windows are (previous capture, this capture]; the first window starts at the session start, and events stamped
  before it are rejected and counted;
- a late-delivered event goes to the next Observation and is counted `late`, its timestamp unchanged;
- a failed capture restores its events and does not advance the window, so nothing is lost;
- a wall clock stepping backwards never produces an inverted window (`clock_regression` is counted);
- a crash loses at most the events of the current interval, which were never on disk.

## Quarantine until scanned (`assets.py`, `ocr_queue.py`)

In async OCR mode the secret scan of the pixels is the OCR text, which arrives after the screenshot is stored. Until it does,
the file lives under `<assets>/quarantine/` with the same relative name; reads, links and deletes find it there. A clean
scan moves it to its final place. A secret in the text deletes it (`secret_on_screen_late`). A scan that cannot finish
(terminal failure, job expiry after 1 h) deletes it (`unscanned`; migration 0006 adds the reason). When the OCR queue is
full no scan can follow, so the screenshot is withheld as `ocr_queue_full` and no pixels are kept. A frame that reuses
the clean text of an identical earlier frame is linked straight into its final place; a frame that must be scanned
itself is linked into quarantine. Files already in their final place (older data, synchronous captures) are never removed
by a failed scan. Residual: in synchronous mode a failed OCR still leaves the screenshot unscanned; a frame reusing the
text of a near-identical earlier frame is not scanned on its own.

## Screenshot retention (`retention.py`)
Policies: permanent (default), 5, 7, 14, 30 days, or a custom duration of at least one hour. `run(store, now_ns)`
removes expired screenshot files oldest first, records each in `screenshot_deletions` and audits the run.
Structured evidence is never touched, and it has no automatic expiry: it is kept until the user forgets it.
Dry runs record what would be deleted.

## macOS adapters: context, screenshot, OCR, accessibility (`trace_v2/macos/`)
The only package that imports pyobjc (`trace_v2.observations` stays importable on Linux). `MacSources` wires
context -> display id -> screenshot -> OCR and accessibility for the pipeline (`capture_version` `macos-capture/1`).
- Context (metadata only): frontmost app (NSWorkspace), its front on-screen layer-0 window (CGWindowList), the display
  holding most of that window (bounds in points, pixel size of the current mode, scale). `window.locked=True` when
  the session reports a locked screen or loginwindow/ScreenSaverEngine is frontmost -> gate reason `screen_locked`.
  `secure_input()` is Carbon `IsSecureEventInputEnabled`, and True when unknown.
- Browsers: URL (and, for Chrome-family, `private` from `mode of front window`) via `osascript` within a 300 ms
  budget. On refusal, timeout or error `url`/`private` are absent and `window.url_error` says why
  (`automation_denied`, `automation_timeout`, ...). Safari has no scriptable private flag: `private_error:
  "unsupported_safari"`, private Safari windows are not detected beyond the title gate.
- Screenshot: ScreenCaptureKit `SCScreenshotManager` (macOS 14+), original pixels, no downscale, no cursor, lossless
  PNG via ImageIO. What the pixels may contain is the setting `screenshot_scope` (below). The PyObjC ScreenCaptureKit wrapper is not installed, so the framework is loaded with
  `objc.loadBundle` and the two block signatures are registered by hand. `CGDisplayCreateImage` is the fallback only
  when SCK cannot load; `ScreenshotSource.last_path` says which ran.
- Reader recovery: both readers are retried on every capture and each failure is recorded as that capture's reader
  status/error code. OCR runs on a worker thread; a request that times out may keep running inside Vision, so it is
  parked and the next capture starts a new request (at most two parked, then fail-fast `Timeout` until one returns;
  before PR E every later capture failed until the first returned, measured at over 20 s on a loaded Mac).
- OCR isolation: by default (`ocr_isolation=process`) Vision runs in a separate worker process that the capture process can
  kill and restart (`ocr_client.py`, `ocr_worker.py`); the reader, its `vision-ocr/1` version, the `raw`/`text` shape and
  the extraction statuses are the same as in process. Details, bounds and limits: `docs/v2/runner.md` ("OCR isolation").
- OCR (`ocr.py`, reader `vision-ocr/1`): `VNRecognizeTextRequest` on the exact PNG bytes of this capture (PNG data ->
  CGImageSource -> CGImage; no second capture, no temp file), accurate level, language correction on, automatic
  language detection (macOS 13+). `raw` = `{lines: [{text, confidence, box [x, y, w, h] in image pixels, top-left
  origin}], image_size, recognition_level, languages, os_version}`; `text` = lines joined with `\n`, ordered row by
  row (top to bottom, left to right inside a row; columns interleave, boxes are kept so a consumer can re-order).
  `started_at_ns`/`finished_at_ns` come from `run_reader`. Vision runs on a worker thread with a 10 s timeout
  (`VisionOCR(timeout_s=)`): on expiry the request is cancelled and the result is `failed` with `error_code`
  `Timeout`; while a cancelled worker has not finished, the next read fails fast with `Timeout` instead of starting
  another. If Vision cannot be imported the result is `unavailable`. OCR is skipped when the screenshot is withheld or
  failed, and never runs for a withheld context. Redaction and `secret_on_screen` apply to OCR output unchanged.
  The first Vision call on a machine can take over a minute (model compilation; seen: 66 s, then <1 s), which a 10 s
  timeout records as `Timeout`; later captures succeed.
- OCR accuracy (synthetic renders): clean 13 pt+ text, dark mode, and Japanese/Chinese/Korean/Cyrillic
  lines were read correctly with automatic detection; 9-11 pt text is unreliable (words garbled); a mixed-case
  `aws_access_key_id` was read as `awS_...`. `confidence` is often exactly 1.0 even for garbled text, so it must not
  be used as a quality signal. Vision may misread look-alike characters (0/O, I/l); a secret misread that way no
  longer matches the redaction patterns, so redaction and the `secret_on_screen` withhold are best effort, not a guarantee.
- Accessibility (`accessibility.py`, reader `ax-tree/1`): `AccessibilityReader.read(context)` -> `(raw, text)` for the
  front app (`context.application.pid`). `AXIsProcessTrusted()` is checked first and never prompts; untrusted or
  `kAXErrorAPIDisabled` -> `unavailable` ("accessibility not trusted"); any other failure to reach the window (hung app,
  invalid element, no window) -> `failed` (`Unresponsive`, `NoWindow`, `AXError`). Window: `AXFocusedWindow`, else the
  `AXWindows` entry titled like `context.window.title`, else `AXMainWindow`. Breadth-first walk under hard budgets,
  all recorded in `raw.limits` and `raw.truncated {nodes, depth, time}`: 3000 nodes, depth 40, 2000 chars per value
  (`value_truncated`/`title_truncated` hold the original length), 1.5 s wall clock, and a 0.25 s AX messaging timeout on
  the app element (eight consecutive timeouts also end the walk as `time`). Per node: role, subrole, title, value
  (strings as strings, numbers as numbers), description, identifier, frame `[x, y, w, h]` in global points, focused,
  selected text (focused nodes only), `url` on `AXWebArea`, children; `raw.document` is the window's `AXDocument`.
  Values of `AXSecureTextField` (by role or subrole), and its selected text, are never requested: the node carries
  `value_omitted: "secure"`. This is in addition to the pipeline redaction, which still runs on raw and text, and a
  secret in AX text withholds the screenshot (`secret_on_screen`). `text` = titles and string values in tree order,
  consecutive duplicates removed.
  Browsers: the page URL is recorded (`AXURL`, `AXDocument`) but no new gate is applied; `excluded_site` already ran
  on the context URL. Electron/Chromium apps (Claude, Slack, VS Code) expose only a shell tree (about 9 nodes) until
  the app turns accessibility on (`AXManualAccessibility`); the reader never sets that, because it changes another
  app's behaviour and cost. For those apps AX adds little and OCR carries the content.
  Permission: Accessibility must be granted to the process that runs the reader (the app hosting Trace; in
  development the terminal or Python that runs the tests).
- Permissions (never prompted by us): Screen Recording (pixels; without it window titles are empty) and, per browser,
  Automation (URL/private). A missing Screen Recording grant raises `PermissionError`, recorded as a screenshot failure.
- Screenshot scope (setting `screenshot_scope`, `settings.get/set_screenshot_scope`, default `front_app`, picked up
  while running): `front_app` uses `SCContentFilter(display, includingApplications=[the front app], exceptingWindows=[])`,
  so only windows of the application the gates judged are composited: other apps' windows, notification banners,
  Notification Center, the Dock and the menu bar are not in the image. The canvas stays the full display size and
  everything that is not the front app is fully transparent (alpha 0), so the PNG is still lossless and un-scaled.
  The app is the one `context()` reported (matched by pid, else a unique bundle id), so the image and the gate always
  concern the same app even if the user switched apps in between. If no running application matches, or
  ScreenCaptureKit is unavailable, the screenshot is withheld as `scope_unavailable`; it never falls back to the whole
  display. `display` is the weaker-privacy mode: the whole display, every visible window and banner on it. Each
  observation records the scope in `metadata.privacy.screenshot_scope`.
  Residual limits of `front_app`: other windows of the SAME app are included (a second window, a floating panel or
  picture-in-picture it owns, its own menus and tooltips); sensitive content of the front app that no gate recognises
  is still captured; a banner belongs to Notification Center, so it is excluded only because it is a different process;
  an app whose pid ScreenCaptureKit cannot see yields `scope_unavailable` (no pixels, but AX and events continue).
  Chrome's AppleScript "front window" can differ from the CGWindow front window.
- Interval: setting `capture_interval_s` (default 3.0, 0.5..3600) via `settings.get/set_capture_interval`;
  `ContinuousObserver` defaults to it. The runner (`docs/v2/runner.md`) reads it; `--interval` overrides it for one run.
- Cost: `scripts/v2_measure_capture.py` (temp store, deleted afterwards): capture and OCR latency p50/p95/max, CPU per
  OCR, peak RSS, text chars, compressed raw bytes and projected GB per 8 h, missed ticks (it drives
  `ContinuousObserver`). Measured at 3 s on one MacBook display (40 captures, a mostly static screen): OCR p50 0.45 s,
  ~1 CPU-second per OCR (about a third of a core), 0 missed ticks, ~7 KB/capture structured (0.07 GB per 8 h) against
  ~786 KB/capture PNG (7.5 GB per 8 h).

## Input events (`trace_v2/inputs/`, `trace_v2/macos/input_tap.py`)
`InputTap(buffer, cursor_sample_ms=150)` is a listen-only session `CGEventTap` (head insert) on its own thread and run
loop, feeding `EventBuffer.add`. It is separate from `MacSources`; the runner (a later PR) starts it with the same buffer
the observer uses, so attribution is unchanged. The decision logic is platform independent and lives in `trace_v2.inputs`
(`CursorSampler`, `MachClock`, key classes, `decode`); the tap only reads CGEvent fields into a plain dict.
- Cursor: moves and drags are sampled, never stored raw: at most one `cursor_move` per `cursor_sample_ms` (setting
  `cursor_sample_ms`, 100..200, default 150, `settings.get/set_cursor_sample_ms`) while moving, plus one `final` position
  once the cursor has been still for an interval (stamped no earlier than the last sample + interval, so samples are never
  closer than the interval). `coalesced` records how many raw moves each sample replaces.
- Clicks (per button, down and up, click count), scrolls (every event: deltas, pixel or line unit, phase incl. momentum)
  and keypresses (down/up, repeat) are stored individually with their own timestamps. Modifier changes are keypresses of
  class `modifier`. A keypress keeps `phase`, `key_class`, `modifiers`, `is_repeat` only; the virtual keycode is read to
  pick the class and dropped, characters are never read (`keys.py` is the class table). The implicit `fn` flag macOS sets
  on arrows and function keys is not reported.
- Time: `CGEventGetTimestamp` is already nanoseconds on the uptime clock (measured: it matches `mach_absolute_time() *
  timebase`, so no tick conversion). Wall time = timestamp + offset captured at start. Uptime stops while the Mac
  sleeps, so a 75 ms housekeeping timer compares wall and uptime progress and re-syncs the offset when they diverge by over a second
  (after wake or a clock step); already converted events keep their timestamps. `stats()["clock_resyncs"]` counts them.
- Privacy: keypresses are passed with `secure_input=IsSecureEventInputEnabled()` (True when unknown) so they are
  suppressed and counted while a password field has focus (macOS also stops delivering key events to taps then).
  The tap also asks an `inputs.InputGate` for every event (PR E): while a gated app is in front or the screen is locked
  it drops EVERY event type (keys, clicks, scrolls and cursor positions, since click positions inside a password manager
  are as sensitive as its keys), counted as `dropped_gated` on the tap and on the buffer (so the next Observation's
  `event_stats.dropped_gated` shows it). The gate is fed by the capture-level decision, by NSWorkspace
  app-activation notifications (an excluded app blocks at once, not at the next capture), by lock/unlock notifications and
  by a front-app re-read every 5 s (repairs a lost notification); it starts blocked. `tap.keyboard_blocked` stays as an
  alias. Independently the capture-level gate withholds all events of a gated capture (`discarded_privacy`).
  Residual: the few milliseconds between a front-app change and its notification are not covered.
- Permission: Input Monitoring only. `CGPreflightListenEventAccess()` is checked, `CGRequestListenEventAccess()` is never
  called. Without it (or if the tap cannot be created) `start()` returns `unavailable` with `reason`
  (`input_monitoring_not_granted`, `tap_create_failed`, `tap_start_timeout`) and capture continues without events.
  macOS disabling the tap (timeout, user input) is answered by re-enabling it, counted as `tap_reenabled`.
- The callback does no I/O: a few integer reads, the sampler, one validated `EventBuffer.add`. `stats()` has raw counts
  per type, stored counts, cursor raw/samples/finals, re-enables, callback errors, callback CPU and clock re-syncs.
- Measured (3 min, 3 s captures, stub capture sources, synthetic bursts of 125 Hz cursor motion and 60 Hz zero-delta
  scrolls: about 2,200 raw moves/min and 555 scrolls/min): callback CPU 49 us per event (0.4 s over 3 min), process 2% of
  one core including the poster, 6,534 raw moves stored as 485 samples (164/min), all 1,638 scrolls stored,
  39 rows per observation on average (max 99), ~173 logical bytes per row, 0 missed ticks. At that continuous rate
  event rows are about 0.06 GB per 8 h; real use is far quieter except while scrolling a trackpad (kept one-to-one by decision).
- Limits: the cursor position and every click/scroll position are screen coordinates in global points; they say nothing
  about which window was hit. Scroll events from a trackpad are frequent (about 60-120 per second while scrolling).

## Tests
`tests/test_v2_observation_{store,retention,migrations,pipeline,privacy,forget,capture_settings}.py`, PR E's `tests/test_v2_{runner,integration_review}.py` (platform independent) and `tests/test_v2_macos_{runner,scope,input_gate}.py` (real run, real screenshot scope, per-event input gate), and, on macOS only,
`tests/test_v2_inputs.py` (platform independent: sampler, clock, key classes, decoding) and `tests/test_v2_macos_{context,capture,ocr,ocr_vision,accessibility,accessibility_real,input}.py` (screenshot tests skip without Screen Recording; `ocr` uses
a fake recognizer, `ocr_vision` renders synthetic PNGs and runs real Vision through `build_draft`, `accessibility` uses
fake elements, `accessibility_real` reads a window owned by a subprocess and skips without Accessibility permission, `input` posts harmless synthetic events and skips without Input Monitoring; its key test also skips while secure keyboard entry is on), run with `./scripts/test.sh`.

## Two database files

Evidence (observations, screenshots, extractions, events, forget records) stays in `observations.sqlite3`. Everything
derived from it (features, Moments, descriptions, Activities) lives in `derived.sqlite3`, with its own connection and lock
(`store.derived`, `store.derived_lock`), so a slow derived write never blocks capture. The derived file has no foreign keys
to evidence and keeps its own `deletion_grants`. Read-only connections (`read_connection`) attach both files, so queries
that join them are unchanged.

`forget` purges the derived file first, then the evidence. A crash between the two leaves observations whose derived rows
are gone (rebuilt on the next pass), never derived rows of a forgotten observation. On open, `reconcile` also removes any
derived row whose observation is missing from the evidence file.

A database made before the split is carried over on open: the derived tables are copied into `derived.sqlite3`, then
evidence migration 0009 drops them. Migration backups (`*.bak`) of the evidence file made earlier may still hold old
derived rows until `forget everything` removes them.

## Repeated screens

A new screenshot is compared with the last 8 distinct screens (`DEDUP_MEMORY`), not only the previous one. An unchanged match
with any of them is stored as a link to that file and reuses its OCR, so flipping between two windows stores two files, not
one per capture. "Only the typing box changed" still applies to the previous screen only, because it replaces that
screenshot.

## Thinning (default retention)

The default screenshot policy is `{"mode": "thin", "seconds": 7 days, "grace_seconds": 7 days}` (`permanent` and fixed
durations still exist). Hourly, screenshots older than 7 days are deleted as before, but first one 640 px JPEG is kept per
stable Moment (`screenshot_thumbnails`, the member nearest the middle whose file still exists). Screenshots that belong to no
stable Moment keep nothing. While vision descriptions are in use, a stable Moment without a description keeps its
screenshots for up to 7 more days so the model can still see them; the description worker falls back to the thumbnail when
the original is gone. Forgetting an observation removes its thumbnail file and row. Text, structure, interaction events and
all derived data are never thinned. Expected size: full-quality PNGs for 7 days, then roughly 30-60 KB per Moment.

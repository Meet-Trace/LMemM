# Trace V2: the observer runner (`trace_v2/runner.py`, `python -m trace_v2.run`)

The runner opens the V2 store, builds the macOS sources, the event buffer and the input tap, and runs the
`ContinuousObserver` every few seconds until it is stopped. It writes Observations and nothing else: no Moments, Visits,
Activities, Projects or inference, and it never reads or writes V1 data (`paths.check_data_dir` refuses a V1 directory).
Observation layer reference: `docs/v2/observations.md`. Review of the adapters it joins: `docs/v2/integration-review.md`.

## Running it

```bash
python -m trace_v2.run                     # default data dir, interval from the stored setting (3 s), until SIGINT/SIGTERM
python -m trace_v2.run --data-dir /tmp/v2  # another data dir (also TRACE_V2_DATA_DIR)
python -m trace_v2.run --interval 5        # this run only; the stored capture_interval_s is not changed
python -m trace_v2.run --duration 600      # stop by itself after 600 s (counted from process start)
python -m trace_v2.run --once              # one observation and exit
python -m trace_v2.run --status            # lock holder, live permission report, settings, last stats; changes nothing
```

Exit codes: 0 clean, 1 the observer thread died, 2 bad arguments or a V1 data directory, 3 another runner holds the lock.
Default data dir: `~/Library/Application Support/Trace/V2` (`TRACE_V2_DATA_DIR` overrides). Use a temp dir when testing.

Settings it reads (all in the store's `settings` table, with history): `capture_interval_s` (default 3), `cursor_sample_ms`
(150), `screenshot_scope` (`front_app` default or `display`), `privacy_policy` (extra excluded apps and sites),
`screenshot_retention` (default permanent). Changes to the privacy policy and the screenshot scope are picked up within
5 s while running; the interval and cursor sampling need a restart.

Permissions, granted to the Python (or app) that runs it: Screen Recording (pixels and window titles), Accessibility (AX
reader), Input Monitoring (event tap); Automation per browser (URL/private flag). The runner only calls preflight
functions, never request functions, so it never prompts and never opens System Settings. The report is logged at
startup, stored under the `permissions_report` setting, and printed live by `--status`. Without Input Monitoring the tap
reports `unavailable` once and the runner carries on without events.

To run it at login use a LaunchAgent; `docs/v2/examples/com.trace.v2.runner.plist.example` is an example. It is not installed
by anything here.

## Startup order

1. exclusive `flock` on `<data dir>/runner.lock` (a second runner exits 3 with the holder's pid)
2. permission report
3. Vision warm-up: one OCR of a small rendered image with a 120 s timeout, on a thread, before the first capture, so the
   first real read cannot time out (a first recognition on a fresh machine has taken 66 s). A stop request during it is
   honoured at once; a failed warm-up is recorded and capture starts anyway.
4. event buffer opens at the session start, then the input tap (cursor sampling from `cursor_sample_ms`)
5. screenshot retention once (a no-op under the default permanent policy), then hourly
6. the observer thread (a capture every interval); the main thread pumps notifications every 0.25 s, refreshes settings
   every 5 s, reconciles the front app, writes stats and runs retention

## Lifecycle

| notification | effect |
|---|---|
| `NSWorkspaceWillSleep` | pause: no capture (so no failed one), the event buffer refuses and counts arriving events |
| `NSWorkspaceDidWake` | re-sync the input clock (uptime stood still while asleep), accept events, resume with a fresh schedule |
| session resign / become active | pause / resume, by reason (a wake while switched out does not resume) |
| `com.apple.screenIsLocked` / `screenIsUnlocked` | logged and counted; captures continue (the gate withholds them as `screen_locked`); the input gate blocks all events while locked |
| `NSWorkspaceDidActivateApplication` | the input gate re-evaluates the front app at once |
| SIGINT / SIGTERM | stop: one final observation, the tap stopped, final stats, store closed, lock file removed |

Missed ticks (a capture that ran longer than the interval, or the time asleep) are counted in `missed_ticks` and skipped, never
replayed, so there is no burst of catch-up captures after wake. Windows of event attribution stay contiguous: the first
observation after a pause spans the gap; events that arrived during the pause are counted (`discarded_paused`), not stored.

## Stats (never in the evidence tables)

`<data dir>/runner_stats.json` is rewritten atomically every `--stats-every` seconds (default 300) and at exit, and each
write is logged. It holds: uptime, process CPU seconds and average %, RSS and peak RSS, captures, failures, missed ticks,
per-capture CPU and wall time (avg, p95), withheld observations by reason, screenshot statuses and bytes, reader failures
by `reader:status:error_code`, event accounting totals (`stored`, `dropped_gated`, `discarded_paused`,
`discarded_privacy`, `suppressed_secure_input`, `before_start`, ...), the screenshot scopes used, DB bytes, the input tap's
counters and gate state, lifecycle counts, warm-up and permission results. The evidence counts are read back from the
observation tables, read-only.

## Reader failure and recovery

A reader that fails is recorded on that observation (`extraction_status.<reader>`: status and error code) and the observation
is still stored. Both readers are called again on every capture. Accessibility keeps no state between captures. OCR runs on a
worker thread; a Vision request that times out can keep running, so it is parked and the next capture starts a new request
(at most two parked; then reads fail fast with `Timeout` until one returns). A stuck Vision request was measured at over
20 s on a loaded 8 GB Mac; before the retry change every capture in that time lost its text.

## Measured: real end-to-end runs (this Mac: 8 cores, 8 GB, one 1440x900 display at 2x, load average about 7 from other work)

Temp data dirs, deleted afterwards; real sources, real input tap, the Mac in normal use by its owner. Scenario during the
10-minute run (synthetic events only, nothing locked or slept): 18 cursor/scroll bursts (cursor moved 2 px and put back,
zero-delta scrolls), a TextEdit window holding a fake AWS-style key as the front app, the same app excluded through the
policy setting (stub), TextEdit with no window, and Finder/Brave switches.

**10 min, `front_app`, interval 3 s** (`--duration 600`; 601 s uptime)

| | |
|---|---|
| observations / missed ticks / store failures | 195 (about 200 expected) / 6 / 0 |
| stored with a screenshot | 165 |
| withheld by the gate, `excluded_app` | 7 (bundle id and time only; the tap had already dropped 92 events, none reached the store) |
| screenshot withheld `secret_on_screen` | 9 (observation kept, AX text redacted to `[REDACTED:aws_access_key]`, no pixels) |
| screenshot withheld `scope_unavailable` | 13 (Finder or a launching TextEdit had no shareable window; AX and events continued) |
| screenshot failed | 1 (`RuntimeError`, a ScreenCaptureKit error while Finder was in front) |
| gates that did not fire | `screen_locked`, `secure_input`, `private_window`, `excluded_site`, `context_unavailable` (not provoked; covered by unit tests; locking the screen was deliberately not tried) |
| CPU (2 s samples of the runner process) | average 26 %, p95 62 %, max 95 % of one core; per capture CPU avg 0.79 s, p95 1.24 s, wall avg 0.89 s |
| memory (RSS) | 283 MB after startup, peak 409 MB (432 MB max RSS), 409 MB at the end |
| PNG | 109.5 MB for 165 stored, 664 KB average (169 to 841 KB) |
| DB | 5.9 MB (195 observations with OCR and AX raw) |
| events | 611 stored rows, 37 KB of payload (18 click, 372 cursor_move, 215 scroll, 6 keypress) from deliberately busy bursts |
| extrapolated, this mix | PNG 0.66 GB per hour, 5.3 GB per 8 h (6.4 GB if every capture stored a screenshot); DB 35 MB per hour, 0.28 GB per 8 h; events 0.2 MB per hour |

Reader failures: OCR `Timeout` x 8, all within one 40 s Vision stall (two requests timed out at 10 s, six more failed fast
while both were parked); the first capture after they returned succeeded and OCR then ran 166 of 166 normal captures.
Accessibility failed 0 times. That stall is the missed ticks. It happened with the machine under load; whether Vision can
hang for longer is not known. A killable worker process for Vision would remove that risk; not done here.

Event attribution, checked in the database: every event timestamp is inside its observation's `events_window_ns`, the 195
windows are contiguous (0 gaps, 0 overlaps), `seq` is gapless in every observation, no event row has a keycode or
character (the word `character` appears only as `key_class`), dropped events by reason: `dropped_gated` 92, everything
else 0 (`discarded_privacy` is 0 because the tap dropped them first).

**2 min, `display`, interval 3 s**: 41 observations, 0 missed, 0 failures; CPU average 42 %, peak RSS 494 MB; PNG 54.6 MB for 41
stored, 1.33 MB average (0.52 to 7.2 MB), i.e. about 1.6 GB per hour; DB 2.3 MB. The two runs saw different screens, so the PNG
size ratio is content, not a controlled comparison; the structural difference is that `front_app` leaves everything that is
not the front app transparent, which also compresses better.

**Provoked failures** (real readers, temp store): OCR `Timeout` (real Vision with a 10 ms timeout for one capture) and
accessibility `Unresponsive`, each once: the next capture of each reader succeeded, and both failures are recorded with their
error codes.

Not exercised for real: sleep/wake and lock/unlock notifications (covered with fakes; the Mac was in use), the Automation
permission report (`unknown` by design).

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
3. Vision warm-up. With `ocr_isolation=inprocess`: one OCR of a small rendered image with a 120 s timeout, on a thread,
   before the first capture, so the first real read cannot time out (a first recognition on a fresh machine has taken
   66 s). With the default `process` isolation there is no blocking step: the OCR worker starts here and warms up inside
   itself while capture begins (OCR is `unavailable` / `WorkerStarting` until it reports ready; see "OCR isolation"). A stop request during it is
   honoured at once; a failed warm-up is recorded and capture starts anyway.
4. event buffer opens at the session start, then the input tap (cursor sampling from `cursor_sample_ms`)
5. screenshot retention once (a no-op under the default permanent policy), then hourly
6. the observer thread (a capture every interval); the main thread pumps notifications every 0.25 s, refreshes settings
   every 5 s, reconciles the front app, writes stats and runs retention

## Change-triggered capture (`observations/trigger.py`, setting `capture_trigger`)

The 3 s interval is now a probe interval. Each tick reads the context, runs the privacy gate and, if the gate allows,
takes a 96 px probe picture (ScreenCaptureKit, same region and scope as the full screenshot, never stored). A full capture
(screenshot, OCR, accessibility, observation row) happens only on: `first`, `context` (app, title, URL or display changed),
`input` (click, key, scroll; cursor movement alone does not count), `screen` (more than `change_fraction` = 0.4 % of probe
pixels differ by more than `pixel_delta` = 12 from the last full capture), or a `heartbeat` (30 s without anything; 120 s
after 300 s with no change or input). Skipped ticks keep their events in the buffer for the next observation. A withheld
tick is always recorded and never probed. No `probe()` source, a failing probe or `enabled: false` captures every tick as
before; the shutdown observation is always taken. Stats: `skipped_ticks` and `capture_trigger` (counts by reason).
Defaults are untuned; the macOS probe (`ScreenshotSource.probe`) has not been run on a Mac yet.

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
is still stored. Both readers are called again on every capture. Accessibility keeps no state between captures. With
`ocr_isolation=inprocess` OCR runs on a worker thread (with the default `process` isolation Vision is in a killable worker
process instead: next section); a Vision request that times out can keep running, so it is parked and the next capture starts a new request
(at most two parked; then reads fail fast with `Timeout` until one returns). A stuck Vision request was measured at over
20 s on a loaded 8 GB Mac; before the retry change every capture in that time lost its text.

## OCR isolation: Vision in a killable worker process (`ocr_isolation`, default `process`)

Settings (store, with history): `ocr_isolation` (`process` | `inprocess`), `ocr_timeout_s` (default 10, 1 to 120),
`ocr_worker_max_rss_mb` (default 800, 200 to 16384), `ocr_worker_max_jobs` (default 500, 10 to 100000). Flags for one run:
`--ocr-isolation process|inprocess`, `--ocr-timeout S`. `inprocess` is the previous behaviour (`VisionOCR` on a thread) and
is kept as a fallback and for comparison. Stats: `runner_stats.json` has an `ocr_worker` object (below).

- **Shape.** `trace_v2/macos/ocr_client.py` `OcrWorkerClient` has the reader interface of `VisionOCR` (`version`
  `vision-ocr/1`, `read(screenshot, context) -> (raw, text)`), so `MacSources` and the pipeline are unchanged. The worker
  (`python -m trace_v2.macos.ocr_worker`, started with `sys.executable`, a subprocess with pipes, never fork) runs the same
  `VisionOCR`, so `raw`, `text`, languages and error classes are the in-process ones (tested equal on real PNGs, and the
  stored extraction row is equal). Screenshot bytes go over the pipe in length-prefixed frames
  (`ocr_protocol.py`); nothing is written to disk. The worker never opens the store or the data directory, its stdout and
  stderr are `/dev/null`, it logs nothing, and it only ever receives PNG bytes of a capture the gate already allowed. A
  withheld or failed screenshot never reaches it; redaction and `secret_on_screen` run in the capture process on the result,
  as before. `trace_v2.macos` now imports its adapters lazily (PEP 562) so that importing the worker or the client does not
  load AppKit, Quartz or the accessibility code.
- **Never blocks.** `read` returns or raises within `ocr_timeout_s` plus milliseconds in every state: starting, wedged, dead,
  garbage, a pipe nobody reads (the pipe write is on its own thread). Failure codes recorded in the extraction row:
  `failed`: `Timeout`, `WorkerCrashed`, `WorkerProtocolError`, `WorkerBusy`, `ImageTooLarge`, or the worker's own error class
  (`ValueError`, `RuntimeError`, ...). `unavailable`: `WorkerStarting` (warming up or being replaced), `WorkerCircuitOpen`,
  `WorkerClosed`, `ReaderUnavailable` (Vision cannot be imported). A failed OCR never stops the capture: the observation is
  stored with the failure, like every other reader failure. `skipped` stays what the pipeline already uses for "no
  screenshot".
- **Timeout and restart.** On timeout the worker is retired at once (the read returns), then killed (SIGTERM, SIGKILL after
  0.5 s, reaped; every wait bounded) and replaced on the supervisor thread. The next captures see `WorkerStarting` until
  the new worker is ready (about 1 s warm; cold start can be much longer, see limits).
- **Association and no duplicates.** The draft is built before `ingest` assigns the observation id, and OCR is read
  synchronously inside `build_draft` for that capture's screenshot, so the result is part of that draft and is stored with
  it; nothing is attached later by id. Inside the client each job has an increasing `job_id` and an idempotency key
  `sha256(png):captured_at_ns`; only a result carrying both for the one job in flight is accepted. A late, duplicate or
  foreign result (including anything from a killed or replaced worker, whose inbox is dropped with it) is discarded and
  counted in `late_results_discarded`. Reading the same key again returns the first result without a second Vision run
  (`idempotent_hits`, the last two keys are remembered).
- **Bounds.** Queue depth is **1**: one job at a time and no waiting line. The observer is single-threaded and a queued
  screenshot would be stale by the time it ran, so a second concurrent `read` is refused at once with `WorkerBusy`
  (`failed`, never queued). Bytes in flight are at most one screenshot, and a screenshot over 64 MB is refused
  (`ImageTooLarge`); a display-scope 1440x900@2x PNG is a few MB. The worker is replaced between jobs when its RSS
  (sampled with `ps` every 2 s on the supervisor thread, not on the capture path) exceeds `ocr_worker_max_rss_mb`, or after
  `ocr_worker_max_jobs` jobs.
- **Circuit breaker.** Unexpected deaths (crash, garbage, not ready within 180 s) back off 0.25 s doubling to 5 s; 5 of them
  within 60 s switch OCR off for 60 s (`WorkerCircuitOpen`, `circuit_opens` in stats) instead of a crash loop. Timeouts and
  planned restarts (memory, job count) do not count: five timeouts in a row are each recovered immediately.
- **Warm-up.** Inside the worker, asynchronously: it says `hello` at once, warms Vision up on an embedded image, then
  `ready`. No job is sent before `ready`, so the first capture is never delayed (`WorkerStarting` is recorded for captures
  that happen first).
- **Shutdown.** `close()` (run by `trace_v2.run` after the runner stops, so after the final observation): no new jobs, the
  one in flight gets up to 2 s, then `bye`, SIGTERM, SIGKILL; no worker is left. The worker also exits by itself when its
  parent dies, SIGKILL included: stdin reaching EOF ends it, and a watchdog thread that polls the parent pid every 0.5 s
  ends it even while Vision is stuck. It ignores SIGINT (the parent decides what Ctrl-C means).
- **Stats** (`ocr_worker`): `jobs`, `succeeded`, `reader_errors`, `timeouts`, `crashes`, `protocol_errors`, `spawns`,
  `restarts` by reason (`timeout`, `crash`, `protocol`, `rss`, `max_jobs`, `ready_timeout`), `busy_refused`,
  `not_ready_skipped`, `circuit_open_skipped`, `circuit_opens`, `late_results_discarded`, `idempotent_hits`, `too_large`,
  `queue_depth` (now) and `queue_depth_max` (1), `in_flight_bytes`, `peak_in_flight_bytes`, `peak_worker_rss_mb`,
  `last_ready_s`, and the current worker (pid, state, jobs, rss, age, warm-up seconds). Evidence tables are unchanged.

Limits, stated plainly:
- A worker that wedges inside Vision is killed and **loses that job**: that capture's OCR is `failed/Timeout` and the capture
  waited the full `ocr_timeout_s`. Captures keep their schedule only when the timeout is shorter than the interval; with
  the default 10 s and a 3 s interval a stall costs about three missed ticks once (counted, not replayed), not an
  unbounded wait, and the loop is then back on schedule.
- Cold start after every restart: Vision's first recognition has taken 66 s on a fresh machine (model compilation); a warm
  restart on this Mac takes about 1 s. While it warms up OCR is `unavailable`.
- A second process costs memory (about 100 to 200 MB resident for the worker) and a pipe copy of each screenshot
  (a few MB, milliseconds).
- RSS is sampled every 2 s, so a burst between samples can overshoot the limit briefly; it is not a hard cap.
- Test-only: the stalling worker used for the demonstration lives in `scripts/` and is selected only by the demo script
  passing its path to the client; nothing in `trace_v2` reads an environment variable or a flag to select it.

## Measured: in-process versus worker process (this Mac: 8 cores, 8 GB, load average 4 to 9 from other work)

`scripts/v2_measure_ocr_isolation.py run`: the real runner (`python -m trace_v2.run`, real sources, `front_app`, 3 s), temp
data dir deleted afterwards, runner and worker sampled with `ps` every 2 s, alternating runs of 2 minutes (the machine was
in normal use, so the screens and the load differ between runs; read the columns as ranges, not as a controlled test).
Capture latency = capture start to the later of the OCR and accessibility readers finishing (read back from the store).

| | in-process 1 | worker 1 | in-process 2 | worker 2 |
|---|---|---|---|---|
| observations (OCR attempted) | 41 (41) | 42 (39) | 41 (40) | 42 (22; 19 were a private window, withheld) |
| OCR success of attempted | 41/41 | 38/39 (1 `unavailable/WorkerStarting`, the first capture) | 40/40 | 21/22 (same) |
| missed ticks / failures | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| capture latency p50 / p95 / max | 646 / 870 / 1072 ms | 778 / 1570 / 1955 ms | 896 / 1464 / 2297 ms | 902 / 1247 / 1580 ms |
| OCR latency p50 / p95 / max | 197 / 224 / 394 ms | 277 / 691 / 1163 ms | 393 / 665 / 1124 ms | 498 / 740 / 846 ms |
| CPU s per capture, main / total | 0.44 / 0.44 | 0.20 / 0.60 | 0.91 / 0.91 | 0.14 / 0.68 |
| CPU, % of one core, total (both processes) | 14.6 | 20.2 | 29.3 | 23.0 |
| RSS median: main + worker = total | 161 + 0 = 161 MB | 46 + 92 = 156 MB | 144 + 0 = 144 MB | 52 + 60 = 111 MB |
| RSS peak: main / worker / total | 396 / 0 / 396 MB | 307 / 140 / 433 MB | 309 / 0 / 309 MB | 316 / 226 / 410 MB |
| worker ready after start | | 1.05 s | | 1.37 s |

Reading it: the CPU moves from the capture process to the worker (main 0.14 to 0.20 s per capture against 0.44 to 0.91
in-process); total CPU is the same order (0.60 to 0.68 against 0.44 to 0.91, inside the run-to-run noise of this loaded
machine, no clear cost). Total resident memory is about the same in steady state because Vision's frameworks live in the
worker instead of the capture process; the peak total is higher by the worker's peak. OCR latency and capture latency in
the worker runs are somewhat higher in the tails (a pipe copy of the PNG plus a loaded machine); no capture was missed.

**Forced stall** (`scripts/v2_measure_ocr_isolation.py stall`, real Vision in a worker that hangs on its first 3 jobs,
`scripts/v2_ocr_stalling_worker.py`, a rendered page as the screen, 90 s at 3 s):

| `ocr_timeout_s` | observations | OCR | missed ticks | longest gap between captures | worker |
|---|---|---|---|---|---|
| 10 (default) | 21 | 3 `failed/Timeout`, 18 succeeded | 9 (3 per stall) | 12 s (3 times) | 3 timeouts, 3 restarts, 0 crashes, 0 late results; the capture after each stall found a ready worker |
| 2 (shorter than the interval) | 31 | 3 `failed/Timeout`, 28 succeeded | **0** | 3.0 s (the schedule) | the same, recovered each time |

So a hang costs exactly one capture's wait, bounded by the timeout, and the schedule is only unaffected when the timeout is
shorter than the capture interval. The default (10 s) keeps OCR latency tolerance for a loaded machine (a stuck request has
been measured at over 20 s; normal reads here are 0.2 to 1.2 s); a timeout of about 2 s to 2.5 s would remove the missed
ticks at the cost of failing slow-but-healthy reads on a loaded machine.

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
hang for longer is not known. A killable worker process for Vision removes that risk; it is the default now (next section).

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

### Layout runs with Moments (G1)

`--moments` also starts the GSA-1 layout step, because Moments use the layout as a signal. `--structure` alone still
starts it without Moments.

## Why OCR has a queue, a worker and three modes (O2, decision)

Decision: keep all of it. The audit asked whether this is a component that only fixes a problem another component made
(OCR placed next to capture). It is kept because each piece does a separate job:

- **Durable job queue (`ocr_queue.py`).** OCR is slow (about 0.8 CPU-s a capture, stalls of 40 s measured) and capture must
  keep its tick. The queue lets capture finish first and survive a restart: jobs are retried, expire after an hour and are
  capped (`QueueFull`). It also carries the privacy scan: a screenshot sits in quarantine until the OCR secret scan has run,
  and removal of unscanned pixels is driven by queue state, so the queue cannot be dropped without redesigning that gate.
- **Killable worker process (`ocr_isolation=process`, default).** Vision can hang and cannot be interrupted from a thread.
  A separate process can be killed and restarted, and its memory growth is bounded by restarting it. This is what the app uses.
- **`sync` mode.** OCR inside the capture call. Used by one-shot runs (`--once`) and tests, where there is no queue worker.
- **`async` mode.** The queue plus worker described above. The app's mode.
- **`inprocess` isolation.** The previous behaviour (Vision on a thread). Kept as a fallback and for machines where the
  worker cannot start; it has no kill-on-stall, so it is not the default.

What would justify removing a piece: if OCR is ever moved off the capture path for good, `sync` and `inprocess` can go; the
queue would remain because of the privacy scan and restart safety. Not measured: how many people ever run `inprocess`.

## Model work only while you are away (V2 resource budget)

First Mac run (4 minutes, vision and activity model on): 12 of 14 screenshots timed out (about 18 s each), 72 ticks were
missed and the desktop lagged. The model had no idle rule, and the activity step had no gate at all. Now both the
description and the activity processors share one gate (`descriptions/gate.py`): they wait while the battery is under
20%, free memory is under 10%, or the last keyboard or mouse input was less than `min_idle_s` (60) seconds ago
(`ioreg -c IOHIDSystem`, no permission needed), or the screen changed in the last 60 s (the capture trigger's last
non-heartbeat capture), so reading or watching a video counts as working. Loaded models are released
after 2 minutes (`keep_alive`), not 10. Unreadable values never pause, so other platforms are unaffected.
Not yet confirmed: that the model was the cause of the timeouts; a run without model flags is the control.

### A running model call stops when you come back

The idle gate only stops a call from starting. `descriptions/call.py` also watches each model call while it runs: when
the keyboard or mouse is used or the screen changes (under 3 s, polled every second) it cuts the connection, Ollama stops generating, and
`Interrupted` is raised. It is a BaseException so it is not counted as a failed attempt and nothing is written; the Moment
is tried again the next time you are away. Embeddings run on the CPU, are short, and are not watched (no `ioreg` poll).

### Trim and migration backups are bounded

One retention run spends at most `TRIM_BUDGET_S` (2 s) trimming raw data; the rest waits for the next hourly run, so a
first run on a big database no longer holds up capture. A migration run writes one backup before its first pending
step (not one per step), so several new migrations on a large database cost one copy of disk, not several. A failed run
renames its backup to `.failed-vN.bak` (kept for inspection; under the old name it would block every later start);
a good run keeps only the newest `.pre-vN.bak`. Trim works in
batches of 100 rows.

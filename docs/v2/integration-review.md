# Trace V2: integration review of PRs A to D (done in PR E)

Scope: how the observation store (#57), context and screenshot (A, #58), Vision OCR (B, #59), accessibility (C, #60) and
input events (D, #61) behave together once a runner drives them. Each finding below has a fix in PR E and a test
(`tests/test_v2_integration_review.py` unless noted). Nothing outside `trace_v2/` was touched; no V1 or Legacy-V1 code or data.

## Findings and fixes

| # | Finding | Fix | Test |
|---|---|---|---|
| 1 | The gate checked `secure_input` before `screen_locked`. The lock screen turns secure keyboard entry on, so a locked screen was recorded as `secure_input`. | `privacy.gate` order is now `context_unavailable`, `screen_locked`, `excluded_app`, `private_window`, `excluded_site`, `secure_input`. A lock screen app in front while secure input is on also counts as locked even if the adapter did not flag it. | `GateOrder` |
| 2 | `InputTap.keyboard_blocked` was set only from the last capture's decision. The user can switch to a password manager and back between two captures (3 s), and those keystrokes were stored. Only keys were ever blocked, not clicks, scrolls or cursor positions. | New `inputs.InputGate`, asked by the tap for every event. It blocks all event types (click positions in a password manager are as sensitive as its keys). It is fed by (a) the capture's full decision, (b) `NSWorkspaceDidActivateApplication` (a built-in or user-excluded bundle id blocks at once, no capture needed), (c) lock notifications, (d) a front-app re-read every 5 s that repairs a lost notification. It starts blocked (fail closed) and treats an unknown front app as blocked. Drops are counted on the tap and, via `EventBuffer.note_dropped`, in the next observation's `event_stats.dropped_gated`. `keyboard_blocked` stays as an alias. The privacy policy is also reloaded while running (before, the runner would have ignored the user's excluded apps). | `InputGateRules`, `RunnerFeedsTheInputGate`, `test_v2_macos_input_gate.py` (real CGEvents) |
| 3 | Locked screen and events. | A withheld capture already discards its events and counts them (`discarded_privacy`) and `commit()` advances the window; verified by test. In addition the tap drops events from the lock notification on, so none is even buffered. | `LockedScreenEvents` |
| 4 | Sleep/wake. | The observer pauses (no capture, so no failed one; paused time is not a missed tick; the schedule restarts on resume, so nothing is replayed). The event buffer refuses and counts events while paused (`discarded_paused`); arrival decides, not the timestamp, because right after wake the input clock is stale until the wake handler re-syncs it. The handler re-syncs `MachClock` first, then accepts events, then resumes captures. The first window after a pause spans the gap (kept contiguous). | `SleepWake` |
| 5 | Shutdown. The D observer took its final observation only if events were pending. A stuck warm-up (up to 120 s) made SIGTERM wait. A failing tap thread could raise out of `_run` and leave `start()` waiting. A CF timer callback could let an exception escape. | `final_always` (the runner always takes one final observation unless paused). Warm-up runs on a thread and a stop request interrupts the wait. The tap thread catches everything, always cleans up and always sets `ready`; `_housekeeping` and every notification handler swallow and log. A dead observer thread is reported (`observer_error`, exit code 1) instead of hanging. `stop()` failures are logged and shutdown continues. | `Shutdown` |
| 5b | Lock file. | An `flock`, so a killed process never blocks the next start (tested with `kill -9`). Released by unlinking while still locked; acquire re-checks that the path is still the locked inode, closing the unlink race. | `InstanceLockRules` |
| 6 | Event attribution across the stack. | End-to-end test with the real buffer, fake clock and fake sources: events across a normal capture, a withheld one, a failed ingest, a gated-app stretch, a pause and the final observation. Every event is in exactly one observation or in a counted drop; windows contiguous. | `EventAttribution` |
| 7a | After one OCR `Timeout`, every following capture failed instantly ("a previous recognition is still running") until Vision returned. Measured: 5 captures in a row on a loaded Mac, then 15 s of nothing but `Timeout 0ms`. | A timed-out worker is parked and the next read starts a new request; at most two parked, then fail fast. See the OCR section below for what is still not solved. | `test_v2_macos_ocr.py`, `ReaderFailureAndRecovery` |
| 7b | The screenshot is the whole display containing the front window, but the gates judge only the front app and window (other windows and notification banners on that display were captured ungated). | `screenshot_scope` setting, default `front_app`; see `docs/v2/observations.md`. | `ScreenshotScope`, `test_v2_macos_scope.py` (real captures) |
| 7c | The pipeline hook and `MacSources.screenshot` had no way to say which scope a capture used. | `ScreenshotCapture.scope`, recorded in `metadata.privacy.screenshot_scope`; a new withheld reason `scope_unavailable` (fail closed). | `ScreenshotScope` |

Pre-existing flake, not changed: `test_v2_macos_input.RealTap.test_moves_click_and_scroll_arrive_sampled_...` posts
synthetic cursor moves and fails if the real user moves the mouse at the same moment (seen once, passed 3 of 3 on rerun).

## Screenshot scope result

Measured on this Mac (macOS 27, one 1440x900 display at 2x, ScreenCaptureKit):

- `front_app` (`SCContentFilter initWithDisplay:includingApplications:exceptingWindows:`): the PNG is still 2880x1800 and
  lossless. Everything that is not the front app's windows is fully transparent (alpha 0): the wallpaper, the menu bar,
  the Dock, other apps' windows, Notification Center. Real test: a magenta window of one process with a cyan window of
  another process floating over half of it: `display` scope contains both, `front_app` contains only the magenta one,
  including the part the cyan window covered.
- Notification banner: `osascript -e 'display notification ...'` (no permission prompt appeared) put a banner over the top right
  of the front app. It is in the `display` capture and absent from the `front_app` capture (Notification Center is a separate
  process, a layer-21 window). Not automated: whether a banner is shown depends on the user's notification settings.
- PNG size: see the end-to-end numbers in `docs/v2/runner.md`.

Fail closed: if the front app is not among the shareable applications (matched by pid, then a unique bundle id) or
ScreenCaptureKit is unavailable, the screenshot is withheld as `scope_unavailable`; no wider capture is ever tried.

Residual limits, stated plainly: other windows of the same app are included; a floating panel or picture-in-picture owned by the
front app is included; the front app's own sensitive content that no gate recognises is captured; a banner is excluded
only because it belongs to another process.

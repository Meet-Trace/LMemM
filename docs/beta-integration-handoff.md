# Trace beta integration — batch 1

Branch: `integration/beta`. This branch combines current shared main with selected page-note and naming fixes. App changes have not been merged into main. The approved website was published separately to main at `892cb77`.

## Included in this beta

- A page's notes remain available when its search/input field gains focus (#40 / f772e60).
- An unknown page gets a blank note card rather than stale notes from the previous same-title page.
- Open-note preference applies to actual web pages/stable sources, not reusable draft slots; a new email draft keeps its own identity.
- Terminal/Finder/document windows get useful names; a VS Code panel ID is not presented as a file (#38 subset / f77c881).
- Legacy terminal refs still resolve to their existing notes without bulk rewriting saved data.
- Grouping score/name ties are deterministic across process hash seeds (adapted from 68fa1d8).

The original checkout's experimental grouping changes were intentionally not carried over: they had mixed results on the reviewed real days. Embedding experiments and model-generated group names are also not enabled by this batch.

## Verification

- Clean baseline: 1,041 Python tests passed.
- Regression failures observed before fixes: page-note lookup, window naming, multiword project-name repeatability, legacy terminal lookup, stale-page suppression and reused-draft preference.
- Final app source: 1,056 Python tests passed with a temporary config data context; 15 browser/editor extension tests passed.
- Four identical-input rules runs on the user-reviewed 65 items match main's measured membership scores. This is a no-regression check, not proof of good grouping or blind accuracy.
- A fresh-context reviewer found two note-identity issues; both were reproduced and fixed. Follow-up review found no remaining blocker in this batch and independently passed 22 focused tests.
- `build_app.sh --standalone` succeeded. Strict ad hoc signature verification and the packaged diagnostics worker passed. A copied bundle also passed signature verification and diagnostics outside the checkout under a temporary path containing spaces. All packaged Python source modules were checked against the worktree. This is a local beta artifact, not a notarized public release.

## Permission recovery update (Nov 10)

- Onboarding now offers **Refresh status**, with visible checking feedback, and **Restart LMemM** when screen access is unavailable. Neither control grants permissions or marks them on optimistically.
- Permission reads are independent: a failed screen check cannot suppress the Accessibility result. Probe errors get a recovery hint instead of silently stopping all checks.
- 1,059 Python tests pass in temporary data, including three permission refresh regressions; the standalone build, strict signature verification and bundled diagnostics pass.
- Live Mac check: System Settings lists LMemM enabled for Screen Recording and Accessibility, but the running beta reports both unavailable even after restart and refresh. The UI refresh itself was verified through Checking → completed. No switches were changed automatically. The user must renew the LMemM grants (off/on, then Quit & Reopen). If that fails, remove the stale LMemM entry and add the exact current worktree `dist/LMemM.app` via the macOS picker. After the user toggled both entries off/on and the beta restarted, the mismatch remained. macOS TCC logs then confirmed `Failed to match existing code requirement for subject com.adityakhuntia.lmemm and service kTCCServiceScreenCapture`. The stale signing requirement is proven for Screen Recording; fresh removal/re-add of the exact beta is pending. Do not claim capture acceptance until the app detects the grants. Future local rebuilds may need renewed grants while ad hoc signing is used; stable distribution signing is a release requirement.

## Private-window safeguards (Nov 10)

- Browser privacy is verified before capture, quick identification, AX reads, event-trail reads and OCR fallback. Failed/unknown probes block reading instead of being treated as public.
- **If any window in a supported browser is incognito/private, the entire browser is excluded** until all its private windows close. This deliberately avoids mismatching a public AppleScript front window with a private native window selected for capture.
- Screenshots and quick OCR use the isolated native window ID, never whole-display fallback. Every full capture revalidates window identity and browser privacy before retention; switched contexts are dropped. Normalized content bounds now describe that isolated window.
- Native event-tap input from browsers is not queued, shortcuts are no longer retained by the native tap, and background click-target labels are disabled. Queued non-browser input is dropped at context/privacy transitions. Browser fallback URLs are never cached.
- Safari and other browsers whose window mode cannot be verified are currently excluded, including ordinary windows. This is a conservative beta limitation, not full browser compatibility.
- Review found and fixed background-window selection, queued input, and rejected-read URL-cache bypasses. All **1,072 Python tests** pass with temporary data. Browser-extension incognito exclusion was already present and unchanged.
- The two user-approved suspect Brave captures, their two derived items, and 31 associated trail events were removed locally. Notes were preserved. A subsequent scan found no remaining references to those capture/item IDs in installed JSON/JSONL data. Private content was not copied into this repo or pushed. Old metadata lacked a private-mode flag, so the two captures cannot be individually certified as incognito; cleanup was conservative.
- The beta was paused and then quit before cleanup/build. Leave it stopped until an explicit manual test. Rebuilding the ad hoc signed app may require renewing macOS permissions again. A live synthetic private/public-window acceptance test remains required; unit tests are not proof of macOS/browser behavior.

## Run and test

A local standalone artifact is in this worktree's `dist/LMemM.app`. It contains the code instead of pointing at the development checkout. Quit an existing LMemM instance before opening the beta so hotkeys/permissions and the running version are unambiguous. No existing app was stopped or replaced automatically.

The standalone app uses `~/Library/Application Support/LMemM/data`. Checkout data is separate and is not silently imported. Use a disposable beta project first; follow the existing installation runbook for any deliberate legacy-data import.

1. Create a note on a test web page, switch away/back, then focus the page's search field. The note should remain visible.
2. Open another page with the same window title but a different URL and no note. The old note must not follow, including before the next capture.
3. Mark a note done/reopen it; check the page again. Start a fresh email draft and verify an earlier draft's note does not take over.
4. Resize a terminal in the same folder. Its name/identity should stay stable and existing notes should remain reachable.
5. Check a Finder folder, Preview document and VS Code panel. Confirm ordinary capture, pause/resume, restart persistence and the retained-text viewer still work.

Report the behavior, which app/page was involved, and the branch/build used. Avoid including passwords or private message text in screenshots.

## App picture and next batches

Current main already contains normal capture/activity history, manual project management, rule-based project suggestions, opt-in scoped project Memory, retained sources/citations, local semantic retrieval, Resume from retained decisions/tasks, explicit browser saves and optional agent context sharing. The website is separate from the Mac app. Its signup form remains explicitly a preview until actual storage is configured.

- **AI grouping (#39 / 10edd91): evaluated in a separate source snapshot, not enabled here.** Current targeted suite: 83 tests, four MLX tests skipped, no failures. Newer work includes hard look-alike/pivot stories, independent held-out calibration and two-judge averaging. The earlier generic-title test failure is resolved by explicitly exempting three generic distractor windows; it is not evidence of improved real grouping. Actual v3/v4 trained adapters/GGUF builds and narrator are still unavailable in the inspected local model setup. Real-model comparison is still required.
- **Reader (#12):** adaptive AX/changed-region OCR remains a separate integration; requires Mac/Retina capture checks.
- **AI handoff (#31):** clipboard/panel/source-adapter work remains separate; requires real Mac hotkey/AX acceptance.
- **Labels/tasks/recall and alternative Resume (#24/#25/#26):** older overlapping branches; select wanted behavior rather than merging all history. Hosted Claude calls/writing capture need explicit product decisions before activation.

Preserve the frozen reviewed labels and the branch audit. Introduce one testable batch at a time; app-main publication follows beta acceptance and final validation.

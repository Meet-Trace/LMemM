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
- `build_app.sh --standalone` succeeded. Strict ad hoc signature verification and the packaged diagnostics worker passed. This is a local beta artifact, not a notarized public release.

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

# Work History from V2 Moments

The Work History page (src/history_model.py, brought from claude/project-thread-wn2d2u) shows V2's Moments when they
exist. `src/v2_history.py` reads them directly from the V2 store, read only: `observations.sqlite3` with
`derived.sqlite3` attached. No JSON files and no model are involved any more.

- Entry: a stable Moment (latest revision) with a description (`moment_descriptions`, status ok) and/or a clear purpose
  (`activity_interpretations`). Title is the purpose, the line below is the description. A Moment with neither is not shown.
- Activities: Moments that belong to the same non-merged Activity of two or more Moments share an index, and the page
  shows them as one entry with a count.
- Forgotten Moments are deleted from the derived file with their description and purpose, so they leave the page.
- The app is the one most of the Moment's observations came from.
- Data folder: `TRACE_V2_DATA_DIR`, else `~/Library/Application Support/Trace/V2`. When the runner uses another folder
  (`--data-dir`), start the app with `TRACE_V2_DATA_DIR` set to the same folder.
- Without a derived file, or with no described Moments, the page falls back to the rule-based Moments as before.

Not verified on a Mac: the page itself (AppKit); only the model, bridge and tests ran here.

### First open and caches

`read()` never waits, so the first call returns None while a background read runs. `v2_history.version()` counts finished
reads; the window's tick (a few times a second) redraws the History page once when it changes, so Moments appear without
a click. The per-Moment app-name cache drops Moments that are no longer shown (forgotten or merged).

### One command, live entries (2026-11-28)

`lmemm.py start` now starts Trace V2 itself (`python -m trace_v2.run --moments --vision-model $LMEMM_V2_VISION_MODEL`,
default `lfm3-vl:latest`, log in the V2 data dir as `runner.log`) and stops it on exit; the old app no longer captures
(`LMEMM_OWN_CAPTURE=1` brings that back). History shows every current Moment, stable or still growing: until the vision
model describes it, the title is the window title it was seen in most. Descriptions go newest first, so today's work is
described before an older backlog. When descriptions wait (battery, memory, you are working) the summary line says why.

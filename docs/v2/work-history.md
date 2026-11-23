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

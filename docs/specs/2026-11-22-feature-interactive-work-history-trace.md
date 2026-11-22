# Work History: your retained work, one click from the pill

## Goal

From the floating pill, click once, then **Explore work history →**, and browse what you worked on: today first, then
yesterday, this week, earlier, or any date. See it as a timeline across everything, grouped by project, or grouped by
app. Search it. Click any entry to inspect what Trace still holds for it (excerpts, notes, project, screenshot).
Close it and carry on. It is a view over the memory Trace already keeps, not a new store, and it shows only what still
exists.

## Behaviour

**Entry.** The pill's card (the one `Widget.toggle_card` opens) gets one quiet text row, **Explore work history →**,
next to the existing "Pause LMemM" row. It shows on the normal card, the empty card and the paused card. The pill's
size, marks, notes and keys do not change, and nothing new notifies. The row opens the main window on the history
page (`Tracker.open_window`, then `open_history`). Reading history never pauses, writes or interrupts capture.

**The page.** A new `history` view in the main window, in the same column and style as the project and thing pages:
- **Header:** "Work History", the selected day ("Today", "Yesterday", "Mon 17 Nov"), a search field, and a back link
  (⌘[ and Esc go back, as elsewhere). A quiet line under the title: "3 h 20 m · 6 things · 4 apps" for the day,
  built only from stored seconds.
- **Time:** Today (default), Yesterday, This week, Earlier, and previous/next-day arrows with a date picker. This week
  lists the last 7 days newest first, each day under its own heading. Earlier lists days that have history, newest
  first, loaded 7 days at a time with "Show earlier days". Days with nothing are skipped in Earlier and say "Nothing
  remembered for this day" when picked directly.
- **Modes:** a three-way switch, **Timeline | Projects | Apps**. The mode and the selected day persist while the window
  is open.
  - Timeline: entries in time order within a day (newest day first; within a day, earliest first, so the spine reads
    like a day).
  - Projects: one section per main project (and "No project"), each section's entries in time order. Sections are
    ordered by most recent entry.
  - Apps: the same, by app.
- **Entry:** thing name; project path if known; app; start–end clock time; duration only when the stored `seconds`
  is at least 60; and a short description only when the thing has a stored `doing` or kept AI summary line (the
  same lines the thing page shows). No description is made up. Entries are separated by a thin vertical spine with a
  small dot, no cards, no badges. Hover and selected rows get a soft fill.
- **Grouping:** consecutive stretches on the same thing are merged into one entry (times are first start to last end;
  duration is the sum). No grouping across different things in this version, so the individual tracked things always
  show.
- **Open an entry:** pressing it opens the existing thing page (`page_model.thing_page`), with Back returning to the
  history page, same day and mode, same scroll position. Nothing about that page changes.
- **Search:** typing in the history search box (⌘K focuses it) filters in place to things matching the text, across
  all retained history, not only the selected day. Each result is a row like an entry with its last-seen stamp. It
  matches, ignoring case and accents (`page_model.fold`): thing title, project names (and aliases), notes (open and
  done), and kept excerpt text. Nothing matches: "Nothing found for “x”". No model, no network.
- **Evidence honesty:** an entry for a thing whose screenshot is gone or whose excerpts are gone simply has no
  screenshot or excerpts on its page. Older entries show times, app, project and name only. Nothing says or implies
  more exists. The last 48 h are built from Moments (one entry per visit, visits under 30 s dropped, so tab switches do not
  clutter it); older time is built from sessions. The event trail is not used. Both join the thing record.
- **Error and empty states:** no sessions yet: "Nothing remembered yet. Your work will appear here as you go." A
  session file that cannot be read is skipped, and the rest still shows. A load failure never raises into the UI.
- **Loading:** one day (plus its session files) is read at a time; a day's rows are cached by session file
  modification time and the thing's `last_seen`, and re-read when either changes or the page opens.

## Design

- **`src/history_model.py` (new).** Plain functions, no UI, in the style of `page_model.py` and `window_model.py`,
  with a docstring of numbered rules H1…. This is where all behaviour lives.
  - `days(sessions_dir)`: day list from session file names (`YYYYMMDD-HHMMSS.json`), newest first, without opening
    files.
  - `day_events(sessions_dir, day, live_events=None)`: reads only the sessions that cover the day. A session starts
    at its id's date and time and continues until its last event; event times are clock-only (`HH:MM:SS`), so a clock
    that goes backwards means midnight passed and the next date applies. The running session is passed in as
    `live_events` (the tracker's `events`) because it is not on disk until saved.
  - `entries(reg, items, events, now, skip_apps)`: joins events to live items by `event["item"]`, merges adjacent
    stretches, and returns rows. The event's own `app` and `doing` are never displayed; the thing's current `title`,
    `app`, project path and summary are used (the session file outlives a Forget and holds those strings).
  - `view(reg, items, state, now, ...)`: `{"kind": "history", "title", "day", "mode", "groups", "more", "empty"}`.
  - `search(reg, items, q, now, skip_apps)`: described above; reuses `fold`, `title_of`, `_thing`.
  - Reuses `page_model` helpers (`_thing`, `ago`, `stamp`, `title_of`, `hue`, `plural`, `fold`) and
    `projects.path_names`, `store.duration`, `notes`. No second copy of any of them.
- **`src/page_model.py`.** Add the `history` view to `new_state`, `press` (actions `history`, `history_day`,
  `history_mode`, `history_more`; `thing` already records `back`, so Back returns to history with day and mode kept),
  and a `history` branch in `main()` that calls `history_model.view`. Opening a thing from history uses the existing
  `thing` press.
- **`src/window.py`.** Draw the `history` kind with the same helpers used for the project page: header, a segmented
  control for the mode, day arrows, rows with a spine. Add `open_history()` beside `open_settings()`. Colours are the
  existing dynamic ones, so light and dark follow the system. Fades follow `motion.py` (and the reduced-motion
  setting).
- **`src/widget.py`.** Add the row (next to `_pause_row`) and an `on_history` callback parameter. **`src/tracker.py`**
  passes `on_history=self.widget_history` which calls `open_window()` then `main_window.open_history()`. The tracker
  passes `sessions_dir`, the live events and the user's `skip_apps` into the page data. The tracking path is not
  touched.
- **Privacy filter in one place:** `history_model` drops any event whose item is missing (forgotten), whose app is in
  the user's `skip_apps` (`apps.clean`/`settings_model`), or whose item is hidden by the same rules the project page
  uses. Search uses the same filter, so it cannot reveal what the timeline hides. Pause needs no filter: nothing is
  recorded while paused, so nothing exists to show, and reading is allowed.
- **Not changed:** retention (`retention.py`), stores, moments, trail, `forget.py`, the pill's layout or size.

## Acceptance criteria

1. The pill card shows **Explore work history →** on the normal, empty and paused cards; pressing it opens the main
   window on the history page for today. The pill's size and every existing control are unchanged.
2. With a day of sessions, Timeline lists that day's merged entries in time order, each with name, app, time range,
   and project when known.
3. Duration appears only when the merged seconds are at least 60; otherwise no duration is shown, and a description
   appears only when the thing has a stored one.
4. Two adjacent stretches on the same thing show as one entry; two different things never merge.
5. Yesterday, This week, Earlier and the previous/next-day control each select the expected days; Earlier loads 7 days
   at a time and "Show earlier days" adds the next 7.
6. Projects mode groups by main project (plus "No project"), Apps mode groups by app; in both, entries within a group
   are chronological.
7. A session whose clock passes midnight puts its later events on the next date.
8. Forgetting a thing removes its entries from every mode and from search, even though the session file still
   references it. After Delete all my data, the history is empty and says so.
9. An app added to Never remember hides that app's past entries and search results.
10. An entry's text never comes from the event's stored `doing` or `app` strings, only from the live item.
11. Search matches thing titles, project names and aliases, notes and kept excerpts, ignoring case and accents, across
    all days; no match shows the empty message; the search code makes no network or model call.
12. Pressing an entry opens the existing thing page; Back returns to history with the same day, mode and scroll
    position. A thing page for an entry whose screenshot or excerpts have expired shows no screenshot or excerpts and
    no error.
13. Retention is unchanged, and history works with the 48 h moments and trail files absent.
14. An unreadable session file is skipped; the others still show. No sessions at all shows the empty message.
15. Opening or paging history takes under 150 ms for 1,000 session files and 50,000 things, and only reads the
    session files for the day shown. It never blocks the capture loop, which is not changed.
16. By hand on macOS, in light and dark: the pill grows nothing; the page is calm (no cards, no badges, hairline
    spine); hover and selected states are visible; ⌘[, Esc and the back link close it; the dock icon returns to the
    menu bar only once the window closes.

## Tests

- `tests/test_history_model.py` (new): criteria 2 to 11, 14 and 15 with temp session files and in-memory items;
  ordering and merging; midnight rollover; forget and skip-apps filters; stored-`doing` never shown; expired
  screenshot and excerpts; search matching including accents; pagination reads only the needed files (spy on file
  opens).
- `tests/test_page_model.py`: `press` for `history`, `history_day`, `history_mode`, `history_more`; `thing` then
  `back` returns to history with day and mode kept; a deleted thing in `back` falls back sensibly.
- `tests/test_scale.py`: add a history case at the sizes in criterion 15.
- `tests/window_smoke.py`: draw the history page in each mode, empty and with entries, and the pill card row.
- `tests/test_objc_names.py` stays green with the new selectors.

## Out of scope

- Grouping different things into one activity (no use of `activity_*` inference in this version), moments, the event
  trail, and any new store, index or LLM search.
- Window titles beyond a thing's stored title, and full-text search over screenshots or OCR.
- Changes to retention, forget, rules, tracking, the pill's size or look, notifications, or the existing thing and
  project pages.
- Exporting history, charts, tags, a separate floating history panel.
- Automated visual verification: the light and dark check (criterion 16) is done by a person on a Mac.

## Assumptions

- "Panel" means a new page in the main window, using the existing page architecture, not a new floating window.
  Opening it brings the window forward (and the Dock icon appears while it is open), which the window already does.
- Session files are the long-lived source for time; the event's `seconds` is the duration, trusted at 60 s or more.
- Days derive from the session id plus clock rollover, since events store clock time only.
- Search is over the live item record (title, notes, kept excerpts, project names), not the semantic index; window
  titles are not stored separately from the title.
- Watched-app rules apply at capture, so past things from an app later removed from the watch list stay visible;
  only the Never-remember list hides past history.
- Each day starts with the earliest entry first, newest day first; Projects and Apps sections order by their most
  recent entry.
- Staging: the whole spec is one change. If it must be split, ship the pill row, Timeline and thing links first, then
  Projects, Apps and search.

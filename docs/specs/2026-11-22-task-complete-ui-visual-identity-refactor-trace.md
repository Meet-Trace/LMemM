# UI visual identity refactor: make the app look like hey-trace.app

Issue #79. Presentation only. Capture, tracking, storage, memory inference, the data model, shortcuts and window
behaviour do not change.

## Goal

Someone who moves between hey-trace.app and the Trace macOS app should recognise one product. Today they do not:
the site is a warm ivory page (`#f4eddf`) set in Instrument Serif and Figtree, with one deep blue accent. The app is
a graphite window with Figtree only, a different blue, and colours and fonts spread over the UI files. This change
gives the app one set of design tokens taken from `trace-landing/hero.css`, a small set of shared primitives built on
them, and applies both to every surface: the pill and its cards, the main window, Home, projects, Memory, Resume,
sources, notes, search, Settings, setup, dialogs, toast and the empty, loading, error and disabled states. The mood is
calm, quiet and human, not a SaaS dashboard.

## Reference (what the site actually uses)

The source of truth is `trace-landing/hero.css` and `index.html`, not the live site, which the pipeline may not reach.
If the live site can be fetched, check it against these values and write any difference in the PR.

| Token | Site value |
|---|---|
| canvas / neutral | `#f4eddf` / `#eae7e1` |
| ink / muted / soft | `#191a20` / `#4a4d58` / `#6b6e79` |
| accent / accent-deep | `#2856d9` / `#1f43b0` (accent words and links use the deep one) |
| error | `#b3382c` |
| success tint | `rgba(58,143,90,.14)` on text `#2f7a4c` |
| Display type | Instrument Serif 400, tight tracking (-0.025 to -0.035 em), the key word in *italic accent blue* |
| UI type | Figtree 400 / 500 / 600; 13 px notes, 15 px nav, 17 px body |
| Mono | SF Mono / Menlo (file names, URLs, code) |
| Surfaces | paper `#fbfaf6` or white on ivory, a 1 px hairline `rgba(25,26,32,.07-.1)`, soft cool shadow `rgba(42,54,94,.12)`; radii 5 / 8 / 14 / full |
| Highlight | evidence is marked with `rgba(40,86,217,.16)` and a 2 px blue underline |
| Controls | tab group is a full-radius track `rgba(25,26,32,.055)` with a white selected pill; links get a blue underline on hover |
| Motion | `cubic-bezier(.2,.7,.1,1)`, 0.3-0.5 s for hover and state changes; reduced motion switches it off |

Trace's concepts map to fixed visuals (the "grammar" in section Design, step 2).

## Behaviour

- **Appearance.** The window follows the Mac's appearance (`config.WINDOW_APPEARANCE = "system"`). Light is the
  site's ivory. Dark is the existing graphite treatment retuned to the same accent and type. The pill and note card keep
  their light-on-dark / dark-on-light inversion. The window must be readable and complete in both.
- **Type.** Page titles, the project name, Home's greeting and empty-state headings use Instrument Serif, with
  at most one italic accent word where a phrase has an obvious key word. Everything else is Figtree. File names, URLs
  and code use the monospace face. No heading above 34 pt in the window (desktop density); the pill stays at its
  current sizes.
- **Concepts look the same everywhere.**
  - *Project*: a name in serif or semibold with a small accent dot (the site's brand dot), never a folder icon.
  - *Note*: a paper surface, a round tick (blue when done), text in Figtree.
  - *Decision*: a note-like surface with a small green "Decided" tag (site success tint). Same surface wherever it shows.
  - *Source / evidence*: a mono line (file, URL or app), an app icon where there is one, and the quoted text marked
    with the blue highlight and underline. Opening one behaves as it does today.
  - *Activity / memory row*: a quiet row with a time in muted text, no card around each row.
  - *Resume / next step*: "Pick up where you left off" items use the note surface, with an accent arrow on the action.
- **Controls.** One primary button (accent fill, white text), one secondary (hairline outline), one text link (deep
  accent, underline on hover). Segmented tabs and the period switch use the site's track-and-pill style; the selected
  one is filled everywhere (earlier audit rule). Search and text fields share one style: hairline, accent focus ring.
- **States, all in the same voice.**
  - *Empty*: a serif heading saying what will appear and how it gets there (for example, "Your projects will gather
    here as you work"), one line of muted text, and the one useful action if there is one. No illustration and no
    sample data.
  - *Loading*: the existing wording ("Enabling Memory...", "Getting memory ready") in muted text with the existing
    spinner; no new placeholders.
  - *Error and disabled*: the error colour with an icon and words (never colour alone); disabled controls are dimmed
    and keep their label.
- **Pill.** Same size, position, hover and animation as now. Its colours, type, radii and the accent come from the
  tokens; its expanded card and action buttons use the shared button and note styles.
- **Unchanged.** Keyboard shortcuts, Tab and focus rings, VoiceOver labels, click targets (24 pt minimum), reduced
  motion, and what each control does. The window keeps its minimum size and lays out correctly from the minimum up to
  a large display.

## Design

Keep one design system and extend it; do not add a second one.

1. **Tokens in `src/palette.py`** (plain numbers, no UI, still testable on any machine).
   - Add a light treatment `"ivory"` to `THEMES` next to `a` and `b` using the site's colours, with the same keys
     (`canvas, sidebar, surface, inset, edge, ink, mute`) plus new keys `soft`, `accent`, `accent_text`, `error`,
     `success`, `success_text`. Add the same new keys to `a` and `b`, with dark-mode values that pass contrast.
   - Update `ACCENT` to the site's `#2856d9` and `ACCENT_TEXT_ON_LIGHT` to `#1f43b0` (`ACCENT_TEXT_ON_DARK` stays
     light blue). `use()` switches treatment; the window chooses `ivory` or the dark theme (`PAGE_THEME`) from the
     effective appearance, and re-applies when the Mac's appearance changes.
   - Add non-colour tokens in the same file or a small `src/tokens.py` if `palette.py` would become unwieldy:
     spacing scale (4, 8, 12, 16, 24, 32, 48), radii (5, 8, 14, full), sidebar width, content max width, type scale
     (display 34 / 26 / 20, body 15, label 13, meta 11), tracking, and the motion curve and durations. `motion.py` reads
     its curve and durations from there.
2. **Primitives in `src/setup_kit.py`** (it already holds `font`, `surface`, `button`, `chip`, `note`, `glyph`, `rule`,
   `display`, `meta`, `body`). Add what is missing: `display()` in the serif, `mono()`, `tag(kind)` for Decided / status,
   `segmented()` for tabs and periods, `field()` for inputs and search, `empty_state()`, `source_line()` (mono line with
   mark) and the highlight run for evidence. Existing functions keep their signatures so callers do not break;
   `widget.py` and `window.py` call these rather than drawing their own.
3. **Fonts.** Add Instrument Serif (Regular and Italic, SIL OFL, already used by the site) to `assets/fonts/` with its
   licence text next to `OFL.txt`; register it in `setup_kit.register_fonts()` like Figtree. If a face is missing,
   fall back to the system serif (`New York`, then Georgia), never to a broken glyph. No new Python dependency.
4. **Surfaces**, one after another, replacing literal colours, fonts, radii and paddings with tokens and primitives:
   - `window.py`: shell, sidebar, banner, Home, project page, thing page, search, Settings, dialogs, scrim, toast,
     and the light-mode backdrop (the faint warm radial of the site replaces theme b's blue glow in ivory; no gradients
     elsewhere).
   - `memory_pane.py`, `memory_view.py`, `memory_model.py`'s presentation helpers only: Memory, Resume, evidence cards
     and the folded "Connections and setup".
   - `widget.py`: pill, pill card, action buttons, celebration.
   - `notecard.py`: note creation card.
   - `onboarding_ui.py`, `setup_kit.py`: setup flow.
   - `menubar.py`: only fonts and colours it already draws; menu structure unchanged.
   - `config.py`: `WINDOW_APPEARANCE = "system"`. (`PAGE_THEME` stays for the dark treatment.)
5. **Hard-coded values.** `grep` for colour, font and radius literals in the files above should find them only in
   `palette.py` / `tokens.py` / `setup_kit.py`. The system semantic colours may remain where the OS should control them
   (for example the system focus ring).
6. **Do not touch** `tracker.py`, `activity*.py`, `project_*.py`, `store.py`, `memory_client.py`, `memory_controller.py`
   logic, `input_*`, `trail_*`, `retention.py`, `forget.py`, `understand.py`, or `trace-landing/`. The app must not fetch
   the site, its fonts or its images at run time.

Order of work, each step reviewable on its own and leaving the app working: (1) tokens, ivory theme and fonts;
(2) primitives; (3) window shell, sidebar, Home; (4) project, thing, search, Memory, Resume, notes; (5) pill and note
card; (6) Settings, setup, dialogs, toast and every empty, loading and error state; (7) sweep for leftover literals.

## Acceptance criteria

1. `palette.THEMES` has `a`, `b` and `ivory`, each with the same keys; `ivory`'s `canvas`, `ink`, `mute` and `soft`
   equal the site's `#f4eddf`, `#191a20`, `#4a4d58`, `#6b6e79` (within 1/255), and `ACCENT` equals `#2856d9`.
2. Contrast holds in all three treatments: `ink` and `mute` on `canvas`, `sidebar`, `surface` and `inset` at 7:1 and
   4.5:1 (ink 7:1 is relaxed to 4.5:1 in `ivory` only if the site's own ink cannot reach it, and a comment says why);
   accent text, error text and success text on those grounds at 4.5:1; white on `ACCENT` at 4.5:1; every pre-existing
   contrast test still passes.
3. With the Mac in light mode the main window is ivory with ink text, and in dark mode it is graphite; changing the
   Mac's appearance while the window is open updates it without a restart. `WINDOW_APPEARANCE` is `"system"`.
4. Page titles, the project name and empty-state headings in the window are Instrument Serif; body and labels are
   Figtree; file names, URLs and code are monospace. No window heading is larger than 34 pt. If Instrument Serif is not
   registered, the system serif is used and the window still draws.
5. Project, note, decision, source, activity row and Resume item each have one visual treatment, drawn by one shared
   primitive, used on every screen where that concept appears (Home, project page, thing page, search results, Memory,
   Resume). A reviewer can find no second implementation of the same component.
6. Buttons, segmented tabs, fields, tags and popovers each come from one primitive. Selected tab and period are
   filled in every place they appear. Every field shows the accent focus ring while focused.
7. Every empty state listed in Behaviour (no projects, no notes, no memory yet, no search results, no sources,
   Memory off) shows a serif heading, one muted line saying what will appear and how, and the existing action if any.
   No sample data appears anywhere.
8. Error and disabled states show an icon and words, not colour alone, and disabled controls keep their label.
9. The pill keeps its size, position, hover, shortcut-open and reduced-motion behaviour; its colours, fonts and radii
   come from the tokens; its card buttons use the shared button primitive. Pill and note card remain readable in
   light and dark (existing contrast tests pass).
10. A search of `src/window.py`, `src/memory_pane.py`, `src/widget.py`, `src/notecard.py`, `src/onboarding_ui.py` and
    `src/menubar.py` finds no literal RGB colour tuples, `colorWithRed`/`colorWithWhite` calls or numeric font sizes
    outside token use, apart from a short listed allowlist (system semantic colours) in the test.
11. Existing shortcuts, Tab order, VoiceOver labels, 24 pt targets and the keyboard behaviour of rows are unchanged
    (existing tests pass; no test is deleted or loosened except where a value is the old accent or the old
    dark-only appearance, with a comment).
12. The app never makes a network request for design assets; fonts and images load from `assets/`.
13. Capture, storage, inference, controller and model logic have no diff other than presentation helpers named above
    (`git diff --stat` shows none of the files in "Do not touch").
14. On a Mac: the app is run and these are captured in light and dark at the minimum window size and at about
    1440 x 900: pill (resting, expanded, card), Home, a project, a thing, search, Memory with and without data,
    Resume, a note and note creation, Settings, a dialog, the toast, setup, and each empty state. The PR lists which
    were captured and which were not, and compares at least Home and the pill to the site's hero.
15. The full test suite passes.

## Tests

- `tests/test_palette.py`: add `ivory` to the theme loops; replace `test_graphite_is_neutral_not_brown_and_not_navy`
  so it covers only the dark themes and add one asserting `ivory` is a warm light ground (`r >= g >= b`, `r > 0.9`);
  add tests for the site-value equality (criterion 1), the new keys' contrast (criterion 2), and `use("ivory")`.
- `tests/test_tokens.py` (new, or in `test_palette.py` if tokens stay there): spacing, radii and type scales are
  ascending; every window heading size is at most 34; motion durations are positive and `motion.reduced()` still drops
  scale and push.
- `tests/test_setup_assets.py`: Instrument Serif files and licence exist and are non-trivial; `font()` falls back
  without raising when a face is missing; the accent decision recorded there is updated to `#2856d9`.
- `tests/test_ui_tokens_only.py` (new): scans the six UI files for literal colour and font-size use against the
  allowlist (criterion 10), and asserts no UI file contains `http://` or `https://` font or image URLs (criterion 12).
- `tests/test_window_smoke.py` and `tests/test_window_shell.py`: draw the window in light and dark and at the minimum
  and a large size against the fake AppKit; draw each empty state; no Python errors.
- `tests/test_memory_view.py`: update for the shared primitives; the presentation helper outputs it checks are the
  same words as now.
- Run `tests/test_window_model.py`, `test_page_model.py` and `test_onboarding.py` unchanged to show behaviour is the same.

## Out of scope

- Any change to capture, tracking, storage, inference, the data model, retention, forgetting or the memory controller.
- The website itself, and the website as a runtime dependency (fonts, images, copy fetched at run time).
- New features, new screens, new settings, sample or placeholder data, illustrations, the site's hero film, grain
  texture or brain artwork.
- Re-laying out the window's structure (sidebar plus page, and what each page shows), or changing shortcuts.
- Pill mechanics from the audit's "Not done yet" list (capsule layer, waveform on Core Animation, SF Symbols
  conversion, tab through rows). Glyph icons stay as they are unless a primitive already replaces them.
- A user-facing theme picker. Light and dark follow the Mac.

## Assumptions

- **The site is light, so the window gets a light treatment.** Triage said not to add a light window theme unless the
  site has one. It does: ivory. So the window follows the system, with ivory for light and the existing graphite for
  dark. This reverses the earlier "dark-only window" decision in `config.py`; the requester asked for both appearances
  to be checked.
- Instrument Serif is bundled (OFL, already in use on the site). It is not in the repo today; the implement agent adds
  the Regular and Italic files and licence from the upstream release. If that cannot be downloaded, use the system serif
  and say so in the PR; the spec's criteria still hold.
- `trace-landing/` is the design reference. The live site was not inspected from this stage, so the table above comes
  from the repo source and may differ from what is live.
- The site's accent is used for the whole window, replacing `#3563E9`. This supersedes the "accent stays blue"
  note in `2026-10-14-ui-audit-fixes.md` (still blue, now the site's blue).
- Hairline widths, shadows and radii are adapted for the desktop: 5 / 8 / 14 pt radii, at most one soft shadow
  on floating things (dialog, toast, pill card), none on in-page surfaces.
- This is one large task. If it cannot all be done in one run, steps 1-3 of the order above are the minimum
  shippable result, and the PR must state which later steps remain.
- Screenshots need a Mac session. If a surface was not run on a Mac, the PR says so; it must not claim it was checked.

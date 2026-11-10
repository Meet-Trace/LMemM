# ADR 0001: One Observation record for both readers (Step 1 of 3)

Status: proposed, implemented in the PR that adds this file.
Scope: evidence collection only. Activity inference (step 2) and the Task/Activity memory layers (step 3) are
not part of this change. The project finder is not touched.

## Context (confirmed in the code)

- Two readers describe the same screen and never meet: the screenshot tracker (`resolver` OCR ->
  `memory_content.extract_content` -> `memory.json` excerpts) and the accessibility trail
  (`trail_engine.read_text` -> daily JSONL). Nothing but `chat_place` (exact chat names) crosses over.
- Stored screen text is OCR only. OCR garbles small text ("Partner$hip Lead$"), and a garbled line is stored as
  if it were fact. Repeats of the same line with different noise are stored as separate lines.
- On the author's Mac the trail currently reads nothing (`accessibility: false` in `trail/.status.json`;
  gaps are `no_accessibility`), so exact text never exists there. The change must work with and without it.

## Decision

Add `src/observation.py` (pure Python, no macOS imports) with one record both readers write and one function
that merges records of the same place.

### Schema

```
Line         text, source ("ax" | "ocr"), conf (the reader's own extraction confidence; None for "ax": the
             accessibility tree reports none), kind, box, in_window (ocr only)
Observation  id, t (wall clock), reader ("tracker" | "trail"), app, bundle_id, pid, window_id, window, url,
             lines [Line], interaction {key/scroll/click ages, pointer; never characters}, evidence_ref
MergedLine   text (original, never normalised), source (primary reader), conf, sources [every reader that saw it],
             agreement ("single" | "agreed" | "differed"), variants [{text, source, conf}]
```

- **Source, confidence and agreement are three separate facts.** `source` is who read the line. `conf` is how sure
  that reader was (OCR reports one; accessibility does not, so it is `None`, never 1.0). `agreement` says whether a
  second reader saw the same line: `single`, `agreed` (same line once case, punctuation and look-alike characters
  such as `$`/`s` are ignored) or `differed` (similar, not the same). Accessibility is a source, not a guarantee of
  correctness, and nothing here says the text is relevant; that stays with the existing filters and later steps.
- Source hierarchy implemented now: `ax` over `ocr`. Native APIs and browser DOM are named but have no reader yet.
  File-system and terminal collection are out of scope. `file_path` / `document_id` are left out until a reader can fill them.

### Merge rule (conservative)

1. **Same place only.** Same bundle id, process id, OS window id when both known, same URL (fragment included when
   both have one), compatible window title, read within `OBS_MAX_AGE` seconds. Two windows of one app that share a
   title and have no URL cannot be told apart by metadata, so rule 2 decides.
2. **The lines must agree.** An app read is used only if at least `OBS_MIN_AGREE` of the OCR's substantial lines
   (8+ characters, inside the window), and at least `OBS_MIN_SHARE` of them, match it. Otherwise the app read is
   ignored entirely and the OCR is used alone (reported as `gate: disagrees` or `no_app_text`).
3. **Matching compares a normalised copy; originals are never modified.** An OCR line matches an app line when
   they are equal after normalising, when the OCR line is a wrapped piece of one app line, when it spans two or more
   app lines, or when they are 0.82+ similar.
4. **Ambiguity means no replacement.** A line that fits two app lines, or a piece contained in two, is left as OCR.
   A similar-but-not-equal match must be one-to-one: an app line claimed by two OCR lines, or already read exactly
   by another OCR line, is not used for the fuzzy match.
5. Where both readers saw a line, the app's text is the line and the OCR original is kept as a variant (capped).
   OCR lines nothing matched are kept as read, `single`. Repeats are not collapsed among OCR lines (see below).

### ObservationLog lifecycle

In memory only (no raw observation is persisted in this change). At most `OBS_LOG_SIZE` entries; entries older than
`OBS_RETENTION` seconds before the newest are dropped; observations may arrive in any order and are kept sorted by
time; an exact repeat of a read already held (same reader, place and lines) only refreshes its time; one lock guards
every operation, and observations are treated as read-only once added. The log ends with the process and is cleared
on shutdown. Privacy skips (private windows, password managers, secure fields) happen before anything is added.

### Who uses it

- Trail: `Engine.read_text` builds an Observation (ax lines, or ocr lines when it falls back to a screenshot, with
  their confidences) from the secrets-filtered lines, adds it to the shared log; its `text` event carries the id.
- Tracker: after OCR, `memory_content.screen_content` shares its own observation in the log, finds the trail's read
  of the same place, and builds the excerpt from the merge. With no trail read it is OCR alone.

## Migration and compatibility

- `memory.json` keeps its shape. Excerpts gain two optional keys: `provenance` (`{"ax_only", "ocr_only", "agreed",
  "differed"}` line counts) and `variants` (the OCR readings of lines the app text replaced). Old excerpts lack
  them; every reader of excerpts (`page_model`, `context`, `favicons`, `project_reasoner`, `input_store`) reads only
  `text`, `source`, `last_seen`, `decision_quotes`. The project finder is not touched.
- **OCR-only output is unchanged.** With no app read, `extract_content` returns the same text, quotes and source as
  before (a test compares it with a copy of the previous function over 400 random screens). Lines are not
  collapsed beyond the exact-text de-duplication that already existed; a case or punctuation variant is still a
  separate line. The only addition is the `provenance` key.
- With an app read, the stored text can differ from before by design: matched lines carry the app's text.
- Trail JSONL: `text` events keep `added`/`removed`/`total` and gain `observation` (id). `trail_cli` is unaffected.
- Capture metadata (`data/<ts>.json`) gains `pid` and `win_id` (window identity for matching).
- Rollback: `config.OBSERVATION_MERGE = False` skips the merge and the log entirely.
- An opt-in evaluation log (`EVIDENCE_LOG`, off by default) writes before/after excerpt text per frame to
  `memory/evidence_eval.jsonl` (owner-only, 20 MB cap). Delete it when done.

## Evaluation

`scripts/eval_evidence.py`: `--memory` (health of stored OCR text), `--log` (paired before/after from the opt-in
log), `--simulate` (matching safety on real pages with simulated OCR noise and a simulated app read, including an
app read from a different page with the same title). Without accessibility, a paired log shows no gain, and the
script says so.

## Alternatives

- Merge inside `memory_content` only: smaller, but the trail would stay disconnected. Rejected.
- Persist every Observation: more storage and privacy surface; retrieval of originals belongs to step 3.
- Run OCR only on regions AX misses: needs an AX line-to-box mapping the tree does not give reliably. Later.

## Consequences

- Gain depends on accessibility access. Without it the only changes are de-duplication and honest `unverified` labels.
- Matching is heuristic (an app read up to `OBS_MAX_AGE` seconds old can replace a changed OCR line, e.g. after a scroll); a wrong match could replace a correct OCR line with a different AX line. The 0.82
  threshold and the same-place rule are tested, and variants keep the original.

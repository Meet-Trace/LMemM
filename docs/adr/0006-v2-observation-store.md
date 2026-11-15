# ADR 0006: Trace V2 Observation store

Status: proposed (2026-11-15). Scope: `trace_v2/observations/` only. Supersedes nothing in V1; V1 code is untouched.

## Context
Trace V2 is built from scratch. V1 data was archived unchanged to `~/Library/Application Support/Trace/Legacy-V1`.
V2 needs a foundation layer that records, continuously and without interpretation, what was on screen and how
the user interacted, so that later layers (Moments, Visits, Activities, Projects) can be built and rebuilt on top
of it. In V1 "Observation" meant an in-memory merge of OCR and accessibility text (ADR 0001) that was never
persisted. V2 uses the word for a different thing: one persisted snapshot per capture.

## Decision
- **One Observation per capture.** It unifies context (application, window, display), screenshot, OCR output,
  accessibility output, interaction events and capture metadata. OCR and accessibility are evidence *of* the
  Observation (`extractions` rows), never Observations of their own.
- **SQLite** (stdlib `sqlite3`, the technology already used by `semantic_memory`), one file
  `<data>/observations.sqlite3`, WAL journal, `synchronous=FULL`, foreign keys on, owner-only file.
- **Screenshots as files** under `<data>/observation-assets/screenshots/YYYY/MM/DD/<observation_id>.<ext>`,
  stored byte-for-byte, with path, format, dimensions, size and sha256 in the database.
- **Append-only by triggers.** UPDATE and DELETE are aborted on every evidence table. Reader re-runs append
  revision n+1 (`supersedes` points at revision n, checked by a trigger); revision 1 is the original.
- **Screenshot retention deletes files only.** The deletion is recorded in `screenshot_deletions`; the
  screenshot metadata row and all structured evidence stay. Default policy: permanent.
- **Migrations** are numbered SQL files applied with `PRAGMA user_version` plus a `schema_migrations` table holding
  each file's sha256, one transaction per migration, with an online backup before migrating a non-empty database.
- **Raw interaction events**, one row each, attributed to the Observation whose capture closes the interval they
  occurred in. Keypresses store a key class, never the character or keycode.
- **UUID v4** identifiers for observations, extractions, events and retention runs. Duplicate prevention uses a
  caller-supplied `capture_key` (UNIQUE) plus a content hash, not content equality, because identical screens at
  different times are legitimately separate Observations.

## Alternatives considered
- *Separate Observation per reader* (my earlier v2 proposal): rejected by the agreed design; one capture, one record.
- *Mutable `status` column on screenshots for expiry*: simpler, but makes the screenshots table updatable.
  A separate append-only deletion table keeps every evidence table strictly immutable.
- *JSONL files per day*: no referential integrity, no transactional multi-part write, slower retrieval by id.
- *UUID v7* (time-ordered): not in the Python 3.13 standard library; time order is served by an index instead.
- *Content-addressed screenshots*: would de-duplicate identical screens but couples deletion of one Observation's
  image to others. Rejected for now.

## Consequences
- Structured evidence currently cannot be deleted at all. A user "forget" (the right to erase, which V1
  `forget.py` offers) needs its own explicit, audited mechanism in a later migration. **Open privacy item.**
- Raw OCR and accessibility payloads are stored whole (compressed). Storage grows with capture rate; there is no
  structured-data retention yet, by design (screenshot and structured retention are independent).
- Privacy gates (excluded apps, private windows, secure fields, secret redaction) must run in the capture
  adapters *before* ingestion. The store records what it is given.
- The macOS capture adapters (screenshot, Vision OCR, accessibility tree, event tap) are not part of this change;
  the pipeline takes them as injected sources.

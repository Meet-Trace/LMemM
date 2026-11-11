# ADR 0004: Activity features and evidence (Step 2, PR 2B)

Status: proposed. Builds on ADR 0003 (the merged design and PR 2A). PR 2B adds **no scoring, assignment, clustering or inference**: it turns `activity_input.Prepared` into per-visit features and pair evidence, so PR 2C does not parse raw Moments again. No model, no CPU judge, no I/O, no change to the Moment or Observation schemas, the project finder, the tracker or `MOMENT_LOG`.

    Prepared -> activity_features.extract() -> FeatureSet -> activity_evidence.build_evidence() -> EvidenceSet
    activity_evidence.analyse(prepared, params, replay_params) returns both.

## Inputs and limits
Only fields already in a Moment are read: `item_id, app, bundle_id, kind, window, url, target, n_frames, provenance` (frame counts), `digest.terms/identifiers/lines` and timestamps. `interaction`, `context`, `obs_id`, `ref` and `digest.text_hash` are never read (a test changes them and requires identical output). Query strings and fragments are dropped again before any use. Whole quoted lines are never carried, only their words at half weight.

## Features (per visit; `VisitFeatures`)
- **artifacts**: `item` (the item id), `page`/`document` (host + URL path), `file` (name, folder only when the target holds one or the window names the workspace), `pr`/`issue` (repository + number, from the URL or a title such as "Pull Request #12 · owner/repo"), `chat`. Each has `id_bearing` and `hub_pattern`.
- **namespaces**: `domain`, `repo`, `folder`, `locator` (host + first path segment).
- **branch** `{name, repo}`: from the exact_links rules (terminal title or output, PR page "x into y"), a `/tree/<branch>` page, or a "(branch)" suffix on a workspace. `repo` is known only from a git-host URL or the workspace name; a terminal's repository is not observable.
- **lineage** `{stem, ext, scope, name}`: version chain key using `exact_links.stem`.
- **bag** `(token, weight)` per design 14.3: terms/identifiers/title/target/URL-path words 1.0, line words 0.5, times q(V); hub-like tokens dropped. `key_tokens`: tokens that come from the artifact key (so scoring can remove them). `identifiers`.
- **quality** `{q, source, frames, digest_empty, tokens, content}`: q = 1.0 (AX present) or 0.8 (OCR only), x0.6 for one frame, 0.5 for an empty digest.
- **glance** (`dwell_s < glance_dwell_s`), **substance** `{ok, reasons}` (`glance, short_dwell, few_frames, empty_digest`; unknown kinds need `unknown_app_seed_dwell_s`), **weak_evidence** (empty digest and no identifying artifact other than the item).
- **internal_shift** `{from_moment_id, to_moment_id, overlap, new_identifiers}`: first and last moments of at least `internal_shift_min_dwell_s`, token overlap below 0.2 or a new identifier. It never splits a visit.
- **Corpus**: N (visits with a non-empty bag), df, idf, class per token (hub > rare/single/common, 14.7, integer arithmetic), and per artifact **specificity** = prior x pattern x volatility x recurrence (13.3), from raw features only.

## Evidence (`Evidence`, symmetric in meaning; source is the earlier visit)
Support: `same_item, same_artifact, same_pr_issue, same_file_name, version_lineage, same_branch, shared_identifiers, shared_terms, same_locator`. Conflict (cannot-link): `different_branch, different_pr_issue, file_in_different_folders`; a conflict is `lifted` only by a direct shared PR/issue identity and stays visible. Each item carries `subject` (what supports it), `strength` (specificity and band, idf and class, or cos_idf / R / x_E), `restrictions` (what the design says that kind may not do alone), and `provenance` (fields, q of both sides, `gap_s`). `version_lineage` carries the content corroboration measured without the lineage's own tokens. No tier, score or verdict is stored. `EvidenceSet.cannot_links()` is the immutable constraint store of 14.1.

## Parameters
`FeatureParams` and `EvidenceParams` are dimensionless placeholders. Every time threshold is a `ReplayParams` field (`glance_dwell_s, seed_dwell_s, unknown_app_seed_dwell_s, internal_shift_min_dwell_s, t_resume_s`), so joint scaling still holds: only seconds-valued outputs (`t_start, t_end, dwell_s, gap_s`) change, by exactly the factor, and translation changes only timestamps.

## Not done, and why
- Time-dependent evidence (T_resume windows, the 60 s handoff, decay) and anything reading assignments belong to PR 2C; `gap_s` is recorded so it can apply them.
- The Moment has no working directory or branch field, so terminal branches have no repository and produce no pair evidence. Smallest decision if wanted: record the repository (or cwd basename) in the Moment.
- A bare `#12` in a title without a repository is only a shared identifier, not PR identity.
- Groups larger than `max_group_visits` (60) emit no pairs and are listed in `EvidenceSet.skipped`.
- Small windows have few rare tokens by design (14.7), so on tiny inputs most tokens are hub-like.

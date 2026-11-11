# ADR 0004: Activity features and evidence (Step 2, PR 2B)

Status: proposed. Builds on ADR 0003 (the merged design and PR 2A). PR 2B adds **no scoring, assignment, clustering or inference**: it turns `activity_input.Prepared` into per-visit features and pair evidence, so PR 2C does not parse raw Moments again. No model, no CPU judge, no I/O, no change to the Moment or Observation schemas, the project finder, the tracker or `MOMENT_LOG`.

    Prepared -> activity_features.extract() -> FeatureSet -> activity_evidence.build_evidence() -> EvidenceSet
    activity_evidence.analyse(prepared, params, replay_params) returns both.

## Inputs and limits
Only fields already in a Moment are read: `item_id, app, bundle_id, kind, window, url, target, n_frames, provenance` (frame counts), `digest.terms/identifiers/lines` and timestamps. `interaction`, `context`, `obs_id`, `ref` and `digest.text_hash` are never read (a test changes them and requires identical output). Query strings and fragments are dropped again before any use. Whole quoted lines are never carried, only their words at half weight.

## Features (per visit; `VisitFeatures`)
- **artifacts**: `item` (the item id), `page`/`document` (host + URL path), `file` (name, folder only when the target holds one or the window names the workspace), `pr`/`issue` (repository + number, from the URL or a title such as "Pull Request #12 · owner/repo"), `chat`. Each has `id_bearing` and `hub_pattern`.
- **namespaces**: `domain`, `repo`, `folder`, `locator` (host + first path segment).
- **branch** `{name, scope, repo, workspace}`: from the exact_links rules (terminal title or output, PR page "x into y"), a `/tree/<branch>` page, or a "(branch)" suffix on an editor workspace. `scope` is `repo` (a git-host URL names `host/owner/repo`), `workspace` (the suffix on a workspace name) or `none` (a branch with no repository, such as a terminal's; a Moment has no working directory).
- **lineage** `{stem, ext, scope, name}`: version chain key using `exact_links.stem`.
- **bag** `(token, weight)` per design 14.3: terms/identifiers/title/target/URL-path words 1.0, line words 0.5, times q(V); hub-like tokens dropped. `key_tokens`: tokens that come from the artifact key (so scoring can remove them). `identifiers`.
- **quality** `{q, source, frames, digest_empty, tokens, content}`: q = 1.0 (AX present) or 0.8 (OCR only), x0.6 for one frame, 0.5 for an empty digest.
- **glance** (`dwell_s < glance_dwell_s`), **substance** `{ok, reasons}` (`glance, short_dwell, few_frames, empty_digest`; unknown kinds need `unknown_app_seed_dwell_s`), **weak_evidence** (empty digest and no identifying artifact other than the item).
- **internal_shift** `{from_moment_id, to_moment_id, overlap, new_identifiers}`: first and last moments of at least `internal_shift_min_dwell_s`, token overlap below 0.2 or a new identifier. It never splits a visit.
- **Corpus**: N (visits with a non-empty bag), df, idf, class per token (hub > rare/single/common, 14.7, integer arithmetic), and per artifact **specificity** = prior x pattern x volatility x recurrence (13.3), from raw features only.

## Branch identity: which source may produce what
| source | support | cannot-link |
|---|---|---|
| repository-backed branch (`scope: repo`) | `same_branch` | `different_branch` (same repository, different branches) |
| workspace-derived branch (`scope: workspace`) | `same_workspace_branch` (soft, flagged `workspace_name_not_unique`) | none; `different_workspace_branch` is contrast only |
| branch with no repository (`scope: none`) | none | none |
A workspace name is not a repository identity: two checkouts can share a name and one repository can have several, so it never yields a hard constraint. Workspace-derived observations are never keyed as a repository.

## Distinct PRs and issues: contrast, not a cannot-link
Design 12.2 and 14.1 make a cannot-link of a different branch of one repository, not of a different PR number. The only PR sentence that mentions a different number (design section on "conflicting exact evidence") demotes a shared *container* artifact; it does not forbid two PRs in one Activity, and one piece of work can span several PRs. PR 2B therefore emits `different_pr_issue` with polarity `contrast` (distinct artifacts, both references listed) and **no cannot-link**. A shared PR or issue identity still lifts a branch or file-folder conflict. **Resolved in PR 2C:** the PR 2A fixture `pr_numbers_differ` no longer lists a `cannot_link`; it keeps its `must_not_merge` expectation between the two PR groups. `different_pr_issue` stays contrast-only: the scoring subtracts a provisional weight and never forbids the pair, so one Activity can span two PRs when stronger corroborating evidence supports it. `CONFLICTS` and `RULES` are unchanged.

## Evidence (`Evidence`, symmetric in meaning; source is the earlier visit)
Support: `same_item, same_artifact, same_pr_issue, same_file_name, version_lineage, same_branch, shared_identifiers, shared_terms, same_locator`. Conflict (cannot-link): `different_branch`, `file_in_different_folders`; a conflict is `lifted` only by a direct shared PR/issue identity and stays visible. Contrast (never a cannot-link): `different_pr_issue`, `different_workspace_branch`. Each item carries `subject` (what supports it), `strength` (specificity and band, idf and class, or cos_idf / R / x_E), `restrictions` (what the design says that kind may not do alone), and `provenance` (fields, q of both sides, `gap_s`). `version_lineage` carries the content corroboration measured without the lineage's own tokens. No tier, score or verdict is stored. 

## How cannot-link completeness is guaranteed
1. The cannot-links that constrain assignment live in `EvidenceSet.constraints`, a `ConstraintStore` holding one observation per visit (its repository-backed branch, its file name and known folder, its PR/issue keys). It is built in one pass over all visits; no group size, count or time limit is read.
2. A pair is cannot-linked iff a rule holds between the two observations (`is_cannot_link`, `reasons`, `pairs`) and no direct shared PR/issue identity lifts it. Nothing is enumerated until `pairs()` is asked, so a group of any size costs memory linear in the visits.
3. `max_group_visits` limits only the *listing* of pair evidence. For support and contrast the group goes to `skipped` and `support_complete` becomes False. For a cannot-link rule only the explanatory `different_branch` / `file_in_different_folders` items are omitted, named in `report["constraint_explanations_omitted"]`; the constraint still holds.
4. Fail closed: `EvidenceSet.require_complete()` raises `ConstraintsIncompleteError` when `constraints.complete` is False, and `cannot_links()` calls it. PR 2C must call it before assigning.
5. Tests: groups of 70 visits (mixed branches, mixed branches with one shared PR, many different PRs, file names in two folders) equal an independent brute-force oracle; the result is identical for caps 2, 5, 29, 60 and 1000; every fixture matches the oracle; reordering, duplicates, translation and scaling change nothing.

## Specificity heuristics (uncalibrated)
All of the following are uncalibrated heuristics, chosen so that scoring has a number to work with. Their constants and outputs are placeholders to be tuned on fixtures and real labels, and no decision may rely on their exact values. Specificity = prior x f_pattern x f_volatility x f_recurrence, each factor in (0, 1].
- **f_pattern** = 0.2 (`f_pattern_hub`) if the artifact matches the hub pattern (inbox, calendar, chat, search, a service's home or list page), else 1.
- **f_volatility** = clamp(`mean_sim` / `vol_ref`, `vol_floor`, 1) with `vol_ref` = 0.3 and `vol_floor` = 0.1. `mean_sim` is the mean of `cos_idf` (design 14.3) over all pairs among the artifact's first `vol_max_visits` = 20 visits ordered by (t_start, visit_id); a single visit gives 1. So `mean_sim` >= 0.3 gives 1, 0.15 gives 0.5, and <= 0.03 gives the floor 0.1.
- **f_recurrence** = 1 if sittings <= 2, else 2 / sittings. `sittings` = 1 + the number of visits that start more than `t_resume_s` after the latest end of the artifact's earlier visits (a gap of exactly `t_resume_s` stays in the sitting). It uses differences only and `t_resume_s` scales with time, so it is translation- and scale-invariant.
- **prior**: 1.0 for document, file, PR, issue; 0.8 for page; 0.6 for chat; 0.5 otherwise; for the `item` artifact the prior of the visit's kind (mail 0.4, terminal 0.3).
- **bands**: `low` below 0.4, `high` from 0.7, else `mid` (13.3). Boundary and sensitivity tests pin these definitions, not their correctness.

## Parameters
`FeatureParams` and `EvidenceParams` are dimensionless placeholders. Every time threshold is a `ReplayParams` field (`glance_dwell_s, seed_dwell_s, unknown_app_seed_dwell_s, internal_shift_min_dwell_s, t_resume_s`), so joint scaling still holds: only seconds-valued outputs (`t_start, t_end, dwell_s, gap_s`) change, by exactly the factor, and translation changes only timestamps.

## Not done, and why
- Time-dependent evidence (T_resume windows, the 60 s handoff, decay) and anything reading assignments belong to PR 2C; `gap_s` is recorded so it can apply them.
- The Moment has no working directory or branch field, so terminal branches have no repository (`scope: none`) and are never paired. Smallest decision if wanted: record the repository (or cwd basename) in the Moment.
- A bare `#12` in a title without a repository is only a shared identifier, not PR identity.
- Groups larger than `max_group_visits` (60) emit no pair support or contrast evidence and are listed in `EvidenceSet.skipped`; this never applies to cannot-links (see above).
- Small windows have few rare tokens by design (14.7), so on tiny inputs most tokens are hub-like.

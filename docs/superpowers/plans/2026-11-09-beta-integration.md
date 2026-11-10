# Trace staged beta integration

User-approved direction: integrate the branch-audit recommendations incrementally while the founder beta tests; prepare a verified final main integration later. The audit is the accepted scope description. This plan does not authorize sending user data to a hosted model or enabling every experiment together.

## First testable batch

Base an isolated `integration/beta` branch on fresh `origin/main`. Keep the original checkout's uncommitted grouping experiments, labels, website assets and personal context untouched. Do not carry the mixed-result grouping patch bundle into the beta branch.

1. **Page notes:** reproduce PR #40's regressions with its tests against the baseline; port the focused-field/page-URL fallback and preference for items with open notes. Check unknown pages, known fields, completed notes and page changes. Run identity/tracker tests.
2. **Practical naming:** port the window/folder naming tests from `f77c881`, observe failure, then port the corresponding code. Add deterministic score/name tie handling from `68fa1d8`, adapted to main's rules-only assigner. Verify repeatability across Python hash seeds. Do not import embedding experiments or model names. Compare baseline and batch against the frozen 65-item reviewed dataset; report tradeoffs rather than claiming a quality win from a naming change.
3. **Beta delivery:** run the full Python suite with temporary config data plus appropriate extension checks. Have a fresh reviewer inspect the complete diff. Build a separate local beta artifact without stopping or replacing the user's running app or importing personal data. Supply exact launch, rollback and manual checks. Record an immutable branch commit and a short beta checklist. Push only the integration branch if all checks pass; do not merge/push main before beta feedback and final validation.

## Next batches, in order

- **AI grouping evaluation (C):** isolate the current #39 pipeline and reproduce its models. Required current tags: `qwen3.5:0.8b`, `lmemm-judge-cpu`, `lmemm-judge-v4-cpu`; optional `embeddinggemma`. Test exact links on/off, narrator contamination and mixed captures using confirmed data. Missing original adapters block exact reproduction, not independent structural-link tests. Do not silently download/train models or start private capture just to check readiness.
- **Capture reader (D):** validate AX coverage, fallback and changed-region OCR on real Mac/Retina windows before normal use. Keep data attribution a separate acceptance criterion.
- **Assistant handoff (E):** native keyboard/panel/clipboard/source-adapter tests; user remains in control of pasting/sending.
- **Labels/tasks/recall and Resume (F/G):** selectively port wanted behaviors against current Memory/Resume architecture. These old branches share substantial history and introduce hosted Claude calls/writing capture; settle those user-facing changes before enabling them.
- **Release:** consolidate beta findings, verify restart/data persistence/privacy/deletion, installation on another Mac, resource use, signing/distribution and end-to-end onboarding. Final main integration follows verified beta acceptance.

## First-batch manual checks

Create a note on a test web page; return to it and focus its search field. The note should remain visible. Visit a different page with a different URL; the old note must not follow. Complete/reopen a note and repeat. Resize a terminal without changing folder; it should keep its name/identity. Check a Finder folder, a Preview file and a VS Code panel/title. Existing saved data is not bulk renamed or migrated. Confirm ordinary capture and the already-merged text viewer still work.

Record test evidence, source commit IDs, model readiness and remaining Mac checks in the integration branch's beta handoff. Native app permissions and personal-data behavior require actual beta observation; passing unit tests alone does not establish them.

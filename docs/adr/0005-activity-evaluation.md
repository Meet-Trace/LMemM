# ADR 0005: Activity evaluation workflow (Step 2, PR 2D)

Status: proposed. Evaluation only: the inference (weights, thresholds, passes), the Moment and Observation schemas, the tracker and `MOMENT_LOG` are untouched. No model and no CPU judge.

## Question it answers
How reliably does the deterministic engine find coherent Activities in real, labelled behaviour? `scripts/eval_activities.py replay` replays labelled datasets through `activity_inference.infer` and scores the grouping with the existing conventions (`activity_abstention`: pure, mixed-gold and unlabelled visits; unsure moments left out).

## Dataset (one folder per labelled stretch of use)
`moments/*.jsonl` (Moment records as MomentStore writes them), `units.csv` (the sheet from `eval_activities.py sheet`, filled in), optional `dataset.json` (`name`, `scenarios`: tag to visit ids). More datasets are added by passing more `--dataset` folders; results are also micro-averaged. Output goes to a folder you name and never inside a dataset's `moments/`. It holds visit ids, activity ids and your label names, never screen content, and is byte-identical for the same data and engine (it records the engine parameters and a data fingerprint, no timestamp).

## Measures
Pair precision and recall over pure visits; false merges and missed merges (missed split into `abstained` and `split`); visit outcomes (joined correct/partial/wrong, alone ok/missed, abstained ok/missed); assigned, abstention and ambiguous rates; mixed-gold, unsure and internal-shift reports and structural baselines at equal coverage (all reused); scenario slices (cross_app, single_app, interleaved, returns, short_visits, noise, internal_shift_flagged, plus your tags; a scenario counts every pair that touches one of its visits); per-activity recall.

## Limits
No real labelled dataset ships with the repository, so no accuracy claim exists yet; the tests use synthetic fixtures only to test this code. Label with `eval_activities.py sheet` on moments recorded with `config.MOMENT_LOG` on. Small datasets are flagged as indicative. Gold is moment-level and the engine assigns visits, so mixed-gold visits cannot be scored and the visit-level ceiling is reported.

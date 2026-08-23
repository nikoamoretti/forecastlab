# ForecastLab forecast research analysis

## Purpose

ForecastLab learns by preserving why a forecast failed, not by hiding bad outcomes or immediately changing the method. The analysis layer reads completed controlled-experiment records and adds internal human classifications. It does not rerun forecasts, edit probabilities, alter profiles, tune prompts, or make a public performance claim.

An incorrect forecast is not automatically a methodological failure, and a correct forecast may still contain one. Reviewers classify the observed mechanism using retained contracts, evidence, reasoning, aggregation traces, provider records, and resolution outcomes.

## Failure taxonomy

Each `ForecastFailure` belongs to one `EvaluationRun`. Multiple distinct classifications may be attached to the same run.

| Group | Categories |
| --- | --- |
| Question | `ambiguous_resolution`, `incorrect_contract`, `wrong_resolver` |
| Research | `missing_evidence`, `poor_source_quality`, `cutoff_failure` |
| Reasoning | `bad_prior`, `overconfidence`, `ignored_counterargument`, `narrative_bias` |
| Aggregation | `incorrect_weighting`, `dependency_failure` |
| Operational | `provider_failure`, `timeout`, `budget_failure` |

Every classification includes an internal annotation, reviewer identity, and creation/update timestamps. The run/category pair is unique, so saving the same classification again updates its annotation instead of multiplying counts. Reviewers may assign multiple categories when causes are materially distinct.

Classifications describe evidence-backed observations. They are not excuses for an outcome and are not instructions to change the current experiment. Any later method change requires its own version, tests, and separately frozen evaluation.

## Analysis report

`GET /api/evaluation/experiments/{id}/analysis` summarizes the existing stored results by profile:

- performance: mean Brier score, mean log loss, gated calibration buckets, and a ten-bin probability distribution;
- reliability: assigned, completed, partial, and failed runs plus their rates;
- research: mean evidence coverage, distinct accepted cutoff-eligible source count, and persisted Evidence Claim count;
- cost: total recorded cost and cost per assigned question;
- review: question-level forecast results and attached failure classifications.

Calibration buckets retain the protocol's minimum-sample gate. An unavailable display is reported as unavailable; small samples are not promoted into a calibration claim. Source counts deduplicate accepted eligible `EvidenceItem.url` values within a profile. Claim counts include persisted graph Evidence Claims and may therefore be zero for the legacy profile even when that profile has accepted evidence items.

The report is descriptive. It does not calculate a superiority conclusion, modify the comparison report, or select a winning profile.

## Internal review workflow

The Evaluation Lab presents:

`Experiment` → `Question` → `Forecast` → `Failure classification`

A reviewer selects one question/profile forecast, inspects its probability, outcome, score, execution error, and existing annotations, then records a taxonomy category and concise rationale. `POST /api/evaluation/runs/{id}/failures` creates or updates that run/category classification. `PATCH /api/evaluation/failures/{id}` updates an annotation while preserving its identity and original creation time.

There is no public publishing endpoint. Analysis output is an internal research surface and must retain the report notice that no profile-superiority claim is established.

## Learning loop

After an experiment:

1. Reconcile all assigned runs, including partial and failed work.
2. Review question contracts and resolver integrity independently of which profile scored better.
3. Review missing, weak, or cutoff-ineligible evidence and claim provenance.
4. Review priors, counterarguments, narratives, dependency handling, and deterministic calculation traces.
5. Classify operational failures from durable provider, timeout, and budget records.
6. Aggregate category counts alongside scores, completion, evidence, cost, and latency.
7. Form a new research hypothesis only after the review is complete.

This task stops at step 6. It deliberately does not implement improvements, calibration, prompt tuning, or profile changes based on the observed results.

# ForecastLab forecast research analysis

## Purpose

The forecast analysis layer turns one controlled `ForecastExperiment` into an internal scientific report. It reads the frozen configuration, question/profile assignments, stored forecast results, evidence records, and optional human failure classifications. It does not execute or rerun forecasts, edit probabilities, change prompts or profiles, calibrate a model, or select a winning system.

The report always compares the controlled profiles in their frozen order:

- `single_model_forecaster_v1`
- `three_track_forecaster`
- `graph_forecaster_v1`

Its outputs are descriptive measurements. They are not a superiority claim.

## Report contents

`GET /api/forecast-experiments/{id}/analysis` returns one profile row for each frozen profile.

### Performance

- mean Brier score over stored valid probabilities;
- mean log loss over stored valid probabilities;
- calibration buckets produced by the existing reliability-display rule;
- the number of scored questions.

The reliability display retains the minimum sample gate. When too few resolved predictions exist, the report preserves the unavailable state and sample count rather than presenting sparse bins as evidence of calibration.

### Operations

- assigned, completed, partial, and failed counts;
- completion, partial, and failure rates;
- total cost and cost per assigned question;
- mean and median latency;
- system error-category counts.

Failed runs remain in operational denominators. They have no invented probability and do not enter Brier, log-loss, or calibration calculations.

### Research

- mean evidence coverage from the controlled experiment results;
- accepted cutoff-eligible evidence-item count;
- distinct accepted source count;
- persisted eligible Evidence Claim count;
- raw `EvidenceItem.source_class` counts;
- mean stored claim-level source-quality assessment and primary-claim rate when claims exist.

Claim-level quality is unavailable for profiles that do not create Evidence Claims. The report returns `null` in that case. Source-class distributions remain available from accepted evidence items. These fields preserve the existing measurements; they do not invent a new cross-profile quality score.

## Failure classification

Internal reviewers can attach one or more classifications to a terminal `ForecastExperimentRun`:

| Category | Review question |
| --- | --- |
| `bad_contract` | Was the question or resolution rule wrong or materially ambiguous? |
| `bad_evidence` | Was decisive evidence missing, unsupported, ineligible, or poor quality? |
| `bad_decomposition` | Did the graph or research plan omit or misstate an important line of inquiry? |
| `bad_node_forecast` | Did a node-level forecast misread its eligible claims or express unjustified probability? |
| `bad_aggregation` | Did deterministic weighting or contribution handling produce a problematic synthesis? |
| `operational_failure` | Did a provider, timeout, budget, or other execution failure prevent a valid result? |

Each classification stores the experiment, run, category, annotation, reviewer identity, and timestamps. A run/category pair is unique. Reposting the same category updates the annotation instead of creating duplicate counts. Classifications are internal observations, not automatic explanations and not instructions to change the method.

API endpoints are:

- `POST /api/forecast-experiment-runs/{id}/failures` to create or update a classification;
- `GET /api/forecast-experiment-runs/{id}/failures` to list a run's classifications;
- `PATCH /api/forecast-failures/{id}` to revise the internal annotation.

## Research workflow

1. Reconcile all assigned cells, including partial and failed runs.
2. Inspect the frozen contract and resolver independently of the forecast outcome.
3. Audit evidence provenance, cutoff eligibility, claim support, and source quality.
4. Inspect graph decomposition, node probabilities, and deterministic aggregation where applicable.
5. Classify only evidence-backed failure mechanisms and retain concise internal annotations.
6. Review descriptive performance, operations, and research measurements together.
7. Form any improvement hypothesis as a separate future task with a new frozen configuration.

This implementation stops at step 6. It deliberately makes no forecasting-system changes based on the report.

## Limitations

The layer does not implement paired uncertainty intervals, significance testing, causal attribution, automated failure diagnosis, inter-reviewer agreement, annotation adjudication, dataset representativeness checks, or public publication. Model-pretraining leakage and dataset-selection bias remain evaluation limitations. A correct forecast can still contain a process failure, and an incorrect forecast is not automatically evidence that one of the six mechanisms occurred.

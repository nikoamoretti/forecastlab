# ForecastLab forecast research analysis

## Purpose

The forecast analysis layer turns one controlled `ForecastExperiment` into an internal scientific report. It reads the frozen configuration, question/profile assignments, stored forecast results, evidence records, and optional human failure classifications. It does not execute or rerun forecasts, edit probabilities, change prompts or profiles, calibrate a model, or rank systems.

The report evaluates the controlled profiles in this fixed order:

- `single_model_forecaster_v1`
- `three_track_forecaster`
- `graph_forecaster_v1`

The analysis reports observed differences and their uncertainty. Interpretation remains bounded by the frozen dataset, experiment design, completion coverage, and limitations below.

## Report contents

`GET /api/forecast-experiments/{id}/analysis` returns profile summaries, three paired comparison records, cost-efficiency records, the existing research diagnostics and failure annotations, and a machine-readable methodology block.

### Paired performance comparisons

The fixed comparisons are:

1. `single_model_forecaster_v1` vs `three_track_forecaster`
2. `three_track_forecaster` vs `graph_forecaster_v1`
3. `single_model_forecaster_v1` vs `graph_forecaster_v1`

Each comparison uses only identical questions with a completed, valid result for both profiles. For every paired question, the analysis computes profile A minus profile B separately for Brier score and log loss. A negative loss difference therefore means profile A recorded the lower loss on the paired population. Failed, partial, missing, or invalid results do not receive an invented score and do not enter the paired population.

`ForecastComparisonResult` records the experiment and profile identities, paired question count, mean Brier and log-loss differences, their confidence intervals, and the frozen bootstrap sample count and seed. It also reports mean paired cost and latency differences. The interval flags state only whether zero lies outside an interval; they do not make an adoption decision.

### Deterministic bootstrap intervals

The V1 analysis uses a paired percentile bootstrap over question-level metric differences:

- confidence level: 95%;
- samples: 2,000;
- random seed: `20260823`;
- minimum paired questions: 20;
- resampling unit: one paired question.

The same frozen inputs produce the same interval. Below 20 paired questions, the observed mean remains visible but both intervals are withheld and the result is labeled exactly `insufficient evidence`. Twenty is a reporting gate, not proof that the sample is representative or that the interval is precise enough for a consequential decision.

The three profile pairs and two loss metrics are reported without a multiplicity adjustment. They must be interpreted together, and any primary endpoint must be registered before a validation or test run. When several questions share an event family, a future registered analysis must use clustered resampling instead of treating those rows as independent.

### Calibration display

Each profile receives the same five fixed probability buckets:

- 0–20%
- 20–40%
- 40–60%
- 60–80%
- 80–100%, inclusive of a probability of exactly 1.0

Every bucket reports forecast count, average predicted probability, and actual outcome frequency. The display is labeled `insufficient evidence` below 20 completed forecasts. Empty buckets retain a count of zero and unavailable averages. This is a reliability diagnostic only; no calibration adjustment is performed.

### Cost efficiency

`CostEfficiencyReport` preserves the assigned-run denominator and reports:

- total recorded cost, including stored failed-run cost;
- cost per assigned question;
- sum of completed Brier scores divided by total recorded cost;
- sum of completed log losses divided by total recorded cost;
- recorded latency per assigned question.

The loss-per-dollar fields are descriptive loss ratios, so lower values are lower recorded loss per dollar. They are unavailable when total recorded cost is zero or no completed score exists. They must be read with raw losses, completion counts, and total costs; they are not standalone quality measures.

### Profile performance and operations

Profile summaries continue to report:

- mean Brier score and mean log loss over completed valid forecasts;
- assigned, completed, partial, and failed counts;
- completion, partial, and failure rates;
- total cost and cost per assigned question;
- mean and median latency;
- system error-category counts.

Failed runs remain in operational denominators. They have no invented probability and do not enter Brier, log-loss, calibration, or paired-quality calculations.

### Research

The report preserves:

- mean evidence coverage from the controlled experiment results;
- accepted cutoff-eligible evidence-item count;
- distinct accepted source count;
- persisted eligible Evidence Claim count;
- raw `EvidenceItem.source_class` counts;
- mean stored claim-level source-quality assessment and primary-claim rate when claims exist.

Claim-level quality is unavailable for profiles that do not create Evidence Claims. The report returns `null` in that case. Source-class distributions remain available from accepted evidence items. These fields preserve the existing measurements; they do not invent a cross-profile quality score.

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

1. Reconcile every assigned cell, including partial and failed runs.
2. Inspect the frozen contract and resolver independently of the forecast outcome.
3. Audit evidence provenance, cutoff eligibility, claim support, and source quality.
4. Inspect graph decomposition, node probabilities, and deterministic aggregation where applicable.
5. Classify only evidence-backed failure mechanisms and retain concise internal annotations.
6. Confirm paired coverage before interpreting quality differences.
7. Review observed loss differences, confidence intervals, calibration displays, operations, cost ratios, and research measurements together.
8. Form any improvement hypothesis as a separate future task with a new frozen configuration.

This analysis remains read-only. It deliberately makes no forecasting-system change based on the report.

## Limitations

The percentile bootstrap characterizes sampling uncertainty for the observed paired question set; it does not establish causal attribution, dataset representativeness, or independence between related questions. The current implementation does not provide clustered or stratified bootstrap intervals, multiplicity-adjusted intervals, exact small-sample inference, calibration confidence bands, inter-reviewer agreement, annotation adjudication, or public publication. Cost ratios do not control for completion selection or differing resource composition. Model-pretraining leakage and dataset-selection bias remain evaluation limitations. A correct forecast can still contain a process failure, and an incorrect forecast is not automatically evidence that one of the six mechanisms occurred.

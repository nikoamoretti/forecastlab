# ForecastLab V1 evaluation workflow

## Purpose

This workflow provides one reproducible environment for comparing ForecastLab's three integrated forecasting systems:

- `single_model_forecaster_v1` version 1;
- `three_track_forecaster` version 1;
- `graph_forecaster_v1` version 4.

It changes neither their forecasting behavior nor their probability calculations. Its job is to freeze shared inputs, execute the same resolved questions through every profile, preserve results and failures, and produce measurement and uncertainty reports.

No real benchmark questions ship with this integration branch. Synthetic fixtures test software behavior only.

## Evaluation lifecycle

```text
Frozen dataset
    ↓
Controlled experiment
    ↓
Question × profile runs
    ↓
Stored forecast results
    ↓
Brier, log-loss, cost, latency, completion, evidence metrics
    ↓
Paired statistical comparisons
    ↓
Internal research analysis report
```

## 1. Prepare a dataset release

Historical evaluation accepts only resolved binary questions with a complete resolution contract, authoritative resolver, forecast date, resolution date, source, and known outcome.

1. Import CSV or JSON with `POST /api/evaluation/datasets/import`.
2. Review the validated draft with `POST /api/evaluation/datasets/{id}/review`.
3. Freeze it with `POST /api/evaluation/datasets/{id}/freeze`.

A frozen release stores its name, semantic version, content hash, provenance, question count, freeze time, and immutable question rows. Corrections require a new dataset version. The system rejects draft datasets when an experiment is created.

## 2. Create a controlled experiment

Create the experiment with `POST /api/forecast-experiments` and all three required profile IDs:

```json
{
  "dataset_id": "<frozen-dataset-id>",
  "profile_ids": [
    "single_model_forecaster_v1",
    "three_track_forecaster",
    "graph_forecaster_v1"
  ],
  "synthetic_test": false
}
```

Real experiments require a clean Git worktree and a committed Python dependency lock. `synthetic_test: true` is reserved for local software tests with mock providers and fixture evidence.

Creation writes one immutable experiment manifest and one run for every question/profile cell. Omitting one of the controlled profiles is rejected, so all profiles receive the same frozen questions.

## 3. What the experiment freezes

The experiment's `configuration_json` is canonicalized and protected by `configuration_hash`. Configuration fields cannot be changed after creation.

| Reproducibility input | Frozen record |
| --- | --- |
| Dataset | ID, name, version, content hash, status, freeze time, provenance |
| Questions | Question hash, text, resolution contract, outcome, forecast date, resolution date, source, evidence cutoff |
| Profiles | Profile ID, version, profile hash, source profile, effective profile, execution strategy |
| Prompts | Complete prompt bundle, prompt versions, individual hashes, bundle hash |
| Model and provider | Model provider, model name, public base URL, timeout, search provider, key-presence flags |
| Budget | Equal effective ceilings for calls, searches, documents, tokens, estimated cost, and wall-clock time |
| Evidence policy | The resolved evidence policy in the global provider snapshot and each profile execution context |
| Pricing | Pricing catalog and catalog hash |
| Code version | Git commit, tracked-source hash, dependency-lock hash, package-lock hash, application version, optional image digest |
| Creation | Experiment creation timestamp |

Raw credentials are never included. The manifest records only whether required keys were configured.

Each profile snapshot carries its own execution context, including prompt hashes, model/provider identity, evidence policy, profile version, budget ceilings, code commit, and context configuration hash. Execution reconstructs these frozen objects rather than reloading mutable profile or prompt files.

Before a non-synthetic run, ForecastLab compares the frozen code and dependency identity with the current environment and fails closed on drift. The stored configuration hash is also recomputed before use.

## 4. Execute profile runs

The worker executes each `ForecastExperimentRun` through its existing strategy:

- single model: one contract-and-evidence forecast call;
- three track: the existing independent base-rate, current-evidence, and skeptic path;
- graph: Contract → Graph → Evidence Claims → Node Forecasts → Graph Aggregation.

The experiment runner does not alter those paths. Every run uses the dataset question's forecast date as its historical evidence cutoff and persists its terminal state. Failed cells retain their error and recorded cost but receive no invented probability.

Use `GET /api/forecast-experiments/{id}` to inspect progress. Its response includes the experiment creation time, configuration hash, frozen dataset/provider/budget/prompt/code summary, per-profile counts, and every assigned cell.

## 5. Read measurements

`GET /api/forecast-experiments/{id}/report` returns:

- the frozen dataset identity and configuration hash;
- question-level results for every profile;
- probabilities and outcomes;
- Brier score and log loss;
- cost and latency;
- evidence coverage;
- completion, partial, and failure measurements.

The report keeps operational denominators separate from quality-score denominators. Failed runs remain visible and are never assigned a fallback probability.

## 6. Compare profiles and quantify uncertainty

`GET /api/forecast-experiments/{id}/analysis` creates three paired comparisons:

1. single model vs three track;
2. three track vs graph forecast;
3. single model vs graph forecast.

Only identical questions with completed valid results for both profiles enter a pair. Differences are profile A minus profile B. Brier and log-loss uncertainty uses a deterministic paired percentile bootstrap:

- 2,000 resamples;
- random seed `20260823`;
- 95% interval;
- minimum 20 paired questions.

Below 20 pairs, observed means remain visible but intervals are withheld as `insufficient evidence`.

The same analysis response includes five fixed calibration buckets, resource ratios, evidence diagnostics, source and claim counts, operational failures, and internal failure classifications. The analysis is read-only and does not update forecasts.

## Allowed conclusions

An evaluation conclusion must remain within the dataset and experiment design:

- Synthetic fixtures establish software correctness only.
- Development data supports debugging and hypothesis generation.
- Validation data supports selection under the predeclared protocol.
- A frozen held-out test supports a bounded retrospective measurement.
- Prospective questions support real-world validation only after independent resolution.

Report observed differences, paired sample counts, confidence intervals, completion coverage, raw costs, latency, and evidence coverage together. An interval that crosses zero is inconclusive for the measured loss difference. An interval that excludes zero still does not establish causality, domain-wide generalization, or operational preference.

Do not treat the 20-question display threshold as evidence of calibration. The current intervals are unadjusted for the three profile pairs and two loss metrics, and row-level resampling assumes questions are independent. Related event families require a pre-registered clustered analysis. Cost-per-loss ratios are descriptive and cannot replace raw quality and completion measurements.

## Reproduction checklist

1. Record the frozen dataset ID, version, and hash.
2. Confirm the three profile IDs and their frozen versions.
3. Record the experiment ID, configuration hash, and creation timestamp.
4. Confirm the prompt bundle, provider/model, common budget, evidence policy, code commit, and dependency hashes in the manifest.
5. Reconcile every assigned question/profile cell.
6. Save the comparison report and statistical analysis response.
7. Preserve failures and exclusions without filling missing probabilities.
8. Label the dataset split and the permitted conclusion boundary.

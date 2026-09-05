# ForecastLab Pilot Benchmark V1 Report

## Scope and result boundary

Experiment `462fe718-64ca-4fab-bed2-f9ecf9c28433` executed the 20 frozen questions in ForecastLab Pilot Benchmark version 1 across all three controlled profiles, producing the expected 60 question/profile assignments.

This was a mock-backed evaluation execution because no live provider calls were authorized. It validates the real-question experiment workflow, frozen configuration, metrics, failure capture, and report generation. It does not measure live model forecasting quality.

## Frozen experiment

| Item | Frozen value |
| --- | --- |
| Dataset | ForecastLab Pilot Benchmark, version 1 |
| Dataset ID | `30e89963-bf10-4fce-bd86-1eeb5b56ca1c` |
| Dataset hash | `c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888` |
| Experiment ID | `462fe718-64ca-4fab-bed2-f9ecf9c28433` |
| Configuration hash | `a6873069bb2b7388cd8e78ce54676fac1425f03abaa73f6705dbc61c87c8e7aa` |
| Code commit | `458c57a1c325b12f5b3f133086f6189d49fde731` |
| Application version | `0.3.1` |
| Prompt bundle hash | `aa90c3b81838ed4df6a822613a1796e7bac0e13bcbe5c60afc665eadfdcad62c` |
| Pricing hash | `87c8a6fd8066c1274986d78ffadbfe43f26d4cb5d7b410e56c9991297a9c157f` |
| Model provider and model | `mock` / `mock-forecast-v1` |
| Search provider | `mock` |
| Evidence policy | `synthetic_historical_fixtures` |
| Created | 2026-08-24 05:26:02 UTC |
| Completed | 2026-08-24 05:27:47 UTC |
| Final status | `completed_with_failures` |

Profile versions were frozen as `single_model_forecaster_v1` v1, `three_track_forecaster` v1, and `graph_forecaster_v1` v4. Full prompt versions and hashes, profile snapshots, question hashes, resolution contracts, forecast dates, resolution dates, evidence cutoffs, provider settings, and pricing metadata are persisted in the experiment configuration. The export in `artifacts/pilot_benchmark_v1/experiment_results.json` preserves the report-facing freeze and every run result.

Every profile used the same ceiling: $0.25 estimated cost, 24 fetched documents, 40 model calls, 36 search calls, 200,000 tokens, and 180 seconds. Each evaluation question was assigned exactly once to each profile through the same frozen question record, contract, resolution date, and evidence cutoff.

## Execution audit

- Expected assignments: 60
- Stored assignments: 60
- Completed: 50
- Failed: 10
- Mock calls: 630 total, comprising 400 search calls and 230 model calls
- Live provider calls: 0
- Actual provider cost: $0.00

A stale pre-migration worker initially claimed the 60 queued jobs but did not recognize the experiment job type. It recorded `unknown_job:forecast_experiment_run` once on each job before any forecast or provider call. The worker was stopped, all error history was preserved, and each original cell was rescheduled once through the existing retry path. Every job therefore has two recorded attempts. No experiment cell was deleted or replaced.

## Profile measurements

Loss and calibration statistics use completed forecasts only. Completion, latency, and failure rates use all assigned runs.

| Profile | Scored / assigned | Brier | Log loss | Cost / question | Mean latency | Completion | Evidence coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `single_model_forecaster_v1` | 20 / 20 | 0.269600 | 0.733969 | $0.00 | 20.25 ms | 100% | 80% |
| `three_track_forecaster` | 20 / 20 | 0.265930 | 0.726068 | $0.00 | 76.95 ms | 100% | 50% |
| `graph_forecaster_v1` | 10 / 20 | 0.300000 | 0.794651 | $0.00 | 37.30 ms | 50% | 50% |

All costs are zero because both providers were mocks. Brier-per-dollar and log-loss-per-dollar are therefore undefined.

## Calibration buckets

The configured buckets were 0-20%, 20-40%, 40-60%, 60-80%, and 80-100%. No calibration adjustment was applied.

| Profile | Occupied bucket | Forecasts | Average probability | Outcome frequency | Evidence status |
| --- | --- | ---: | ---: | ---: | --- |
| `single_model_forecaster_v1` | 20-40% | 20 | 0.360000 | 0.500000 | Interval estimate available |
| `three_track_forecaster` | 20-40% | 20 | 0.373786 | 0.500000 | Interval estimate available |
| `graph_forecaster_v1` | 60-80% | 10 | 0.600000 | 0.300000 | Insufficient evidence |

Each profile produced one repeated probability across its completed assignments. The buckets therefore describe this deterministic mock behavior and should not be generalized to live forecasts.

## Paired observed differences

Differences are profile A minus profile B. The analysis used identical completed questions, a fixed seed of `20260823`, 2,000 paired percentile bootstrap samples, and unadjusted 95% intervals. The minimum for an interval was 20 paired questions.

| Profile A | Profile B | Pairs | Brier difference | 95% interval | Log-loss difference | 95% interval | Evidence status |
| --- | --- | ---: | ---: | --- | ---: | --- | --- |
| `single_model_forecaster_v1` | `three_track_forecaster` | 20 | +0.003670 | [-0.001844, 0.009184] | +0.007902 | [-0.003969, 0.019772] | Interval estimate available |
| `three_track_forecaster` | `graph_forecaster_v1` | 10 | -0.084556 | Not estimated | -0.171786 | Not estimated | Insufficient evidence |
| `single_model_forecaster_v1` | `graph_forecaster_v1` | 10 | -0.086400 | Not estimated | -0.175755 | Not estimated | Insufficient evidence |

The interval for the first pair includes zero for both loss measures. The two graph comparisons are descriptive values on the 10 jointly completed questions only. Missing graph runs may be systematic, so these values cannot be treated as full-dataset comparisons.

## Failure analysis

Ten `graph_forecaster_v1` assignments failed. Each stored seven node-level `no_eligible_evidence` records with `node_forecast_evidence_required`, for 70 node failures in total. No node probability or graph aggregation was produced for those assignments. All 10 were classified as **evidence failure** based on that durable trace.

| Failure category | Count |
| --- | ---: |
| Contract failure | 0 |
| Evidence failure | 10 |
| Graph failure | 0 |
| Node forecast failure | 0 |
| Aggregation failure | 0 |
| Operational failure | 0 |

## Limitations

- Real resolved questions were used, but model and search behavior came from deterministic fixtures.
- The metrics do not estimate live provider forecasting quality.
- The graph profile completed only half of its assignments, preventing confidence intervals for graph comparisons.
- Repeated profile probabilities make calibration analysis weak.
- Zero mock cost prevents cost-efficiency ratios.
- The frozen snapshot records `working_tree_dirty=true` because an untracked database backup existed when the experiment was created. Tracked code was pinned by the commit and tracked-source hash.
- Three profile pairs and two loss measures are reported without a multiple-comparison adjustment.
- The pilot contains 20 retrospectively selected questions and is not large enough for broad claims.

No prompts, profiles, forecasting logic, aggregation, or dataset records were changed in response to these results.

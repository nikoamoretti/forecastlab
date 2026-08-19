# Benchmark experiments

## Datasets

A `BenchmarkDataset` is an immutable named collection of questions. Importing CSV or JSON creates a dataset with a content hash. Every row in one dataset must agree on synthetic vs real. Mixed files are rejected. The bundled `synthetic_fixtures_v1` dataset is labeled synthetic and is not a public leaderboard.

## Experiments

Creating an experiment:

1. Freezes dataset hash, profiles, prompt hashes, providers, evidence policy, ceilings, and git commit.
2. Creates one `BenchmarkTask` per question × profile.
3. Enqueues one idempotent `benchmark_task` job per task.
4. Returns immediately.

The HTTP handler does not call `execute_run()`. Settings changes after creation do not rewrite the frozen configuration. API keys are never stored on the experiment.

## Tasks

Each real task runs with `mode=backtest` and `as_of=benchmark_question.forecast_date`. Synthetic experiments may use mock providers but remain labeled synthetic. Benchmark-only questions have `is_benchmark=true` and are filtered from the board.

## Profiles

- `model_only_v1`: contract only, no search or fetches.
- `single_agent_equal_budget_v1` and `three_track_equal_budget_v1`: matching total search, fetch, token, cost, and wall-clock ceilings.
- `three_track_full_v1`: larger research budget.
- `single_agent_baseline` and `three_track_ensemble` remain for demo compatibility.

Equal ceilings do not guarantee identical spend. Actual calls, tokens, cost, and latency are reported.

## Scoring

Summaries are scoped to one experiment. Reliability is computed separately per profile. The 20-row reliability display threshold is not a calibration claim.

## Paired comparisons

Profiles that answered the same questions are compared: n, mean Brier, paired Brier difference, win/tie/loss, log-loss difference, cost difference, latency difference.

## Bootstrap intervals

Paired Brier differences use a deterministic bootstrap: seed `20260818`, 2,000 samples, 95% percentile interval. A difference is labeled significant only when n ≥ 20 and the interval excludes zero.

## Synthetic versus real

Every score payload includes dataset name, dataset hash, synthetic flag, experiment id, experiment hash, profile hashes, and sample size. Synthetic results show:

```text
Software-verification fixtures only. Not evidence of real-world forecasting quality.
```

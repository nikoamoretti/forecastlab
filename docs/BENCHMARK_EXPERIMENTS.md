# Benchmark experiments

## Datasets

A `BenchmarkDataset` is an immutable named collection of questions. Importing CSV or JSON creates a dataset with a content hash over the question, the full resolution contract, dates, outcome, category, provenance, and synthetic flag. Rows are canonicalized and sorted before hashing, so file order does not change identity. Uniqueness is `UNIQUE(dataset_id, import_hash)`, so the same question may appear in separately versioned datasets. Every row in one dataset must agree on synthetic vs real. Mixed files are rejected. Real imports require nonempty `exact_yes` and `exact_no`. The bundled `synthetic_fixtures_v1` dataset is labeled synthetic and is not a public leaderboard.

## Experiments

Creating an experiment:

1. Writes one `BenchmarkProfileSnapshot` per selected profile with the source and effective profile payloads, full prompt texts (`PromptBundle`), prompt versions and hashes, frozen provider fields, per-profile limits, pricing snapshot, and execution context. API keys are never stored.
2. Freezes dataset hash, profile hashes, prompt hashes, model provider, model name, model base URL, search provider, timeout, evidence policy, and git commit.
3. Creates one `BenchmarkTask` per question × profile.
4. Enqueues one idempotent `benchmark_task` job per task.
5. Returns immediately.

The HTTP handler does not call `execute_run()`. Execution later uses only the stored snapshot. Editing prompt files, profile YAML, or Settings after creation does not change that experiment. If a frozen real provider later has no key, the task fails clearly. Providers are never switched. Each profile keeps its own stored limits.

## Tasks

Each real task runs with `mode=backtest` and `as_of=benchmark_question.forecast_date`. Tasks load the stored effective `ForecastProfile` and `PromptBundle`; they do not call `load_profile()` or `load_prompt()` against mutable files. Every profile forecasting one benchmark question receives the identical stored resolution contract from the dataset. Each task still owns its own benchmark-only `Question` and `ForecastRun`. Retries reuse those rows. Benchmark tasks do not operationalize the question through a model. Synthetic experiments may use mock providers but remain labeled synthetic. Benchmark-only questions have `is_benchmark=true` and are filtered from the board.

## Identity and terminal states

Invariant for the lifetime of a task, including crashes and retries:

1. one `BenchmarkTask`
2. one benchmark-only `Question`
3. one `ForecastRun`
4. zero or one `BenchmarkResult`

`finalize_benchmark_task()` is the only path that marks a task completed or failed, writes or reuses the result, and refreshes experiment counts. Experiment statuses:

- `pending`
- `running`
- `completed` — every task succeeded
- `completed_with_failures` — mix of successes and failures
- `failed` — every task failed

The Lab stops polling and loads the summary for every terminal experiment status. Transient provider errors reschedule the job. When attempts are exhausted, the job, run, task, and experiment become terminal. Track-local permanent errors may leave a partial result; transient, configuration, evidence-integrity, and structured-output errors are not converted into missing-track results.

## Profiles

Default scientific comparison:

- `single_agent_equal_budget_v1`
- `three_track_equal_budget_v1`

Those two share configured search, fetch, token, cost, and wall-clock ceilings and are the default Lab selection. The full ensemble (`three_track_ensemble`, `three_track_full_v1`) remains optional. Each task still reserves model calls before they start and uses a per-run search/fetch cache, so a retry or rerun does not reuse another run's evidence. `model_only_v1` is contract-only. `single_agent_baseline` and `three_track_ensemble` remain for demo compatibility.

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

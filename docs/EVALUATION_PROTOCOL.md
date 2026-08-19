# Evaluation protocol

## Allowed data

A benchmark row needs: question, forecast date, resolution date, outcome (0/1), resolution source, category, provenance. Import hashes detect duplicates. ForecastLab ships **synthetic** CSV fixtures for software tests (`fixtures/benchmarks/synthetic_binary.csv`) and a blank template (`fixtures/benchmarks/import_template.csv`). Those rows are labeled synthetic in the UI. They are not a public scoring set. This MVP does not invent public questions or scrape Metaculus.

## Fair baseline

Every comparison uses at least:

1. `single_agent_baseline`
2. `three_track_ensemble`

Lab comparisons should use `single_agent_equal_budget_v1` and `three_track_equal_budget_v1` when the question is “does coded aggregation help at the same ceiling?” `three_track_full_v1` is the higher-research product profile. Older IDs remain for the demo.

## Scoring

- Brier: `(p - y)^2`
- Log loss: uses clipped probabilities so zeros do not explode
- Also reported: mean/median cost, latency, failure rate

## Time and leakage

Backtests enforce `as_of` on publication and snapshot timestamps. Search snippets dated after the cutoff cannot be used as evidence. Model weights may still contain later facts. Call this an evidence-cutoff backtest.

Prefer temporal splits: forecast dates before resolution dates, and never score a run with evidence published after `forecast_date`.

## Calibration displays

Reliability is computed separately per profile and never mixed. A reliability diagram is shown only when at least 20 resolved predictions exist for that profile. Twenty observations are a display threshold, not enough for a calibration claim.

See `docs/BENCHMARK_EXPERIMENTS.md`.

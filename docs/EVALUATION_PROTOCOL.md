# Evaluation protocol

## Allowed data

A benchmark row needs: question, forecast date, resolution date, outcome (0/1), resolution source, category, provenance. Import hashes detect duplicates. ForecastLab ships **synthetic** CSV fixtures for software tests (`fixtures/benchmarks/synthetic_binary.csv`) and a blank template (`fixtures/benchmarks/import_template.csv`). Those rows are labeled synthetic in the UI. They are not a public scoring set. This MVP does not invent public questions or scrape Metaculus.

## Fair baseline

Every comparison uses at least:

1. `single_agent_baseline`
2. `three_track_ensemble`

The single-agent profile receives the same contract family and a comparable evidence budget. It is not starved of documents to make the ensemble look good.

## Scoring

- Brier: `(p - y)^2`
- Log loss: uses clipped probabilities so zeros do not explode
- Also reported: mean/median cost, latency, failure rate

## Time and leakage

Backtests enforce `as_of` on publication and snapshot timestamps. Search snippets dated after the cutoff cannot be used as evidence. Model weights may still contain later facts. Call this an evidence-cutoff backtest.

Prefer temporal splits: forecast dates before resolution dates, and never score a run with evidence published after `forecast_date`.

## Calibration displays

A reliability diagram is shown only when at least 20 resolved predictions exist. Below that, the UI reports the sample size and refuses the chart.

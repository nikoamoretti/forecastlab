# Evaluation protocol

## Allowed data

A benchmark row needs: question, forecast date, resolution date, outcome (0/1), resolution source, category, provenance, and a resolution contract (`exact_yes`, `exact_no`, `resolution_deadline`, `authoritative_source`, plus optional fallback sources, geography, units, and notes). Real imports require `exact_yes` and `exact_no`. Forecast date must precede resolution date. Import hashes are unique per dataset, not globally. Dataset hashes include the full contract and are independent of row order. ForecastLab ships **synthetic** CSV fixtures for software tests (`fixtures/benchmarks/synthetic_binary.csv`) and a blank template (`fixtures/benchmarks/import_template.csv`). The current built-in identity is `forecastlab.synthetic.binary` version `2`. Those rows are labeled synthetic in the UI. They are not a public scoring set. This MVP does not invent public questions or scrape Metaculus.

## Fair baseline

Every default comparison uses:

1. `single_agent_equal_budget_v1`
2. `three_track_equal_budget_v1`

The full ensemble remains optional. `three_track_full_v1` is the higher-research product profile. Older IDs remain for the demo. Every profile on one benchmark question forecasts the same stored contract.

## Scoring

- Brier: `(p - y)^2`
- Log loss: uses clipped probabilities so zeros do not explode
- Also reported: mean/median **total** cost, latency, completion/partial/failure rates
- All-valid metrics include full and partial probabilities
- Full-run-only metrics exclude partials and failures
- Brier-per-dollar uses total run cost, including failed attempts and search charges
- Experiment spend includes failed-task cost; failed tasks do not disappear from commercial reporting
- Paired comparisons are produced for both all-valid and full-only sets

## Time and leakage

Backtests enforce `as_of` on publication and snapshot timestamps. Search snippets dated after the cutoff cannot be used as evidence. Model weights may still contain later facts. Call this an evidence-cutoff backtest.

Prefer temporal splits: forecast dates before resolution dates, and never score a run with evidence published after `forecast_date`.

## Calibration displays

Reliability is computed separately per profile and never mixed. A reliability diagram is shown only when at least 20 resolved predictions exist for that profile. Twenty observations are a display threshold, not enough for a calibration claim.

See `docs/BENCHMARK_EXPERIMENTS.md`.

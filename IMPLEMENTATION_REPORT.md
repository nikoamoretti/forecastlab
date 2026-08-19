# Implementation report

## Created

- Python forecasting package with aggregation, evaluation, providers, fetch/SSRF, Wayback helpers, watchers, and the three-track engine.
- FastAPI app with SQLite, durable jobs, settings, demo indicators, benchmark import, and Markdown/JSON export.
- Next.js UI: board, new question, report, lab, settings.
- Mock fixtures, synthetic benchmark CSV, Mac start/stop scripts, Docker Compose, tests, and docs.

## Decisions

- New repo at `~/Projects/forecastlab`. The home-directory Civic Ledger / RailHub checkout was not modified.
- Sequential track execution, informational independence.
- Logit-mean aggregation in code with 10% shrinkage toward the base-rate track.
- Historical mode labeled as evidence-cutoff backtest.
- Synthetic benchmarks only unless the user imports data.

## How to run

`./scripts/dev_up.sh` then http://127.0.0.1:3000

API: http://127.0.0.1:8765/health

## Tests

See the final BUILD_REPORT.md after the test run in this session.

## Next milestone

Replace synthetic lab rows with a small, dated, user-imported set of resolved binary questions and report Brier/log loss with confidence intervals — still without claiming calibration until n is honest.

# ForecastLab Implementation Plan

## Context

The Cursor workspace opened on the home directory, which is an unrelated Civic Ledger / RailHub git checkout. That repository was **not** modified. ForecastLab is a new local-first project at `~/Projects/forecastlab` on branch `grok/forecastlab-mvp`.

No reusable forecasting engine, provider adapters, or evaluation lab existed to import.

## Assumptions

- Product name: **ForecastLab**. Binary questions only. Single user. No auth.
- Default mode is **mock/demo** and requires no paid credentials.
- App: http://127.0.0.1:3000
- API: http://127.0.0.1:8765
- SQLite + Alembic for local development; schema stays PostgreSQL-compatible.
- OpenAI-compatible model adapter covers xAI and OpenAI. Tavily is the live search adapter.
- Missing search keys do not break demo, manual URL evidence, or mock mode.
- JSONPath-like watchers support dotted paths and numeric indexes only (`$.foo.bar[0]`).
- Reliability diagrams appear only when at least **20** resolved benchmark predictions exist.
- Benchmark fixtures shipped with the app are **synthetic** and labeled as such. No invented public outcomes.
- Metaculus import is omitted if a reliable public API is not available during implementation.
- Tracks run sequentially for SQLite simplicity. Informational independence is enforced: no track sees another track’s probability or reasoning before aggregation.
- Historical mode is labeled **Evidence-cutoff backtest mode**. Model-pretraining leakage is documented, not claimed away.
- Auto-rerun on watcher changes defaults to **off**.

## Architecture

- `packages/forecasting`: typed engine, aggregation, evaluation, providers, fetch/SSRF, Wayback eligibility, watchers.
- `apps/api`: FastAPI, SQLAlchemy models, durable jobs, worker, settings/secrets, demo fixtures.
- `apps/web`: Next.js App Router UI.
- Durable DB-backed jobs (`pending/running/completed/failed`) with heartbeats and restart recovery.
- Secrets in `data/local/credentials.json` (mode `0600`, gitignored). Never returned to the browser or written to logs.

## Implementation sequence

1. Foundation: schemas, providers, mock adapters, jobs, health.
2. Engine: operationalize → independent tracks → evidence → aggregate → version.
3. Product UI: dashboard, new forecast, contract review, progress, report, settings, export.
4. Evaluation lab: import, baseline vs ensemble, Brier/log loss, cost/latency.
5. Watchers: HTML/JSON, demo indicators, simulate change, stale + rerun.
6. Verification: pytest, Playwright, ruff, mypy, production web build, browser inspection.

## Success

A Mac user can double-click `Start ForecastLab.command`, complete the sample forecast without API keys, inspect three tracks and deterministic aggregation, simulate a watcher update, and produce a second forecast version. The same workflow works with user-supplied OpenAI-compatible and Tavily credentials.

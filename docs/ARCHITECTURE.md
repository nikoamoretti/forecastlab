# Architecture

ForecastLab is a local-first process, not a hosted agent mesh.

## Layout

- `packages/forecasting`: typed engine, aggregation, evaluation, providers, fetch/SSRF, Wayback eligibility, watchers.
- `apps/api`: FastAPI, SQLAlchemy, durable jobs, settings, demo indicators.
- `apps/web`: Next.js App Router UI.
- `configs/forecast_profiles`: versioned YAML profiles.
- `prompts`: versioned prompt files.
- `fixtures`: local HTML sources and synthetic benchmarks.

## Request path

1. The UI creates a question and optionally a run.
2. The API writes a `Job` with an idempotency key.
3. A worker claims the job, heartbeats, and executes `run_forecast_engine`.
4. Each track plans, searches, fetches, and forecasts without seeing other tracks.
5. Aggregation is ordinary Python. The model may summarize disagreement afterward; it cannot change the number.
6. A `ForecastVersion` is stored. Watches can mark the question stale.

## Providers

`ModelProvider.complete_json` and `SearchProvider.search` are the only model/search seams. Mock implementations satisfy the same types as the OpenAI-compatible and Tavily adapters.

## Jobs

Jobs are rows, not threads. Statuses: pending, running, completed, failed. Heartbeats older than 45 seconds are recovered on worker start. Duplicate `idempotency_key` values reuse the existing job.

## Storage

SQLite by default. Schema types stay PostgreSQL-friendly. Secrets live in `data/local/credentials.json` and are never selected into API responses. Local boot uses SQLAlchemy `create_all`. Alembic is installed for later revisioned migrations; do not treat the MVP database as a production cluster.

SQLite strips timezone info on read. Health checks, watch polling, and latency math always run datetimes through `as_utc`.

## Request commit timing

FastAPI dependencies that `yield` a session commit *after* the HTTP response is sent. Write endpoints therefore call `db.commit()` before returning so a follow-up operationalize or worker claim can see the row.

## UI proxy

The Next.js app calls relative `/api/...` and `/demo/...` routes. Catch-all App Router handlers forward those requests to `http://127.0.0.1:8765` so the browser does not depend on cross-origin access to the API port.

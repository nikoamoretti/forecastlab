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

Jobs are rows, not threads. Statuses: pending, running, completed, failed. A worker claims a pending job with `UPDATE ... WHERE status='pending' RETURNING` under a SQLite write lock. Leases last 180 seconds. A background heartbeat refreshes both the job lease and `/health/worker` during long provider calls. Transient provider errors (timeouts, HTTP 429, HTTP 5xx) retry with backoff and return `rescheduled` or `exhausted`. Exhaustion marks the job, run, task, and experiment terminal. Configuration, evidence-integrity, structured-output, and other 4xx failures do not retry. Duplicate `idempotency_key` values reuse the existing job. `persist_engine_result` is idempotent on `run_id`.

A benchmark task is one durable identity for its whole lifetime, including crashes and retries:

`BenchmarkTask` → one benchmark-only `Question` → one `ForecastRun` (`forecast_runs.benchmark_task_id`, unique) → zero or one `BenchmarkResult`.

## Storage

SQLite by default. Schema types stay PostgreSQL-friendly. Secrets live in `data/local/credentials.json` and are never selected into API responses. API and worker startup inspect the current revision on a dedicated connection, skip Alembic when already at head, and otherwise upgrade under a file lock. A revision error fails closed. `20260818_0001` creates a complete empty schema. `20260819_0002` inspects an original MVP database and adds missing integrity columns, experiment tables, backfills, and uniqueness constraints. `20260819_0003` adds per-experiment profile snapshots, stored resolution-contract fields, and `UNIQUE(dataset_id, import_hash)`. `20260819_0004` adds `ForecastRun.benchmark_task_id`, task `question_id` / `error_category`, and `BenchmarkResult.partial`. Isolated unit tests may still call `create_all`. Back up `data/forecastlab.db` before the first launch after an upgrade (`cp data/forecastlab.db data/forecastlab.db.bak`). Do not treat the local database as a production cluster.

SQLite strips timezone info on read. Health checks, watch polling, and latency math always run datetimes through `as_utc`.

## Request commit timing

FastAPI dependencies that `yield` a session commit *after* the HTTP response is sent. Write endpoints therefore call `db.commit()` before returning so a follow-up operationalize or worker claim can see the row.

## UI proxy

The Next.js app calls relative `/api/...` and `/demo/...` routes. Catch-all App Router handlers forward those requests to `FORECASTLAB_API_ORIGIN` (default `http://127.0.0.1:8765`) so the browser does not depend on cross-origin access to the API port. Docker Compose sets `FORECASTLAB_API_ORIGIN=http://api:8765` on the web service.

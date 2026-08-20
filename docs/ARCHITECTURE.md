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

`ModelProvider.complete_json` and `SearchProvider.search` are the only model/search seams. Mock implementations satisfy the same types as the OpenAI-compatible and Tavily adapters. The shared HTTP transport may stay OpenAI-compatible while `provider_id` (`xai`, `openai`, or `openai_compatible`) is the identity used for ledger rows, `ModelUsage.provider`, pricing lookup, cost labels, provider audit output, and report execution identity. Each logical model call is reserved in memory (`budget.reserve_model_call`) before the provider adapter starts. Every physical model request reserves estimated input tokens, maximum output tokens, and both input and output estimated cost. Before every physical HTTP request, `PersistentUsageLedger` writes that reservation through a short-lived database session, checks the run-lifetime ceiling, and only then calls the provider. Timeouts, HTTP 429, and HTTP 5xx without usage keep the conservative input-plus-output reservation. Successful requests reconcile against actual usage. OpenAI-compatible and search adapters use an explicit retry loop (`run_physical_attempts`) so every physical attempt is a ledger row. Worker rollback cannot erase those rows. Retrying the same `ForecastRun` initializes `Budget` from persisted ledger totals; it does not receive a fresh full budget. `ForecastRun.cost_usd` is total lifetime cost (model + search + failed attempts). Each `ForecastRunAttempt` stores only ledger usage for its own `run_attempt_id`. ForecastLab does not claim a provider-enforced hard dollar cap.

Search and fetch caches live on one `RunCache` per forecast run. The cache is keyed to run id, model provider, search provider, mode, `as_of`, and configuration hash. A later run, including a rerun after a watch change, starts empty and fetches fresh evidence.

## Jobs

Jobs are rows, not threads. Statuses: pending, running, completed, failed. A worker claims a pending job with `UPDATE ... WHERE status='pending' RETURNING` under a SQLite write lock. Leases last 180 seconds. A background heartbeat refreshes both the job lease and `/health/worker` during long provider calls. Transient provider errors (timeouts, HTTP 429, HTTP 5xx) retry with backoff and return `rescheduled` or `exhausted`. Exhaustion marks the job, run, task, and experiment terminal. Configuration, evidence-integrity, structured-output, and other 4xx failures do not retry. Duplicate `idempotency_key` values reuse the existing job. `persist_engine_result` is idempotent on `run_id`.

A benchmark task is one durable identity for its whole lifetime, including crashes and retries:

`BenchmarkTask` → one benchmark-only `Question` → one `ForecastRun` (`forecast_runs.benchmark_task_id`, unique) → zero or one `BenchmarkResult`.

## Storage

SQLite by default. Schema types stay PostgreSQL-friendly. Secrets live in `data/local/credentials.json` and are never selected into API responses. API and worker startup inspect the current revision on a dedicated connection, skip Alembic when already at head, and otherwise upgrade under a file lock. A revision error fails closed. `20260818_0001` is a frozen explicit Alembic baseline (no live `Base.metadata.create_all()`). `20260819_0002` inspects an original MVP database and adds missing integrity columns, experiment tables, backfills, and uniqueness constraints. `20260819_0003` adds per-experiment profile snapshots, stored resolution-contract fields, and `UNIQUE(dataset_id, import_hash)`. `20260819_0004` adds `ForecastRun.benchmark_task_id`, task `question_id` / `error_category`, and `BenchmarkResult.partial`. `20260819_0005` adds run-attempt and provider-call ledger tables, run cost breakdown columns, final Wayback metadata, environment-identity columns, built-in dataset identity, and the remaining foreign keys. `20260819_0006` adds `ForecastRunAttempt.provider_request_count` so attempt totals stay attempt-specific. Isolated unit tests may still call `create_all`. Fresh empty→head and original-MVP→head databases must match on tables, columns, nullability, keys, and indexes, allowing only the documented SQLite representation differences. `PRAGMA foreign_key_check` must be empty. Back up `data/forecastlab.db` before the first launch after an upgrade (`cp data/forecastlab.db data/forecastlab.db.bak`). Do not treat the local database as a production cluster.

SQLite strips timezone info on read. Health checks, watch polling, and latency math always run datetimes through `as_utc`.

## Request commit timing

FastAPI dependencies that `yield` a session commit *after* the HTTP response is sent. Write endpoints therefore call `db.commit()` before returning so a follow-up operationalize or worker claim can see the row.

## UI proxy

The Next.js app calls relative `/api/...` and `/demo/...` routes. Catch-all App Router handlers forward those requests to `FORECASTLAB_API_ORIGIN` (default `http://127.0.0.1:8765`) so the browser does not depend on cross-origin access to the API port. Docker Compose sets `FORECASTLAB_API_ORIGIN=http://api:8765` on the web service.

## Version and Python lock

Application version `0.3.1` lives in `packages/forecasting/forecastlab/version.py`. The Python package, FastAPI app, frozen environment identity, `/health`, `/api/meta`, and the Lab nav derive from that file. Real experiments require a committed `uv.lock` (or `poetry.lock`) hash. Synthetic experiments may still run when explicitly marked synthetic. Local scripts, GitHub Actions, and Docker Compose install from that lockfile instead of resolving broad minimum versions on every run.

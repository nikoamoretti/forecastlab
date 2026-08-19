# ForecastLab integrity repair report

Date: 2026-08-19. Branch: `grok/integrity-evaluation-repair`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

Reviewed baseline: `2caa33abc0cbd3e2c379de0763f13878839b3b6e` on `grok/forecastlab-mvp` (not modified).

## Completion status

The integrity and evaluation repair is implemented on this branch. ForecastLab remains the same local-first demo workflow. Live and backtest execution now fail closed. Historical retrieval no longer falls back to the current page. Benchmarks are asynchronous experiments in actual backtest mode. Job claims, persistence, and HTTP retrieval are hardened. GitHub Actions is present and does not require paid keys.

This pass does not claim calibration and does not claim that ForecastLab matches or outperforms Preseen.

## Original defects

1. Provider factories silently returned mock model or mock search when a key was missing.
2. Live and backtest runs could use fixture hosts under `fixtures.forecastlab.local`.
3. Backtest fetch fell back to the current page when no eligible Wayback snapshot existed.
4. `POST /api/benchmarks/run` executed synchronously in `demo` mode and could enqueue plus execute the same run.
5. Job claim was select-then-update. Persistence could append a second forecast version.
6. The Settings cost ceiling was stored but not applied to the effective profile.
7. Reliability mixed profiles. Score summaries hard-coded `synthetic: true`.
8. The UI persisted `auto_rerun` but did not implement it.
9. Trusted-domain checks used hostname substring matching.
10. Alembic was declared but startup used `create_all` only.
11. Docker web could not reliably reach the API service name.

## Implemented corrections

### Execution identity

- `ExecutionContext` is built by `resolve_execution_context()` before any job or run is created.
- Demo always uses mock model and mock search, allows fixture evidence, and never spends even if live keys exist.
- Live requires a non-mock model, key, model name, applicable base URL, and a non-mock search provider. Missing configuration returns HTTP 422 and creates no pending run.
- Backtest requires `as_of` and real providers unless the experiment is an explicit synthetic fixture run.
- Synthetic status is never inferred from missing credentials.
- Profile, prompt, execution, dataset, and experiment hashes use canonical JSON.
- Git commit is recorded when available and stored as null otherwise.
- API keys are excluded from contexts, exports, and benchmark records.
- `/health/providers` returns demo/live/backtest readiness without secrets.
- Settings can test model and search connections.

### Cost ceiling

- Effective cost is `min(yaml_profile.max_estimated_cost_usd, settings.max_cost_usd)`.
- The shared loaded YAML profile is copied, not mutated.
- Pre-run estimates that exceed the ceiling are rejected.
- Pricing rates live in `configs/pricing/models.yaml` and are labeled estimated/manual.
- Budget reservations happen before each provider call. Partial results survive a budget stop.

### Historical evidence

- Backtest eligibility requires an archive snapshot at or before `as_of`, or a dedicated historical adapter.
- No eligible snapshot means reject. The current page is never fetched as fallback.
- Wayback CDX queries include `to=<as_of>`, `filter=statuscode:200`, `limit=1`, `sort=reverse`.
- Search snippets are discovery only. Only fetched accepted documents enter the forecast packet.
- Live undated pages may be kept and labeled `published_at_unknown`.

### Benchmark experiments

- First-class `BenchmarkDataset`, `BenchmarkQuestion`, `BenchmarkExperiment`, `BenchmarkTask`, and `BenchmarkResult`.
- Import creates a named dataset with an immutable hash. Mixed synthetic/real files are rejected.
- Experiment creation freezes configuration, creates `questions × profiles` tasks, enqueues one job per task, and returns immediately.
- Real tasks use `mode=backtest` and `as_of=forecast_date`.
- Benchmark-only questions have `is_benchmark=true` and are filtered from the board.
- Reliability is per profile. Paired comparisons and a deterministic bootstrap (seed `20260818`, 2000 samples, 95% percentile) are returned.
- Equal-budget profiles `single_agent_equal_budget_v1` and `three_track_equal_budget_v1` share configured ceilings.

### Jobs and retrieval

- SQLite-safe atomic claim: `UPDATE ... WHERE status='pending' RETURNING` under a write lock.
- 180s leases with a heartbeat on a short-lived session.
- Tenacity retries only `TransientProviderError`.
- `persist_engine_result()` returns the existing version when a run is already persisted.
- Shared `safe_get()` for evidence, Wayback, and external watchers: no unrestricted redirects, hop revalidation, streamed byte cap, content-type allowlist, SSRF checks.
- Trusted domains use exact hostname boundaries.
- Auto-rerun is disabled. Copy: “Changes mark the forecast stale. Reruns require user action.”

### Database

Startup applies Alembic to head under a lock and fails closed. Isolated unit tests may still call `create_all`. Original MVP databases are upgraded in place by `20260819_0002`. Back up `data/forecastlab.db` before the first launch after an upgrade.

## Database migrations added

| Revision | File | Role |
|---|---|---|
| `20260818_0001` | `alembic/versions/20260818_0001_integrity_schema.py` | Baseline `create_all` for an empty database. Not rewritten. |
| `20260819_0002` | `alembic/versions/20260819_0002_legacy_integrity_upgrade.py` | Inspect-and-alter upgrade from the original MVP schema, including backfills and uniqueness constraints. |

A file lock (`data/migrate.lock`, gitignored) prevents API and worker from migrating the same SQLite file concurrently.

## Exact tests

Python suite after the repair (from `/Users/nico-yardlogix/projects/forecastlab`):

```text
python -m pytest -q
...........................................................              [100%]
59 passed
```

Focused additions include:

- Execution identity: demo mock-only, live 422, no silent mock, hash changes, ceiling, readiness.
- Evidence cutoff: no snapshot, undated current page, snapshot after cutoff, newest prior snapshot, rejected docs never enter the packet, snippets are not evidence.
- Experiments: dataset hash, mixed import reject, task count, backtest `as_of`, idempotent persist, board filter, per-profile reliability, synthetic flag from dataset, deterministic bootstrap, equal-budget ceilings.
- Jobs: concurrent claim, heartbeat, transient vs permanent retry, exhaustion.
- HTTP/watchers: loopback, private redirect, oversized body, unsafe content type, exact hostname boundaries.
- Migrations: Alembic creates integrity tables.
- Frontend: Playwright demo happy path and synthetic experiment path.

## Exact local URLs

- App: http://127.0.0.1:3000
- API health: http://127.0.0.1:8765/health
- Worker health: http://127.0.0.1:8765/health/worker
- Providers: http://127.0.0.1:8765/health/providers
- Execution preview: http://127.0.0.1:8765/api/execution/preview
- Demo indicator: http://127.0.0.1:8765/demo/indicators/unemployment

## Demo verification

Playwright `e2e/happy-path.spec.ts` and a rendered report at http://127.0.0.1:3000/forecasts/a95e35e9-3c99-4a62-9d35-46ed2e2dcebf:

- Sample question operationalized and launched in demo.
- Ensemble 37.4%. Tracks 42.0% / 31.0% / 38.0%.
- Execution strip: DEMO, mock model, mock search, `demo_fixtures`, fixture evidence used yes, profile `three_track_ensemble`, configuration hash, git commit, $5.00 ceiling / $0.0000 actual.
- Synthetic-execution warning visible.
- Watch change marked stale. Explicit rerun created version 2. No auto-rerun claim.

## Benchmark-experiment verification

Playwright `e2e/experiment.spec.ts` and Lab at http://127.0.0.1:3000/lab:

- Dataset `synthetic_fixtures_v1` (5 questions, synthetic badge, dataset hash shown).
- Experiment created asynchronously and polled to completion.
- Task count 10 for two profiles × five questions.
- Per-profile scores and a paired comparison table.
- Notice: `Software-verification fixtures only. Not evidence of real-world forecasting quality.`
- Benchmark questions did not appear on the board.

## Docker verification

```text
docker compose up -d
```

- API healthy at http://127.0.0.1:8765/health
- Web 200 at http://127.0.0.1:3000
- From the web container: `fetch('http://api:8765/health')` returned `{"status":"ok","service":"forecastlab-api"}`
- Web proxy `/api/dashboard` returned the board payload
- Worker health `fresh: true`

The web image is `node:24-bookworm-slim` so Compose can start from a local image. A pull of `node:22-slim` hung on this machine. Next.js 15 runs on Node 24. Python image remains `python:3.12-slim`. Named volumes isolate container `node_modules` and `.next` so a Compose run cannot replace the Mac Next.js binary with a Linux one.

Compose was then torn down. The first one-click relaunch failed because Compose had overwritten host `node_modules` with Linux Next binaries. After named volumes were added and `npm ci` restored the Mac tree, `Start ForecastLab.command` reached http://127.0.0.1:3000, API `/health`, and a fresh worker.

## CI status

`.github/workflows/ci.yml` runs pytest, ruff, mypy, npm typecheck, production build, and Playwright without paid keys. The branch has not been pushed, so GitHub has not recorded a green run yet.

## Remaining limitations

- Binary questions only. No numeric forecasts or date distributions.
- One local user. No accounts, billing, or public deployment.
- Mock research is deterministic pipeline theater.
- Wayback coverage is incomplete; a missing snapshot is a rejection, not proof of absence.
- Model pretraining can still leak post-cutoff facts into backtests.
- Pricing rates are estimated unless a provider-reported table is supplied.
- Equal ceilings do not guarantee identical spend.
- Reliability diagrams stay hidden below 20 rows; 20 rows are not a calibration claim.
- Auto-rerun remains unimplemented by choice.
- Next 15 still pulls transitive `postcss` / `sharp` advisories. `npm audit fix --force` would jump to Next 16 and was not applied.
- Isolated unit tests may still use `create_all`. An existing MVP database is upgraded in place; back it up first. Uniqueness is installed only after exact duplicate clones are collapsed. Distinct duplicate history stops the migration.

## Recommended next milestone

Import a small dated set of **real** resolved binary questions into a non-synthetic dataset and run the existing experiment system (`single_agent_equal_budget_v1` vs `three_track_equal_budget_v1`, then `three_track_full_v1`) under genuine backtest evidence rules. Do not add hierarchical subforecasts or extra connectors until that evaluation path has been used on real rows.

# ForecastLab build report

Date: 2026-08-19. Branch: `grok/integrity-evaluation-repair`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

The previous MVP report on `grok/forecastlab-mvp` @ `2caa33a` remains the reviewed baseline. This file records the integrity-repair verification pass. See `INTEGRITY_REPAIR_REPORT.md` for defects, corrections, and remaining limits.

## Completion status

The local-first demo still works without paid keys. Live and backtest modes fail closed. Historical evidence no longer falls back to the current page. Benchmarks are asynchronous experiments. Settings cost ceilings are enforced. GitHub Actions is present.

Temporary product name: **ForecastLab**. Preseen branding was not used. No calibration claim.

## What changed in this pass

- Explicit `ExecutionContext` on every run.
- Provider factories no longer substitute mocks because a key is missing.
- Strict historical evidence policy and CDX cutoff queries.
- First-class benchmark datasets, experiments, and tasks.
- Atomic job claims, leases, transient-only retries, idempotent persist.
- Shared safe HTTP client for evidence, Wayback, and external watchers.
- Alembic baseline `20260818_0001`.
- Docker web uses `FORECASTLAB_API_ORIGIN=http://api:8765`.
- UI execution strip, readiness, connection tests, and Lab experiment polling.

## Local URLs

- App: http://127.0.0.1:3000
- API health: http://127.0.0.1:8765/health
- Worker health: http://127.0.0.1:8765/health/worker
- Providers: http://127.0.0.1:8765/health/providers
- Demo indicator: http://127.0.0.1:8765/demo/indicators/unemployment

## How to launch

Double-click `Start ForecastLab.command`, or `./scripts/dev_up.sh`. Stop with `Stop ForecastLab.command` or `./scripts/dev_down.sh`.

If a local database predates the integrity schema, back it up (`cp data/forecastlab.db data/forecastlab.db.bak`) and let Alembic upgrade it in place.

## Exact test results (this session)

Commands run from `/Users/nico-yardlogix/projects/forecastlab`.

```text
python -m pytest -q
59 passed
```

```text
python -m ruff check packages apps/api tests
All checks passed!
```

```text
python -m mypy
Success: no issues found in 43 source files
```

```text
cd apps/web && npm run typecheck
# tsc --noEmit  (exit 0)
```

```text
cd apps/web && npm run build
# Next.js 15.5.23 production build succeeded
```

```text
cd apps/web && npx playwright test --workers=1 --reporter=line
[chromium] › e2e/experiment.spec.ts
[chromium] › e2e/happy-path.spec.ts
2 passed
```

`npm audit --omit=dev` reported 3 high findings in Next 15 transitive `postcss` / `sharp`. A force fix would install Next 16 and was not applied.

## Visual verification

Inspected in the running app:

- Board: demo questions only; sample unemployment question at 37.4%; no synthetic series mixed into the working board.
- New question: mode readiness, workload estimate, effective ceiling, profile list including equal-budget profiles.
- Report: DEMO execution strip with mock providers, `demo_fixtures`, fixture evidence used, configuration hash, commit, ceiling vs actual, synthetic warning, watcher honesty copy.
- Settings: provider selects, live/backtest readiness, connection tests, clear-key actions, estimated-cost note.
- Lab: synthetic dataset badge and hash, profile checkboxes, Create experiment (async), no leftover “Run baseline vs ensemble” primary action.

## Docker

`docker compose up -d` then:

- API `/health` 200
- Web `/` 200
- Web container `fetch('http://api:8765/health')` 200
- Web proxy `/api/dashboard` 200
- Worker heartbeat fresh

Web image: `node:24-bookworm-slim` (local; `node:22-slim` pull hung). Named volumes keep container `node_modules` and `.next` off the Mac tree. Stack torn down after the smoke test.

## CI

`.github/workflows/ci.yml` is present. The branch was not pushed, so GitHub has not run it.

## Recommended next milestone

Run the existing experiment system on a small **real**, dated, user-imported resolved set. Keep synthetic fixtures labeled as software verification only.

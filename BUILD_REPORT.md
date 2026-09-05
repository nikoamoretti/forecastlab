# ForecastLab build report

Date: 2026-08-19. Current verification branch: `grok/live-readiness`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

The previous MVP report on `grok/forecastlab-mvp` @ `2caa33a` remains the reviewed baseline. Integrity-repair reports remain historical. **Current live-provider readiness numbers are in `LIVE_READINESS_REPORT.md`.** Integrity-repair-3 remains the prior schema/ledger certificate. Do not use older test counts as the current status.

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

## Exact test results (first integrity pass; superseded)

The 2026-08-19 `grok/integrity-evaluation-repair` session recorded 59 pytest tests, mypy on 43 files, and a local Playwright pass. That count is historical.

The current `grok/live-readiness` gate is **151 pytest tests**, mypy on **50 files**, install from `uv.lock`, `npm ci` + typecheck + production build, and Playwright `2 passed`. See `LIVE_READINESS_REPORT.md` for the exact command output.

`npm audit --omit=dev` still reported 3 high findings in Next 15 transitive `postcss` / `sharp`. A force fix would install Next 16 and was not applied.

## Visual verification

Inspected in the running app:

- Board: demo questions only; sample unemployment question at 37.4%; no synthetic series mixed into the working board.
- New question: mode readiness, workload estimate, effective ceiling, profile list including equal-budget profiles.
- Report: DEMO execution strip with mock providers, `demo_fixtures`, fixture evidence used, configuration hash, commit, ceiling vs actual, synthetic warning, watcher honesty copy.
- Settings: provider selects, live/backtest readiness, connection tests, clear-key actions, estimated-cost note.
- Lab: synthetic dataset badge and hash, profile checkboxes, Create experiment (async), Experiment spend totals, no leftover “Run baseline vs ensemble” primary action.
- Nav shows application version `v0.3.1` from `/api/meta`.

## Docker

`docker compose up -d` then:

- API `/health` 200
- Web `/` 200
- Web container `fetch('http://api:8765/health')` 200
- Web proxy `/api/dashboard` 200
- Worker heartbeat fresh

Web image: `node:24-bookworm-slim` (local; `node:22-slim` pull hung). Named volumes keep container `node_modules` and `.next` off the Mac tree. Stack torn down after the smoke test.

## CI

`.github/workflows/ci.yml` is present and runs pytest, ruff, mypy, `npm ci`, typecheck, production build, and Playwright without paid keys. Judge CI against the exact SHA on `grok/live-readiness` in GitHub Actions. Do not treat this file as a CI certificate.

## Recommended next milestone

Run the existing experiment system on a small **real**, dated, user-imported resolved set. Keep synthetic fixtures labeled as software verification only.

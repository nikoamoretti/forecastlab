# ForecastLab live-readiness report

Date: 2026-08-19. Branch: `grok/live-readiness`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

This is a small live-provider readiness patch. It does not add forecasting architecture, agents, benchmark features, connectors, authentication, deployment, or a visual redesign. Previous branches were not modified, merged, or force-pushed.

## Starting commit

`c18a5e829cb2b1dd96c87d3d258ed02bc337469d` on `grok/integrity-repair-3`.

## What changed

1. Every physical OpenAI-compatible model request reserves estimated input tokens, maximum output tokens, and both estimated input and output cost. Timeouts, 429s, and 5xx responses without usage keep that reservation. Successful calls still reconcile actual usage.
2. Transport stays OpenAI-compatible. Vendor identity (`xai`, `openai`, `openai_compatible`) is used for ledger rows, `ModelUsage`, pricing, cost labels, provider audit, and report execution identity.
3. `uv.lock` is committed. Local scripts, GitHub Actions, and Docker Compose install the locked environment. Real experiments fail closed without a Python lock hash. Synthetic experiments may still run when marked synthetic.
4. Application version is `0.3.1` from `packages/forecasting/forecastlab/version.py`. FastAPI, frozen environment identity, `/health`, `/api/meta`, and the nav label use that value.
5. `ForecastRun` totals stay cumulative. Each `ForecastRunAttempt` contains only ledger usage for its own `run_attempt_id`.
6. Benchmark summaries and the Lab UI report total experiment cost, full/partial/failed-task cost, mean cost per started task, model cost, search cost, and failed-attempt cost. Failed tasks stay in commercial totals.
7. Rejected final Wayback captures keep the actual final snapshot URL, timestamp, archived original URL, and failure reason. The mismatching archived original is not replaced with the requested URL.
8. `scripts/paid_smoke.py` executes nothing unless `FORECASTLAB_RUN_PAID_SMOKE=1` and live model plus search credentials exist. HTTP-stubbed tests cover the runner. No paid call was executed.

## Exact command for the first paid smoke forecast

```bash
FORECASTLAB_RUN_PAID_SMOKE=1 uv run python scripts/paid_smoke.py
```

Requirements the runner checks itself: clean Git working tree, committed `uv.lock`, live model and search credentials (not hard-coded), profile `live_smoke_v1`, one binary question with a manually specified BLS U-3 resolution contract, one research track, one subquestion, one search call, at most two fetched documents, at most three model calls, 4000 tokens, and a $0.25 estimated-cost ceiling. The run fails if mock or fixture evidence appears, vendor identity is wrong, a completed provider request is missing from the ledger, or lifetime totals exceed that ceiling under ForecastLab’s conservative accounting.

## Python tests

```text
python -m pytest -q
151 passed, 1 warning in 22.75s
```

Locked environment:

```text
uv sync --extra dev --frozen
python -m pytest -q
151 passed, 1 warning in 23.93s
```

Required coverage included:

- failed real-model request reserves input and output tokens and both reserved costs
- xAI identity remains `xai` through ledger, usage, and execution context
- OpenAI identity remains `openai`
- `uv.lock` is required for real experiments; synthetic experiments may run without it
- version is consistently `0.3.1`
- attempt totals are attempt-specific and sum to cumulative run totals
- total experiment cost includes failed tasks
- rejected Wayback records preserve actual final metadata
- paid smoke does nothing without opt-in
- paid smoke refuses missing credentials
- stubbed smoke completes with no mock or fixture evidence

## Ruff and mypy

```text
python -m ruff check packages apps/api tests
All checks passed
python -m mypy
Success: no issues found in 50 source files
```

The same commands passed again after `uv sync --extra dev --frozen`.

## Frontend and Playwright

```text
cd apps/web
npm ci
npm run typecheck
npm run build
# Next.js 15.5.23 production build succeeded

npx playwright test --workers=1 --reporter=line
[1/2] [chromium] › e2e/experiment.spec.ts:3:5 › synthetic benchmark experiment
[2/2] [chromium] › e2e/happy-path.spec.ts:3:5 › mock happy path
2 passed (11.6s)
```

`npm audit --audit-level=high` still reports 3 high Next 15 transitive findings (`postcss` / `sharp`). `npm audit fix --force` was not applied because it would install Next 16.

## Docker

```text
docker compose up -d
```

| Check | Result |
|---|---|
| API `/health` | `200` `{"status":"ok","service":"forecastlab-api","version":"0.3.1"}` |
| API `/health/db` | `200` `{"status":"ok"}` |
| API `/health/worker` | `fresh: true`, `status: idle` |
| API `/api/meta` | `application_version=0.3.1` |
| Web `/` | `200` |
| Web proxy `/api/dashboard` | `200` |
| Web container `fetch('http://api:8765/health')` | `200` including version `0.3.1` |
| Demo forecast | completed, ensemble ≈ `0.3738`, 1 run attempt, 19 ledger rows |
| Synthetic experiment | `completed`, `10/10` tasks, `0` failed |
| Experiment spend | present: total/full/partial/failed-task/mean/model/search/failed-attempt |
| Built-in dataset | one active `forecastlab.synthetic.binary` v2 |
| Board filter | no “synthetic series” titles |

```text
docker compose down
```

No paid call was made inside Docker.

## GitHub Actions

CI now installs with `uv sync --extra dev --frozen` and runs `uv run python -m pytest -q`, `uv run python -m ruff`, and `uv run python -m mypy`. Adding `.` to pytest `pythonpath` keeps `from tests...` imports working in the locked environment. Judge the Actions run against the exact final SHA after push. Do not treat an earlier integrity-repair SHA as this certificate.

## Secret scan

Tracked files contain only test placeholders (`sk-test`, `xai-test-key`, `tvly-test-key`, `sk-secret`) and the redaction regex. No live keys, bearer tokens, `.env`, credentials files, or SQLite databases are tracked.

## Paid call

Not executed. `FORECASTLAB_RUN_PAID_SMOKE` was unset during implementation, tests, CI preparation, and Docker verification.

## Remaining limitations

- Binary questions only.
- One local user. No accounts, billing, sharing, or public deployment.
- Mock model and search are deterministic pipeline theater, not live research.
- Live providers inherit vendor outages, rate limits, and prompt brittleness.
- A reserved failed request is conservative accounting, not a vendor invoice.
- Search prices remain estimated unless the provider reports a cost.
- Wayback coverage is incomplete. A rejected capture now keeps the actual archived original, but missing snapshots are still missing evidence.
- Model pretraining can leak post-cutoff facts into backtests.
- Synthetic fixtures cannot support product-level forecasting-quality claims.
- The first real paid smoke still has to be run by a human with live keys.

Do not merge and do not deploy.

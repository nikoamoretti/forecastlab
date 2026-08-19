# ForecastLab build report

Date: 2026-08-19. Branch: `grok/forecastlab-mvp`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

## Completion status

The local-first MVP is working in mock/demo mode without paid keys. A user can operationalize a binary question, run three independent tracks, inspect deterministic aggregation, read provenance, simulate a watcher change, and create a second forecast version. Synthetic benchmarks can be imported and scored against `single_agent_baseline` and `three_track_ensemble`. Live OpenAI-compatible and Tavily adapters are wired through Settings; they were not exercised with paid credentials in this session.

## What was built

- Python 3.12 package `forecastlab`: schemas, mock + OpenAI-compatible model providers, Tavily + mock search, SSRF-safe fetch, Wayback eligibility, coded logit aggregation, evaluation metrics, versioned prompts and YAML profiles.
- FastAPI app `forecastlab_api`: SQLite, durable jobs, worker, health endpoints, resolution contracts, forecast runs/versions, watchers, demo JSON indicators, benchmark import (CSV/JSON), Markdown/JSON export, write-only Settings.
- Next.js 15 App Router UI: Board, New question, Forecast report, Lab, Settings. Relative `/api` and `/demo` proxies to port 8765.
- One-click Mac launchers: `Start ForecastLab.command` / `Stop ForecastLab.command` wrapping `scripts/dev_up.sh` and `scripts/dev_down.sh`.
- Tests: 25 pytest cases, one Playwright happy path, ruff, mypy, `next build`.

Temporary product name: **ForecastLab**. Preseen branding was not used.

## Architecture decisions

- New repository. The home-directory Civic Ledger / RailHub checkout was not modified.
- Default mode is mock/demo. Missing search keys do not block demo, pasted URLs, or fixtures.
- Tracks run sequentially for SQLite; they do not see each other's probabilities before aggregation.
- Ensemble probability is equal-weight logit mean with 10% shrinkage toward the base-rate track (else 0.50). The LLM may summarize disagreement after the number is fixed.
- Historical mode is labeled **Evidence-cutoff backtest**. Model pretraining leakage is documented, not claimed away.
- Bundled lab rows are **synthetic**. Reliability diagrams require 20 resolved predictions.
- FastAPI write endpoints commit before returning so the next request (operationalize, worker) can see the row.
- SQLite datetimes are normalized with `as_utc` before subtraction.
- Secrets: `data/local/credentials.json` mode `0600`, never returned to the browser.

## Local URLs

- App: http://127.0.0.1:3000
- API health: http://127.0.0.1:8765/health
- Worker health: http://127.0.0.1:8765/health/worker
- Providers: http://127.0.0.1:8765/health/providers
- Demo indicator: http://127.0.0.1:8765/demo/indicators/unemployment

## How to launch

Double-click `Start ForecastLab.command`, or:

```bash
./scripts/dev_up.sh
```

Stop with `Stop ForecastLab.command` or `./scripts/dev_down.sh`.

Developer loop:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export PYTHONPATH="$PWD/packages/forecasting:$PWD/apps/api"
export FORECASTLAB_DATABASE_URL="sqlite:///$PWD/data/forecastlab.db"
python -m uvicorn forecastlab_api.main:app --host 127.0.0.1 --port 8765
python -m forecastlab_api.worker
cd apps/web && npm install && npm run dev -- --hostname 127.0.0.1 --port 3000
```

## Real-provider setup

1. Open http://127.0.0.1:3000/settings
2. Set model provider to `openai_compatible` (or `openai`).
3. Set base URL (xAI example: `https://api.x.ai/v1`; OpenAI: `https://api.openai.com/v1`).
4. Set model name and paste the model API key. The key is write-only after save.
5. Optionally set search provider `tavily` and a Tavily key. If the search key is missing, mock search and manual URLs still work.
6. Set a cost ceiling and timeout. Save.
7. Create a question with run mode `live`. Invalid credentials should fail the run with a stored error rather than a silent mock answer.

## Exact test results (this session)

Commands run from `/Users/nico-yardlogix/projects/forecastlab` unless noted.

```text
python -m pytest -q
.........................                                                [100%]
25 passed, 1 warning in 0.99s
```

Warning: Starlette TestClient deprecation (`install httpx2`) from FastAPI; not a ForecastLab failure.

```text
python -m ruff check packages apps/api tests
All checks passed!
```

```text
python -m mypy
Success: no issues found in 35 source files
```

```text
cd apps/web && npm run typecheck
# tsc --noEmit  (exit 0)
```

```text
cd apps/web && npm run build
# Next.js 15.5.23 production build succeeded
# Routes: / /new /lab /settings /forecasts/[id] /api/[...path] /demo/[...path] /_not-found
```

```text
cd apps/web && npx playwright test --reporter=line
[chromium] › e2e/happy-path.spec.ts:3:5 › mock happy path
1 passed (6.0s)
```

Playwright flow verified: mock mode → sample question → contract review → run → ensemble 37.4% → three tracks → evidence link present → simulate watch → rerun → 2 versions.

Live synthetic lab run after import/seed:

```text
POST /api/benchmarks/run → {"created": 10}
single_agent_baseline     n=5  Brier≈0.2453  failure_rate=0
three_track_ensemble      n=5  Brier≈0.2407  failure_rate=0
reliability available=false  sample_count=10  (threshold 20)
```

These scores are for **synthetic fixtures** only.

## Visual verification

Screenshots in `docs/screenshots/`:

- `board.png` — demo mode, questions, 37.4% ensemble, watcher events, New forecast CTA.
- `new.png` — binary question, deadline, mode, profile, resolution source, notes.
- `report.png` — ensemble 37.4%, stage timeline, three tracks (42% / 31% / 38%), evidence ledger, contract, versions, watchers.
- `lab.png` — synthetic disclaimer, baseline vs ensemble actions, profile YAML dump in JSON.
- `settings.png` — mock providers, keys write-only, cost ceiling, timeout.

The Playwright `screenshot` CLI was too fast for client fetch; screenshots were recaptured after `networkidle` + heading wait.

## Known limitations

See `docs/KNOWN_LIMITATIONS.md`. In short: binary only; local single user; mock research is deterministic pipeline theater; backtests are evidence-cutoff not leakage-free; Alembic is installed but local boot uses `create_all`; tracks are sequential; no calibration claim.

## Recommended next milestone

Replace the synthetic lab with a small, dated, user-imported set of **real** resolved binary questions (same as_of rules, same two profiles) and report Brier/log loss with confidence intervals. Do not call the product calibrated until that sample is large enough and out of sample.

# AGENTS.md

## Cursor Cloud specific instructions

ForecastLab is a local-first laboratory for binary (yes/no) forecasting questions. It has three
runnable pieces plus a shared Python library:

- `apps/api` (`forecastlab_api`): FastAPI backend on port `8765`, SQLite storage, durable jobs.
- `apps/api` worker (`forecastlab_api.worker`): background process that executes queued forecast runs.
- `apps/web` (`forecastlab-web`): Next.js 15 App Router UI on port `3000`, proxies `/api` and `/demo` to `127.0.0.1:8765`.
- `packages/forecasting` (`forecastlab`): schemas, providers, aggregation, evaluation library used by the API.

Default mode is **mock/demo**, so everything runs with no API keys. Live model/search providers are
configured through the Settings page and stored in `data/local/credentials.json`.

### Environment already prepared by the update script

The startup update script creates `.venv` (Python 3.12), installs the package with dev extras
(`pip install -e ".[dev]"`), and runs `npm install` in `apps/web`. You do not need to reinstall.
The system package `python3.12-venv` is required to create the venv and is expected to already be
present in the base image/snapshot (do not add it to the update script).

### Running the services (non-obvious caveats)

Do NOT use `scripts/dev_up.sh` / `Start ForecastLab.command` in cloud: they are `#!/bin/zsh`,
Mac-oriented, and call `webbrowser.open`, then `wait` (blocks). Start the three services directly
instead. The API and worker both need these env vars, and the worker is a **separate process** — the
API does not run runs by itself (`FORECASTLAB_EMBEDDED_WORKER=0`), so forecast runs stay `pending`
until the worker is running:

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD/packages/forecasting:$PWD/apps/api"
export FORECASTLAB_DATABASE_URL="sqlite:///$PWD/data/forecastlab.db"
export PYTHONUNBUFFERED=1
# terminal 1 (API):
python -m uvicorn forecastlab_api.main:app --host 127.0.0.1 --port 8765
# terminal 2 (worker):
python -m forecastlab_api.worker
# terminal 3 (web), from apps/web:
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Health checks: `GET /health`, `GET /health/worker` (expect `"fresh": true` once the worker is up),
`GET /health/providers`. On API startup a sample question and synthetic benchmarks are seeded.

### Lint / test / typecheck (see `Makefile` for the canonical commands)

- Python lint: `python -m ruff check packages apps/api tests` (passes).
- Python types: `python -m mypy` (passes).
- Python tests: `python -m pytest -q` from repo root (`pyproject.toml` sets `pythonpath`; 25 tests pass).
- Web types: `cd apps/web && npm run typecheck` (`tsc --noEmit`, passes).
- Web lint: `npm run lint` runs `next lint`, but no ESLint config is committed, so it drops into an
  **interactive setup prompt** and is not usable non-interactively. Use the typecheck as the web
  static check unless you intentionally add an ESLint config.
- Web e2e (`npm run test:e2e`, Playwright) needs browsers installed via `npx playwright install`.

### Known caveats

- `npm audit` reports high-severity transitive advisories in `postcss`/`sharp` via `next@15`; the only
  fix is a breaking `next@16` upgrade, so leave the pinned versions unless explicitly upgrading.

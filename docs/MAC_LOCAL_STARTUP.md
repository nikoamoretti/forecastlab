# Mac-local startup

ForecastLab's supported local Mac entry point is the existing `Start ForecastLab.command`. No competing launcher, package, installer, notarization, or deployment path is introduced.

## Start

From Finder, double-click `Start ForecastLab.command`. From Terminal, the equivalent command is:

```bash
./scripts/dev_up.sh
```

The launcher:

1. verifies `uv.lock` and `apps/web/package-lock.json`;
2. installs Python dependencies with `uv sync --extra dev --frozen`;
3. installs web dependencies with `npm ci` when they are not already present;
4. starts the API, worker, and web application;
5. waits for API, web, and fresh-worker readiness;
6. prints the local URL, `http://127.0.0.1:3000`, and opens it unless `FORECASTLAB_NO_BROWSER=1`.

Credentials remain in the existing local credentials store and are never printed. The launcher does not create or modify tracked files.

If readiness fails, the launcher names the relevant local log and shuts down the services it started. It does not claim success until all three surfaces are ready.

## Stop

Double-click `Stop ForecastLab.command`, press Control-C in the launcher Terminal window, or run:

```bash
./scripts/dev_down.sh
```

Shutdown uses the three recorded service PIDs. It does not issue a broad process-name kill, so unrelated Python or Node processes are left alone.

## Offline verification

Maintainers can verify the complete startup and shutdown path against isolated data, log, credentials, and port locations with mock providers:

```bash
uv run --frozen --no-sync python scripts/verify_mac_local_startup.py
```

The verifier makes no OpenAI or Tavily call, requires a fresh isolated SQLite database, waits for readiness, requests immediate clean shutdown, and rejects residual PID files.

This verifies the supported local startup mechanism. It does not establish live-provider reliability, forecasting quality, or calibration. The latest bounded live operational acceptance remains a hard 3-of-5 failure.

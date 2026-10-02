# ForecastLab

Local-first laboratory for **binary** questions. Mock mode runs with no API keys. Live mode can use an OpenAI-compatible model endpoint and either Tavily search or OpenAI's built-in web search (`openai_web_search`).

## Autopilot on Vercel

The private cloud mode selects and monitors U.S. macro questions under a frozen policy and spending limits. It starts paused and requires qualified live runs before enablement. See [Autopilot operations](docs/AUTOPILOT_OPERATIONS.md) for hosting, login, migration, restore, and release instructions.

## Run on a Mac

Double-click `Start ForecastLab.command`, or:

```bash
./scripts/dev_up.sh
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). Stop with `Stop ForecastLab.command` or `./scripts/dev_down.sh`.

The launcher installs from the frozen locks, waits for API/web/worker readiness, and shuts down by recorded service PID. See [Mac-local startup](docs/MAC_LOCAL_STARTUP.md).

Developer commands:

```bash
uv sync --extra dev --frozen
source .venv/bin/activate
pytest -q
cd apps/web && npm ci && npm run build
```

Application version is `0.3.1` from `packages/forecasting/forecastlab/version.py`. Real experiments require the committed `uv.lock`.

## First paid smoke forecast

This command does nothing unless you set the opt-in flag and already have live model plus search credentials in `data/local/credentials.json` or the matching environment. It spends real money under a tight cap. Do not run it from CI.

```bash
FORECASTLAB_RUN_PAID_SMOKE=1 uv run python scripts/paid_smoke.py
```

`live_smoke_v1` allows one research track, one subquestion, one search call, at most two fetched documents, at most three model calls, 4000 tokens, and a $0.25 estimated-cost ceiling. The runner refuses a dirty Git tree, a missing `uv.lock`, mock/fixture evidence, the wrong vendor identity, a missing ledger row, or lifetime cost above that ceiling.

## What it does

1. Turns a question into an editable resolution contract.
2. Runs independent tracks: base rate, current evidence, skeptic. A single-agent profile exists as a comparison, not a handicapped straw man.
3. Searches, fetches, and stores evidence with hashes, timestamps, and rejection reasons.
4. Aggregates track probabilities in code: clip, logit mean, shrink toward the base-rate track.
5. Versions the result. A watch can mark a question stale. Reruns require user action.
6. Lets the user audit and attach a source URL to a question or graph node. Intake creates no claim or run; accepted evidence enters only a fresh explicit run through the existing extractor.
7. Evaluation Lab runs asynchronous benchmark experiments. Real tasks use backtest mode. Creating an experiment freezes profiles, prompts, provider settings, the resolution contract, pricing, and the code/dependency identity. Later edits to those files or to Settings do not change that experiment. A benchmark task fails closed if the executing code or dependency identity differs from the freeze.

## Settings

The settings page stores provider, base URL, model name, keys, timeout, and a cost ceiling in `data/local/credentials.json` with mode `0600`. Keys are never returned to the browser. The cost ceiling applies to the full lifetime of a `ForecastRun`, including retries, failed attempts, and search charges. `cost_usd` is total lifetime cost. Live and backtest modes fail closed when providers are missing. Monetary figures are labeled provider reported, estimated, mixed, or unavailable.

## Honesty constraints

- Binary questions only.
- Historical mode is an **evidence-cutoff backtest**. Model pretraining can still leak later facts.
- Reliability diagrams appear only after 20 resolved predictions. Twenty rows are not a calibration claim.
- Bundled benchmarks are **synthetic fixtures**.
- The product does not claim calibration.
- Existing local MVP databases are upgraded in place. Fresh and upgraded databases share the same schema contract. Back up first: `cp data/forecastlab.db data/forecastlab.db.bak`.
- Built-in synthetic fixtures are versioned (`forecastlab.synthetic.binary` v2). Older versions stay as archived history.
- Lab summaries separate full, partial, and failed forecasts.

See `docs/` for method, evaluation protocol, architecture, security, and limits.

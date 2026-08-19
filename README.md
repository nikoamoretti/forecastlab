# ForecastLab

Local-first laboratory for **binary** questions. Mock mode runs with no API keys. Live mode can use an OpenAI-compatible model endpoint and Tavily search.

## Run on a Mac

Double-click `Start ForecastLab.command`, or:

```bash
./scripts/dev_up.sh
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). Stop with `Stop ForecastLab.command` or `./scripts/dev_down.sh`.

Developer commands:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
cd apps/web && npm install && npm run build
```

## What it does

1. Turns a question into an editable resolution contract.
2. Runs independent tracks: base rate, current evidence, skeptic. A single-agent profile exists as a comparison, not a handicapped straw man.
3. Searches, fetches, and stores evidence with hashes, timestamps, and rejection reasons.
4. Aggregates track probabilities in code: clip, logit mean, shrink toward the base-rate track.
5. Versions the result. A watch can mark a question stale. Reruns require user action.
6. Evaluation Lab runs asynchronous benchmark experiments. Real tasks use backtest mode. Creating an experiment snapshots profiles, prompts, provider settings, and the resolution contract. Later edits to those files or to Settings do not change that experiment.

## Settings

The settings page stores provider, base URL, model name, keys, timeout, and a cost ceiling in `data/local/credentials.json` with mode `0600`. Keys are never returned to the browser. The cost ceiling is applied to every run. Live and backtest modes fail closed when providers are missing. Model cost figures may be estimated.

## Honesty constraints

- Binary questions only.
- Historical mode is an **evidence-cutoff backtest**. Model pretraining can still leak later facts.
- Reliability diagrams appear only after 20 resolved predictions. Twenty rows are not a calibration claim.
- Bundled benchmarks are **synthetic fixtures**.
- The product does not claim calibration.
- Existing local MVP databases are upgraded in place. Back up first: `cp data/forecastlab.db data/forecastlab.db.bak`.

See `docs/` for method, evaluation protocol, architecture, security, and limits.

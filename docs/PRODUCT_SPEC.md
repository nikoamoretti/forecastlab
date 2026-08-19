# Product spec (MVP)

ForecastLab is a local-first laboratory for **binary** future-event questions.

## In scope

- One user, no authentication
- Question operationalization into an editable resolution contract
- Three independent tracks (base rate, current evidence, skeptic) plus a single-agent baseline profile
- Dynamic subquestions, search, fetch, provenance
- Deterministic logit-mean aggregation with shrinkage
- Forecast versions, Markdown/JSON export
- Synthetic benchmark import and Brier / log-loss comparison
- URL/JSON watchers with an opt-in auto-rerun (default off)
- Mock mode without credentials; live OpenAI-compatible + Tavily when keys are supplied
- One-click Mac start

## Out of scope

Numeric or date distributions, model training, payments, accounts, multi-tenancy, graph databases, arbitrary code execution, public publishing, autonomous prompt mutation, and any claim of calibration without sufficient out-of-sample data.

## Honest labels

- The headline number is an **ensemble estimate**, not a calibrated probability.
- Historical runs are **evidence-cutoff backtests**.
- Bundled benchmark rows are **synthetic fixtures**.

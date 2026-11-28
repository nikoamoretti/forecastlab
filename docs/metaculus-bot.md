# Metaculus FutureEval bot

`scripts/metaculus_bot.py` (logic in `packages/forecasting/forecastlab/metaculus_bot.py`) forecasts open yes/no
questions in the Fall 2026 FutureEval tournament (id 33121, slug `fall-futureeval-2026`). Binary only.

**Per run:** list open binary questions, skip those this account already forecast, take up to 5 (and no more than 30
per rolling 24 hours, counted from our own forecast timestamps). For each: a model writes search queries, Tavily
searches with forecasting and prediction-market sites excluded, a model writes a research digest, then a separate
blind call sees only the question text and the digest and returns `{probability, reasoning}` (clipped to 1%-99%).
The forecast is posted, then a private comment with the reasoning and research highlights.

**Isolation:** the question view is an allowlist (title, description, resolution criteria, fine print, open, close
and resolve times, today). Community prediction, aggregations and forecaster counts are never passed on. Digests are
told to drop any market or forecasting-platform odds.

**Schedule:** `.github/workflows/metaculus-bot.yml`, cron `7,27,47 * * * *` plus manual dispatch (inputs: `dry_run`,
`tournament`, `max_questions`; use `bot-testing-area` to test). Read-only repo permissions; it never pushes. The run
log JSON is uploaded as an artifact (30 days) and never committed.

**Token:** add the repo secret `METACULUS_TOKEN` (the bot account's API token; sent as `Authorization: Token ...`).
Without it every run prints `METACULUS_TOKEN not set; skipping` and stays green. Model and search keys use the same
secrets as the daily run (`OPENROUTER_API_KEY` or `FORECASTLAB_MODEL_API_KEY`, `TAVILY_API_KEY` or
`FORECASTLAB_SEARCH_API_KEY`).

**Costs:** model `anthropic/claude-sonnet-5.5` on OpenRouter (override with `--model` or `METACULUS_BOT_MODEL`),
three model calls plus 3-4 Tavily searches per question, roughly $0.03-0.10 per question (Sonnet 5.5 lists at $2 in and $10 out per million tokens). A run stops starting new
questions once it has spent $1.50 (OpenRouter's reported cost, else a token estimate).

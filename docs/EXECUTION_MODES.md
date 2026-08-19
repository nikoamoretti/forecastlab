# Execution modes

Every forecast goes through `resolve_execution_context()` before a job or run is created. The result is persisted on `ForecastRun.execution_context_json` without API keys.

## Demo

- Always uses `MockModelProvider` and `MockSearchProvider`.
- Fixture evidence is allowed and labeled.
- Configured live keys never cause demo mode to call paid APIs.
- Evidence policy: `demo_fixtures`.

## Live

- Requires a non-mock model provider, model name, API key, and a base URL when the provider is `openai_compatible`.
- Requires a non-mock search provider and a search key when that provider needs one.
- Fixture hosts under `fixtures.forecastlab.local` are forbidden. A final check fails the run with `live_run_fixture_evidence_violation` if they appear.
- Missing configuration returns HTTP 422 and does not create a pending run.
- Evidence policy: `live_current`.
- Unknown publication dates may be kept and labeled `published_at_unknown`.

## Backtest

- Requires `as_of`.
- Requires real model and search providers unless the run is an explicit synthetic fixture experiment.
- Evidence policy: `strict_historical_snapshot`.
- A general web page is eligible only with an archive snapshot at or before `as_of`, or a dedicated adapter that can prove an immutable historical version.
- No eligible snapshot means reject. The current page is never fetched as a fallback.

## Synthetic internal benchmark execution

- Allowed only when the dataset is labeled synthetic and the experiment sets `synthetic_fixture_run`.
- May use mock providers so CI and the Lab can run without paid keys.
- Must stay labeled synthetic in every summary.
- Evidence policy: `synthetic_historical_fixtures`.

Synthetic status is never inferred from missing credentials.

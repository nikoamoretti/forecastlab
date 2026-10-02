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
- Search providers are `tavily` and `openai_web_search`. `openai_web_search` calls the OpenAI Responses API `web_search` tool with `gpt-5-mini` and reuses the model API key when the model provider is `openai` and no search key is set. Result URLs come only from the tool's citations and source list; publication dates are not taken from the search and are read from each fetched page as usual. Each search is charged as the tool fee plus its tokens at the `openai` catalog rate, recorded as search cost rather than forecasting tokens.
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

## Frozen experiment execution

Product forecasts may still load prompt files and YAML profiles from disk. Benchmark execution does not.

At experiment creation ForecastLab stores a `BenchmarkProfileSnapshot` for each selected profile, including full prompt text, the effective profile, provider fields, timeout, evidence policy, limits, and a pricing snapshot. Tasks reconstruct `ForecastProfile`, `PromptBundle`, and `ExecutionContext` from that row. They do not call `load_profile()` or `load_prompt()`, and they do not re-read Settings for provider, model, base URL, search provider, timeout, or evidence policy.

API keys are loaded at execution time only. If a frozen real provider is missing a required key, the task fails. The run never switches to a different provider. Changing Settings or editing source files after creation leaves already-created experiments unchanged.

# Integrity Repair Plan

Baseline: `2caa33abc0cbd3e2c379de0763f13878839b3b6e` on `grok/forecastlab-mvp`.
Branch: `grok/integrity-evaluation-repair`.

Existing checks at branch creation: `pytest` 25 passed.

## Defects

1. Provider factories return mock when a key is missing.
2. Live/backtest runs can use fixture hosts.
3. Backtest fetch falls back to the current page when no snapshot exists.
4. `/api/benchmarks/run` executes synchronously in `demo` mode and can enqueue plus execute the same run.
5. Job claim is select-then-update; persistence is not idempotent.
6. Settings cost ceiling is stored but not applied to the effective profile.
7. Reliability mixes profiles; summaries hard-code `synthetic: true`.
8. `auto_rerun` is shown but not implemented.
9. Hostname classification uses substring matching.

## Approach

Keep the demo happy path. Add a single `resolve_execution_context` gate. Fail closed for live and real backtests. Rebuild benchmarks as async experiments. Apply Alembic on startup. Disable unsupported auto-rerun.

## Decisions

- Benchmark-only questions get `is_benchmark=true` and are filtered from the board.
- Synthetic fixture experiments may use mock providers only when the dataset is synthetic and the experiment flag is explicit.
- Old profile IDs remain for demo compatibility. Lab experiments use the four new versioned profiles.
- Auto-rerun is disabled in API and UI. Watch changes only mark stale.
- Conservative pricing YAML is labeled estimated; unknown models still enforce call/token/time limits.
- Isolated unit tests may use `create_all`. The app and API tests apply migrations.

## Commit series

1. Execution identity and provider fail-closed behavior
2. Backtest evidence integrity
3. Benchmark experiment architecture
4. Job and retrieval hardening
5. UI, CI, tests, and documentation

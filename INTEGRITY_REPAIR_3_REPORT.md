# ForecastLab integrity repair 3 report

Date: 2026-08-19. Branch: `grok/integrity-repair-3`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

This is the final verification record for the integrity-repair-3 series. It supersedes session-specific numbers in `BUILD_REPORT.md` and `INTEGRITY_REPAIR_2_REPORT.md` for current test counts, Docker results, and remaining work. Those earlier files remain historical notes.

ForecastLab is still a local-first binary forecasting lab. This pass does not claim calibration and does not claim that ForecastLab matches or outperforms any other product. No new forecasting features, connectors, authentication, payments, public deployment, or visual redesign were added.

## Starting commit

Verification started from `grok/integrity-repair-2` at:

`54ec08a25b229e54d2550912be1460d5b7a01283` (`Record integrity-repair-2 verification results`)

`git fetch origin`, checkout, and `git pull --ff-only` were applied. HEAD was exactly that SHA before edits. Pre-edit `python -m pytest -q` passed **109** tests. Work continued on `grok/integrity-repair-3` only. Previous branches were not modified, merged, force-pushed, or deployed.

## Final branch and commits

Branch: `grok/integrity-repair-3`

Confirm the exact final SHA with `git rev-parse HEAD` after the last commit lands. GitHub Actions must be judged against that SHA, not an earlier one.

## Migration revisions

| Revision | File | Role |
|---|---|---|
| `20260818_0001` | `alembic/versions/20260818_0001_integrity_schema.py` | Frozen explicit Alembic baseline. Revision ID unchanged. No live `Base.metadata.create_all()`. |
| `20260819_0002` | `alembic/versions/20260819_0002_legacy_integrity_upgrade.py` | In-place upgrade from the original MVP schema. |
| `20260819_0003` | `alembic/versions/20260819_0003_experiment_snapshots.py` | Frozen profile/prompt snapshots and import uniqueness. |
| `20260819_0004` | `alembic/versions/20260819_0004_benchmark_task_run_identity.py` | One `ForecastRun` per `BenchmarkTask`, `partial`. |
| `20260819_0005` | `alembic/versions/20260819_0005_ledger_environment_and_parity.py` | Run attempts, provider-call ledger, cost columns, final Wayback fields, environment identity, built-in dataset identity, remaining foreign keys. |

Existing databases already stamped `20260818_0001` still upgrade through later revisions. Back up `data/forecastlab.db` before the first launch after an upgrade.

## Persistent usage-ledger design

The in-memory `Budget` object is no longer the source of truth across worker retries.

- `ForecastRunAttempt` records each worker attempt for one `ForecastRun`.
- `ProviderCallLedger` records every physical HTTP request (model or search).
- `PersistentUsageLedger` writes through independent short-lived sessions so a worker rollback cannot erase paid usage.
- Before a physical request: reserve, commit, verify run-lifetime totals plus the reservation fit `max_estimated_cost_usd` / `max_tokens`. If not, do not call the provider.
- After success: store actual tokens/cost and provider request ID when available; unused reservation is released.
- After failure: keep provider-reported or reserved-estimated usage; mark the physical request failed; keep the attempt row.
- OpenAI-compatible and search adapters use `run_physical_attempts` instead of opaque Tenacity decorators. One logical call can have multiple physical rows.
- On retry, `Budget.from_persisted` loads ledger totals. Remaining limits apply. The retry does not receive a fresh full budget.
- Wall-clock accounting uses the original `ForecastRun.started_at`.

`ForecastRun` now exposes `model_cost_usd`, `search_cost_usd`, `failed_attempt_cost_usd`, `total_cost_usd`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `provider_request_count`, and `run_attempt_count`. **`cost_usd` is total lifetime cost** for backward compatibility (successful calls + failed attempts + search).

`BenchmarkResult.cost_usd` and Brier-per-dollar use that total. Monetary figures are labeled `provider_reported`, `estimated`, `mixed`, or `unavailable`. Search pricing supports per-request, provider-reported, manual estimated, and unavailable. Tavily rates in `configs/pricing/models.yaml` are manual conservative estimates, not invoices. Mock search cost is $0.

Proof that retry budgets are cumulative is in `tests/test_cumulative_retries.py` and `tests/test_usage_ledger.py`: prior spend survives a transient error and worker retry; a second attempt starts with that spend consumed; lifetime ceilings reject a fresh full budget; failed physical attempts remain in totals; replaying reconcile is idempotent.

## Frozen runtime identity

At experiment creation ForecastLab persists:

- Git commit SHA and dirty-tree flag
- Hash of tracked `packages/forecasting/forecastlab/**/*.py` and `apps/api/forecastlab_api/**/*.py`
- `pyproject.toml` hash
- `uv.lock` or `poetry.lock` hash when present (live-readiness now requires a Python lock hash for real experiments)
- `apps/web/package-lock.json` hash
- Prompt-bundle hash and profile-snapshot hashes
- Pricing-catalog payload and hash
- Application version `0.3.0` (superseded by `0.3.1` on `grok/live-readiness`; see `LIVE_READINESS_REPORT.md`)
- Optional `FORECASTLAB_IMAGE_DIGEST`

Local databases, logs, secrets, caches, and ignored files are excluded.

Before a benchmark task executes, the current code/dependency identity is compared to the freeze. Mismatch raises `ExperimentEnvironmentMismatch` (permanent; not retried). The frozen identity is never silently updated. Compared keys: `git_commit`, `tracked_source_hash`, `pyproject_hash`, `dependency_hash`, `package_lock_hash`, `application_version`, and container digest when both sides set it.

Prompt and profile **file** hashes are stored for audit but are not fail-closed at execute time, because those payloads are already snapshotted. Pricing-file mutation does not fail-close an existing experiment; `Budget` uses `BenchmarkProfileSnapshot.pricing_snapshot_json`.

Real experiments cannot be created from a dirty tracked working tree (`dirty_working_tree`). Synthetic software-verification experiments may, and remain labeled synthetic. Ordinary product runs use current code.

## Final Wayback verification

CDX queries use `urllib.parse.urlencode`. Fragments are stripped. Source URLs with query parameters, ampersands, encoded characters, and Unicode paths are encoded as query values, not concatenated.

After the streamed fetch, `SafeResponse.final_url` is parsed for timestamp, archived original URL, and replay modifier. Acceptance requires a recognized Wayback replay URL, final timestamp ≤ `as_of`, canonical original-URL match, and no escape to a live page.

Reject reasons: `final_snapshot_not_wayback`, `final_snapshot_after_as_of`, `final_snapshot_original_url_mismatch`, `final_snapshot_metadata_unparseable`.

Evidence rows store `requested_snapshot_url`, `requested_snapshot_at`, `final_snapshot_url`, `final_snapshot_at`, `archived_original_url`, and `snapshot_verification_status`.

## Partial versus full metrics

Each profile report includes total, full, partial, and failed counts plus completion, partial, and failure rates.

| Set | Includes |
|---|---|
| All-valid | Full and partial outputs that produced a probability |
| Full-run-only | Successful non-partial runs only |

Both sets report Brier, log loss, mean/median total cost, mean latency, and Brier per dollar. Paired comparisons exist for both sets. Lab UI labels: Outcome mix, All-valid metrics, Full-run-only metrics, Paired comparisons (all valid), Paired comparisons (full-run-only). Question tables and CSV export include status `full` | `partial` | `failed`.

## Schema parity

`tests/test_schema_parity.py` builds:

1. A fresh database migrated empty → head
2. An original MVP database upgraded → head

Compared: table names, columns, types, nullability, server defaults, primary keys, foreign keys, unique constraints, and indexes. Allowed SQLite representation differences: `unique_constraint_vs_unique_index`, `boolean_default_0_vs_false`, `datetime_timezone_omitted`. `PRAGMA foreign_key_check` is empty for both. Duplicate-parent collapse retargets children first or fails closed (`SchemaParityError`).

## Built-in dataset versioning

Current fixture identity:

- `builtin_key`: `forecastlab.synthetic.binary`
- `builtin_version`: `2`
- unique on that identity
- `archived_at` for superseded versions

Seeding finds that exact key and version, creates it once, upgrades unnamed legacy rows when content matches, and archives on content change instead of leaving a second current version. `/api/benchmarks/run` uses `current_builtin_dataset()`, not the first synthetic row.

## Exact local tests

Commands run from `/Users/nico-yardlogix/projects/forecastlab` on 2026-08-19 with `.venv`.

```text
python -m pytest -q
137 passed, 1 warning in 14.53s
# warning: StarletteDeprecationWarning (httpx + starlette.testclient)
```

```text
python -m ruff check packages apps/api tests
All checks passed!
```

```text
python -m mypy
Success: no issues found in 48 source files
```

```text
cd apps/web && npm ci
# 109 packages, 3 high severity Next 15 transitive advisories (postcss / sharp)
# npm audit fix --force was not applied (would install Next 16)

cd apps/web && npm run typecheck
# tsc --noEmit  (exit 0)

cd apps/web && npm run build
# Next.js 15.5.23 production build succeeded

cd apps/web && npx playwright test --workers=1 --reporter=line
[1/2] [chromium] › e2e/experiment.spec.ts:3:5 › synthetic benchmark experiment
[2/2] [chromium] › e2e/happy-path.spec.ts:3:5 › mock happy path
2 passed (11.5s)
```

## Docker verification

```text
docker compose up -d
```

| Check | Result |
|---|---|
| API `/health` | `200` `{"status":"ok","service":"forecastlab-api"}` |
| API `/health/db` | `200` `{"status":"ok"}` |
| API `/health/worker` | `fresh: true`, `status: idle` |
| Web `/` | `200` |
| Web proxy `/api/dashboard` | `200` |
| Web container `fetch('http://api:8765/health')` | `200` |
| Demo forecast | completed, ensemble ≈ `0.3738` |
| Provider usage audit | 1 run attempt, 19 ledger rows (model + search) |
| Synthetic experiment | `completed`, `10/10` tasks, `0` failed |
| Partial metrics | All-valid and full-run-only blocks present; row status `full` |
| Built-in dataset | exactly one active `forecastlab.synthetic.binary` v2 |
| Board filter | `is_benchmark=0`; no “synthetic series” titles |

```text
docker compose down
```

## Secret scan

Tracked files contain only test placeholders (`sk-secret`, `tvly-test`, `sk-test-secret-value-123456`) and the redaction regex. No live keys, bearer tokens, `.env`, credentials files, or SQLite databases are tracked.

Ignored local artifacts:

- `data/local/credentials.json`
- `data/forecastlab.db`
- `.env`
- `logs/`

`.env.example` is the only tracked env template and has empty key fields. Ledger rows do not store API keys, authorization headers, or complete provider responses.

## Paid smoke test

```text
Paid live smoke test not executed because explicit opt-in or credentials were absent.
```

`scripts/paid_smoke.py` documents a one-question live command and runs only when `FORECASTLAB_RUN_PAID_SMOKE=1` and live model plus search credentials exist. This verification used mocks and stubs only. It does not claim the real provider path was tested.

## GitHub Actions

CI (`.github/workflows/ci.yml`) already runs pytest, ruff, mypy, `npm ci`, typecheck, production build, and Playwright. New tests are picked up automatically.

Record the run ID and conclusion for the exact final SHA after push. Do not treat an earlier SHA as the certificate.

## Remaining limitations

- Binary questions only.
- One local user. No accounts, billing, sharing, or public deployment.
- Mock model and search are deterministic pipeline theater, not live research.
- Live providers inherit vendor outages, rate limits, and prompt brittleness.
- Wayback coverage is incomplete. A missing snapshot is a rejection, not proof of absence.
- Model pretraining can leak post-cutoff facts into backtests.
- Budget reservations are not a vendor-enforced hard dollar cap. Missing usage stays estimated. Search prices are estimated unless reported.
- Equal ceilings do not guarantee identical spend.
- Reliability diagrams stay hidden below 20 rows per profile. That is not a calibration claim.
- Synthetic fixtures cannot support product-level forecasting-quality claims.
- Auto-rerun remains disabled. Watches only mark a forecast stale.
- Tracks run sequentially to stay SQLite-safe; they remain informationally independent.
- Isolated unit tests may still use `create_all`. Back up `data/forecastlab.db` before upgrading an existing file.
- Next 15 still reports transitive `postcss` / `sharp` advisories. A force fix would install Next 16 and was not applied.

## First real benchmark

Do this next. Do not add hierarchical subforecasts or extra connectors first.

1. Keep the product on `grok/integrity-repair-3` until you choose to merge later. Do not merge or deploy from this verification pass.
2. Start from a clean tracked working tree. Real experiments are refused while tracked files are dirty.
3. Back up the local database: `cp data/forecastlab.db data/forecastlab.db.bak`.
4. In Lab, download `fixtures/benchmarks/import_template.csv`.
5. Import a small dated set of **real** resolved binary questions. Each row needs question, forecast date, resolution date, outcome `0` or `1`, resolution source, category, provenance, `exact_yes`, `exact_no`, `resolution_deadline`, and `authoritative_source`. Forecast date must precede resolution date. Do not mix synthetic and real rows.
6. Confirm the dataset is not marked synthetic.
7. Create one experiment with the default profiles only: `single_agent_equal_budget_v1` and `three_track_equal_budget_v1`.
8. Use live or backtest providers as required. Real tasks run `mode=backtest` and `as_of=forecast_date`. Missing or unverified final Wayback captures must reject.
9. Wait until the experiment is `completed`, `completed_with_failures`, or `failed`.
10. Read Outcome mix, All-valid, and Full-run-only scores plus both paired comparisons. Treat synthetic fixture results as software verification, not quality evidence.
11. Confirm run audit totals include failed attempts and search charges. Only after that path has been used on real rows, consider `three_track_full_v1` as an optional higher-research profile.

See `docs/EVALUATION_PROTOCOL.md` and `docs/BENCHMARK_EXPERIMENTS.md`.

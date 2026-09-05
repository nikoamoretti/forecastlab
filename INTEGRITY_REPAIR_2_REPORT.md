# ForecastLab integrity repair 2 report

Date: 2026-08-19. Branch: `grok/integrity-repair-2`. Workspace: `/Users/nico-yardlogix/projects/forecastlab`.

This is the final verification record for the integrity-repair-2 series. It supersedes the session-specific numbers in `BUILD_REPORT.md` and `INTEGRITY_REPAIR_REPORT.md` for current test counts, Docker results, and remaining work. Those earlier files remain historical notes for the first integrity pass on `grok/integrity-evaluation-repair`.

ForecastLab is still a local-first binary forecasting lab. This pass does not claim calibration and does not claim that ForecastLab matches or outperforms any other product.

## Starting commit

Verification started from a clean `grok/integrity-repair-2` at:

`a1f697108c3159a3f18f5a75afacc2c7b9ba7448` (`Harden budgets retrieval caches and watchers`)

The branch descends from:

`3a3ced32dda657b2457268cf150479b6e1724044` (`Surface integrity in the UI, CI, and documentation.`)

`git pull --ff-only` reported already up to date. The working tree was clean.

## Final branch and commits

Branch: `grok/integrity-repair-2`

Repair-series commits after `3a3ced32`:

| SHA | Message |
|---|---|
| `9e0e5152153bc2ca534ea5ff2955c35fccb2ab6c` | Add real legacy database upgrade path |
| `3841c054c2cee757cf0bc4f5e52034f34faad6c5` | Freeze benchmark configurations and contracts |
| `c457f35fae4b53fd0e4a3a0aec01dcf2f48e6de9` | Make benchmark retries idempotent and terminal |
| `a1f697108c3159a3f18f5a75afacc2c7b9ba7448` | Harden budgets retrieval caches and watchers |

This documentation commit is the verification record. Confirm the exact final SHA with `git rev-parse HEAD` after it lands. GitHub Actions must be judged against that SHA, not an earlier one.

`grok/forecastlab-mvp` and `grok/integrity-evaluation-repair` were not modified, merged, or deployed.

## Migration revisions

| Revision | File | Role |
|---|---|---|
| `20260818_0001` | `alembic/versions/20260818_0001_integrity_schema.py` | Baseline empty-schema create. Not rewritten. |
| `20260819_0002` | `alembic/versions/20260819_0002_legacy_integrity_upgrade.py` | In-place upgrade from the original MVP schema. Old rows survive. |
| `20260819_0003` | `alembic/versions/20260819_0003_experiment_snapshots.py` | Frozen profile/prompt snapshots, stored resolution contracts, per-dataset import uniqueness. |
| `20260819_0004` | `alembic/versions/20260819_0004_benchmark_task_run_identity.py` | One `ForecastRun` per `BenchmarkTask`, terminal experiment states, result `partial`. |

Acceptance:

- Unversioned original MVP database upgrades without deletion (`test_unversioned_mvp_database_upgrades`).
- Legacy question, run, and version rows survive (`_assert_legacy_rows_survived`).
- A database stamped `20260818_0001` but missing integrity columns upgrades (`test_stamped_baseline_missing_integrity_columns`).
- A migration failure stops startup and does not apply later columns (`test_migration_failure_stops_startup`).

Back up `data/forecastlab.db` before the first launch after an upgrade. Isolated unit tests may still call `create_all`.

## Frozen experiment design

Experiment creation copies the selected YAML profiles, prompt bundle, provider settings, evidence policy, and resolution contract into `BenchmarkExperiment` / `BenchmarkProfileSnapshot` rows. Workers execute those snapshots. They do not reload `prompts/`, `configs/forecast_profiles/`, or current Settings.

Acceptance:

- Editing prompt files after creation does not change execution (`test_frozen_source_files`).
- Editing profile YAML after creation does not change execution (same test; `max_tokens` mutation ignored).
- Changing Settings after creation does not change providers, model name, timeout, or evidence policy (`test_frozen_settings`).
- Every profile on one benchmark question uses the same stored resolution contract (`test_shared_resolution_contract`).

## Task and retry design

Identity for the lifetime of a benchmark task:

`BenchmarkTask` → one benchmark-only `Question` (`is_benchmark=true`) → one `ForecastRun` (`forecast_runs.benchmark_task_id`, unique) → zero or one `BenchmarkResult`.

Retries reuse that graph. They do not create a replacement question or run.

Acceptance:

- Crash after forecast persistence creates no replacement run (`test_crash_boundaries_recover_without_duplicates`, including `after_forecast_version`).
- A transient error reschedules and then succeeds on the same run (`test_transient_retry_reuses_run`).
- Exhaustion marks the job, run, task, and experiment `failed` (`test_exhausted_retry_marks_graph_failed`).
- Mixed success and failure ends `completed_with_failures` (`test_mixed_experiment_completed_with_failures`).

Terminal experiment statuses: `completed`, `completed_with_failures`, `failed`. Transient provider errors (timeouts, HTTP 429, HTTP 5xx) retry. Configuration, evidence-integrity, and structured-output errors do not.

## Budget reservations

Before each model call, `budget.reserve_model_call(...)` reserves one call, estimated input tokens, maximum output tokens, estimated cost, and a wall-clock allowance. If the reservation cannot fit, the provider is not called. After a response, unused reservation is released. Missing usage keeps the reserved amounts and is labeled `estimated`. Failed calls release the reservation. Details persist on the run audit (`budget_json.reservations`).

ForecastLab does not claim a provider-enforced hard dollar cap. The guarantee is only that it will not intentionally start a call whose reservation cannot fit.

The provider request includes a maximum output-token limit (`max_output_tokens` / OpenAI-compatible `max_tokens`).

Acceptance:

- Reservation rejection happens before `complete_json` (`test_reserve_happens_before_provider_call`).
- Provider receives a positive max output-token limit (`test_provider_receives_max_output_tokens`).
- Actual usage is reconciled (`test_reconcile_releases_unused_reservation`).
- Missing usage is labeled estimated (`test_missing_usage_keeps_reserved_estimate`).
- Later reservation failure keeps earlier completed tracks: a three-track run with `max_model_calls=4` finished `base_rate` and `current_evidence`, stopped at `plan:skeptic`, set `partial=True`, and still produced an ensemble probability.

## HTTP and watcher controls

Evidence, Wayback, and external watcher bodies use `client.stream("GET", ...)`. Chunks are counted incrementally. The client stops as soon as the running total would exceed the byte ceiling and does not retain the overflow body. `client.get()` is not used for those bodies.

Also enforced: HTTP/HTTPS only, no embedded credentials, DNS and IP validation, redirect-target validation, redirect count, timeouts, content-type allowlist, and rejection of private, loopback, link-local, multicast, and metadata addresses.

User-created JSON and HTML watches are validated at creation with `allow_local_fixtures=False`. Users cannot submit the internal demo source type. The demo unemployment indicator is read in-process.

Search and fetch caches are one `RunCache` per forecast run. They do not leak across run IDs, model or search providers, demo/live modes, `as_of` timestamps, or configuration hashes. A rerun after a watch change fetches fresh evidence.

Acceptance:

- No eligible historical snapshot rejects; the current page is not fetched (`test_no_eligible_snapshot_rejects_without_current_page`, `test_current_undated_page_rejected_in_backtest`).
- Chunked bodies without `Content-Length` stop while streaming (`test_chunked_response_without_content_length_is_capped`).
- HTML and JSON watches cannot reach loopback, private IPv4/IPv6, or metadata hosts.
- Redirects to private addresses fail.
- A watch-change rerun contains content B, not cached A (`test_watch_change_rerun_fetches_fresh_evidence`).

## Scientific comparison

Default experiment selection is exactly:

- `single_agent_equal_budget_v1`
- `three_track_equal_budget_v1`

Those two share configured search, fetch, token, cost, wall-clock, and model-call ceilings. The full ensemble remains optional.

Reliability is computed separately per profile. A reliability diagram is withheld below 20 resolved rows for that profile; 20 rows are a display threshold, not a calibration claim. Paired comparisons and the deterministic bootstrap (seed `20260818`, 2000 samples, 95% percentile) are scoped to one experiment.

## Exact test results

Commands run from `/Users/nico-yardlogix/projects/forecastlab` on 2026-08-19.

```text
python -m pytest -q
109 passed, 1 warning in 5.92s
# warning: StarletteDeprecationWarning (httpx + starlette.testclient)
```

```text
python -m ruff check packages apps/api tests
All checks passed!
```

```text
python -m mypy
Success: no issues found in 44 source files
```

```text
cd apps/web && npm ci
# 109 packages, 3 high severity Next 15 transitive advisories (postcss / sharp)
# npm audit fix --force was not applied

cd apps/web && npm run typecheck
# tsc --noEmit  (exit 0)

cd apps/web && npm run build
# Next.js 15.5.23 production build succeeded

cd apps/web && npx playwright test --workers=1 --reporter=line
[1/2] [chromium] › e2e/experiment.spec.ts:3:5 › synthetic benchmark experiment
[2/2] [chromium] › e2e/happy-path.spec.ts:3:5 › mock happy path
2 passed (11.0s)
```

No product defects were found. No feature work was added in this verification pass.

## Docker verification

```text
docker compose up -d
```

Then from the host:

| Check | Result |
|---|---|
| API `/health` | `200` `{"status":"ok","service":"forecastlab-api"}` |
| API `/health/db` | `200` `{"status":"ok"}` |
| API `/health/worker` | `fresh: true`, `status: idle` |
| Web `/` | `200` |
| Web proxy `/api/dashboard` | `200` |
| Demo forecast | completed, ensemble ≈ `0.3738` |
| Synthetic experiment | `completed`, `10/10` tasks, `0` failed |
| Board filter | no `is_benchmark` rows; no “synthetic series” titles before or after the experiment |

```text
docker compose down
```

## Secret scan

Tracked files contain only test placeholders (`sk-secret`, `tvly-test`) and the redaction regex. No live keys, bearer tokens, `.env`, credentials files, or SQLite databases are tracked.

Ignored local artifacts:

- `data/local/credentials.json` (mode `0600`; model and search keys empty)
- `data/forecastlab.db`
- `.env` (empty key fields; not committed)
- `logs/`

Runtime logs had no `sk-`, `tvly-`, `Bearer`, or `api_key=` matches. `.env.example` is the only tracked env template and has empty key fields.

## Remaining limitations

- Binary questions only.
- One local user. No accounts, billing, sharing, or public deployment.
- Mock model and search are deterministic pipeline theater, not live research.
- Live providers inherit vendor outages, rate limits, and prompt brittleness.
- Wayback coverage is incomplete. A missing snapshot is a rejection, not proof of absence.
- Model pretraining can leak post-cutoff facts into backtests.
- Budget reservations are not a vendor-enforced hard dollar cap. Missing usage stays estimated.
- Equal ceilings do not guarantee identical spend.
- Reliability diagrams stay hidden below 20 rows per profile. That is not a calibration claim.
- Synthetic fixtures cannot support product-level forecasting-quality claims.
- Auto-rerun remains disabled. Watches only mark a forecast stale.
- Tracks run sequentially to stay SQLite-safe; they remain informationally independent.
- Isolated unit tests may still use `create_all`. Back up `data/forecastlab.db` before upgrading an existing file.
- Next 15 still reports transitive `postcss` / `sharp` advisories. A force fix would install Next 16 and was not applied.

## First real benchmark

Do this next. Do not add hierarchical subforecasts or extra connectors first.

1. Keep the product on `grok/integrity-repair-2` until you choose to merge later. Do not merge or deploy from this verification pass.
2. Back up the local database: `cp data/forecastlab.db data/forecastlab.db.bak`.
3. In Lab, download `fixtures/benchmarks/import_template.csv`.
4. Import a small dated set of **real** resolved binary questions. Each row needs question, forecast date, resolution date, outcome `0` or `1`, resolution source, category, provenance, `exact_yes`, `exact_no`, `resolution_deadline`, and `authoritative_source`. Forecast date must precede resolution date. Do not mix synthetic and real rows.
5. Confirm the dataset is not marked synthetic.
6. Create one experiment with the default profiles only: `single_agent_equal_budget_v1` and `three_track_equal_budget_v1`.
7. Use live or backtest providers as required. Real tasks run `mode=backtest` and `as_of=forecast_date`. Missing historical snapshots must reject; do not expect the current page as fallback.
8. Wait until the experiment is `completed`, `completed_with_failures`, or `failed`.
9. Read the per-profile scores and the paired comparison for that experiment only. Treat synthetic fixture results as software verification, not quality evidence.
10. Only after that path has been used on real rows, consider `three_track_full_v1` as an optional higher-research profile.

See `docs/EVALUATION_PROTOCOL.md` and `docs/BENCHMARK_EXPERIMENTS.md`.

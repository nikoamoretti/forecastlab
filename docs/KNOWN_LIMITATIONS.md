# Known limitations

- Binary questions only.
- One local user. No accounts, billing, or sharing.
- Mock search/model quality is deterministic theater for the pipeline, not a substitute for live research.
- Live providers inherit vendor outages, rate limits, and prompt brittleness.
- Wayback coverage is incomplete; missing snapshots mean missing evidence, not proof of absence.
- Model pretraining can leak post-cutoff facts into backtests.
- Auto-rerun is disabled. Watches only mark a forecast stale. Reruns require user action.
- Reliability diagrams are withheld below 20 resolved rows. That threshold is not a calibration claim.
- Synthetic benchmarks cannot support product-level forecasting-quality claims.
- Startup applies Alembic to head and stops if a revision fails. Isolated unit tests may still use `create_all`. Back up `data/forecastlab.db` before upgrading an existing local file.
- Tracks run sequentially to stay SQLite-safe; they remain informationally independent.
- Benchmark retries reuse the same task, question, and run. They do not create replacement identities. Exhausted transient errors fail the job, run, task, and experiment.
- Experiment terminal states are `completed`, `completed_with_failures`, and `failed`. A running task after a permanently failed job is a defect.

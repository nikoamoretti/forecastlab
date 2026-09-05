# Autopilot rollout evidence

Recorded September 4–5, 2026. This release is deployed with automatic spending
disabled. It has **not** passed live forecasting qualification.

## Application and data

- API: https://forecastlab-api.vercel.app
- Web: https://forecastlab-web.vercel.app
- Existing Vercel Pro team: `yard-logix`; separate API and web projects in iad1.
- Neon PostgreSQL 18.6, production connections only; private Vercel Blob store.
- SQLite-to-Neon import: **67 tables, 33,010 records**, full row parity, contract
  hashes, IDs, ledger totals, export payloads and foreign keys verified.
- Private Blob snapshot downloaded, hash-checked and restored into a separate
  Postgres database: **67 tables, 33,011 records**, full row parity verified.
  The extra row is the persisted release-maintenance control.
- The working local SQLite database was not migrated or replaced. Original local
  services remain separate from the cloud deployment.

## Checks

- Python: **789 passed**, including SQLite and isolated Postgres concurrency,
  budgets, lease recovery, append-only history and import/restore tests.
- Ruff and mypy: passed for application packages and operational scripts.
- TypeScript, ESLint and production frontend build: passed.
- Browser workflows: **24 passed, 2 skipped**. The two optional private-V1
  verification cases require externally supplied fixture question IDs.
- Dependency audits: no reported npm vulnerabilities or Python advisories.
- Protected cloud API: unauthenticated settings access returns 401; readiness
  confirms migration `20260904_0032`, automatic spending disabled, qualification
  disabled and a verified restore receipt.

Machine receipts are retained in ignored `data/local/autopilot/`, including
`neon-import-receipt.json`, `cloud-restore-receipt.json` and the source-preflight
receipts. Secret environment files are not included in the repository.

## Activation remains blocked

1. GitHub OAuth client ID and client secret must be configured in the API
   project's production environment. The owner ID is already pinned to
   `146488758`. End-to-end owner login cannot be verified until then.
2. BLS annual and monthly release-calendar requests return HTTP 403 from Vercel.
   The monthly fallback works in local source checks, but it also fails from
   the deployed function. This is a failed preflight, not a passed qualification.
   No model/search calls or live qualification forecasts have been run: **$0**
   automatic provider spending in this rollout. Resolve official-source access
   before running the bounded three-indicator qualification batch.
3. Neon compute size, automatic suspension and native seven-day restore history
   still need provider-dashboard verification. The tested daily private Blob
   backup mechanism is separate from Neon point-in-time recovery. Database cost
   has not yet been measured against the $6–10/month target.
4. The reviewed-release workflow must be reviewed and merged into the repository's
   existing default branch before it can operate there. The GitHub production
   environment and separate project-scoped Vercel credentials are configured.
   The current GitHub plan rejects required deployment-reviewer rules, so releases
   instead require the owner's numeric identity and approval of the exact full
   commit SHA, followed by CI. The end-to-end GitHub release workflow has not run.

Do not enable the policy or describe ForecastLab as qualified for unattended
forecasting until these activation checks pass. The operational procedure and
recovery rules are in [AUTOPILOT_OPERATIONS.md](AUTOPILOT_OPERATIONS.md).

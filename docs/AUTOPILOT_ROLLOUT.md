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
- Ten additional release regression tests passed for credential isolation,
  source-bundle exclusions and preview-environment isolation.
- Ruff and mypy: passed for application packages and operational scripts.
- TypeScript, ESLint and production frontend build: passed.
- Browser workflows: **24 passed, 2 skipped**. The two optional private-V1
  verification cases require externally supplied fixture question IDs.
- Application dependency audits: no reported npm vulnerabilities or Python advisories.
- Protected cloud API: unauthenticated settings access returns 401; readiness
  confirms migration `20260904_0032`, automatic spending disabled, qualification
  disabled and a verified restore receipt.
- GitHub CI passed for the deployed application commit `efe4eff`; local checks
  separately cover the subsequent release-tooling changes.
- The project-scoped REST path then built and promoted both projects from
  `cddc79e`. Credential access, staging without moving production, protected
  health/access checks, a fresh private backup, promotion and the registered
  15-minute cron were verified. Receipt: `rest-release-receipt.json`.

Machine receipts are retained in ignored `data/local/autopilot/`, including
`neon-import-receipt.json`, `cloud-restore-receipt.json` and the source-preflight
receipts. Secret environment files are not included in the repository.

## Activation remains blocked

1. GitHub OAuth client ID and client secret must be configured in the API
   project's production environment. The owner ID is already pinned to
   `146488758`. End-to-end owner login cannot be verified until then.
2. The original BLS annual/monthly source preflight failed with HTTP 403 from
   Vercel. A subsequent isolated Vercel preview verified HTTP 200 for the DOL
   original release PDFs, New York Fed calendars, and the separate BLS data API.
   The source repair adds that official fallback with exact-period/time checks;
   its production preflight must pass before running the bounded three-indicator
   qualification batch. No model/search qualification calls have been made.
   Source access alone is not live forecasting qualification.
3. Neon compute size, automatic suspension and native seven-day restore history
   still need provider-dashboard verification. The tested daily private Blob
   backup mechanism is separate from Neon point-in-time recovery. Database cost
   has not yet been measured against the $6–10/month target.
4. The reviewed-release workflow was merged in PR #3 after CI passed (802 Python
   tests and 24 browser tests). It is active on the default branch. The GitHub production
   environment and separate project-scoped Vercel credentials are configured.
   Cross-project denial was verified for both tokens. They expire September 5,
   2027 and must be rotated before subsequent releases after that date.
   The current GitHub plan rejects required deployment-reviewer rules, so releases
   instead require the owner's numeric identity and approval of the exact full
   commit SHA, followed by CI. End-to-end release receipts are retained separately
   from the initial deployment evidence above.

Do not enable the policy or describe ForecastLab as qualified for unattended
forecasting until these activation checks pass. The operational procedure and
recovery rules are in [AUTOPILOT_OPERATIONS.md](AUTOPILOT_OPERATIONS.md).

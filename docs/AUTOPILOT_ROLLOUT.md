# Autopilot rollout evidence

Recorded September 4–5, 2026. This opening section is the initial deployment
snapshot. Automatic spending was disabled. At that time live forecasting
qualification had **not** passed; the historical source-preflight failure and
qualification-not-started result below are preserved. Later qualification
evidence is in the September 9, 2026 additive note.

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

## Additive note: September 9, 2026

This note does not replace the September 4–5 snapshot. The original BLS
calendar 403, unverified Neon operating settings, and missing owner OAuth
remain part of that history.

A later September 5 qualification batch completed one live forecast for each
indicator (CPI `0548b8d4-7103-4c01-8b22-f0120a79a215`, payrolls
`75f230a4-ff9b-46a5-b7cd-7d8a0e4d17e5`, unemployment
`aff8e7bb-2b6f-4062-9896-e9c43f44d46a`). The conservative ledger total was
**$1.525625**. Automatic spending stayed disabled. Those three probabilities
are operational evidence that the frozen method ran to completion inside the
qualification ceiling. They are not an accuracy, calibration, or superiority
claim.

Read-only production state checked on September 9, 2026: `enabled=false`,
`qualification_enabled=false`, no pending or running jobs, and the same three
completed qualification runs. The Vercel API production target was
`dpl_HG85vToqjV8tqDkBjhZ8xvtTMk5r` at commit
`20c549c62c8b1f01bb4535517fa2e2f23d62ebe3`. The GitHub OAuth client ID and
client secret were still absent, so owner login remains unverified.

The stored `restore_verified` flag was true. On September 9 a fresh production
snapshot was restored into a **separate PostgreSQL 18** instance. All-row
parity passed for **67 tables / 33,481 records** at revision `20260904_0032`.
Receipt: `data/local/autopilot/v1-restore-2026-09-09.json` (`verified: true`,
SHA-256
`66a4b8fd52141edb181bc7cdec2589f9eae5339a93ad2d25356ce118788c85b4`,
`verified_at` 2026-09-09T20:37:52Z). Checks included all-row hashes, record
counts, primary keys, contract hashes, ledger totals, export payloads, and
foreign keys. Focused SQLite plus PostgreSQL tests: **61 passed**. Native Neon
seven-day point-in-time recovery remains unverified. Daily private Blob
backups remain a separate mechanism.

Source preflight receipt `data/local/autopilot/v1-source-preflight-2026-09-09.json`
(`checked_at` 2026-09-09T20:00:25Z): **no gaps**, **zero paid calls**. The BLS
annual calendar still returned HTTP 403; official schedule recovery via DOL/Fed
(`dol_fed_schedule_v1`) completed and left three selectable questions. GitHub
OAuth remains unconfigured, so owner login is unverified.

Isolated browser/API/database outcome acceptance is documented in
[AUTOPILOT_V1_OUTCOME_ACCEPTANCE.md](AUTOPILOT_V1_OUTCOME_ACCEPTANCE.md). Codex
verified the local workflow on September 9
(`data/local/autopilot/v1-codex-verification-2026-09-09.json`). That is not a
production ship. Previous production rollback targets remain API
`dpl_HG85vToqjV8tqDkBjhZ8xvtTMk5r` and web `dpl_8qt1jyoBqCc5YADho5upwWotwm2p`
(`data/local/autopilot/v1-rollback-targets-2026-09-09.json`). Keep dispatch
paused. OAuth, native Neon seven-day recovery, exact release approval, and
activation remain outstanding. Paid Neon stays the production database.

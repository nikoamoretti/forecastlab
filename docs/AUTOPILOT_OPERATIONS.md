# ForecastLab Autopilot operations

Autopilot owns an explicit set of questions. Ordinary manual forecasts and frozen
pilot assignments retain their contracts, artifacts, configuration hashes and
exports. The default policy is $25 per Los Angeles calendar week, $5 per run,
three initial questions and two refreshes per week. Limits are ceilings.

## Deployment layout

- Vercel team: `yard-logix` (existing Pro team).
- API: `forecastlab-api`, FastAPI, repository root, `app.py`, iad1, 600 seconds.
- Web: `forecastlab-web`, Next.js, `apps/web`, iad1.
- Neon: `forecastlab-db`, Launch, iad1. Application connections use the pooled
  URL with SQLAlchemy NullPool; migrations and imports use the direct URL.
- Blob: `forecastlab-artifacts`, private, iad1. Source documents, calendar and
  release snapshots, exports and logical backups have verified content hashes.

Production starts without migrations, seeding, credential files or writable local
artifact paths. SQLite and the existing local launchers remain available. Cloud
provider keys live in Vercel environment settings. Nonsecret editable settings
live in `app_settings`. Preview projects have no production database, Blob,
provider or internal credentials. Provider reservations additionally reject
preview execution. The web proxy fails closed without a separate preview API.

Configure Neon compute to 0.25 CU with automatic suspension and seven days of
restore history in the provider dashboard. The roughly $6–10/month database
figure is a target, not a measured bill or an enforced hosting limit. Review
Neon compute hours, storage and retention after launch. Logical Blob backups are
separate from Neon's point-in-time recovery settings.

## Owner login

Create a GitHub OAuth application with homepage
`https://forecastlab-web.vercel.app` and callback
`https://forecastlab-web.vercel.app/api/auth/github/callback`.
Set `FORECASTLAB_GITHUB_CLIENT_ID` and `FORECASTLAB_GITHUB_CLIENT_SECRET` in the
API project's **production** environment. The configured owner ID is `146488758`.
The API checks the numeric ID returned by GitHub, not a browser-supplied username.

Use distinct persistent secrets for `FORECASTLAB_SESSION_SECRET`,
`FORECASTLAB_INTERNAL_SECRET`, and `CRON_SECRET`. The web project receives only
the internal secret and API origin. OAuth uses signed short-lived state; ordinary
sessions are opaque, hashed in Postgres, expire after seven days, and require CSRF
and same-origin verification for mutations. Direct API URLs also enforce access.

## Daily operation

`GET /internal/cron` requires `Authorization: Bearer <CRON_SECRET>` and runs every
15 minutes. It checks daily backups, reconciles due source/outcome work and
processes one persisted job. Owner launches also request immediate processing
through Next.js `after()`. The database job remains the recovery mechanism.

The scheduler and paid worker use separate expiring leases. The paid worker uses
a monotonically increasing fencing token and job lease. Before each physical
provider attempt, a transaction checks pause state, suspended questions, the
prerelease deadline, remaining final-estimate capacity and the weekly/run limits.
The five-minute execution allowance accumulates preparation and execution across
attempts; queue and question-review waits are excluded. Interrupted attempts are
bounded by their last heartbeat. Completed model/search/document calls and root
estimates replay from saved results instead of making new calls.

Unknown physical-call results retain their conservative charge and a started
checkpoint. They fail with `interrupted_provider_call_requires_reconciliation`
and are listed in the Autopilot execution records. Do not delete that checkpoint
or reset its charge to force a retry. Inspect the provider receipt and ledger
before deciding whether to reconcile or abandon the run. Three consecutive
physical provider failures pause automatic forecasting and create one incident.

The selector uses explicit BLS observation periods, release times and deterministic
thresholds from the latest observed measurement. Official monthly schedule lists
can replace an unavailable annual schedule; an ICS date alone cannot establish
an observation period. Calendars and observations have independent cache state
so a calendar error does not exhaust the BLS observation quota. Normal discovery
is within seven days; refreshes have a 24-hour cooldown and a two-per-week ceiling.
A changed or ambiguous calendar suspends the question, including already queued
paid calls. Forecasting stops 15 minutes before release.

Every refresh creates an ordinary new run. A latest failure or abstention remains
the latest result. Older probabilities and failed versions remain dated in the
history. Scores use Autopilot runs only: initial and latest eligible prerelease
versions are reported separately, with resolved denominators, matched questions,
coverage, failures, abstentions, cost, latency and shared release-event groups.
Manual reruns are not automatically counted as Autopilot assignments.

Outcome proposals require the official dated release headline, exact units,
period, publication time and a retained hash-verified source. Current revised
API values cannot replace missing first-release evidence. Confirmation and
corrections append adjudications; unconfirmed proposals never enter scores or
historical evaluation datasets. Inbox messages and weekly summaries are generated
without a writing-model call.

## Import and restore

Use a **fresh**, privately accessible target. Pause dispatch and drain any existing
service before import or restore. Keep the old instance from dispatching the same
nonterminal jobs during a cutover. The current cutover had no nonterminal jobs.

```bash
# Supply a direct Postgres URL through the environment, not a command argument.
uv run python scripts/cloud_data.py import-sqlite \
  --source data/forecastlab.db --receipt data/local/autopilot/import.json
```

Set `FORECASTLAB_IMPORT_URL` first. The importer uses SQLite's online backup API,
upgrades the copy, migrates the target and verifies every row, ID, contract,
ledger value, export payload and foreign key. It never edits the working SQLite
source. An identical unchanged target can be verified again; conflicting target
records fail instead of being overwritten. Operational changes after import are
not an invitation to overwrite the target with an older snapshot.

Daily snapshots use a consistent Postgres transaction, gzip JSON, private Blob
and readback hash verification. Keep at least seven complete daily points and all
points younger than seven days. A failed backup pauses automatic spending.

To test a restore, download and hash-check the retained snapshot and run:

```bash
uv run python scripts/cloud_data.py restore \
  --snapshot /private/path/snapshot.json.gz \
  --receipt data/local/autopilot/restore.json
```

Use a separate fresh Postgres target. A restore receipt is valid only after full
row parity succeeds. When using a restored database operationally, keep dispatch
paused, invalidate copied sessions, review nonterminal jobs and reconcile unknown
provider calls before reconnecting the service. A populated Autopilot migration
cannot be destructively downgraded; use a verified backup to recover.

## Qualification and activation

Deploy with automatic spending disabled. Confirm the restore receipt, owner login
and live public-source preflight first. The one requested qualification batch uses
the next official release of each indicator (bounded to 90 days), allowing all
three indicators to be tested in one week even when their seven-day windows do
not overlap. Its policy/contract/method/settings/cutoff are frozen and recorded.
**This is a qualification-only scheduling exception**; ordinary automatic
discovery retains the seven-day rule. The qualification batch has a $15 lifetime
ceiling within the weekly $25 allowance, including all physical retries.

All three indicators must produce valid live probabilities within their frozen
limits before enablement is allowed. Investigate a failed or withheld result;
do not count it as a qualification success. Changed method/model settings require
another approved policy revision and qualification. A successful batch validates
operation, not superior accuracy.

## Reviewed software releases

Git-triggered Vercel deployments are disabled in both project configurations.
The current GitHub plan does not support required deployment reviewers in this
private repository. Instead, `.github/workflows/release.yml` requires the owner's
numeric GitHub identity and an explicitly approved full commit SHA. It checks that
SHA against the selected default-branch commit, then runs CI before deployment.
Dispatching the workflow with that SHA is the release approval; dependency PRs
and pushes cannot invoke it automatically. Supply separate project-scoped
`FORECASTLAB_API_VERCEL_TOKEN` and `FORECASTLAB_WEB_VERCEL_TOKEN`, plus
`FORECASTLAB_INTERNAL_SECRET`, as `production` environment secrets. Do not
copy a broad personal CLI token into repository secrets. The workflow must be
merged into the repository's default branch before it can be dispatched there.

`scripts/release_cloud.py` pauses dispatch, drains jobs, takes and read-verifies a
private `pg_dump` snapshot, applies additive migrations, stages both applications,
checks their health/access boundaries, then promotes API and web. Failed release
steps leave dispatch paused. Completion resumes a previously approved policy
only when its method/settings still qualify; a changed method stays paused.
Dependency updates create reviewable Dependabot PRs for uv, npm and Actions.
They are not merged or deployed automatically.

The provisioned Neon database is PostgreSQL 18. The release workflow installs the
PostgreSQL 18 client from the [official repository](https://www.postgresql.org/download/linux/ubuntu/)
before taking a schema-inclusive backup; an older `pg_dump` cannot back it up.
The CLI redacts sensitive environment variables. Release tooling loads only the
integration's direct database and Blob credentials and rejects unavailable values;
it does not need to export model, search, OAuth or session secrets.

`GET /internal/readiness` reports migration, login and restore readiness without
secrets. `POST /internal/source-preflight` runs only free public-data checks.
These use the internal secret, not the owner cookie or cron secret. Keep receipts
in ignored `data/local/autopilot/`; never attach secret environment files to PRs.

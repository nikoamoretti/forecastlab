# Autopilot V1 outcome acceptance

Isolated check that an unconfirmed official-release proposal stays out of
scores, that owner confirmation and one append-only correction change the
visible Brier and log-loss numbers, and that a later failed refresh remains in
history without becoming the current probability.

This is a software-acceptance workflow. It does not measure forecasting quality
or calibration and does not authorize unattended Autopilot spending.

## What it covers

- Private temp workspace outside the repository, refuse existing
  production/local SQLite targets, sanitized environment, mock providers.
- Seeded CPI question from a frozen July 2026 / August 12 calendar: completed
  probability 0.6, later failed refresh, unconfirmed proposal from the retained
  official July 2026 CPI PDF
  (`tests/fixtures/official_releases/cpi_08122026.pdf`). Threshold observations
  are a labeled synthesized fixture (latest published June 2026 CPI = 3.0% as of
  the August 5 seed clock).
- The runner copies web source into that workspace, runs a fresh `npm ci`
  there (and `npm audit` when the install reports warnings), then runs the
  normal Turbopack `next build`. It does not symlink the repository
  `node_modules`, does not treat an existing repository `.next` directory as a
  current build, and does not write the repository `.next`.
- Browser uses the real API. Dashboard, outcome, metrics, and report routes are
  not mocked.
- Expected scores are `0.16` and `-ln(0.6)` after confirm, then `0.36` and
  `-ln(0.4)` after correction to outcome 0. Those numbers are computed in the
  test helpers, not by importing production scoring functions.
- After the browser finishes, Python opens the isolated SQLite file and requires
  two append-only adjudications, an unchanged first row, an unchanged saved
  probability, and rejected raw `UPDATE`/`DELETE`.

## How to run

The dedicated spec lives in `apps/web/e2e/` beside the ordinary suite. The
default Playwright config `testIgnore`s it; the dedicated config `testMatch`es
it. Missing receipts raise; they do not skip. Use the dedicated runner so the
stack cannot attach to the running app:

```bash
uv run python tests/run_autopilot_outcome_acceptance.py
```

The runner allocates free ports, starts only the API and isolated-web processes
it owns, does not start a worker, and stops those process groups on exit. Each
run keeps a unique workspace with `seed-receipt.json`, `browser-actions.json`,
logs, and `final-receipt.json`. Pass `--purge-workspace` only when that
evidence should be deleted after success.

CI invokes the same command after the ordinary browser suite. It must not be
skipped.

## Verified results (September 9, 2026)

Codex independently reviewed the isolated acceptance and official DOL
correction-URL repair. Receipt:
`data/local/autopilot/v1-codex-verification-2026-09-09.json`
(`checked_at` 2026-09-09T20:57:27Z), against baseline
`261f7e7df96dd31ad1b7bb448cb453d042bd8b12`.

- New isolated browser → API → database acceptance: **passed** (one Playwright
  case; owned servers stopped; `npm audit` reported zero vulnerabilities).
- Targeted Python/PostgreSQL checks: **73 passed**.
- Earlier baseline full Python suite: **822 passed** (PostgreSQL 18), collected
  before these new tests.
- Existing browser suite: **24 passed, 2 skipped** (optional private-V1
  fixture-ID cases). Ordinary discovery still excludes this dedicated spec.
- Final Ruff, mypy, TypeScript, ESLint, and a fresh Turbopack production build:
  passed. The repository `.next` was left untouched.

This is local and isolated verification. It is **not** a production ship, an
accuracy claim, or authorization to enable Autopilot spending.

Still outstanding: GitHub OAuth / owner login, native Neon seven-day
point-in-time recovery, exact reviewed-release approval of a production SHA,
and activation. Production remains on paid Neon PostgreSQL 18; any
free-infrastructure comparison is historical and is not this launch plan.

## Rollback

If a later reviewed release of this change must be withdrawn, keep dispatch
paused and restore the previous production deployments. No schema rollback is
required. Receipt:
`data/local/autopilot/v1-rollback-targets-2026-09-09.json`
(`checked_at` 2026-09-09T21:04:07Z).

| App | Previous deployment | Commit |
| --- | ------------------- | ------ |
| API | `dpl_HG85vToqjV8tqDkBjhZ8xvtTMk5r` | `20c549c62c8b1f01bb4535517fa2e2f23d62ebe3` |
| Web | `dpl_8qt1jyoBqCc5YADho5upwWotwm2p` | `20c549c62c8b1f01bb4535517fa2e2f23d62ebe3` |

Do not resume automatic spending after a rollback until owner login, native
Neon recovery, and activation checks are independently verified.

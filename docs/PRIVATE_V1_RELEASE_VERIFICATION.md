# Private V1 Release Verification

ForecastLab's private-V1 verifier checks whether a specific tracked commit can be installed, migrated, exercised, rendered, and reproduced without using live providers or private machine state.

This verifier establishes software reproducibility only. It does not supersede the latest live operational acceptance result, which remains a hard **3-of-5 FAIL**, and it does not establish forecasting quality or calibration.

## Invocation

Run from an exact, clean candidate commit with credential-bearing environment variables removed:

```bash
python scripts/verify_private_v1_release.py \
  --source-sha "$(git rev-parse HEAD)" \
  --output-dir /absolute/path/outside/the/repository
```

The output directory must be outside the source repository. The verifier writes one sanitized `manifest.json` there. It never writes a database, credential file, runtime log, dependency cache, or provider response into the candidate checkout.

## Fail-closed preflight

Before creating a clone, the verifier requires:

- the requested commit to equal `HEAD`;
- no tracked worktree changes;
- frozen Python and web lockfiles;
- all tracked profiles, prompts, and migrations;
- no tracked `.env`, database, or credential artifact;
- no model or search API-key environment variable;
- mock model and search provider configuration only;
- no external database, data-directory, or credential-path setting;
- an output directory outside the source repository.

The preflight checks only tracked files and explicit environment-variable names. It does not read an untracked `.env`, Keychain entry, live Settings file, live database, or credential value.

## Two independent checkouts

The verifier creates two true local clones, not worktrees. Each checkout receives its own:

- temporary `HOME` and XDG cache;
- `uv` and npm caches;
- database and migration lock directory;
- API, worker, and web processes;
- loopback ports;
- mock provider configuration;
- synthetic release fixture.

Each clone installs Python and web dependencies from frozen locks. Temporary directories are removed after the audit, and process groups are terminated without broad process matching.

## Database gate

Each checkout verifies both database paths:

1. an empty SQLite database migrated to Alembic head;
2. the tracked legacy/MVP schema, including a marker row, upgraded to head.

The verifier compares fresh and upgraded schemas, checks that the legacy marker survived, requires `PRAGMA integrity_check = ok`, and requires zero foreign-key violations. Structural parity excludes the repository's documented SQLite server-default metadata representation differences, which are enumerated separately with raw schema hashes rather than hidden.

## Synthetic positive journey

The checked-in fixture is labeled `synthetic_release_verification_only`. The positive path uses `graph_forecaster_v1` version 9 with mock providers and exercises the real executor integration for:

- an approved Forecast Contract and approved five-node Forecast Graph;
- a frozen ResearchPlan and material-node plan audit;
- deterministic provenance-bearing EvidenceItems and EvidenceClaims;
- three ForecastNodeRuns;
- passed evidence-sufficiency and material-node assessments;
- exactly three grounded ScenarioSynthesis pathways (`base_case`, `yes_case`, and `no_case`);
- `relationship_mass_conserving_log_odds_v1` aggregation;
- ForecastAggregation, ForecastVersion, finite probability, JSON report, Markdown report, and web report.

The release fixture is not a model-quality fixture. It contains no resolved outcome and cannot be used to make an accuracy or calibration claim.

The positive audit independently recomputes aggregation without scenario prose, verifies identical probability, checks Decimal mass conservation, confirms omitted nodes receive no probability, and requires evidence, material, and scenario identities in the deterministic trace.

## Insufficient-evidence negative journey

The negative path preserves three researched nodes and their claims and node forecasts, but marks one included node's extraction as deterministic fallback-only evidence. The private evidence gate must fail. The verifier requires:

- a terminal failed ForecastRun at `evidence_sufficiency`;
- preserved research, evidence, and node-run artifacts;
- a failed immutable EvidenceSufficiencyAssessment;
- no ScenarioSynthesis, ForecastAggregation, ForecastVersion, retry, or probability;
- zero live-provider calls and zero cost.

## Frontend and browser gate

In each checkout the verifier runs frozen web installation, TypeScript checking, a production build, and a single-worker Chromium workflow against the isolated API, worker, database, and built web application. The browser must render both the complete positive path and the failed/no-probability negative path.

The frozen web lock currently reports three high-severity dependency advisories involving Next, its nested PostCSS, and sharp. The available npm fix is a semver-major Next upgrade and is intentionally not applied by this verification task.

## Reproducibility boundary

Only UUIDs, timestamps, temporary paths, and ports may be normalized. Profile and prompt identities, graph/node structure, selected nodes, provenance classifications, deterministic gate results, scenario structure and grounding, aggregation method and mass accounting, final probability, and failure classifications remain substantive.

The normalized outputs from the two clones must be identical and produce one SHA-256 digest. Any substantive difference fails the verifier.

The tracked manifest attests the exact candidate commit that was cloned and verified. Because a manifest cannot include itself while also identifying the commit that contains it, the manifest and report are added in a later evidence-only commit. The final branch commit receives its own normal test and exact-SHA CI gates; it does not retroactively change the attested candidate result.

## Security and cleanup

The verifier scans tracked text and generated JSON/logs for credential-shaped values. Generated artifacts also reject raw-provider and authorization fields. It requires mock-only terminal ledger rows, zero dollars, no active jobs, no unreconciled reservations, stopped services, a clean tracked checkout, and clean SQLite integrity at shutdown.

No tag, merge, release publication, or deployment is performed or authorized by this workflow.

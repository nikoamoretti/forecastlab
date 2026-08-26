# ForecastLab Private V1 Release Verification Report

## Verdict

**Offline fresh-checkout verification: PASS.**

This result establishes deterministic software installation, migration, mock-backed execution, rendering, and reproducibility for the attested candidate. It does not establish live operational acceptance, forecasting quality, or calibration.

The latest live operational acceptance remains a hard **3-of-5 FAIL**. No tag, merge, release publication, or deployment is authorized by this report.

## Attested candidate

- Candidate label: `private-v1-verification-v1`
- Candidate commit: `a265580333e0b98828778817650fbded2ee51521`
- Candidate tree: `ec279e25e34d017489272f5b340009e6fe7188d2`
- Application version: `0.3.1`
- Tracked-source SHA-256: `741e031e20819c564b435763c6976ba498e2dcec4ceda088aa740ebd32c814d2`
- Normalized two-checkout result SHA-256: `2e0471cf76ee6e8ebe3a7b165cfcf737fad7605dc62f54c06235f0d670331b5f`
- Sanitized manifest SHA-256 before tracking: `6b1e0d069e9bc16d19b797677c11f9984f5b1fc9eec497062545e3ccae6d0b6a`

The complete individual lock, profile, prompt, and migration hashes are recorded in [`artifacts/private_v1_release_verification/manifest.json`](../artifacts/private_v1_release_verification/manifest.json). The manifest attests the candidate commit above. This report and manifest are added by an evidence-only follow-up commit, avoiding a circular self-hash claim.

## Two-checkout reproducibility

Two true local clones completed independently with separate temporary homes, caches, databases, ports, API/worker/web stacks, and mock-provider state. Both used frozen Python and frontend locks.

- Isolated checkout count: 2
- Substantive differences: 0
- Equivalent: yes
- Allowed normalization: UUIDs, timestamps, temporary paths, and ports only
- Profiles, prompts, graph structure, selected nodes, provenance, gates, scenario structure, aggregation mass, probability, and failure classifications remained substantive

## Database verification

Both checkouts verified an empty database migrated to head and the tracked legacy/MVP schema upgraded to head.

- Alembic head: `20260826_0026`
- Fresh database: integrity `ok`, zero foreign-key violations
- Legacy upgrade: integrity `ok`, zero foreign-key violations, marker row preserved
- Structural schema parity: passed with zero unsupported differences
- Fresh raw schema SHA-256: `74f46a12429f8a0419eb45cb433df2399aecd58fdacd35631f3265e3e4c4dc98`
- Upgraded raw schema SHA-256: `85da31b6d6d44e22a7a332102f26d7d7478a73bcb12a26d571e2e8f93de8f58c`
- Raw schema hashes equal: no
- Documented SQLite server-default metadata representation differences: 21

The 21 default-metadata paths are listed in the manifest. They are an existing documented SQLite fresh-versus-upgrade representation difference, not silently normalized into equal raw hashes.

## Positive private-V1 journey

The fixture was explicitly labeled `synthetic_release_verification_only`, contained no resolved outcome, and used `graph_forecaster_v1` version 9 with mock providers.

- ForecastRun status: completed
- Approved graph nodes: 5
- ResearchPlan selected nodes: 3
- Eligible EvidenceClaims: 3
- ForecastNodeRuns: 3
- Evidence-sufficiency assessment: passed
- Material-node assessment: passed
- Scenario synthesis: passed with exactly `base_case`, `yes_case`, and `no_case`
- Aggregation: `relationship_mass_conserving_log_odds_v1`
- ForecastAggregation and ForecastVersion: created
- Final synthetic probability: `0.480867069435`
- Neutral residual fraction: `0.25`
- Mass conservation: passed
- Omitted-node probability imputation: none
- Scenario numerical effect: none; probability invariance passed
- JSON, Markdown, and browser report surfaces: passed

## Insufficient-evidence negative journey

The negative fixture preserved three evidence items, three claims, and three node runs, but used fallback-only evidence for one included node.

- ForecastRun status: failed
- Failure stage: `evidence_sufficiency`
- Failure code: `evidence_sufficiency_gate_failed`
- Evidence-sufficiency assessment: failed and preserved
- ScenarioSynthesis: absent
- ForecastAggregation: absent
- ForecastVersion: absent
- Final probability: null
- Provider requests: 0
- Cost: `$0.00`

The failed state and no-probability result rendered successfully in the browser.

## Frontend and browser

Each checkout passed frozen npm installation, TypeScript checking, production build, and the single-worker Chromium workflow against its isolated built stack.

The frozen frontend lock reports three known high-severity advisories involving `next`, nested `postcss`, and `sharp`; critical advisories are zero. The available npm remediation requires the unauthorized semver-major Next 16.3.3 upgrade, so dependencies were not changed.

## Security, provider use, and cleanup

- OpenAI calls: 0
- Tavily calls: 0
- Live-provider ledger rows: 0
- Total provider cost: `$0.00`
- Suspected tracked secrets: 0
- Generated secret matches: 0
- Forbidden generated keys: 0
- Raw provider responses persisted: 0
- Committed environment, database, or credential artifacts: 0
- Known hash-pinned synthetic/redaction fixtures: 11
- Active jobs after each checkout: 0
- Nonterminal ledgers: 0
- Unreconciled reservations: 0
- Services stopped: yes
- Temporary service files preserved: no

No Keychain, real credential, live database, user setting, untracked file, live provider, tag, merge, deployment, or release-publication path was used.

## Remaining limitations

- The latest live operational acceptance remains a hard 3-of-5 failure.
- Synthetic mock verification is evidence of software reproducibility only.
- Forecasting accuracy, calibration, and real-data evaluation remain unestablished.
- The three known frontend advisories remain unresolved.
- This is a private release candidate verification artifact, not a published release.

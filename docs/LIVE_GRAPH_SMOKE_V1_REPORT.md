# Live Graph Smoke V1 Report

> Single live-provider execution test. This result validates operational execution only and is not evidence of forecasting quality.

## Result

**FAIL — preserved without retry.**

The one authorized `graph_forecaster_v1` service invocation stopped locally before a `ForecastRun` was created and before OpenAI or Tavily was contacted:

```text
ConfigurationError: Upper-bound workload exceeds the Settings cost ceiling. Choose a lower-workload profile.
```

The frozen `graph_forecaster_v1` upper-bound estimate was `$1.600000`. The existing Settings ceiling was `$0.250000`. This task prohibited changing budgets or provider settings, so the failure was preserved and the execution was not resubmitted.

## Source and invocation identity

- Branch: `grok/live-graph-smoke-v1`
- Starting commit: `056f34c8ae315eb5799a860db44094730fd30158`
- Application: ForecastLab `0.3.1`
- Authorized graph service invocations: `1`
- ForecastRuns created: `0`
- Whole-run retries: `0`
- Physical provider requests: `0`
- Invocation wall time: `1,213 ms`
- ForecastRun latency: not available because no run was persisted
- External log: `/Users/nico-yardlogix/forecastlab-backups/live-graph-smoke-20260824-235341.log`
- External log SHA-256: `ac001b81e7e3fd07555ab78b4789a7b73389f6abc5d0bba90e7949ae0e656454`

## Required feature preflight

Before the invocation, source inspection and 51 targeted offline tests verified:

- `graph_forecaster_v1` version 5 includes Forecast Contract, Forecast Graph, Research Planner, live retrieval, Evidence Claims, Node Forecasts, and graph aggregation.
- Research Planner limits are eight selected nodes, five searches per node, and twenty claims total.
- OpenAI GPT-5 and o-series requests use `max_completion_tokens` and omit `max_tokens` and `temperature`.
- xAI and generic OpenAI-compatible request behavior remains unchanged.
- The cutoff-consistent validation adapter rejects live mode, ordinary backtests, and user-configured provider selection.
- The persistent run-lifetime provider ledger enforces the effective cost and token ceilings.

## Locked environment and readiness

- `uv sync --extra dev --frozen`: passed; 61 packages audited.
- Worktree after sync: clean.
- API health: healthy.
- Database health: healthy.
- Worker status before invocation: fresh and idle.
- Web application: not required for the supported service path and was not opened.
- Public provider-readiness endpoint calls: one.
- Separate provider probes: zero.

| Readiness field | Value |
| --- | --- |
| Mode | `live` |
| Model provider | `openai` |
| Model | `gpt-5-mini-2025-08-07` |
| Model key present | `true` |
| Search provider | `tavily` |
| Search key present | `true` |
| Live readiness | `true` |
| Reasons | none |

No raw credential value was read or printed.

## Database backup and baseline

- Pre-run UTC timestamp: `2026-08-24T23:53:41Z`
- Backup: `/Users/nico-yardlogix/forecastlab-backups/forecastlab-before-live-graph-smoke-20260824-235341.db`
- Backup stored outside repository: yes
- Baseline maximum Question creation time: `2026-08-24T05:27:47.302235Z`
- Baseline Questions: `214`
- Baseline ForecastRuns: `219`
- Baseline provider-ledger rows: `1,296`

## Question and contract

- Question ID: `ebe815cf-0fe6-491c-b094-4e5ecf777b24`
- Contract ID: `1681fea3-5e04-4d0c-983d-3010801e4b65`
- Contract status: approved
- Requested profile: `graph_forecaster_v1`
- Requested mode: `live`
- Historical cutoff: none
- Question status after invocation: `contract_ready`

The question and contract match the task specification. The contract was created from the manually supplied fields and approved without model generation.

## Budget rejection

| Measure | Value |
| --- | ---: |
| Settings ceiling | `$0.250000` |
| Source graph-profile ceiling | `$5.000000` |
| Effective ceiling | `$0.250000` |
| Upper-bound workload estimate | `$1.600000` |
| Maximum model calls | `40` |
| Maximum search calls | `36` |
| Maximum fetched documents | `24` |
| Maximum tokens | `200,000` |
| Maximum wall time | `180 seconds` |

The execution context rejected the workload before provider construction. The Research Planner therefore never received a graph and could not reduce the workload.

## Read-only execution audit

### Question and run

- New Questions: `1`
- New ForecastRuns: `0`
- Run ID: none
- Persisted execution context: none
- Run profile/version/mode/status: none
- Reduced-confidence state: none

### Research plan

- Forecast Graphs: `0`
- Graph nodes: `0`
- ResearchPlans: `0`
- Selected/skipped nodes: `0 / 0`
- Critical selected nodes: none
- Priority scores: none
- Planner model/search/total estimates: none
- Worker concurrency: `0`

### Research execution

- Queries: `0`
- Successful/failed Tavily requests: `0 / 0`
- Search results: `0`
- Document fetches: `0`
- Accepted/rejected documents: `0 / 0`
- Accepted evidence URLs: none
- EvidenceClaims: `0`
- Extraction retries/fallbacks/failures: `0 / 0 / 0`

### Node forecasting and aggregation

- NodeForecastRuns: `0`
- Critical/noncritical node failures: `0 / 0`
- ForecastAggregations: `0`
- ForecastVersions: `0`
- Final probability: none
- Calculation trace: none

### Provider ledger

No provider-ledger rows were added.

| Provider | Type | Model | Stage | Physical attempt | Status | Reserved input | Reserved output | Actual input | Actual output | Reserved cost | Actual/estimated cost | Cost source | Request ID | Error category |
| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| none | none | none | none | 0 | not executed | 0 | 0 | 0 | 0 | `$0.000000` | `$0.000000` | no provider request | none | none |

### Costs

| Cost | Value |
| --- | ---: |
| Model | `$0.000000` |
| Search | `$0.000000` |
| Failed attempts | `$0.000000` |
| Total | `$0.000000` |
| Effective ceiling | `$0.250000` |
| Remaining | `$0.250000` |

## Twenty-four-condition gate

| # | Condition | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Exactly one new Question was created | PASS | `1` |
| 2 | Exactly one new graph ForecastRun was created | **FAIL** | `0`; context rejected before persistence |
| 3 | Run uses `graph_forecaster_v1` | **FAIL** | No ForecastRun |
| 4 | Run mode is live | **FAIL** | No ForecastRun |
| 5 | `model_is_mock` is false | **FAIL** | No persisted execution context |
| 6 | `search_is_mock` is false | **FAIL** | No persisted execution context |
| 7 | Fixture evidence is false | **FAIL** | No ForecastRun |
| 8 | Validation adapter not selected | PASS | No adapter or provider instantiated |
| 9 | ResearchPlan persisted | **FAIL** | `0` |
| 10 | At least one graph node selected | **FAIL** | `0` |
| 11 | At least one Tavily request succeeded | **FAIL** | `0` |
| 12 | At least one external document accepted | **FAIL** | `0` |
| 13 | At least one EvidenceClaim persisted | **FAIL** | `0` |
| 14 | At least two NodeForecastRuns persisted | **FAIL** | `0` |
| 15 | No selected critical node failed | PASS | No nodes were selected; node execution was not reached |
| 16 | ForecastAggregation persisted | **FAIL** | `0` |
| 17 | ForecastVersion persisted | **FAIL** | `0` |
| 18 | Final probability is finite and in range | **FAIL** | No probability |
| 19 | At least one OpenAI request succeeded | **FAIL** | `0` |
| 20 | Every provider-ledger row is terminal | PASS | `0` new rows and `0` nonterminal rows; provider execution was not reached |
| 21 | Provider identities are OpenAI and Tavily | **FAIL** | Readiness matched, but no run/ledger identity was persisted |
| 22 | No secret appears in outputs or stored errors | PASS | Sanitized log, report, artifact, and database-error scan passed with zero matches |
| 23 | Total cost is within ceiling | PASS | `$0.000000 <= $0.250000`; no ForecastRun |
| 24 | Tracked worktree remains clean | PASS | No source/configuration changes; only this report and its artifact were added after execution |

Overall result: **FAIL** because the required ForecastRun and provider-backed graph execution were never created.

## Verification

- `uv run --frozen --no-sync python -m pytest -q`: `378 passed`, one existing Starlette/httpx deprecation warning, `66.93 s`.
- `uv run --frozen --no-sync python -m ruff check packages apps/api tests`: passed.
- `uv run --frozen --no-sync python -m mypy`: passed across 76 source files.
- Targeted preflight tests: `51 passed`, one existing warning.
- Sanitized log, report, artifact, and post-baseline database-error secret scan: passed with zero secret-value matches.
- Artifact invariants: passed.
- Frontend checks: not required because no application, report-builder, or UI code changed.
- Additional graph invocations during verification: `0`.

## Integrity confirmations

- Source code changed: no.
- Forecasting behavior, prompts, profiles, graph generation, planner, extraction, node forecasting, or aggregation changed: no.
- Budgets or provider settings changed: no.
- API keys created, rotated, copied, or printed: no.
- Mock providers or validation fixtures used: no.
- OpenAI requests: `0`.
- Tavily requests: `0`.
- Full pilot run: no.
- Merge or deployment: no.
- Accuracy evaluation or tuning: no.
- Second graph invocation: no.

Single live-provider execution test. This result validates operational execution only and is not evidence of forecasting quality.

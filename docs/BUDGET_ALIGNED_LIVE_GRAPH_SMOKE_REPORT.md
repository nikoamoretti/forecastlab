# Budget-Aligned Live Graph Smoke Report

> Single live-provider graph execution test. This result validates operational execution only and is not evidence of forecasting quality.

## Result

**FAIL — preserved without retry.**

Exactly one authorized `graph_live_smoke_v1` invocation ran through real OpenAI and Tavily. The graph and ResearchPlan were created, five OpenAI requests and four Tavily requests succeeded, and four external documents were accepted as EvidenceItems. All four selected nodes then failed evidence extraction because every accepted document lacked a verified publication date. No EvidenceClaims, NodeForecastRuns, ForecastAggregation, ForecastVersion, or final probability were produced.

The invocation was not retried. Source code, prompts, profiles, graph generation, Research Planner behavior, evidence behavior, node forecasting, and aggregation were not changed.

## Source and CI identity

- Branch: `grok/graph-live-smoke-budget`
- Verified patch commit: `570089768936f9d82e3de7c5096f83ec97848ece`
- Required GitHub Actions run: `32797008567`
- CI head SHA: `570089768936f9d82e3de7c5096f83ec97848ece`
- CI status/conclusion: `completed` / `success`
- CI URL: <https://github.com/nikoamoretti/forecastlab/actions/runs/32797008567>
- Locked dependency sync: passed; 61 packages audited
- Tracked worktree before execution: clean
- `uv.lock`: present

## Service and provider readiness

The application was started through the locked local startup path. API health, database health, and worker freshness all passed. The public provider-readiness endpoint was called once. No separate live provider probe was run.

| Readiness field | Value |
| --- | --- |
| Mode | `live` |
| Model provider | `openai` |
| Model | `gpt-5-mini-2025-08-07` |
| Model key present | `true` |
| Search provider | `tavily` |
| Search key present | `true` |
| Live readiness | `true` |
| Readiness reasons | none |

No credential value was inspected or printed.

## Settings and preflight

Only the public maximum-cost setting was changed, then restored:

| Point in time | Maximum cost |
| --- | ---: |
| Before preview and execution | `$0.250000` |
| During preview and execution | `$0.500000` |
| After audit | `$0.250000` |

Provider, model, base URL, timeout, model-key metadata, search provider, and search-key metadata were unchanged.

The public execution preview for `graph_live_smoke_v1` in live mode reported:

| Measure | Value |
| --- | ---: |
| Model upper-bound estimate | `$0.400000` |
| Search upper-bound estimate | `$0.032000` |
| Total upper-bound estimate | `$0.432000` |
| Effective ceiling | `$0.500000` |
| Estimate exceeds ceiling | `false` |

## Database backup and baseline

- Backup timestamp: `2026-08-25T01:36:56Z`
- Backup: `/Users/nico-yardlogix/forecastlab-backups/forecastlab-before-budget-aligned-graph-smoke-20260825-013656.db`
- Backup location: outside the repository
- Baseline Questions: `215`
- Baseline ForecastRuns: `219`
- Baseline provider-ledger rows: `1,296`
- Post-invocation Questions: `216`
- Post-invocation ForecastRuns: `220`
- Post-invocation provider-ledger rows: `1,305`

## Question and approved contract

- Question ID: `6a36cd0f-7c2c-4992-b5da-7f4a8632cd0c`
- Contract ID: `62b4818e-7824-4c0c-8d99-7b39cf430fce`
- Contract version/status: `1` / `approved`
- Question status after execution: `failed`
- Requested profile/mode: `graph_live_smoke_v1` / `live`
- Historical cutoff: none; evidence policy was `live_current`

The contract was created and approved from the manually supplied fields without model generation.

**Question:** Will the US civilian unemployment rate (U-3, seasonally adjusted) be at or above 5.0 percent for any month whose official BLS release date is on or before 30 June 2027?

**YES:** The US Bureau of Labor Statistics U-3 unemployment rate, seasonally adjusted, is reported at or above 5.0% for any month whose official release date is on or before 30 June 2027.

**NO:** No BLS U-3 seasonally adjusted monthly reading at or above 5.0% is published with a release date on or before 30 June 2027.

- Resolution deadline: `2027-07-15T00:00:00Z`
- Authoritative source: <https://www.bls.gov/news.release/empsit.toc.htm>
- Fallback source: <https://fred.stlouisfed.org/series/UNRATE>
- Geography: United States
- Units: Percent, seasonally adjusted U-3
- Resolution method: inspect official BLS Employment Situation releases dated on or before 30 June 2027; resolve YES if any first-released seasonally adjusted U-3 monthly reading is at least 5.0%, otherwise NO, subject only to a BLS restatement of that first-release figure before 15 July 2027.
- Ambiguity rule: the first official monthly BLS release binds unless BLS restates that first-release figure before the deadline.
- Cancellation rule: invalidate if BLS discontinues U-3 without a directly comparable replacement.
- Resolver risk: do not substitute U-6 or a three-month average.

## Exactly-once invocation

- Service path: `pipeline.start_run` to `pipeline.execute_run` to `GraphForecastExecutor`
- Authorized graph-smoke invocations: `1`
- Whole-run retries: `0`
- Automatic whole-run retry: disabled
- Run ID: `c9606e4d-adbc-438e-8d43-767d83d3dc96`
- Run-attempt ID: `269a01a6-9f54-4b22-aab5-a4be966f8a89`
- Profile/version: `graph_live_smoke_v1` / `1`
- Mode: `live`
- Status: `failed`
- Failure stage/category: `node_research` / `GraphForecastExecutionError`
- Started: `2026-08-25T01:40:45.764729Z`
- Finished: `2026-08-25T01:41:38.073835Z`
- Invocation wall latency: `52,307 ms`
- Stored failed-run latency field: `0 ms`; the external invocation log and run timestamps establish the actual wall latency above
- External log: `/Users/nico-yardlogix/forecastlab-backups/graph-live-smoke-budget-aligned-20260825-013656.log`
- External log SHA-256: `d8e467b9a9ea01e08aeb90f76bdb61858993a7b886e3f0eb0e44811cb84a2b12`
- Reduced-confidence state: not produced; reliability impact was `forecast_failed` with research coverage `0.0`

## Forecast graph and ResearchPlan

- ForecastGraph ID: `93979581-e8ad-481c-825d-0bfc5cc0d0aa`
- Graph version/status: `1` / `approved`
- Generation provider/model: `openai:gpt-5-mini-2025-08-07`
- Total graph nodes: `8`
- ResearchPlan ID: `bee437bc-b2a1-4e1a-bdea-ea8eb9a2d6dc`
- Selected/skipped nodes: `4 / 4`
- Selected critical nodes: none
- Parallel research workers: `4`

### Selected nodes

| Node ID | Type | Weight | Priority | Question |
| --- | --- | ---: | ---: | --- |
| `50952ec3-856c-41af-bbb1-6cc01f042490` | trend | `0.20` | `4.85` | Recent first-release U-3 trajectory and whether it materially raises the chance of reaching 5.0% by the deadline |
| `1872c406-bed0-428d-9416-55da2cc34396` | scenario | `0.18` | `4.18` | No-recession, mild-recession, and severe-recession scenario weights |
| `5be68285-96ba-491f-b384-c6fd904e40cf` | driver | `0.22` | `3.97` | Payroll-decline magnitudes and durations needed to raise U-3 to 5.0% |
| `c6a57a06-ce07-4569-a3f4-52c32df94c0b` | driver | `0.12` | `2.87` | Labor-force and participation changes that could move U-3 to 5.0% |

### Skipped nodes

| Node ID | Type | Weight | Priority | Reason |
| --- | --- | ---: | ---: | --- |
| `09a593a6-78ee-4c8c-8917-5a72732d917a` | base_rate | `0.15` | `2.50` | lower priority or budget limited |
| `479e1b9b-e273-476a-934f-24145d619263` | resolver | `0.05` | `2.50` | lower priority or budget limited |
| `67e8cd1e-5d01-43fe-bcbb-9707226ecd2d` | adversarial | `0.06` | `1.96` | lower priority or budget limited |
| `31a1196c-6cda-4efc-983c-447747a259fd` | dependency | `0.02` | `1.72` | lower priority or budget limited |

### Allocation

The planner allocated each selected node one search, one fetch, three possible model calls, up to 11,096 tokens, and up to five claims. Its deterministic persistence order was trend, payroll driver, participation driver, then scenario.

| Allocation measure | Value |
| --- | ---: |
| Estimated model cost per selected node | `$0.096440` |
| Estimated search cost per selected node | `$0.008000` |
| Estimated total cost per selected node | `$0.104440` |
| Estimated total selected-node cost | `$0.417760` |
| Available cost before research | `$0.439530` |
| Available model calls before research | `13` |
| Available search calls before research | `4` |
| Available fetches before research | `8` |
| Available tokens before research | `45,394` |
| Available wall time before research | `299.992 s` |

## Evidence audit

Four Tavily searches succeeded and four external EvidenceItems were accepted. Every item had `published_at = null` and `published_at_unknown = true`. All three extraction passes for each item—full document, smaller chunk, and document fallback—were therefore rejected with `publication_date_unverified` and `publication_date_required`.

| Node | Accepted URL | Source class | HTTP | Extraction outcome |
| --- | --- | --- | ---: | --- |
| trend | <https://tradingeconomics.com/united-states/unemployment-rate> | secondary | 200 | failed: publication date unverified/required |
| payroll driver | <https://research.titanfx.com/glossary/what-is-nfp> | secondary | 200 | failed: publication date unverified/required |
| participation driver | <https://www.bls.gov/charts/employment-situation/civilian-labor-force-participation-rate.htm> | primary | 200 | failed: publication date unverified/required |
| scenario | <https://www.federalreserve.gov/publications/2026-stress-test-scenarios.htm> | primary | 200 | failed: publication date unverified/required |

- Accepted EvidenceItems: `4`
- Rejected EvidenceItems: `0`
- EvidenceClaims: `0`
- Successful/failed Tavily ledger requests: `4 / 0`
- Extraction failures: `4`

No fixture evidence, mock provider, synthetic adapter, or cutoff-consistent validation adapter was used.

## Node forecasting, aggregation, and version

All four selected nodes were noncritical, but all four failed research. The policy requires at least three successful eligible nodes, so the executor failed rather than producing a probability from incomplete research.

- Failed critical nodes: none
- Failed noncritical nodes: all four selected nodes
- NodeForecastRuns: `0`
- Node probabilities: none
- ForecastAggregation ID: none
- Aggregation method: none
- Normalized weights: none
- Log-odds contributions: none
- Calculation trace: none
- ForecastVersion ID: none
- Final probability: none

## Complete sanitized provider ledger

All nine physical-request rows are terminal and succeeded. No provider-level retry occurred. Costs use the committed estimator because provider billing data was not returned.

| Ledger ID | Logical call | Attempt | Stage | Provider/model | Started | Completed | Status | Reserved in/out | Actual in/out | Reserved cost | Actual estimated cost | Request ID | Error |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| `857f0e09-dcd9-4f69-ba17-208c1f211a9a` | `3da914e4-7f7a-46ad-9965-7e1199fb9629` | 1 | model | openai / gpt-5-mini-2025-08-07 | 01:40:45.906063Z | 01:41:21.791087Z | succeeded | 0 / 4,096 | 862 / 3,744 | `$0.061440` | `$0.060470` | `req_122fc231d14d4e839c69924421bf8092` | none |
| `248008f7-0472-4653-a475-ac951e1c6f03` | `7fc76038-40d4-49bd-862c-ba3e2e10fa3a` | 1 | model | openai / gpt-5-mini-2025-08-07 | 01:41:21.809974Z | 01:41:32.436742Z | succeeded | 397 / 1,024 | 365 / 1,024 | `$0.017345` | `$0.017185` | `req_aadc76c09dfc4089b24731746f4d875e` | none |
| `cd0d8b23-f7b9-4e06-9fa5-47f16724c7dd` | `c8b923e8-346a-4473-b49e-237909535a20` | 1 | model | openai / gpt-5-mini-2025-08-07 | 01:41:21.810378Z | 01:41:32.552583Z | succeeded | 416 / 1,024 | 377 / 1,024 | `$0.017440` | `$0.017245` | `req_1184dff8803c4996bb27a76debedd886` | none |
| `688e0082-7afa-407c-bc6c-ee381b033a79` | `3ed1e756-a823-4c99-9936-bf8db0c1b1a1` | 1 | model | openai / gpt-5-mini-2025-08-07 | 01:41:21.813545Z | 01:41:32.880347Z | succeeded | 399 / 1,024 | 360 / 1,024 | `$0.017355` | `$0.017160` | `req_1f36c8aba5cf4d0893b9771b58e69998` | none |
| `e9f373ff-d864-42d8-adce-6b54a5e556a2` | `69aad1cb-6cf1-4210-b8c8-1e9952200b1a` | 1 | model | openai / gpt-5-mini-2025-08-07 | 01:41:21.813710Z | 01:41:32.533858Z | succeeded | 385 / 1,024 | 346 / 1,024 | `$0.017285` | `$0.017090` | `req_143788c1036849eda941d6c79f29f930` | none |
| `02dafa64-b36a-496a-947f-71aeec6a061d` | `cde7f1df-22f4-40de-9bb6-dc4203dd5928` | 1 | search | tavily / tavily-search | 01:41:32.445045Z | 01:41:35.156896Z | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| `f69751ca-f585-489b-b05a-07abc32624ff` | `2f86549f-1660-4d74-b599-0099b1143e30` | 1 | search | tavily / tavily-search | 01:41:32.541320Z | 01:41:35.014234Z | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| `b2a6841a-b798-4a0a-a98f-207f18f4518b` | `ddbc4e7a-75af-417b-b872-c66711454b27` | 1 | search | tavily / tavily-search | 01:41:32.561838Z | 01:41:35.146397Z | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| `f75e8cba-0324-4ad9-a75f-17a0f7af369b` | `9dd08238-9db4-4787-8ed2-c336a3eaf703` | 1 | search | tavily / tavily-search | 01:41:32.888635Z | 01:41:35.213589Z | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |

All rows belong to run-attempt `269a01a6-9f54-4b22-aab5-a4be966f8a89`; every `physical_attempt_number` is `1`, every `error_category` and `error_message` is null, and every `cost_source` is `estimated`.

## Usage and cost

| Measure | Actual | Profile ceiling |
| --- | ---: | ---: |
| Model calls | `5` | `14` |
| Search calls | `4` | `4` |
| Fetches | `4` | `8` |
| Prompt tokens | `2,310` | — |
| Completion tokens | `7,840` | — |
| Total tokens | `10,150` | `50,000` |
| Model cost | `$0.129150` | — |
| Search cost | `$0.032000` | — |
| Failed-attempt cost | `$0.000000` | — |
| Total cost | `$0.161150` | `$0.500000` |
| Remaining cost ceiling | `$0.338850` | — |

## Twenty-five-condition gate

| # | Condition | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | One new Question exists | PASS | `216 - 215 = 1` |
| 2 | One new ForecastRun exists | PASS | `220 - 219 = 1` |
| 3 | Profile is `graph_live_smoke_v1` version 1 | PASS | persisted execution context |
| 4 | Mode is live | PASS | persisted run and execution context |
| 5 | No mock provider or fixture adapter is used | PASS | model/search mock flags false; fixture and synthetic flags false |
| 6 | A ResearchPlan exists | PASS | `bee437bc-b2a1-4e1a-bdea-ea8eb9a2d6dc` |
| 7 | Between two and four nodes are selected | PASS | `4` |
| 8 | At least one Tavily request succeeds | PASS | `4` succeeded |
| 9 | At least one external document is accepted | PASS | `4` accepted EvidenceItems |
| 10 | At least two EvidenceClaims exist | **FAIL** | `0`; all extraction paths rejected unverified publication dates |
| 11 | At least two NodeForecastRuns exist | **FAIL** | `0` |
| 12 | No selected critical node fails | PASS | no selected node was classified critical; critical failures `0` |
| 13 | A ForecastAggregation exists | **FAIL** | `0` |
| 14 | A ForecastVersion exists | **FAIL** | `0` |
| 15 | Final probability is finite and between 0.01 and 0.99 | **FAIL** | no final probability |
| 16 | At least one OpenAI request succeeds | PASS | `5` succeeded |
| 17 | Every ledger row is terminal | PASS | `9/9` succeeded and terminal |
| 18 | Provider identities are OpenAI and Tavily | PASS | persisted run context and ledger |
| 19 | Total model calls are no more than 14 | PASS | `5 <= 14` |
| 20 | Search calls are no more than 4 | PASS | `4 <= 4` |
| 21 | Fetches are no more than 8 | PASS | `4 <= 8` |
| 22 | Total token usage is no more than 50,000 | PASS | `10,150 <= 50,000` |
| 23 | Total cost is no more than $0.50 | PASS | `$0.161150 <= $0.500000` |
| 24 | No credential appears in output, ledger, or stored errors | PASS | zero generic secret-pattern matches across audited surfaces |
| 25 | Git worktree remains clean | PASS | clean before execution and after committing the two authorized result files |

Overall: **FAIL**, because conditions 10, 11, 13, 14, and 15 failed.

## Integrity confirmations

- Exactly one live graph-smoke invocation occurred; it was not rerun.
- No separate OpenAI or Tavily probe occurred.
- No mock provider, fixture evidence, synthetic adapter, or cutoff-consistent validation adapter was used.
- No credential was printed, rotated, copied, or changed.
- Only the maximum-cost setting changed temporarily, from `$0.25` to `$0.50` and back to `$0.25`.
- No source code, prompt, profile, graph, Research Planner, evidence, node-forecasting, aggregation, benchmark, or production-cutoff behavior changed.
- No benchmark, five-question acceptance test, merge, or deployment occurred.
- The failure was preserved without a second invocation.

Single live-provider graph execution test. This result validates operational execution only and is not evidence of forecasting quality.

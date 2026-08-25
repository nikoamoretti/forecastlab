# Mode-Aware Live Graph Smoke Report

> Single live-provider graph execution test. This validates operational and temporal-provenance behavior only and is not evidence of forecasting quality.

## Result

**FAIL — preserved without retry.**

Exactly one authorized `graph_live_smoke_v1` invocation ran through real OpenAI and Tavily. The run created a ForecastGraph, a ResearchPlan, four accepted external EvidenceItems, four EvidenceClaims, and two NodeForecastRuns. It then exhausted the profile's 14-model-call limit before a third selected node could be forecast. The executor correctly failed rather than aggregating fewer than the required three successful nodes. No ForecastAggregation, ForecastVersion, or final probability was produced.

The temporal-provenance failure from the prior smoke did not recur: all four accepted documents produced EvidenceClaims with a live source-availability timestamp, `temporal_basis = retrieval_date`, and `cutoff_verified = true` under the `live_current` evidence policy. However, this run did **not** directly exercise the unknown-publication-date claim path. Conservative date discovery found a publication date for every selected document, so all four claims have non-null publication dates and `publication_date_verified = true`. The conditional rule for unknown dates was therefore not violated, but its live-provider behavior was not observed in this invocation.

The invocation was not retried. Source code, prompts, profiles, graph generation, Research Planner behavior, evidence behavior, node forecasting, and aggregation were not changed.

## Source and CI identity

- Branch: `grok/mode-aware-evidence-time`
- Verified patch commit: `d7d855a9ee687333f353297065f555cf5d6feaa6`
- Required GitHub Actions run: `32803460217`
- CI head SHA: `d7d855a9ee687333f353297065f555cf5d6feaa6`
- CI status/conclusion: `completed` / `success`
- CI URL: <https://github.com/nikoamoretti/forecastlab/actions/runs/32803460217>
- Tracked worktree before execution: clean
- `uv.lock`: present

## Service and provider readiness

ForecastLab was started through the locked local startup path. API health, database health, and worker freshness passed. The public provider-readiness endpoint was called once. No separate live provider probe was run.

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

All other public Settings fields were unchanged. Provider configuration and credential metadata were not altered.

The public execution preview for `graph_live_smoke_v1` in live mode reported:

| Measure | Value |
| --- | ---: |
| Model upper-bound estimate | `$0.400000` |
| Search upper-bound estimate | `$0.032000` |
| Total upper-bound estimate | `$0.432000` |
| Effective ceiling | `$0.500000` |
| Estimate exceeds ceiling | `false` |

## Database backup and deltas

- Backup timestamp: `2026-08-25T03:11:48Z`
- Backup: `/Users/nico-yardlogix/forecastlab-backups/forecastlab-before-temporal-evidence-smoke-20260825-031148.db`
- Backup SHA-256: `edaca3c780bbc0d26d7b8765169f88b567705a31821fb22b06e75314958bd95e`
- Backup location: outside the repository

| Table | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Questions | 216 | 217 | +1 |
| ForecastRuns | 220 | 221 | +1 |
| EvidenceItems | 2,536 | 2,540 | +4 |
| EvidenceClaims | 70 | 74 | +4 |
| NodeForecastRuns | 70 | 72 | +2 |
| ForecastAggregations | 10 | 10 | 0 |
| ForecastVersions | 198 | 198 | 0 |
| Provider ledger rows | 1,305 | 1,323 | +18 |

## Question and approved contract

- Question ID: `b9a5ea80-8a7d-4cf9-befb-8b79e4921cf1`
- Contract ID: `b86e17ea-4b2c-4fef-a18e-32a849d103b8`
- Contract version/status: `1` / `approved`
- Question status after execution: `failed`
- Source run cloned: `c9606e4d-adbc-438e-8d43-767d83d3dc96`
- Identity-free contract-content hash: `138f2736258f9319a4581ccd0ce0e70cff4a7ede530c5d466477831c0f11e3d0`
- Requested profile/mode: `graph_live_smoke_v1` / `live`
- Historical cutoff: none; evidence policy was `live_current`

The approved contract was cloned transactionally from the specified prior run without model regeneration.

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
- Run ID: `1490d99f-7bdc-4026-a1a9-e14a8b0cbb0b`
- Job ID: `ef71393b-af80-48e9-b100-41909d28c32b`
- Run-attempt ID: `c4ce4e23-89ba-44d7-bc46-220cb9955884`
- Profile/version: `graph_live_smoke_v1` / `1`
- Mode: `live`
- Status: `failed`
- Failure stage: `forecast_node:1d64a891-044e-4a88-abaa-8f592c40b0ce`
- Failure reason: `budget_exceeded; budget_exceeded`
- Budget stop reason/stage: `max_model_calls` / `forecast_node:1d64a891-044e-4a88-abaa-8f592c40b0ce`
- Started: `2026-08-25T03:14:56.921459Z`
- Finished: `2026-08-25T03:17:45.659527Z`
- Persisted timestamp latency: `168,738 ms`
- External invocation/poll latency: `169,833 ms`
- Stored failed-run latency field: `0 ms`; timestamps and the external log establish actual elapsed time
- External log: `/Users/nico-yardlogix/forecastlab-backups/mode-aware-live-graph-smoke-20260825-031455.log`
- External log SHA-256: `718dd9f880acc630b81d694bf4af28ec5df22a03d63f180ac98b06382ed6e6eb`
- Final run snapshot: `/Users/nico-yardlogix/forecastlab-backups/mode-aware-live-graph-smoke-20260825-031455-final-run.json`
- Final snapshot SHA-256: `56a953e74d95c4c73dd0b2ad6acf6643424f1c6b88642e4e056182da1be0ad14`
- Reduced-confidence forecast: not produced; reliability impact was `forecast_failed`
- Research coverage factor: `0.330434782609`

## Forecast graph and ResearchPlan

- ForecastGraph ID: `ff4290b3-be14-46c6-b0bb-c8d61ac80a31`
- Graph version/status: `1` / `approved`
- Generation provider/model: `openai:gpt-5-mini-2025-08-07`
- Total graph nodes: `8`
- ResearchPlan ID: `63eed1f6-84a3-41dc-8c89-ade319b63c05`
- Selected/skipped nodes: `4 / 4`
- Selected critical nodes: none
- Parallel research workers: `4`

### Selected nodes

| Node ID | Type | Weight | Priority | Question |
| --- | --- | ---: | ---: | --- |
| `e70f22d4-2d2f-4ff7-afd1-3cea49458ba7` | adversarial | 0.20 | 6.10 | Most plausible shock, timing, or measurement pathway producing first-release U-3 at or above 5.0% |
| `34cd4b3d-c785-4b85-8653-a445dbb40822` | resolver | 0.20 | 4.65 | Exact first-release U-3 resolution mechanics, restatements, and resolver risks |
| `50eaef78-60a4-47c9-8a68-1bdc8d7232e9` | base_rate | 0.18 | 4.53 | Fraction of monthly first-release U-3 readings since 1990 at or above 5.0% |
| `1d64a891-044e-4a88-abaa-8f592c40b0ce` | driver | 0.25 | 4.00 | Conditional probability from payroll and household-survey trends |

### Skipped nodes

| Node ID | Type | Weight | Priority | Reason |
| --- | --- | ---: | ---: | --- |
| `2b8c0f9f-195b-4d08-9585-ecf7eb928592` | driver | 0.12 | 2.87 | lower priority or budget limited |
| `7b43cfc4-4175-490d-b968-44397a1abdb0` | driver | 0.05 | 1.80 | lower priority or budget limited |
| `a8b124c0-4f9a-44af-8b11-fe5ecf1a9153` | driver | 0.10 | 3.85 | lower priority or budget limited |
| `b9ddd69d-8cd3-429e-b26b-985887c32882` | dependency | 0.05 | 2.75 | lower priority or budget limited |

### Allocation

The deterministic persistence order was base-rate, resolver, driver, then adversarial. Each selected node was allocated one search, one fetch, three possible model calls, up to 11,096 tokens, and up to five claims.

| Allocation measure | Value |
| --- | ---: |
| Estimated model cost per selected node | `$0.096440` |
| Estimated search cost per selected node | `$0.008000` |
| Estimated total cost per selected node | `$0.104440` |
| Estimated total selected-node cost | `$0.417760` |
| Available cost before research | `$0.436980` |
| Available model calls before research | `13` |
| Available search calls before research | `4` |
| Available fetches before research | `8` |
| Available tokens before research | `45,224` |
| Available wall time before research | `299.980 s` |

## Evidence and temporal-provenance audit

Four Tavily searches succeeded. Four external documents were fetched, content-hashed, accepted, and converted into four EvidenceClaims. No document was rejected. Every claim has a retrieval timestamp, a source-availability timestamp equal to that live retrieval time, `temporal_basis = retrieval_date`, and `cutoff_verified = true` under live eligibility.

All four pages yielded a discovered publication date, so none has `publication_date = null`. This means the live unknown-publication-date behavior was not directly observed. In particular, the parsed `2011-02-03` date for the arXiv page is retained exactly as stored and should not be interpreted by this operational smoke as an independent validation of that metadata's factual accuracy.

### Accepted URLs

| EvidenceItem | Node | URL | Source | HTTP | Publication metadata | Live availability |
| --- | --- | --- | --- | ---: | --- | --- |
| `c498151c-95a9-5d73-8d00-eb070b158785` | base rate | <https://fred.stlouisfed.org/release/tables?eid=4773&rid=50> | primary | 200 | `2026-07-01`, `trafilatura_metadata:date`, verified | `2026-08-25T03:16:12.053443Z`, retrieval basis |
| `b69d4a12-7567-5c1c-b500-ca867d234a4f` | resolver | <https://ca.marketscreener.com/news/latest/BLS-U-S-Bureau-of-Labor-Statistics-State-Employment-and-Unemployment-Monthly-28929547> | secondary | 200 | `2019-07-19T14:29:09Z`, `html_meta:article:published_time`, verified | `2026-08-25T03:16:14.413799Z`, retrieval basis |
| `de6a9db4-ecd9-51ed-9fca-1903034cf14e` | driver | <https://www.bls.gov/news.release/empsit.htm> | primary | 200 | `2026-08-07`, `trafilatura_metadata:date`, verified | `2026-08-25T03:16:12.178629Z`, retrieval basis |
| `14ef2bc5-494e-55c9-a34c-b796fe573abe` | adversarial | <https://arxiv.org/html/2507.15066v5> | secondary | 200 | `2011-02-03`, `trafilatura_metadata:date`, verified as stored | `2026-08-25T03:16:11.578955Z`, retrieval basis |

Rejected URLs: none.

### EvidenceClaims

#### `b9249a16-edcd-5964-8f61-df9ad0fce6a2` — base-rate node

- Claim/excerpt: “Tools and resources to find and use economic data worldwide / U.S.”
- Source: <https://fred.stlouisfed.org/release/tables?eid=4773&rid=50>
- Publication date/source/verified: `2026-07-01T00:00:00Z` / `trafilatura_metadata:date` / `true`
- Retrieval date: `2026-08-25T03:16:12.053443Z`
- Source available at: `2026-08-25T03:16:12.053443Z`
- Temporal basis: `retrieval_date`
- Cutoff verified / as-of eligible: `true` / `true` for live eligibility
- Supports/refutes, confidence, source quality: `supports`, `0.35`, `0.50`

#### `692c8c38-b07e-5e90-b2b4-4d013808a3b8` — resolver node

- Claim/excerpt: a 19 July 2019 state employment and unemployment release header and opening sentence reporting lower or stable state unemployment rates.
- Source: <https://ca.marketscreener.com/news/latest/BLS-U-S-Bureau-of-Labor-Statistics-State-Employment-and-Unemployment-Monthly-28929547>
- Publication date/source/verified: `2019-07-19T14:29:09Z` / `html_meta:article:published_time` / `true`
- Retrieval date: `2026-08-25T03:16:14.413799Z`
- Source available at: `2026-08-25T03:16:14.413799Z`
- Temporal basis: `retrieval_date`
- Cutoff verified / as-of eligible: `true` / `true` for live eligibility
- Supports/refutes, confidence, source quality: `supports`, `0.35`, `0.50`

#### `43c69ef8-7fb6-5ce8-b1d0-70916d9aa299` — driver node

- Claim/excerpt: “An official website of the United States government / Transmission of material in this news release is embargoed until USDL-26-1291 / 8:30 a.m.”
- Source: <https://www.bls.gov/news.release/empsit.htm>
- Publication date/source/verified: `2026-08-07T00:00:00Z` / `trafilatura_metadata:date` / `true`
- Retrieval date: `2026-08-25T03:16:12.178629Z`
- Source available at: `2026-08-25T03:16:12.178629Z`
- Temporal basis: `retrieval_date`
- Cutoff verified / as-of eligible: `true` / `true` for live eligibility
- Supports/refutes, confidence, source quality: `supports`, `0.35`, `0.50`

#### `347f376f-018a-5ed4-b535-94452ad4efb0` — adversarial node

- Claim/excerpt: the title and opening abstract sentence of “Time-RA: Towards Time Series Reasoning for Anomaly Diagnosis with LLM Feedback.”
- Source: <https://arxiv.org/html/2507.15066v5>
- Publication date/source/verified: `2011-02-03T00:00:00Z` / `trafilatura_metadata:date` / `true` as stored
- Retrieval date: `2026-08-25T03:16:11.578955Z`
- Source available at: `2026-08-25T03:16:11.578955Z`
- Temporal basis: `retrieval_date`
- Cutoff verified / as-of eligible: `true` / `true` for live eligibility
- Supports/refutes, confidence, source quality: `supports`, `0.35`, `0.50`

These claims are recorded for operational audit. Their low confidence, generic excerpts, weak node relevance, and the apparently questionable arXiv metadata date are evidence-quality limitations. This smoke does not establish forecast quality or publication-date truth.

## Node forecasting, failures, aggregation, and version

Two node forecasts were persisted before the model-call ceiling was exhausted.

### NodeForecastRun `4a20d382-728e-47e2-9814-19d049623882`

- Node: base rate (`50eaef78-60a4-47c9-8a68-1bdc8d7232e9`)
- Probability: `0.70`
- Confidence / uncertainty: `0.074375` / `0.925625`
- Supporting claim: `b9249a16-edcd-5964-8f61-df9ad0fce6a2`
- Opposing claims: none
- Reasoning: the supplied FRED table reference suggests relevant labor-underutilization data is available, but the claim does not establish first-release rather than revised U-3 values or full coverage from January 1990.
- Model: `openai:gpt-5-mini-2025-08-07`
- Aggregation fields remained zero because aggregation never ran.

### NodeForecastRun `c502c489-ef97-4144-ac85-bd9f7e8f949d`

- Node: resolver (`34cd4b3d-c785-4b85-8653-a445dbb40822`)
- Probability: `0.10`
- Confidence / uncertainty: `0.074375` / `0.925625`
- Supporting claim: `692c8c38-b07e-5e90-b2b4-4d013808a3b8`
- Opposing claims: none
- Reasoning: the supplied release excerpt shows that BLS publishes unemployment releases, but it does not establish first-release U-3 definitions, revision procedures, or the contract's enumerated resolver risks.
- Model: `openai:gpt-5-mini-2025-08-07`
- Aggregation fields remained zero because aggregation never ran.

### Persisted failures

- Failed critical nodes: none.
- Failed noncritical nodes: driver `1d64a891-044e-4a88-abaa-8f592c40b0ce` and adversarial `e70f22d4-2d2f-4ff7-afd1-3cea49458ba7`.
- Both failure records report `budget_exceeded`; the stopping stage was the driver node forecast and the stop reason was `max_model_calls`.
- Minimum successful nodes required: `3`; successful node forecasts: `2`.
- ForecastAggregation ID: none.
- Aggregation method, normalized weights, log-odds contributions, and calculation trace: none.
- ForecastVersion ID: none.
- Final probability: none.

## Complete sanitized provider ledger

All 18 physical-request rows are terminal and succeeded. Every physical-attempt number is `1`; there were no provider-level retries or failed-attempt costs. Costs use the committed estimator because direct provider billing data was not returned.

| # | Ledger ID | Logical call | Attempt | Stage | Provider/model | Started UTC | Completed UTC | Status | Reserved in/out | Actual in/out | Reserved cost | Actual estimated cost | Provider request ID | Error |
| ---: | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| 1 | `5e657396-55c7-45df-b97b-55fb68974328` | `70ff2684-4508-4799-8d69-9f5d8d74a9a8` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:14:56.980254 | 03:15:55.165630 | succeeded | 0 / 4,096 | 862 / 3,914 | `$0.061440` | `$0.063020` | `req_59d9c6ac3d5d44cfa1581ae114f8a25a` | none |
| 2 | `a6a4ea3e-fb6f-4546-9b23-5780a803f700` | `a49a62b8-4f2a-4e25-8d60-b5bce66d1116` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:15:55.187698 | 03:16:09.720109 | succeeded | 457 / 1,024 | 392 / 1,024 | `$0.017645` | `$0.017320` | `req_bec139803ab24db8a0f8698681e100fe` | none |
| 3 | `05212fe9-2dd6-472b-b2c5-d7bb92d0e025` | `7e19a515-d7e6-442c-82aa-abad2bd36372` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:15:55.187886 | 03:16:09.713134 | succeeded | 410 / 1,024 | 374 / 1,024 | `$0.017410` | `$0.017230` | `req_801d43d4831841369cb22532d1715030` | none |
| 4 | `747ec1ca-41fe-45c7-b244-81a2cb01e5ae` | `f21dbd3c-4ade-4ed4-9a92-beb62e6ed12a` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:15:55.191138 | 03:16:10.146789 | succeeded | 368 / 1,024 | 333 / 1,024 | `$0.017200` | `$0.017025` | `req_41f67001a9604bafb12d985f13ea8eb5` | none |
| 5 | `e2836bad-d765-4723-94ca-ae6a453746ab` | `2588f5dd-8621-4969-bbea-1d89b53c2938` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:15:55.191613 | 03:16:09.735184 | succeeded | 428 / 1,024 | 410 / 1,024 | `$0.017500` | `$0.017410` | `req_cee3af5180d44964b02fd47eddcb6f41` | none |
| 6 | `c9fcf1ea-78f8-45a4-b225-5158d4ffebe5` | `858231c0-dce0-421e-a34e-c4dd53a94c49` | 1 | search | tavily / tavily-search | 03:16:09.717236 | 03:16:12.175138 | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| 7 | `e8dd0483-33ca-4292-9677-a85a05817f58` | `525e99fc-7a8a-4bf0-9507-4c8aefd5c4e0` | 1 | search | tavily / tavily-search | 03:16:09.723177 | 03:16:14.408241 | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| 8 | `bcf08998-ba51-42bc-9b44-0b42f2eb4ed2` | `c12d06d5-e660-4209-8d44-07e09d521153` | 1 | search | tavily / tavily-search | 03:16:09.738017 | 03:16:11.574572 | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| 9 | `5601cd94-9b50-4fe8-bab5-946aa78fb040` | `f17b86f7-0e35-41b6-9771-b460f78e0afb` | 1 | search | tavily / tavily-search | 03:16:10.149676 | 03:16:12.051571 | succeeded | 0 / 0 | 0 / 0 | `$0.008000` | `$0.008000` | none | none |
| 10 | `ebae4adc-4e2d-45f0-a500-dddaaebfd92e` | `8316de40-ee33-435d-8232-37a1389bd72f` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:15.554954 | 03:16:19.436798 | succeeded | 573 / 1,536 | 566 / 208 | `$0.025905` | `$0.005950` | `req_a7212bee52584640a1c05dd6e28e07f3` | none |
| 11 | `c9fe130c-049c-4cbd-b0fd-c04d462b6e1c` | `afd18ca7-6bc5-4bf7-a2e4-2fe2c862daea` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:15.923791 | 03:16:34.164758 | succeeded | 2,639 / 1,536 | 2,544 / 1,536 | `$0.036235` | `$0.035760` | `req_c18c07d1982f40fc905677c39b554339` | none |
| 12 | `6e512125-a4a6-44e7-b05e-b66ef28c662d` | `bc22fd08-1ccd-4323-b583-57f092464104` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:18.332875 | 03:16:21.522350 | succeeded | 2,557 / 1,536 | 2,136 / 208 | `$0.035825` | `$0.013800` | `req_f81cffbba4e84a0fac1c82808b3a1b8d` | none |
| 13 | `d969729e-b5df-44a1-af52-a586c9e9e74a` | `a1a213de-2d80-453b-ba99-f0bc4805660e` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:18.530480 | 03:16:36.724191 | succeeded | 2,537 / 1,536 | 2,397 / 1,536 | `$0.035725` | `$0.035025` | `req_e96aa850668f4c66bf9651c7c6adfae9` | none |
| 14 | `3f4956c5-0873-478d-9714-cec4e30750a7` | `7598b397-ad04-4d24-9dcd-2d7d96bd770f` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:21.529362 | 03:16:24.754931 | succeeded | 1,545 / 1,536 | 1,333 / 206 | `$0.030765` | `$0.009755` | `req_12575033140c4da7bf8f01bad05f7c16` | none |
| 15 | `5461469d-df1e-46e3-9e6b-1aa416928594` | `bb080ff0-86cb-4d9f-a9b5-6f207a6468ee` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:34.170822 | 03:17:02.989235 | succeeded | 1,624 / 1,536 | 1,611 / 1,536 | `$0.031160` | `$0.031095` | `req_9fe04caa53cf4073a9f996940efca29e` | none |
| 16 | `5df2680d-09c5-4196-8c26-46bd992a55e3` | `8d0bd648-d90e-4377-8976-31bc471c1e70` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:16:36.727554 | 03:17:06.417592 | succeeded | 1,525 / 1,536 | 1,505 / 1,536 | `$0.030665` | `$0.030565` | `req_9d7645813e4644ac9c9e2311e1b3ffdf` | none |
| 17 | `8375f454-dce4-4ba3-b6d8-841d1d4b75d6` | `6b9193a1-a952-43e2-8616-2ed3c43ba494` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:17:06.457445 | 03:17:20.112323 | succeeded | 1,019 / 1,536 | 1,127 / 1,134 | `$0.028135` | `$0.022645` | `req_0e38fee34d464f058844092369a33c89` | none |
| 18 | `7743049e-839f-4449-8932-3b5132f211b4` | `2fa7f3af-4de5-4531-90c9-963e9b3de30a` | 1 | model | openai / gpt-5-mini-2025-08-07 | 03:17:20.130893 | 03:17:45.632165 | succeeded | 1,337 / 1,536 | 1,568 / 1,204 | `$0.029725` | `$0.025900` | `req_a132c31e6d744770a537beaf9b3255be` | none |

All rows belong to run-attempt `c4ce4e23-89ba-44d7-bc46-220cb9955884`; every error category and error message is null, and every cost source is `estimated`.

## Usage and cost

| Measure | Actual | Profile ceiling |
| --- | ---: | ---: |
| Model calls | `14` | `14` |
| Search calls | `4` | `4` |
| Fetches | `4` | `8` |
| Prompt tokens | `17,158` | — |
| Completion tokens | `17,114` | — |
| Total tokens | `34,272` | `50,000` |
| Model cost | `$0.342500` | — |
| Search cost | `$0.032000` | — |
| Failed-attempt cost | `$0.000000` | — |
| Total cost | `$0.374500` | `$0.500000` |
| Remaining cost ceiling | `$0.125500` | — |
| Persisted run time | `168.738 s` | `300 s` |

## Twenty-seven-condition gate

| # | Condition | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Exactly one new Question exists | PASS | `217 - 216 = 1` |
| 2 | Exactly one new ForecastRun exists | PASS | `221 - 220 = 1` |
| 3 | Profile is `graph_live_smoke_v1` version 1 | PASS | persisted execution context |
| 4 | Mode is live | PASS | persisted run and execution context |
| 5 | No mock or fixture provider is used | PASS | model/search mock flags false; fixture and synthetic flags false |
| 6 | A ResearchPlan exists | PASS | `63eed1f6-84a3-41dc-8c89-ade319b63c05` |
| 7 | Between two and four nodes are selected | PASS | `4` |
| 8 | At least one Tavily request succeeds | PASS | `4` succeeded |
| 9 | At least one external document is accepted | PASS | `4` accepted EvidenceItems |
| 10 | At least two EvidenceClaims exist | PASS | `4` |
| 11 | Every claim has source availability, temporal basis, and cutoff verification | PASS | all `4/4` have all three fields |
| 12 | Any unknown-date claim preserves null/unverified/retrieval semantics | PASS (conditional) | no claim had an unknown date; therefore no violating claim exists, but the target case was not directly exercised |
| 13 | No retrieval-basis claim is represented as historically verified evidence | PASS | mode `live`, policy `live_current`, `as_of = null`; all claims explicitly retain `retrieval_date` basis |
| 14 | At least two NodeForecastRuns exist | PASS | exactly `2` |
| 15 | No selected critical node fails | PASS | no selected node was classified critical; critical failures `0` |
| 16 | A ForecastAggregation exists | **FAIL** | `0`; executor stopped at model-call ceiling |
| 17 | A ForecastVersion exists | **FAIL** | `0` |
| 18 | Final probability is finite and between 0.01 and 0.99 | **FAIL** | no final probability |
| 19 | At least one OpenAI request succeeds | PASS | `14` succeeded |
| 20 | At least one Tavily request succeeds | PASS | `4` succeeded |
| 21 | Every ledger row is terminal | PASS | `18/18` succeeded and terminal |
| 22 | Provider identities are OpenAI and Tavily | PASS | persisted context and ledger |
| 23 | Calls, fetches, and tokens remain within profile limits | PASS | model `14/14`, search `4/4`, fetch `4/8`, tokens `34,272/50,000` |
| 24 | Total cost is at or below $0.50 | PASS | `$0.374500 <= $0.500000` |
| 25 | No credential appears in output, ledger, claims, or errors | PASS | zero secret-pattern matches across external logs, final snapshot, runtime logs, and run-scoped database surfaces |
| 26 | Historical cutoff logic was not invoked or bypassed | PASS | live mode, no `as_of`, `live_current` policy, no validation adapter |
| 27 | Git worktree remains clean | PASS | clean during execution and after committing the two authorized result files |

Overall: **FAIL**, because conditions 16, 17, and 18 failed. In addition, although condition 12 is conditionally satisfied, the run selected no unknown-publication-date document and therefore did not directly demonstrate that target live-provider case.

## Integrity confirmations

- Exactly one live graph-smoke invocation occurred; it was not rerun.
- No separate OpenAI or Tavily probe occurred.
- No mock provider, fixture evidence, synthetic adapter, or cutoff-consistent validation adapter was used.
- No historical cutoff path was invoked or bypassed.
- No credential was printed, rotated, copied, or changed.
- Only the maximum-cost setting changed temporarily, from `$0.25` to `$0.50` and back to `$0.25`.
- No source code, prompt, profile, graph, Research Planner, evidence, node-forecasting, aggregation, benchmark, or cutoff behavior changed.
- No benchmark, five-question acceptance test, merge, or deployment occurred.
- The failure was preserved without a second invocation.

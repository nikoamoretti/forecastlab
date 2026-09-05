# Cutoff-Consistent Mock Validation Report

> Cutoff-consistent synthetic workflow validation. This result tests execution reliability only and is not evidence of forecasting quality.

## Result

- Overall gate: **PASS**
- Validation ID: `83f84eec-b516-4638-8fbd-00857a422f2f`
- Code commit frozen before execution: `bac73ca79c8db7eba442470fbae9de6db6317b22`
- Graph profile: `graph_forecaster_v1` version 5
- Corpus: `forecastlab.graph_validation.cutoff_consistent` version 1
- Corpus hash: `b145d971c23082be67cfe27b37f1182258739b26f540068b42206b71f8fe11b3`
- Execution providers: MockModelProvider and cutoff-consistent mock search/fetch adapter
- Shadow pricing identity: `openai` / `gpt-5-mini-2025-08-07` / `tavily`
- Live provider calls authorized: no
- Automatic rerun: no
- Accuracy metrics: not computed

## Exact reused questions and corpus

| # | Evaluation question ID | Domain | Forecast cutoff | Eligible / excluded docs | Fixture document IDs |
| ---: | --- | --- | --- | ---: | --- |
| 1 | `94f806bf-fc0b-41da-84bd-f9ce9792b8e2` | economics | 2024-04-05T00:00:00+00:00 | 3 / 0 | `unemployment-leading-indicators-2024-04`, `unemployment-prior-trend-2024-03`, `unemployment-reference-class-2023` |
| 2 | `67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56` | economics | 2024-07-10T00:00:00+00:00 | 3 / 0 | `cpi-prior-trend-2024-06`, `cpi-reference-class-2024-02`, `cpi-wages-shelter-2024-06` |
| 3 | `dfda5684-99a3-492b-89d3-316be4bedc23` | business | 2024-02-02T00:00:00+00:00 | 3 / 0 | `apple-pre-cutoff-guidance-2024-02`, `apple-q1-prior-revenue-2024-02`, `apple-q2-seasonality-2023` |
| 4 | `e93a736f-df6d-4fbf-a833-8afa2c557aa6` | technology | 2024-03-15T00:00:00+00:00 | 3 / 0 | `apple-developer-strategy-2024-02`, `apple-ml-activity-2024-01`, `apple-wwdc-reference-class-2023` |
| 5 | `292e0bf3-3b58-43bb-b877-61412481e2d1` | regulation | 2024-01-05T00:00:00+00:00 | 3 / 0 | `sec-proposed-climate-rule-2022`, `sec-public-statements-2023`, `sec-rulemaking-reference-class-2023` |

## Previous versus current

| Measure | Validation A | Validation B | Current corrected validation |
| --- | ---: | ---: | ---: |
| Completion | 0/5 | 1/5 | 5/5 |
| Evidence Claims | 0 | 7 | 20 |
| NodeForecastRuns | 0 | 7 | 20 |
| ForecastAggregations | 0 | 1 | 5 |
| ForecastVersions | 0 | 1 | 5 |
| Cutoff rejections | n/a | 28 | 0 |
| Critical failures | n/a | 12 | 0 |
| Actual/mock total cost | $1.029790 | $0.000000 | $0.000000 |
| Mean latency | 228884.8 ms | 307.8 ms | 153.8 ms |

Validation A stopped with budget exhaustion before claims or node forecasts. Validation B used zero-cost generic mock evidence, completed one run, and recorded 28 cutoff rejections. The current result changes only the frozen validation corpus and validation-only execution boundary.

## Current totals

- Eligible / excluded corpus documents: 15 / 0
- Graph nodes selected / skipped: 20 / 15
- Evidence Claims: 20
- NodeForecastRuns: 20
- ForecastAggregations: 5
- ForecastVersions: 5
- Cutoff rejections: 0
- Critical failures: 0
- Queries / fetched documents: 20 / 20
- Extraction fallbacks / failures: 0 / 0
- Mock model / search calls: 40 / 20
- Shadow estimated model / search / total cost: $1.074400 / $0.160000 / $1.234400
- Actual mock model / search / total cost: $0.000000 / $0.000000 / $0.000000
- Total / mean latency: 769.0 ms / 153.8 ms
- Retries: 0

## Per-question execution

| # | Domain | Status | Selected / skipped | Shadow model | Shadow search | Shadow total | Actual mock total | Claims | Node runs | Aggregation | Version | Cutoff rejects | Critical failures | Latency |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |
| 1 | economics | completed | 4 / 3 | $0.214880 | $0.032000 | $0.246880 | $0.000000 | 4 | 4 | yes | yes | 0 | 0 | 202.0 ms |
| 2 | economics | completed | 4 / 3 | $0.214880 | $0.032000 | $0.246880 | $0.000000 | 4 | 4 | yes | yes | 0 | 0 | 125.0 ms |
| 3 | business | completed | 4 / 3 | $0.214880 | $0.032000 | $0.246880 | $0.000000 | 4 | 4 | yes | yes | 0 | 0 | 151.0 ms |
| 4 | technology | completed | 4 / 3 | $0.214880 | $0.032000 | $0.246880 | $0.000000 | 4 | 4 | yes | yes | 0 | 0 | 142.0 ms |
| 5 | regulation | completed | 4 / 3 | $0.214880 | $0.032000 | $0.246880 | $0.000000 | 4 | 4 | yes | yes | 0 | 0 | 149.0 ms |

## Detailed node and corpus audit

### 1. Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?

- Evaluation question ID: `94f806bf-fc0b-41da-84bd-f9ce9792b8e2`
- Forecast run ID: `747babe2-08fa-4ebc-960b-0f21af5626c6`
- Eligible / excluded corpus documents: 3 / 0
- Selected / skipped nodes: 4 / 3
- Worker concurrency: 4
- Shadow estimated model / search / total: $0.214880 / $0.032000 / $0.246880
- Actual mock model / search / total: $0.000000 / $0.000000 / $0.000000
- Claims / node runs: 4 / 4
- Final probability: 0.6
- Reduced confidence: no
- Retries: 0

| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | base_rate | yes | yes | 1.250000 | 1 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | trend | yes | yes | 4.500000 | 1 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | driver | yes | yes | 4.550000 | 1 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | no | 4.350000 | 0 | 0 | no | none |
| Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | scenario | no | yes | 4.700000 | 1 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | adversarial | no | no | 3.650000 | 0 | 0 | no | none |
| What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? | resolver | no | no | 1.050000 | 0 | 0 | no | none |

Corpus query rankings:

- Query: How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Ranking: unemployment-prior-trend-2024-03=8.850, unemployment-reference-class-2023=8.100, unemployment-leading-indicators-2024-04=3.050
- Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Ranking: unemployment-prior-trend-2024-03=8.850, unemployment-reference-class-2023=5.650, unemployment-leading-indicators-2024-04=3.050
- Query: What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Ranking: unemployment-prior-trend-2024-03=9.050, unemployment-leading-indicators-2024-04=3.350, unemployment-reference-class-2023=2.950
- Query: Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Ranking: unemployment-prior-trend-2024-03=8.850, unemployment-leading-indicators-2024-04=3.300, unemployment-reference-class-2023=2.950

### 2. Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?

- Evaluation question ID: `67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56`
- Forecast run ID: `a5826f13-6d39-40c6-afee-c3a55b4c8231`
- Eligible / excluded corpus documents: 3 / 0
- Selected / skipped nodes: 4 / 3
- Worker concurrency: 4
- Shadow estimated model / search / total: $0.214880 / $0.032000 / $0.246880
- Actual mock model / search / total: $0.000000 / $0.000000 / $0.000000
- Claims / node runs: 4 / 4
- Final probability: 0.6
- Reduced confidence: no
- Retries: 0

| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | base_rate | yes | yes | 1.250000 | 1 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | trend | yes | yes | 4.500000 | 1 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | driver | yes | yes | 4.550000 | 1 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | no | 4.350000 | 0 | 0 | no | none |
| Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | scenario | no | yes | 4.700000 | 1 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | adversarial | no | no | 3.650000 | 0 | 0 | no | none |
| What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? | resolver | no | no | 1.050000 | 0 | 0 | no | none |

Corpus query rankings:

- Query: How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Ranking: cpi-prior-trend-2024-06=12.000, cpi-reference-class-2024-02=7.850, cpi-wages-shelter-2024-06=5.600
- Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Ranking: cpi-prior-trend-2024-06=12.000, cpi-reference-class-2024-02=5.600, cpi-wages-shelter-2024-06=5.600
- Query: What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Ranking: cpi-prior-trend-2024-06=12.200, cpi-wages-shelter-2024-06=6.100, cpi-reference-class-2024-02=5.100
- Query: Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Ranking: cpi-prior-trend-2024-06=12.000, cpi-reference-class-2024-02=5.600, cpi-wages-shelter-2024-06=5.600

### 3. Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?

- Evaluation question ID: `dfda5684-99a3-492b-89d3-316be4bedc23`
- Forecast run ID: `5bf827fd-4ed8-425d-ba43-e31a13f3b7aa`
- Eligible / excluded corpus documents: 3 / 0
- Selected / skipped nodes: 4 / 3
- Worker concurrency: 4
- Shadow estimated model / search / total: $0.214880 / $0.032000 / $0.246880
- Actual mock model / search / total: $0.000000 / $0.000000 / $0.000000
- Claims / node runs: 4 / 4
- Final probability: 0.6
- Reduced confidence: no
- Retries: 0

| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | base_rate | yes | yes | 1.250000 | 1 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | trend | yes | yes | 4.500000 | 1 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | driver | yes | yes | 4.550000 | 1 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | no | 4.350000 | 0 | 0 | no | none |
| Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | scenario | no | yes | 4.700000 | 1 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | adversarial | no | no | 3.650000 | 0 | 0 | no | none |
| What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? | resolver | no | no | 1.050000 | 0 | 0 | no | none |

Corpus query rankings:

- Query: How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Ranking: apple-q2-seasonality-2023=11.500, apple-pre-cutoff-guidance-2024-02=8.800, apple-q1-prior-revenue-2024-02=8.800
- Query: What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Ranking: apple-q2-seasonality-2023=9.250, apple-pre-cutoff-guidance-2024-02=9.000, apple-q1-prior-revenue-2024-02=9.000
- Query: What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Ranking: apple-q1-prior-revenue-2024-02=9.000, apple-pre-cutoff-guidance-2024-02=8.800, apple-q2-seasonality-2023=8.550
- Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Ranking: apple-pre-cutoff-guidance-2024-02=8.800, apple-q1-prior-revenue-2024-02=8.800, apple-q2-seasonality-2023=8.550

### 4. Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?

- Evaluation question ID: `e93a736f-df6d-4fbf-a833-8afa2c557aa6`
- Forecast run ID: `3ea7a9ad-518c-4ba5-bb9c-d1ca66ed483a`
- Eligible / excluded corpus documents: 3 / 0
- Selected / skipped nodes: 4 / 3
- Worker concurrency: 4
- Shadow estimated model / search / total: $0.214880 / $0.032000 / $0.246880
- Actual mock model / search / total: $0.000000 / $0.000000 / $0.000000
- Claims / node runs: 4 / 4
- Final probability: 0.6
- Reduced confidence: no
- Retries: 0

| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | base_rate | yes | yes | 1.250000 | 1 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | trend | yes | yes | 4.500000 | 1 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | driver | yes | yes | 4.550000 | 1 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | no | 4.350000 | 0 | 0 | no | none |
| Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | scenario | no | yes | 4.700000 | 1 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | adversarial | no | no | 3.650000 | 0 | 0 | no | none |
| What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? | resolver | no | no | 1.050000 | 0 | 0 | no | none |

Corpus query rankings:

- Query: How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Ranking: apple-wwdc-reference-class-2023=3.400, apple-developer-strategy-2024-02=2.950, apple-ml-activity-2024-01=2.950
- Query: What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Ranking: apple-wwdc-reference-class-2023=3.350, apple-developer-strategy-2024-02=3.150, apple-ml-activity-2024-01=3.150
- Query: What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Ranking: apple-developer-strategy-2024-02=2.950, apple-ml-activity-2024-01=2.950, apple-wwdc-reference-class-2023=2.650
- Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Ranking: apple-developer-strategy-2024-02=2.950, apple-ml-activity-2024-01=2.950, apple-wwdc-reference-class-2023=2.900

### 5. Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?

- Evaluation question ID: `292e0bf3-3b58-43bb-b877-61412481e2d1`
- Forecast run ID: `c10c8119-6327-4e6b-afe6-8047c998fc0d`
- Eligible / excluded corpus documents: 3 / 0
- Selected / skipped nodes: 4 / 3
- Worker concurrency: 4
- Shadow estimated model / search / total: $0.214880 / $0.032000 / $0.246880
- Actual mock model / search / total: $0.000000 / $0.000000 / $0.000000
- Claims / node runs: 4 / 4
- Final probability: 0.6
- Reduced confidence: no
- Retries: 0

| Node | Type | Critical | Selected | Priority | Searches | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | base_rate | yes | yes | 1.250000 | 1 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | trend | yes | yes | 4.500000 | 1 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | driver | yes | yes | 4.550000 | 1 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | no | 4.350000 | 0 | 0 | no | none |
| Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | scenario | no | yes | 4.700000 | 1 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | adversarial | no | no | 3.650000 | 0 | 0 | no | none |
| What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? | resolver | no | no | 1.050000 | 0 | 0 | no | none |

Corpus query rankings:

- Query: How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Ranking: sec-proposed-climate-rule-2022=11.800, sec-rulemaking-reference-class-2023=9.300, sec-public-statements-2023=8.550
- Query: What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Ranking: sec-proposed-climate-rule-2022=12.000, sec-rulemaking-reference-class-2023=9.250, sec-public-statements-2023=8.750
- Query: What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Ranking: sec-proposed-climate-rule-2022=12.000, sec-public-statements-2023=8.750, sec-rulemaking-reference-class-2023=8.750
- Query: Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Ranking: sec-proposed-climate-rule-2022=11.800, sec-public-statements-2023=8.550, sec-rulemaking-reference-class-2023=8.550

## Fourteen-condition pass/fail gate

| # | Condition | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Five of five runs produce a ForecastVersion | PASS | 5/5 |
| 2 | Five of five runs produce a ForecastAggregation | PASS | 5/5 |
| 3 | Every run produces at least one NodeForecastRun | PASS | 4, 4, 4, 4, 4 |
| 4 | Every run produces at least one accepted EvidenceClaim | PASS | 4, 4, 4, 4, 4 |
| 5 | Zero runs fail due to cutoff_rejection | PASS | 0 |
| 6 | Zero critical selected nodes fail | PASS | 0 |
| 7 | Every shadow research plan fits within $0.25 | PASS | $0.246880, $0.246880, $0.246880, $0.246880, $0.246880 |
| 8 | Every run respects committed model, search, fetch, and token limits | PASS | within, within, within, within, within |
| 9 | Actual provider cost remains exactly $0 | PASS | model $0.000000; search $0.000000; total $0.000000 |
| 10 | No live OpenAI or Tavily request occurs | PASS | live ledger rows: 0 |
| 11 | The corpus hash is frozen and recorded | PASS | b145d971c23082be67cfe27b37f1182258739b26f540068b42206b71f8fe11b3 |
| 12 | No production cutoff rule was weakened | PASS | {"eligible_fixture_accepted": true, "missing_snapshot_reason": "no_eligible_historical_snapshot", "passed": true, "pre_cutoff_fixture_reason": "published_after_as_of"} |
| 13 | No graph, planner, prompt, node-forecasting, or aggregation behavior was tuned | PASS | none |
| 14 | The exact five questions were executed once | PASS | 94f806bf-fc0b-41da-84bd-f9ce9792b8e2, 67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56, dfda5684-99a3-492b-89d3-316be4bedc23, e93a736f-df6d-4fbf-a833-8afa2c557aa6, 292e0bf3-3b58-43bb-b877-61412481e2d1 |

## Integrity confirmations

- Production historical cutoff rules changed: no.
- Wayback verification rules changed: no.
- Forecast Contracts, questions, outcomes, graph generation, weights, planner ranking, limits, prompts, extractor, node forecaster, aggregation, profiles, benchmark methodology, scoring, or calibration changed: no.
- Live OpenAI or Tavily calls: none.
- API keys loaded by the validation: none.
- Full pilot benchmark executed: no.
- Accuracy evaluated: no.
- Automatic rerun: no.

## Limitations

- The corpus is synthetic and outcome-blind; it validates workflow mechanics, not retrieval quality or forecast accuracy.
- Shadow pricing estimates workload selection against a frozen target identity, but execution uses zero-cost mocks.
- Five questions cover a narrow execution surface and cannot support comparative forecasting claims.
- Query relevance uses deterministic lexical matching and is not a production retrieval system.

Cutoff-consistent synthetic workflow validation. This result tests execution reliability only and is not evidence of forecasting quality.

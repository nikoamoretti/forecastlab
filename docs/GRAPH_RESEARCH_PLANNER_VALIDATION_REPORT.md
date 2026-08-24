# Graph Research Planner Validation Report

## Result

- Overall gate: **FAIL**
- Validation ID: `b0159d60-4b86-4638-8fe7-337a7d1ab300`
- Planner source commit: `83f1ff8fbcebbf90ed631e0823ece154bd651df2`
- Validation harness commit: `1cf854c367af0d505bc1a9344d9f9eb6393519b0`
- Graph profile: `graph_forecaster_v1` version 5
- Dataset hash: `c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888`
- Providers: built-in mock model and built-in mock search only
- Live OpenAI/Tavily authorization: none
- Whole-run retries: disabled

This is an execution-reliability validation. The first execution result below is retained without tuning or rerun.

## Exact reused questions

| Order | Evaluation question ID | Domain | Forecast cutoff | Question |
| ---: | --- | --- | --- | --- |
| 1 | `94f806bf-fc0b-41da-84bd-f9ce9792b8e2` | economics | 2024-04-05T00:00:00+00:00 | Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? |
| 2 | `67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56` | economics | 2024-07-10T00:00:00+00:00 | Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? |
| 3 | `dfda5684-99a3-492b-89d3-316be4bedc23` | business | 2024-02-02T00:00:00+00:00 | Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? |
| 4 | `e93a736f-df6d-4fbf-a833-8afa2c557aa6` | technology | 2024-03-15T00:00:00+00:00 | Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? |
| 5 | `292e0bf3-3b58-43bb-b877-61412481e2d1` | regulation | 2024-01-05T00:00:00+00:00 | Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? |

## Previous versus current

| Measure | Previous validation | Current validation |
| --- | ---: | ---: |
| Forecasts completed | 0/5 | 1/5 |
| Successful nodes | 0 | 7 |
| Failed nodes | 36 | 28 |
| Budget-exceeded runs | 5 | 0 |
| Extraction failures | 6 | 0 |
| Nodes skipped after budget stop | 25 | 0 |
| Evidence Claims | 0 | 7 |
| NodeForecastRuns | 0 | 7 |
| ForecastAggregations | 0 | 1 |
| ForecastVersions | 0 | 1 |
| Total cost | $1.029790 | $0.000000 |
| Mean latency | 228884.8 ms | 307.8 ms |

## Current totals

- All graph nodes: 35
- Selected nodes: 35
- Skipped nodes: 0
- Critical-node failures: 12
- Queries attempted: 140
- URLs discovered: 10
- Documents fetched: 35
- Cutoff rejections: 28
- Extraction attempts: 0
- Smaller-chunk retries: 0
- Document-level fallbacks: 0
- Extraction failures: 0
- Actual mock model calls: 42
- Actual mock search calls: 140
- Provider ledger rows: 182
- Live provider calls: 0
- Estimated pre-execution cost: $0.000000
- Actual cost: $0.000000
- Mean latency: 307.8 ms

## Question-level result

| # | Domain | Status | Selected / skipped | Critical failures | Claims | Node runs | Aggregation | Version | Est. cost | Actual cost | Latency |
| ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |
| 1 | economics | failed | 7 / 0 | 3 | 0 | 0 | no | no | $0.000000 | $0.000000 | 358.0 ms |
| 2 | economics | completed | 7 / 0 | 0 | 7 | 7 | yes | yes | $0.000000 | $0.000000 | 323.0 ms |
| 3 | business | failed | 7 / 0 | 3 | 0 | 0 | no | no | $0.000000 | $0.000000 | 296.0 ms |
| 4 | technology | failed | 7 / 0 | 3 | 0 | 0 | no | no | $0.000000 | $0.000000 | 299.0 ms |
| 5 | regulation | failed | 7 / 0 | 3 | 0 | 0 | no | no | $0.000000 | $0.000000 | 263.0 ms |

## Detailed execution audit

### 1. Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?

- Evaluation question ID: `94f806bf-fc0b-41da-84bd-f9ce9792b8e2`
- Forecast run ID: `abed5fbe-c786-4af5-b493-9a25e34eb4e1`
- Selected / skipped nodes: 7 / 0
- Allocated searches: 35
- Estimated pre-execution cost: $0.000000
- Selected worker concurrency: 4
- Claims / node runs: 0 / 0
- Final probability: not produced
- Reduced confidence: no
- Actual model / search calls: 7 / 28
- Actual cost / remaining budget: $0.000000 / $0.250000
- Budget-exceeded events: 0
- Whole-run / physical retries: 0 / 0

| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | base_rate | yes | yes | 1.250000 | 0.051975 | 5 | 0 | no | cutoff_rejection |
| What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | trend | yes | yes | 4.500000 | 0.187110 | 5 | 0 | no | cutoff_rejection |
| How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | driver | yes | yes | 4.550000 | 0.189189 | 5 | 0 | no | cutoff_rejection |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | yes | 4.350000 | 0.180873 | 5 | 0 | no | cutoff_rejection |
| Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | scenario | no | yes | 4.700000 | 0.195426 | 5 | 0 | no | cutoff_rejection |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | adversarial | no | yes | 3.650000 | 0.151767 | 5 | 0 | no | cutoff_rejection |
| What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? | resolver | no | yes | 1.050000 | 0.043659 | 5 | 0 | no | cutoff_rejection |

Queries and discovered URLs:

- `d4844688-6b16-465b-b1b4-6f8614bb8f17` What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official historical series published on or before 2024-04-05
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? peer-reviewed reference-class studies published on or before 2024-04-05
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `c4b0479a-cdc5-4144-820d-619f1a3a205a` What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? current official statistics published on or before 2024-04-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? primary institutional releases published on or before 2024-04-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `c2000f1d-6f3d-41fd-ac78-671b9e2b50f1` What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome?
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? published on or before 2024-04-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? https://www.bls.gov/news.release/archives/empsit_07052024.htm published on or before 2024-04-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? official dated evidence published on or before 2024-04-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/empsit_07052024.htm could change the scored outcome? official source official resolution rule published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `5d692a70-a72c-4ec3-9de1-0b2004067303` How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?
  - Query: How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Query: How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? primary records published on or before 2024-04-05
  - Query: How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? domain-specific empirical research published on or before 2024-04-05
  - Query: How could historical base rate change the likelihood of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `cd4b916a-66bd-4114-8585-837d1386b299` How does shared upstream assumptions constrain or mediate the primary driver?
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? published on or before 2024-04-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official methodology published on or before 2024-04-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? primary dependency indicators published on or before 2024-04-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `d2dc2c39-a82d-4496-bcba-58bc243019f0` Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? scenario analyses published on or before 2024-04-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? leading indicators published on or before 2024-04-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `e9fa3f51-258c-41ab-8a64-798e98f3f52f` What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%?
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? published on or before 2024-04-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? contradictory primary evidence published on or before 2024-04-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? methodological critiques published on or before 2024-04-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? official dated evidence published on or before 2024-04-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0

Failure categories: `cutoff_rejection` (7)

### 2. Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?

- Evaluation question ID: `67ca0f85-ee8f-4371-b5b3-0b9cc2f9bd56`
- Forecast run ID: `5483a5bf-8829-4f47-a698-6eaa8dadabba`
- Selected / skipped nodes: 7 / 0
- Allocated searches: 35
- Estimated pre-execution cost: $0.000000
- Selected worker concurrency: 4
- Claims / node runs: 7 / 7
- Final probability: 0.6
- Reduced confidence: no
- Actual model / search calls: 14 / 28
- Actual cost / remaining budget: $0.000000 / $0.250000
- Budget-exceeded events: 0
- Whole-run / physical retries: 0 / 0

| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | base_rate | yes | yes | 1.250000 | 0.051975 | 5 | 1 | yes | none |
| What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | trend | yes | yes | 4.500000 | 0.187110 | 5 | 1 | yes | none |
| How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | driver | yes | yes | 4.550000 | 0.189189 | 5 | 1 | yes | none |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | yes | 4.350000 | 0.180873 | 5 | 1 | yes | none |
| Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | scenario | no | yes | 4.700000 | 0.195426 | 5 | 1 | yes | none |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | adversarial | no | yes | 3.650000 | 0.151767 | 5 | 1 | yes | none |
| What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? | resolver | no | yes | 1.050000 | 0.043659 | 5 | 1 | yes | none |

Queries and discovered URLs:

- `1d6b23e7-43f0-46c8-8d59-bddbd5ccd8d3` What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official historical series published on or before 2024-07-10
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? peer-reviewed reference-class studies published on or before 2024-07-10
  - Query: What base rate does Previously resolved economics questions with comparable conditions. imply for: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `72adb512-10ec-4405-97e3-d5bd6c1d9796` What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? current official statistics published on or before 2024-07-10
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? primary institutional releases published on or before 2024-07-10
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `01f81c37-c2d7-426b-b0a1-74223e139c60` What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome?
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? published on or before 2024-07-10
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? https://www.bls.gov/news.release/archives/cpi_10102024.htm published on or before 2024-07-10
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? official dated evidence published on or before 2024-07-10
  - Query: What resolver, publication, revision, or boundary risk at https://www.bls.gov/news.release/archives/cpi_10102024.htm could change the scored outcome? official source official resolution rule published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `fddb79fc-3906-4fed-960a-4632324d73d6` How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?
  - Query: How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Query: How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? primary records published on or before 2024-07-10
  - Query: How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? domain-specific empirical research published on or before 2024-07-10
  - Query: How could historical base rate change the likelihood of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `006aeeb2-ea65-4a1b-8b6a-1c8015b2a618` How does shared upstream assumptions constrain or mediate the primary driver?
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? published on or before 2024-07-10
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official methodology published on or before 2024-07-10
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? primary dependency indicators published on or before 2024-07-10
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `34bbf3f6-dd0a-450d-9c5e-e7649121d0ff` Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? scenario analyses published on or before 2024-07-10
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? leading indicators published on or before 2024-07-10
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0
- `43349c1a-072d-406c-a143-bd23c5a5e278` What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%?
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? published on or before 2024-07-10
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? contradictory primary evidence published on or before 2024-07-10
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? methodological critiques published on or before 2024-07-10
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? official dated evidence published on or before 2024-07-10
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 0 / 0 / 0 / 0 / 0

### 3. Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?

- Evaluation question ID: `dfda5684-99a3-492b-89d3-316be4bedc23`
- Forecast run ID: `d5a171ad-bfb3-4988-87aa-6d5d880377f7`
- Selected / skipped nodes: 7 / 0
- Allocated searches: 35
- Estimated pre-execution cost: $0.000000
- Selected worker concurrency: 4
- Claims / node runs: 0 / 0
- Final probability: not produced
- Reduced confidence: no
- Actual model / search calls: 7 / 28
- Actual cost / remaining budget: $0.000000 / $0.250000
- Budget-exceeded events: 0
- Whole-run / physical retries: 0 / 0

| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | base_rate | yes | yes | 1.250000 | 0.051975 | 5 | 0 | no | cutoff_rejection |
| What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | trend | yes | yes | 4.500000 | 0.187110 | 5 | 0 | no | cutoff_rejection |
| How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | driver | yes | yes | 4.550000 | 0.189189 | 5 | 0 | no | cutoff_rejection |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | yes | 4.350000 | 0.180873 | 5 | 0 | no | cutoff_rejection |
| Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | scenario | no | yes | 4.700000 | 0.195426 | 5 | 0 | no | cutoff_rejection |
| What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | adversarial | no | yes | 3.650000 | 0.151767 | 5 | 0 | no | cutoff_rejection |
| What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? | resolver | no | yes | 1.050000 | 0.043659 | 5 | 0 | no | cutoff_rejection |

Queries and discovered URLs:

- `0124cc06-a16f-46d0-a0be-2ecdd34d173e` What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?
  - Query: What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Query: What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official historical series published on or before 2024-02-02
  - Query: What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? peer-reviewed reference-class studies published on or before 2024-02-02
  - Query: What base rate does Previously resolved business questions with comparable conditions. imply for: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `fd2e756b-c67a-4dd3-a0ca-27149ce9142c` What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? current official statistics published on or before 2024-02-02
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? primary institutional releases published on or before 2024-02-02
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `227ae317-8be3-4fa5-8e8b-4e2055eb6cfc` What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome?
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? published on or before 2024-02-02
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ published on or before 2024-02-02
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? official dated evidence published on or before 2024-02-02
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/ could change the scored outcome? official source official resolution rule published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `4e277151-a759-4cfb-95f3-4767f116ed59` How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?
  - Query: How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Query: How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? primary records published on or before 2024-02-02
  - Query: How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? domain-specific empirical research published on or before 2024-02-02
  - Query: How could historical base rate change the likelihood of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `11e4b120-4aab-45c4-b641-c4d893e477cb` How does shared upstream assumptions constrain or mediate the primary driver?
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? published on or before 2024-02-02
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official methodology published on or before 2024-02-02
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? primary dependency indicators published on or before 2024-02-02
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `5533982b-0592-410c-a983-df5e83f334fa` Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? scenario analyses published on or before 2024-02-02
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? leading indicators published on or before 2024-02-02
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `21b194c9-bfe9-4c2c-b3b3-942d622b7afb` What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024?
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? published on or before 2024-02-02
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? contradictory primary evidence published on or before 2024-02-02
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? methodological critiques published on or before 2024-02-02
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? official dated evidence published on or before 2024-02-02
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0

Failure categories: `cutoff_rejection` (7)

### 4. Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?

- Evaluation question ID: `e93a736f-df6d-4fbf-a833-8afa2c557aa6`
- Forecast run ID: `41f21d4d-65c9-4f86-8449-380015545ad0`
- Selected / skipped nodes: 7 / 0
- Allocated searches: 35
- Estimated pre-execution cost: $0.000000
- Selected worker concurrency: 4
- Claims / node runs: 0 / 0
- Final probability: not produced
- Reduced confidence: no
- Actual model / search calls: 7 / 28
- Actual cost / remaining budget: $0.000000 / $0.250000
- Budget-exceeded events: 0
- Whole-run / physical retries: 0 / 0

| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | base_rate | yes | yes | 1.250000 | 0.051975 | 5 | 0 | no | cutoff_rejection |
| What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | trend | yes | yes | 4.500000 | 0.187110 | 5 | 0 | no | cutoff_rejection |
| How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | driver | yes | yes | 4.550000 | 0.189189 | 5 | 0 | no | cutoff_rejection |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | yes | 4.350000 | 0.180873 | 5 | 0 | no | cutoff_rejection |
| Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | scenario | no | yes | 4.700000 | 0.195426 | 5 | 0 | no | cutoff_rejection |
| What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | adversarial | no | yes | 3.650000 | 0.151767 | 5 | 0 | no | cutoff_rejection |
| What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? | resolver | no | yes | 1.050000 | 0.043659 | 5 | 0 | no | cutoff_rejection |

Queries and discovered URLs:

- `e612c5b5-099b-4503-92c4-3b5b5d4dd431` What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?
  - Query: What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Query: What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official historical series published on or before 2024-03-15
  - Query: What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? peer-reviewed reference-class studies published on or before 2024-03-15
  - Query: What base rate does Previously resolved technology questions with comparable conditions. imply for: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `7d8af8d5-2670-4022-9320-242032c5fbca` What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? current official statistics published on or before 2024-03-15
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? primary institutional releases published on or before 2024-03-15
  - Query: What is the current level and direction of the observable indicators most relevant to: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `148aeb13-4299-4af0-b334-472e1e7292ca` What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome?
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? published on or before 2024-03-15
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ published on or before 2024-03-15
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? official dated evidence published on or before 2024-03-15
  - Query: What resolver, publication, revision, or boundary risk at https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/ could change the scored outcome? official source official resolution rule published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `85cd49c2-a95e-46a1-a779-b152d2a86aeb` How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?
  - Query: How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Query: How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? primary records published on or before 2024-03-15
  - Query: How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? domain-specific empirical research published on or before 2024-03-15
  - Query: How could historical base rate change the likelihood of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `f1282620-4526-48a8-9bdf-4559c07828f8` How does shared upstream assumptions constrain or mediate the primary driver?
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? published on or before 2024-03-15
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official methodology published on or before 2024-03-15
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? primary dependency indicators published on or before 2024-03-15
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `34b22a35-015d-4bee-a335-d5728c33ebe7` Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? scenario analyses published on or before 2024-03-15
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? leading indicators published on or before 2024-03-15
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `dff5d577-5bfa-4d4d-968e-f273c7b8e0f1` What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024?
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? published on or before 2024-03-15
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? contradictory primary evidence published on or before 2024-03-15
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? methodological critiques published on or before 2024-03-15
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? official dated evidence published on or before 2024-03-15
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0

Failure categories: `cutoff_rejection` (7)

### 5. Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?

- Evaluation question ID: `292e0bf3-3b58-43bb-b877-61412481e2d1`
- Forecast run ID: `6f68fa8e-fbdf-474b-a496-aca08d5bdf86`
- Selected / skipped nodes: 7 / 0
- Allocated searches: 35
- Estimated pre-execution cost: $0.000000
- Selected worker concurrency: 4
- Claims / node runs: 0 / 0
- Final probability: not produced
- Reduced confidence: no
- Actual model / search calls: 7 / 28
- Actual cost / remaining budget: $0.000000 / $0.250000
- Budget-exceeded events: 0
- Whole-run / physical retries: 0 / 0

| Node | Type | Critical | Selected | Raw priority | Normalized priority | Searches allocated | Claims | Forecast | Failure |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | base_rate | yes | yes | 1.250000 | 0.051975 | 5 | 0 | no | cutoff_rejection |
| What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | trend | yes | yes | 4.500000 | 0.187110 | 5 | 0 | no | cutoff_rejection |
| How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | driver | yes | yes | 4.550000 | 0.189189 | 5 | 0 | no | cutoff_rejection |
| How does shared upstream assumptions constrain or mediate the primary driver? | dependency | no | yes | 4.350000 | 0.180873 | 5 | 0 | no | cutoff_rejection |
| Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | scenario | no | yes | 4.700000 | 0.195426 | 5 | 0 | no | cutoff_rejection |
| What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | adversarial | no | yes | 3.650000 | 0.151767 | 5 | 0 | no | cutoff_rejection |
| What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? | resolver | no | yes | 1.050000 | 0.043659 | 5 | 0 | no | cutoff_rejection |

Queries and discovered URLs:

- `393e896a-9515-4b6e-8718-99559e42d1b5` What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?
  - Query: What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Query: What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official historical series published on or before 2024-01-05
  - Query: What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? peer-reviewed reference-class studies published on or before 2024-01-05
  - Query: What base rate does Previously resolved regulation questions with comparable conditions. imply for: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `8a5cd33f-12b4-4bc8-99a4-66adf29eedc6` What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? current official statistics published on or before 2024-01-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? primary institutional releases published on or before 2024-01-05
  - Query: What is the current level and direction of the observable indicators most relevant to: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `4c44ff6b-a4fb-471d-991d-5bed6b24abb4` What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome?
  - Query: What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? published on or before 2024-01-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 published on or before 2024-01-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? official dated evidence published on or before 2024-01-05
  - Query: What resolver, publication, revision, or boundary risk at https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624 could change the scored outcome? official source official resolution rule published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `f816f5f4-4d24-4e6c-8dd2-76c234a85525` How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?
  - Query: How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Query: How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? primary records published on or before 2024-01-05
  - Query: How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? domain-specific empirical research published on or before 2024-01-05
  - Query: How could historical base rate change the likelihood of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `28c12504-6e84-4334-9c8d-1aacaeeb7869` How does shared upstream assumptions constrain or mediate the primary driver?
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? published on or before 2024-01-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official methodology published on or before 2024-01-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? primary dependency indicators published on or before 2024-01-05
  - Query: How does shared upstream assumptions constrain or mediate the primary driver? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `3531f54d-e058-4afa-b550-afeb97059cc5` Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? scenario analyses published on or before 2024-01-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? leading indicators published on or before 2024-01-05
  - Query: Which plausible alternative scenario would most change the expected outcome of: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0
- `83ab0d4c-5eeb-4bd0-a2f7-002bea99576f` What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions?
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? published on or before 2024-01-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? contradictory primary evidence published on or before 2024-01-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? methodological critiques published on or before 2024-01-05
  - Query: What strongest evidence or hidden assumption could overturn the leading view on: Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? official dated evidence published on or before 2024-01-05
  - URL: https://fixtures.forecastlab.local/bls-employment-situation
  - URL: https://fixtures.forecastlab.local/fred-unrate
  - Fetch/cutoff/extraction/smaller-chunk/document-fallback/failure: 1 / 1 / 0 / 0 / 0 / 0

Failure categories: `cutoff_rejection` (7)

## Pass/fail gate

| # | Condition | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Five of five graph runs produce a ForecastVersion | FAIL | 1/5 |
| 2 | Five of five graph runs produce a ForecastAggregation | FAIL | 1/5 |
| 3 | Every run produces at least one NodeForecastRun | FAIL | 0, 7, 0, 0, 0 |
| 4 | Every run produces at least one accepted EvidenceClaim | FAIL | 0, 7, 0, 0, 0 |
| 5 | No run ends with run-level budget_exceeded | PASS | 0 |
| 6 | No critical selected node fails | FAIL | 12 |
| 7 | Every run remains within the $0.25 lifetime ceiling | PASS | $0.000000, $0.000000, $0.000000, $0.000000, $0.000000 |
| 8 | The planner selects no more than eight nodes per run | PASS | 7, 7, 7, 7, 7 |
| 9 | Search and evidence limits remain within the committed profile and plan | PASS | within, within, within, within, within |
| 10 | No live OpenAI or Tavily request occurs | PASS | live ledger rows: 0 |
| 11 | No forecasting or research behavior is changed during validation | PASS | validation-only files |

## Integrity confirmations

- Forecasting and research behavior changed during validation: no.
- Validation-only harness/report/artifact changes: yes.
- Unexpected non-validation files: none.
- Live provider ledger rows: 0.
- Full 20-question pilot benchmark executed: no.
- Automatic validation rerun: no.

## Limitations

- Built-in mock providers validate execution mechanics and cutoff enforcement, not real-world retrieval coverage or forecast quality.
- Five questions measure a narrow reliability surface and do not support accuracy conclusions.
- Mock-provider monetary cost is zero; the planner estimate still records the pre-execution budget decision.
- Latency is diagnostic and is not part of the pass/fail gate.

Execution reliability only. This validation does not compute forecast accuracy, does not tune behavior, and does not run the full pilot benchmark.

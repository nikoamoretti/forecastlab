# Graph Execution Validation Report

## Scope

This is a five-question execution-reliability validation of `graph_forecaster_v1` version 5. It does not compare forecast accuracy.

- Validation ID: `4a5c23e0-0cf6-463b-a608-a1ce65c88e63`
- Pilot dataset hash: `c9cd748c1114eff5d16321ecf7d78b18be4a0b65b49ff7719aa5276e302f7888`
- Execution code commit: `99a3a9beebb8c285f079adb5964da30f84941b89`
- Configuration hash: `27be0a301d79220b53b6ca0debf8c47d39caa9ab61f8cee6fe57228ce732f568`
- Model: `openai / gpt-5-mini-2025-08-07`
- Search: `tavily`
- Evidence policy: `strict_historical_snapshot`
- Hard cost ceiling: $0.25/question, $1.25 total
- Selection rule: first two economics rows and first business, technology, and regulation rows in the frozen pilot manifest

## Previous state and current validation

| Measure | Previous graph benchmark | Current validation |
| --- | ---: | ---: |
| Questions | 20 | 5 |
| Completed | 10 | 0 |
| Failed | 10 | 5 |
| Completion rate | 50.0% | 0.0% |

The previous benchmark's ten failures were evidence failures. The current sample is smaller and uses profile version 5, so this is a descriptive execution comparison only.

## Current execution measures

- Successful nodes: 0 / 36
- Failed nodes: 36
- Evidence Claims created: 0
- Evidence success rate: 0.0%
- Node forecasts created: 0
- Final aggregations created: 0
- Forecast Versions created: 0
- Total latency: 1144424 ms
- Mean latency: 228884.8 ms/question
- Total measured cost: $1.029790
- Whole-run retries: 0
- Physical provider retries: 0
- Failed provider attempts: 0

## Question-level execution audit

| Domain | Question | Status | Successful / total nodes | Failed nodes | Claims | Node forecasts | Aggregation | Latency ms | Cost USD | Retries | Failure categories |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| economics | Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024 be at least 4.1%? | failed | 0 / 7 | 7 | 0 | 0 | no | 228811 | 0.225020 | 0 | budget_exceeded (1), extraction_failure (1), not_run_after_budget_stop (5) |
| economics | Will the 12-month U.S. CPI-U all-items inflation rate for September 2024 be below 2.5%? | failed | 0 / 7 | 7 | 0 | 0 | no | 166309 | 0.201855 | 0 | budget_exceeded (1), extraction_failure (1), not_run_after_budget_stop (5) |
| business | Will Apple report more than $90 billion in total net sales for fiscal Q2 2024? | failed | 0 / 7 | 7 | 0 | 0 | no | 249830 | 0.202965 | 0 | budget_exceeded (1), extraction_failure (1), not_run_after_budget_stop (5) |
| technology | Will Apple publicly introduce a product or service named Apple Intelligence by June 15, 2024? | failed | 0 / 8 | 8 | 0 | 0 | no | 214430 | 0.171690 | 0 | budget_exceeded (1), extraction_failure (1), not_run_after_budget_stop (6) |
| regulation | Will the SEC's final climate-disclosure rules adopted on March 6, 2024 require registrants to disclose Scope 3 greenhouse-gas emissions? | failed | 0 / 7 | 7 | 0 | 0 | no | 285044 | 0.228260 | 0 | budget_exceeded (1), extraction_failure (2), not_run_after_budget_stop (4) |

## Evidence failures

- `budget_exceeded`: 5
- `extraction_failure`: 6
- `not_run_after_budget_stop`: 25

## Limitations

- Five questions are enough to test the execution path, not forecasting quality or statistical reliability.
- Historical retrieval depends on search indexing and verified archive availability at each forecast cutoff.
- Cost is ledger-derived and may be estimated where a provider does not report billing data.
- No failed question was automatically rerun, and the full 20-question pilot benchmark was not executed.

Execution reliability only. No forecast-accuracy metric or superiority claim is computed from this five-question validation.

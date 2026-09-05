# ForecastLab Pilot Benchmark v1 protocol

Status: curated historical pilot dataset. It validates the real-question dataset and experiment plumbing; it is not large or representative enough to establish forecasting quality or profile superiority.

## Dataset purpose and identity

ForecastLab Pilot Benchmark v1 contains 20 retrospectively formulated, resolved binary questions in [`fixtures/benchmarks/pilot_v1.csv`](../fixtures/benchmarks/pilot_v1.csv). Each question has a historical forecast cutoff, an exact binary rule, a resolution date, an authoritative first-party resolver, a linked resolution record, and a known outcome.

The fixed composition is:

| Domain | Questions |
| --- | ---: |
| Economics | 8 |
| Business | 5 |
| Technology | 4 |
| Regulation | 3 |

There are 10 `YES` outcomes (`1`) and 10 `NO` outcomes (`0`). Recorded forecast-to-resolution windows range from 61 to 92 days. This balance is a pilot design constraint, not an estimate of real-world event prevalence.

The CSV contains every requested field: `question`, `yes_condition`, `no_condition`, `forecast_date`, `resolution_date`, `outcome`, `resolution_source`, `domain`, and `category`. It also contains `authoritative_resolver`, which the existing fail-closed evaluation contract requires so that a URL is never treated as a substitute for resolver identity.

## Selection criteria

A question was eligible only when all of the following were true:

- The outcome is binary and already resolved.
- The yes and no conditions are mutually exclusive and collectively exhaustive at the stated threshold or deadline.
- The forecast cutoff precedes resolution by 30 to 120 days; v1's actual range is 61 to 92 days.
- A named first-party institution or company is authoritative for the result.
- The linked source states the exact released value, action, announcement, or release date used for scoring.
- The outcome can be scored without opinion or post hoc interpretation.
- The topic belongs to the frozen 8/5/4/3 domain mix.

For a deadline question that resolved `NO`, `resolution_date` is the date on which the linked first-party release made the missed deadline auditable. It is not a claim that the later release existed at the historical forecast cutoff.

## Exclusions

The pilot excludes subjective outcomes, vague thresholds, opinion-based resolution, geopolitical events, unresolved questions, questions whose resolver is only a news or aggregation site, horizons outside the pilot window, and outcomes that require choosing among conflicting sources. Secondary reporting may help discover a candidate but cannot resolve a row.

## Resolution ledger

The observed value is included here as a human review aid. The CSV's exact yes/no conditions remain the scoring contract.

| # | Domain | Outcome | Observed resolution | Authoritative record |
| ---: | --- | :---: | --- | --- |
| 1 | Economics | YES | June 2024 U-3 unemployment: 4.1% | [BLS Employment Situation, July 5, 2024](https://www.bls.gov/news.release/archives/empsit_07052024.htm) |
| 2 | Economics | YES | September 2024 CPI-U, 12 months: 2.4% | [BLS CPI, October 10, 2024](https://www.bls.gov/news.release/archives/cpi_10102024.htm) |
| 3 | Economics | NO | Q2 2024 real GDP third estimate: 3.0% | [BEA third estimate, September 26, 2024](https://www.bea.gov/index.php/news/2024/gross-domestic-product-third-estimate-corporate-profits-revised-estimate-and-gdp-0) |
| 4 | Economics | YES | Federal funds target range: 4.75%-5.00% | [FOMC statement, September 18, 2024](https://www.federalreserve.gov/newsevents/pressreleases/monetary20240918a.htm) |
| 5 | Economics | YES | November 2024 payroll change: +227,000 | [BLS Employment Situation, December 6, 2024](https://www.bls.gov/news.release/archives/empsit_12062024.htm) |
| 6 | Economics | NO | December 2024 U-3 unemployment: 4.1% | [BLS Employment Situation, January 10, 2025](https://www.bls.gov/news.release/archives/empsit_01102025.htm) |
| 7 | Economics | NO | January 2025 CPI-U, 12 months: 3.0% | [BLS CPI, February 12, 2025](https://www.bls.gov/news.release/archives/cpi_02122025.htm) |
| 8 | Economics | NO | Q1 2025 real GDP advance estimate: -0.3% | [BEA advance estimate, April 30, 2025](https://www.bea.gov/index.php/news/2025/gross-domestic-product-1st-quarter-2025-advance-estimate) |
| 9 | Business | YES | Apple fiscal Q2 2024 net sales: $90.753B | [Apple fiscal Q2 results, May 2, 2024](https://www.apple.com/newsroom/2024/05/apple-reports-second-quarter-results/) |
| 10 | Business | YES | Tesla Q2 2024 deliveries: 443,956 | [Tesla production and deliveries, July 2, 2024](https://ir.tesla.com/press-release/tesla-vehicle-production-deliveries-and-date-financial-results-webcast-second-quarter-2024) |
| 11 | Business | NO | NVIDIA fiscal Q2 2025 revenue: $30.0B | [NVIDIA fiscal Q2 results, August 28, 2024](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2025) |
| 12 | Business | NO | Netflix Q3 2024 revenue: $9.824703B | [Netflix Q3 shareholder letter, October 17, 2024](https://ir.netflix.net/files/doc_financials/2024/q3/FINAL-Q3-24-Shareholder-Lettter.pdf) |
| 13 | Business | NO | Microsoft fiscal Q2 2025 revenue: $69.6B | [Microsoft fiscal Q2 results, January 29, 2025](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q2/press-release-webcast) |
| 14 | Technology | YES | Apple Intelligence introduced June 10, 2024 | [Apple Intelligence announcement](https://www.apple.com/newsroom/2024/06/introducing-apple-intelligence-for-iphone-ipad-and-mac/) |
| 15 | Technology | YES | GPT-4o announced May 13, 2024 | [OpenAI GPT-4o announcement](https://openai.com/index/hello-gpt-4o/) |
| 16 | Technology | NO | Python 3.13.0 final released October 7, 2024 | [Python 3.13.0 release record](https://www.python.org/downloads/release/python-3130/) |
| 17 | Technology | NO | Kubernetes v1.31 released August 13, 2024 | [Kubernetes v1.31 announcement](https://kubernetes.io/blog/2024/08/13/kubernetes-v1-31-release/) |
| 18 | Regulation | NO | Final SEC rule omitted Scope 3 disclosure | [SEC statement on final rules, March 6, 2024](https://www.sec.gov/newsroom/speeches-statements/gensler-statement-mandatory-climate-risk-disclosures-030624) |
| 19 | Regulation | YES | FTC voted to issue final rule April 23, 2024 | [FTC final-rule announcement](https://www.ftc.gov/news-events/news/press-releases/2024/04/ftc-announces-rule-banning-noncompetes) |
| 20 | Regulation | YES | FCC adopted Title II order April 25, 2024 | [FCC net-neutrality announcement](https://docs.fcc.gov/public/attachments/DOC-402082A1.pdf) |

## Import, review, and freeze workflow

The repository provides one idempotent workflow for this named release:

```bash
uv run python scripts/import_pilot_benchmark.py
```

The command applies pending database migrations, loads the checked-in CSV, validates every row atomically, verifies the pilot manifest constraints, imports version `1` as a draft, reviews its canonical contents, and freezes it. It prints only dataset identity, status, count, and SHA-256 hash. It does not create an experiment or run a forecast.

The generic API remains available for a manual two-person review flow:

1. Import the CSV with `POST /api/evaluation/datasets/import`, name `ForecastLab Pilot Benchmark`, version `1`, and documented provenance.
2. Independently inspect the returned questions and source ledger.
3. Mark it reviewed with `POST /api/evaluation/datasets/{id}/review`.
4. Freeze it with `POST /api/evaluation/datasets/{id}/freeze`.
5. Record the returned dataset hash before any experiment is created.

The dedicated workflow rejects a missing outcome, a missing resolver, any outcome other than `0` or `1`, vague questions or generic resolution conditions, a non-first-party source host, a missing category, a date window outside 30-120 days, the wrong domain mix, the wrong outcome balance, or a row count other than 20. Import is atomic. Frozen datasets and their questions are immutable.

## No-tuning rule

Once execution begins against this dataset hash, no forecasting prompt, profile, model choice, threshold, evidence policy, question wording, resolution rule, outcome, category, or source may be tuned using pilot results and then rerun as the same benchmark version. Any correction to the dataset requires a documented new dataset version and invalidates direct comparison with v1 unless both systems are rerun under the new frozen release.

The pilot may be used to find software, instrumentation, and operational defects. It must not be repeatedly used as a development set for improving forecast scores.

## Limitations and allowed conclusions

The questions were selected retrospectively and are not a random or preregistered sample. The dataset is small, intentionally outcome-balanced, and concentrated in events with unusually clean first-party resolution. It does not yet include a frozen as-of evidence corpus, event-family clustering, licensing metadata, or an independent adjudicator log. Company and agency pages can also change presentation after publication, although the linked records identify the authoritative release.

Allowed conclusion: the platform did or did not complete its real-question workflow under the frozen dataset and configuration.

Not allowed: a claim that one profile is better, calibrated, production-ready, or generally accurate based on this pilot alone.

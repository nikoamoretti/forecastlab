# Personal V1 prospective pilot, September 2026

This is an operational pilot with ten reviewed binary macro contracts and three frozen methods per question. Outcomes are unknown at entry. No accuracy conclusion is available until official releases and human adjudication; ten questions cannot establish superiority.

The method set is `root_event_ensemble_v1`, `single_model_forecaster_v1`, and `three_track_forecaster`. All use the existing configured model (`gpt-5-mini-2025-08-07`) and search provider. Preflight rejects all three methods under the prior saved $0.25 ceiling (conservative workload estimates are $1.608 to $1.696). The implementation therefore applies the user-approved $5 ceiling before freeze. There is also an atomic $150 cohort ceiling, including retries. Existing model/call/token safeguards stay fixed.

Post-pilot repair verification is limited to one existing question under all three methods, with a separate frozen manifest and **$3 total ceiling across the three verification assignments**. It is a regression check, not another independent sample. The first pilot's $6.553525 cost and maximum per-assignment cost below $1, plus this $3 cap, keep combined work below the original $150 total and $5 per question/method. The failed pilot remains the pilot record; verification cannot replace it or expand into another campaign.

The common forecast completion cutoff is **2026-09-05 06:00 UTC**, before the earliest outcome release. Evidence may be retrieved live until forecast completion; all completed forecasts must precede the fixed cutoff. Methods and contracts are frozen before execution. Failed and withheld assignments remain in the coverage denominator.

| Indicator / observation month | Yes condition on first published value | Release UTC | Shared release event |
| --- | --- | --- | --- |
| CPI, August 2026 | Year-over-year CPI-U inflation > 3.2% | 2026-09-11 12:30 | cpi-2026-09-11 |
| CPI, August 2026 | Year-over-year CPI-U inflation > 3.4% | 2026-09-11 12:30 | cpi-2026-09-11 |
| Unemployment, September 2026 | Seasonally adjusted U-3 > 4.1% | 2026-10-02 12:30 | employment-2026-10-02 |
| Payrolls, September 2026 | Seasonally adjusted monthly change > 150,000 jobs | 2026-10-02 12:30 | employment-2026-10-02 |
| CPI, September 2026 | Year-over-year CPI-U inflation > 3.3% | 2026-10-14 12:30 | cpi-2026-10-14 |
| Unemployment, October 2026 | Seasonally adjusted U-3 > 4.2% | 2026-11-06 13:30 | employment-2026-11-06 |
| Payrolls, October 2026 | Seasonally adjusted monthly change > 150,000 jobs | 2026-11-06 13:30 | employment-2026-11-06 |
| Unemployment, November 2026 | Seasonally adjusted U-3 > 4.2% | 2026-12-04 13:30 | employment-2026-12-04 |
| Payrolls, November 2026 | Seasonally adjusted monthly change > 150,000 jobs | 2026-12-04 13:30 | employment-2026-12-04 |
| CPI, November 2026 | Year-over-year CPI-U inflation > 3.3% | 2026-12-10 13:30 | cpi-2026-12-10 |

These are **six shared release events**, not ten independent observations. The two August CPI thresholds are nested and deliberately identified as correlated. October 2026 CPI is excluded because the October 2025 observation is unavailable; the adapter does not invent a prior-year denominator.

Contracts use BLS series LNS14000000, CES0000000001, and CUUR0000SA0. CPI means the published one-decimal all-items CPI-U twelve-month change before seasonal adjustment. Payrolls means the published change in jobs, not its thousands-of-jobs source units. Later revisions cannot replace the first release. Unavailable or withdrawn resolution measurements require a recorded cancellation with supporting evidence.

Dates were checked against the [BLS employment schedule](https://www.bls.gov/schedule/news_release/empsit.htm) and [CPI schedule](https://www.bls.gov/schedule/news_release/cpi.htm). Release times are 08:30 America/New_York, converted with the applicable daylight-saving offset. Thresholds were selected before execution, around the [August employment release](https://www.bls.gov/news.release/archives/empsit_09042026.htm) (4.1% unemployment and +162,000 payrolls) and [July CPI release](https://www.bls.gov/news.release/cpi.nr0.htm) (3.4% annual inflation), not by optimizing against future outcomes.

Contract review is recorded as **Codex, delegated implementation review**, not as an outcome confirmation by Nico. Actual outcomes require human confirmation in the Lab. The execution receipt will record the cohort ID, frozen manifest, source commit, terminal statuses, cost, and unresolved issues. Historical outcome tables and past results are not modified.

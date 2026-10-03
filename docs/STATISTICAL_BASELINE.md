# Statistical baseline (`statistical_baseline_v1`)

`statistical_baseline_v1` is a deterministic, transparent forecasting method for macro questions. It uses no
language model, no search and no paid call: one official-data request, a fixed rule, and a plain-language
explanation built from the numbers. It is the fourth method in newly frozen prospective cohorts and the
benchmark that the AI methods must beat. Its probabilities are coherent across thresholds by construction,
it costs $0, and, unlike a language model that has read history in training, it can be backtested without
leakage.

## In plain language

To ask whether next month's unemployment rate will be above 4.2%, the baseline looks at every one-month
change in the published rate over the last ten years, adds each of those changes to the latest value, and
counts how often the result is above 4.2%. If 29 of 114 historical changes would end above 4.2%, the
probability is (29 + 0.5) / (114 + 1) = 26%. The same set of simulated values gives the most likely published
value and a range, for example "most likely 4.2%, 80% range 3.9% to 4.3%".

## The rule (`statistical_baseline_rule_v1`)

The rule lives in `packages/forecasting/forecastlab/statistical_baseline.py`.

| Indicator | Series | Point forecast | Trailing window | Horizon unit | Publication precision |
| --- | --- | --- | --- | --- | --- |
| `unemployment` | BLS LNS14000000 (UNRATE) | Last value | 120 months | month | 0.1 point |
| `cpi` | BLS CUUR0000SA0 (CPIAUCNS) 12-month change | Last value | 120 months | month | 0.1 point, half-up |
| `payrolls` | BLS CES0000000001 (PAYEMS) monthly change | Mean of the last 3 monthly changes | 120 months | month | 1,000 jobs |
| `jobless_claims` | FRED ICSA | Last value | 156 weeks | week | 1 claim |
| `treasury_10y` | FRED DGS10 | Last value | 520 weekdays | weekday | 0.01 point |

1. **Horizon.** `h` is the number of periods from the last available observation to the target
   `observation_period`: months, week-ending Saturdays, or weekdays (holidays count as weekdays and simply
   have no value).
2. **Window.** The window is the trailing 120 months, 156 weeks or 520 weekdays that end at the last
   observation. If the data adapter returns less history, the rule uses what it returns; the result records
   the actual start, end and number of observations.
3. **Point forecast.** The point forecast is a random walk for levels and year-over-year inflation, so the
   forecast for any horizon is the last value. For the payroll change it is the mean of the origin month and
   the two months before it; all three must be published.
4. **Errors.** The rule is applied at every origin `t` inside the window whose target `t + h` is also inside
   it (for payrolls, the two months before `t` must be inside it too). The error is the value at `t + h`
   minus the rule's forecast made at `t`. Origins overlap, and a pair
   with a missing value (for example the unpublished October 2025 unemployment rate) is skipped, never
   imputed. `n` is the number of errors; fewer than 24 withholds.
5. **Predictive sample.** The sample is `point + error` for every error, rounded half-up (ties away from
   zero) to the publication precision. Arithmetic is decimal, so a sample value equal to the threshold is
   exactly equal: starting from 4.2%, an unchanged outcome never counts as "above 4.2%".
6. **Probability.** `(k + 0.5) / (n + 1)`, where `k` counts sample values that satisfy the question's
   comparison (`gt`, `ge`, `lt`, `le`) against the threshold. The probability is then clipped to the bounds
   of the existing aggregators, `[0.02, 0.98]`. For `gt`, the probability can only fall as the threshold
   rises, and before clipping `P(> x) + P(<= x) = 1`.
7. **Value forecast.** The quantiles 5, 10, 25, 50, 75, 90 and 95 are the inverse empirical CDF of the
   rounded sample, so each is a publishable value. The mode is the most frequent rounded value; ties go to
   the value closest to the rounded point forecast, then to the lower value.

The rationale is generated deterministically from these numbers, for example:

> Over the last 117 months of official data (2017-01 to 2026-09), the U.S. unemployment rate series has 114
> changes over 1 month. Adding each of those changes to the latest value, 4.2% for September 2026, puts the
> October 2026 figure above 4.2% in 29 of 114 cases. The (k + 0.5) / (n + 1) rule gives a probability of 26%
> that the October 2026 figure is above 4.2%. The most likely published value is 4.2%, and the 80% range is
> 3.9% to 4.3%.

## Data and point in time

The baseline reads official observations through the same `fetch_macro` path as `root_event_ensemble_v1`.

- **Live, monthly series.** The keyless BLS v1 API returns ten calendar years. After the payroll and 12-month
  transforms that is roughly 94 to 119 usable months, depending on the series and the month of the year
  (106 to 118 for unemployment and 94 to 106 for CPI in the backtest), so the 120-month window is never
  full. These values are the latest revisions, not first releases, and are labeled that way.
- **Live, weekly and daily series.** The keyless FRED CSV path is used. The baseline passes optional
  `fetch_macro` arguments (`fred_history_limit`, `fred_lookback_days`) that widen only its own history to
  156 weeks or 520 weekdays. The root method's capped history (104 weekly, 130 daily) is unchanged.
- **Snapshot checks.** After the fetch the baseline runs the root method's snapshot check
  (`validate_snapshot`): indicator, raw-payload hash, series id, units, seasonal adjustment, every period
  before the target, and every observation available at or before the frozen forecast cutoff.
- **Backtest mode.** Monthly series use the previous-day ALFRED vintage and need a FRED API key, as the root
  method does. An ALFRED snapshot is necessarily retrieved after its cutoff, so in backtest mode only the
  vintage bound on each observation's availability is checked against the cutoff. Weekly and daily FRED
  series withhold with `historical_fred_series_not_supported`, because `fetch_macro` fails closed for them.

The baseline withholds (outcome `insufficient_evidence`, null probability) with a stable gap reason:

| Gap | When |
| --- | --- |
| `statistical_baseline_requires_macro_spec` | The question has no `MacroSpec`, for example a general question |
| `statistical_baseline_demo_mode_no_official_data` | Demo mode, which never fetches official data and never invents a series |
| `historical_fred_series_not_supported`, `historical_macro_vintage_key_required`, `macro_current_conditions_stale`, ... | `fetch_macro` gaps, as for the root method |
| `macro_observation_period_or_cutoff_mismatch` and other snapshot-check reasons | Data after the target or after the cutoff |
| `statistical_baseline_history_insufficient` | Fewer than 24 errors at the requested horizon |
| `statistical_baseline_point_inputs_missing` | A payroll month needed for the three-month mean is missing |

A failed official-data request (`macro_request_failed:*`, `bls_request_not_succeeded`) is an execution
failure, not an abstention, as for the root method. A run that passes its wall-clock deadline also fails.

## Where it runs

- **Prospective cohorts.** `prospective.METHODS` now lists `statistical_baseline_v1` after the three AI
  methods, so every newly frozen entry gets four assignments. The profile ceiling is $0, so the baseline
  adds nothing to the cohort budget and runs even when the AI methods have spent most of it. Cohorts frozen
  earlier keep the methods in their manifest, and reports read the manifest.
- **One personal macro question.** `POST /api/questions/{id}/runs` with
  `{"profile_id": "statistical_baseline_v1", "mode": "live"}` creates a baseline run from the question's
  approved contract and macro specification. The run uses the same personal envelope as a root rerun.
- **Report.** The forecast page shows the rationale, the most likely value, the 80% and 90% ranges, the
  quantiles, the rule and window, and the official source with its retrieval time and raw-response hash.
- **Other runs.** The profile is listed with the others, but a run without a personal envelope and macro
  specification, such as a Lab benchmark task on a synthetic question, completes without a probability. The
  run's progress message names the gap (`statistical_baseline_requires_macro_spec`).

Each run stores a `ForecastVersion` with `ensemble_probability` set and the personal outcome `forecasted`.
The personal result JSON has `statistical_baseline`, which holds the distribution (each rounded value with
its count and share), quantiles, mode, mean, 80% and 90% intervals, the rule version, the probability rule
and bounds, `n`, `k`, the horizon, the window, the last observation with its period, availability, vintage
and revision basis, and the snapshot's `raw_hash`, source URL and `retrieved_at`. It also records
`model_calls: 0`, `search_calls: 0` and `cost_usd: 0`. The run's usage ledger has no provider calls. The
attempt record, latency, frozen-assignment checks and deadline work as for the other methods.

## Historical backtest (`statistical_baseline_backtest_v1`)

`scripts/backtest_statistical_baseline.py` backtests the deployed rule and writes
`artifacts/statistical_baseline_backtest_v1/results.json` and `summary.md`. Run it with
`uv run --frozen python scripts/backtest_statistical_baseline.py`; add `--offline` to replay the cached
responses. These are backtest results for a fixed rule, not prospective results, and they say nothing about
the language-model methods.

**Questions.** The question rule was fixed before any result was computed and not tuned. For every target
month from 2016-01 to the latest first release, three questions ask whether the first-release value is
greater than the last published value minus one step, the last published value, and the last published
value plus one step. The step is 0.1 point for unemployment and CPI and 50,000 jobs for payrolls.

**Point in time.** The script reads ALFRED's published list of vintage dates for UNRATE, PAYEMS and
CPIAUCNS, one page per series. It then requests up to 12 vintages per keyless `alfredgraph.csv` call, which
is ALFRED's limit on lines per graph; every range spans at least two observations. A target's first
release is the first vintage after its month that contains it. The script checks that the vintage dated
the day before does not contain it.

- The **information set** is that previous-day vintage. It is cut to the ten calendar years the live BLS v1
  adapter returns and transformed by the adapter's own code.
- The **outcome** is the target in its first-release vintage: UNRATE as published, the PAYEMS level minus
  the same vintage's prior month in jobs, and the CPIAUCNS 12-month change rounded half-up to 0.1. These
  are ALFRED reconstructions of the BLS headline figures, not parsed news releases. Spot checks match the
  published first prints, for example unemployment 4.3% for July 2024, payrolls +114,000 for July 2024,
  -105,000 for October 2025 and +64,000 for November 2025, and CPI 2.7% for November 2025.
- **CPI rounding.** The adapter's rounding agreed with half-up rounding for every CPI value in every
  information set (0 mismatches).

The script made 71 requests in total, 3 vintage-list pages, 66 ALFRED CSVs and 2 FRED CSVs. It honored the
robots.txt crawl delays (2 s for ALFRED, 1 s for FRED) and retried transient failures. Raw responses are
cached under `data/local/statistical_baseline_backtest_v1/`, and each one's URL and SHA-256 is listed in
`results.json`.

**Comparators.** The uninformed forecast is 50% for every question. Persistence forecasts 98% if the last
published value already satisfies the question and 2% otherwise.

**Approximations.** ICSA (weekly) and DGS10 (daily) are backtested one period ahead from the current FRED
vintage, with steps of 10,000 claims and 0.05 point. They are approximations: ICSA advance figures are
revised the following week and its seasonal factors every year, and DGS10 revisions are rare but were not
verified.

### Results (data through 2026-10-03)

| Series | Targets | Questions | Brier: baseline | Brier: 50% | Brier: persistence | Log loss: baseline | Log loss: 50% | Log loss: persistence | ECE | 80% interval coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Unemployment rate (UNRATE) | 128 | 384 | 0.186 | 0.250 | 0.275 | 0.555 | 0.693 | 1.135 | 0.030 | 0.82 |
| Payroll change (PAYEMS) | 129 | 387 | 0.232 | 0.250 | 0.372 | 0.731 | 0.693 | 1.529 | 0.102 | 0.78 |
| CPI year-over-year (CPIAUCNS) | 127 | 381 | 0.246 | 0.250 | 0.406 | 0.686 | 0.693 | 1.665 | 0.015 | 0.80 |
| **Monthly series pooled (point in time)** | 384 | 1,152 | 0.221 | 0.250 | 0.351 | 0.657 | 0.693 | 1.442 | 0.036 | 0.80 |
| Initial claims (ICSA), approximation | 561 | 1,683 | 0.178 | 0.250 | 0.254 | 0.535 | 0.693 | 1.047 | 0.033 | 0.83 |
| 10-year yield (DGS10), approximation | 2,688 | 8,064 | 0.164 | 0.250 | 0.240 | 0.500 | 0.693 | 0.991 | 0.010 | 0.83 |

ECE is the count-weighted mean absolute gap between forecast and observed frequency over ten probability
bins. `summary.md` has the full reliability tables, the scores by threshold position, and the 90% interval
coverage.

### Reading the results

- **Pooled monthly series.** The rule's Brier score was 0.221, against 0.250 for 50% and 0.351 for
  persistence. That is a Brier skill of about 0.12 against the uninformed forecast. Persistence scores badly
  because it is always 2% or 98% and lands on the wrong side on 37% of the pooled questions. For
  unemployment and CPI the baseline lands on the wrong side of 50% about as often as persistence (29% and
  43% of questions); its advantage comes from calibrated rather than extreme probabilities.
- **Unemployment** shows the clearest gain (0.186). Its forecasts were reasonably calibrated (ECE 0.03) and
  the mode equaled the first print 25% of the time.
- **Payrolls** barely beat 50% on Brier (0.232), and the baseline's log loss was worse than 50% (0.731 against
  0.693). The trailing three-month mean lagged the 2020-2021 swings. In its 0.9-1.0 bin, 38 questions
  resolved yes only half the time, and in the 0.0-0.1 bin 20 questions resolved yes 45% of the time. Every
  confident miss in those two bins is from 2020 or 2021; for example, the April 2020 forecast started from a
  three-month mean of -71,000 jobs, and the first print was -20.5 million.
- **CPI** was essentially no better than 50% (0.246 against 0.250). Its forecasts were calibrated but not
  sharp, staying between 0.3 and 0.7, because one-month changes in 12-month inflation are about as likely to
  go either way.
- **80% intervals** contained 78% to 82% of the monthly first prints, and the 90% intervals 87% to 90%.
- **The ICSA and DGS10 approximations** scored 0.178 and 0.164, against 0.250 for 50%, but they are not
  point in time.

### Caveats

- The three questions per target are nested, and adjacent targets share most of their history, so the
  questions are strongly correlated. The effective sample is far smaller than 1,152, and no significance
  test is reported.
- Anchoring thresholds on the last value is a fixed question design; a different design would give
  different scores.
- The 120-month window contains the 2020 pandemic shock until 2030, which widens the monthly distributions.
- The live baseline reads latest revised BLS data, while the backtest reads ALFRED vintages for the same
  series and the same ten calendar years.

## Limitations

- **Own history only.** The baseline knows nothing beyond the series' own recent history: no consensus
  forecast, no other indicator, no known one-off event such as a strike, hurricane or shutdown, and no
  scheduled seasonal-factor revision. It assumes recent errors represent future errors.
- **Overlapping errors.** Errors at horizons above one overlap, so `n` overstates the independent
  information.
- **Turning points.** The payroll rule's three-month mean adapts slowly at turning points; see the 2020-2021
  backtest misses.
- **Revised history, first-release target.** Live monthly history is the latest revised data, while the
  questions resolve on first releases. The backtest uses point-in-time vintages, so live behavior can differ
  slightly.
- **Exact values.** At 1,000-job precision, a payroll mode is rarely the exact print; the intervals are more
  informative.
- **BLS quota.** Each baseline run on a BLS question makes one keyless BLS v1 request, and BLS allows 25 per
  day per address for unregistered use. A 10-question BLS cohort now makes about 20 such requests between
  the root method and the baseline. When the quota is exhausted, both fail with
  `bls_request_not_succeeded`, an execution failure.
- **Provider settings.** In live mode the run's execution context still records the configured model and
  search providers, because live context resolution requires them. The baseline never calls them, and its
  ledger stays empty.

# Statistical baseline backtest v1

Backtest of the fixed rule `statistical_baseline_rule_v1` (method `statistical_baseline_v1`), generated 2026-10-03 with data through 2026-10-03, from uncommitted code on top of commit `c6d20481cd332a5d20cfd3c7bd9ef2ae4c540608` (source SHA-256 in `results.json`). This is a historical backtest of a deterministic rule, not a prospective result, and not evidence about the language-model methods. Raw ALFRED/FRED responses are cached under `data/local/` and their SHA-256 values are listed in `results.json`.

## Questions

For every target period, three questions: is the value greater than the last published value minus one step, the last published value, and the last published value plus one step? Steps: 0.1 point (unemployment, CPI), 50,000 jobs (payrolls), 10,000 claims (ICSA), 0.05 point (DGS10). The rule and steps were fixed before any result was computed.

The monthly series are point in time: the information set is the ALFRED vintage dated the day before the target's first release, limited to the ten calendar years the live BLS v1 adapter returns; the outcome is the target in its first ALFRED vintage (unemployment as published, payroll level minus the same vintage's prior month, CPI 12-month change rounded half-up to 0.1). ICSA and DGS10 use the current FRED vintage and are approximations.

## Results

| Series | Targets | Questions | Brier: baseline | Brier: 50% | Brier: persistence | Log loss: baseline | Log loss: 50% | Log loss: persistence | ECE | 80% interval coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Unemployment rate (UNRATE) | 128 | 384 | 0.186 | 0.250 | 0.275 | 0.555 | 0.693 | 1.135 | 0.030 | 0.82 |
| Payroll change (PAYEMS) | 129 | 387 | 0.232 | 0.250 | 0.372 | 0.731 | 0.693 | 1.529 | 0.102 | 0.78 |
| CPI year-over-year (CPIAUCNS) | 127 | 381 | 0.246 | 0.250 | 0.406 | 0.686 | 0.693 | 1.665 | 0.015 | 0.80 |
| **Monthly series pooled (point in time)** | 384 | 1152 | 0.221 | 0.250 | 0.351 | 0.657 | 0.693 | 1.442 | 0.036 | 0.80 |
| Initial claims (ICSA), approximation | 561 | 1683 | 0.178 | 0.250 | 0.254 | 0.535 | 0.693 | 1.047 | 0.033 | 0.83 |
| 10-year yield (DGS10), approximation | 2688 | 8064 | 0.164 | 0.250 | 0.240 | 0.500 | 0.693 | 0.991 | 0.010 | 0.83 |

Persistence forecasts 98% when the last value already satisfies the question and 2% otherwise; the uninformed forecast is 50%. ECE is the count-weighted mean absolute gap between forecast and observed frequency over ten probability bins. Interval coverage is the share of first-release values inside the baseline's 10th-90th percentile range.

## Brier score by threshold position

| Series | Position | Questions | Outcome rate | Mean forecast | Brier: baseline | Brier: 50% | Brier: persistence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unemployment rate (UNRATE) | below | 128 | 0.54 | 0.52 | 0.250 | 0.250 | 0.443 |
| Unemployment rate (UNRATE) | at | 128 | 0.30 | 0.27 | 0.211 | 0.250 | 0.285 |
| Unemployment rate (UNRATE) | above | 128 | 0.10 | 0.08 | 0.096 | 0.250 | 0.098 |
| Payroll change (PAYEMS) | below | 129 | 0.61 | 0.64 | 0.218 | 0.250 | 0.372 |
| Payroll change (PAYEMS) | at | 129 | 0.45 | 0.51 | 0.240 | 0.250 | 0.432 |
| Payroll change (PAYEMS) | above | 129 | 0.33 | 0.38 | 0.237 | 0.250 | 0.313 |
| CPI year-over-year (CPIAUCNS) | below | 127 | 0.57 | 0.58 | 0.249 | 0.250 | 0.409 |
| CPI year-over-year (CPIAUCNS) | at | 127 | 0.48 | 0.48 | 0.255 | 0.250 | 0.462 |
| CPI year-over-year (CPIAUCNS) | above | 127 | 0.36 | 0.36 | 0.235 | 0.250 | 0.348 |
| Initial claims (ICSA), approximation | below | 561 | 0.78 | 0.77 | 0.174 | 0.250 | 0.211 |
| Initial claims (ICSA), approximation | at | 561 | 0.45 | 0.45 | 0.249 | 0.250 | 0.430 |
| Initial claims (ICSA), approximation | above | 561 | 0.12 | 0.13 | 0.110 | 0.250 | 0.120 |
| 10-year yield (DGS10), approximation | below | 2688 | 0.84 | 0.83 | 0.135 | 0.250 | 0.156 |
| 10-year yield (DGS10), approximation | at | 2688 | 0.46 | 0.46 | 0.248 | 0.250 | 0.443 |
| 10-year yield (DGS10), approximation | above | 2688 | 0.13 | 0.13 | 0.108 | 0.250 | 0.121 |

## Reliability (baseline probability, ten bins)

### Monthly series pooled (point in time)

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.0-0.1 | 124 | 0.06 | 0.16 |
| 0.1-0.2 | 63 | 0.16 | 0.14 |
| 0.2-0.3 | 146 | 0.25 | 0.26 |
| 0.3-0.4 | 180 | 0.35 | 0.34 |
| 0.4-0.5 | 201 | 0.46 | 0.45 |
| 0.5-0.6 | 244 | 0.55 | 0.55 |
| 0.6-0.7 | 83 | 0.63 | 0.58 |
| 0.7-0.8 | 44 | 0.76 | 0.80 |
| 0.8-0.9 | 29 | 0.84 | 0.83 |
| 0.9-1.0 | 38 | 0.95 | 0.50 |

### Unemployment rate (UNRATE)

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.0-0.1 | 104 | 0.06 | 0.11 |
| 0.1-0.2 | 25 | 0.17 | 0.12 |
| 0.2-0.3 | 101 | 0.25 | 0.28 |
| 0.3-0.4 | 26 | 0.32 | 0.35 |
| 0.4-0.5 | 52 | 0.47 | 0.52 |
| 0.5-0.6 | 63 | 0.54 | 0.54 |
| 0.6-0.7 | 13 | 0.61 | 0.62 |

### Payroll change (PAYEMS)

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.0-0.1 | 20 | 0.05 | 0.45 |
| 0.1-0.2 | 38 | 0.16 | 0.16 |
| 0.2-0.3 | 45 | 0.26 | 0.22 |
| 0.3-0.4 | 35 | 0.34 | 0.23 |
| 0.4-0.5 | 51 | 0.45 | 0.35 |
| 0.5-0.6 | 47 | 0.55 | 0.55 |
| 0.6-0.7 | 40 | 0.65 | 0.60 |
| 0.7-0.8 | 44 | 0.76 | 0.80 |
| 0.8-0.9 | 29 | 0.84 | 0.83 |
| 0.9-1.0 | 38 | 0.95 | 0.50 |

### CPI year-over-year (CPIAUCNS)

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.3-0.4 | 119 | 0.36 | 0.38 |
| 0.4-0.5 | 98 | 0.46 | 0.46 |
| 0.5-0.6 | 134 | 0.55 | 0.55 |
| 0.6-0.7 | 30 | 0.63 | 0.53 |

### Initial claims (ICSA), approximation

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.0-0.1 | 74 | 0.09 | 0.14 |
| 0.1-0.2 | 487 | 0.14 | 0.12 |
| 0.3-0.4 | 83 | 0.37 | 0.46 |
| 0.4-0.5 | 363 | 0.46 | 0.44 |
| 0.5-0.6 | 116 | 0.52 | 0.47 |
| 0.6-0.7 | 123 | 0.64 | 0.76 |
| 0.7-0.8 | 130 | 0.76 | 0.71 |
| 0.8-0.9 | 307 | 0.83 | 0.82 |

### 10-year yield (DGS10), approximation

| Forecast bin | Questions | Mean forecast | Observed frequency |
| --- | ---: | ---: | ---: |
| 0.0-0.1 | 1154 | 0.08 | 0.08 |
| 0.1-0.2 | 1129 | 0.14 | 0.14 |
| 0.2-0.3 | 390 | 0.23 | 0.19 |
| 0.3-0.4 | 159 | 0.39 | 0.45 |
| 0.4-0.5 | 2179 | 0.45 | 0.46 |
| 0.5-0.6 | 365 | 0.51 | 0.50 |
| 0.7-0.8 | 768 | 0.76 | 0.78 |
| 0.8-0.9 | 1541 | 0.85 | 0.85 |
| 0.9-1.0 | 379 | 0.91 | 0.89 |

## Value forecasts

| Series | Targets | 80% interval coverage | 90% interval coverage | Mode equals outcome | Median equals outcome |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unemployment rate (UNRATE) | 128 | 0.82 | 0.90 | 0.25 | 0.23 |
| Payroll change (PAYEMS) | 129 | 0.78 | 0.88 | 0.00 | 0.00 |
| CPI year-over-year (CPIAUCNS) | 127 | 0.80 | 0.87 | 0.07 | 0.08 |
| Initial claims (ICSA), approximation | 561 | 0.83 | 0.92 | 0.05 | 0.03 |
| 10-year yield (DGS10), approximation | 2688 | 0.83 | 0.92 | 0.09 | 0.09 |

## Caveats

- Historical backtest of a fixed, deterministic rule; these are not prospective results.
- The three questions per target are nested (thresholds on one value) and targets in adjacent periods share most of their history, so questions are strongly correlated. The effective sample is far smaller than the question count; no significance test is reported.
- Thresholds are anchored on the last published value, which favors persistence for the 'below' and 'above' questions and makes 'at' questions roughly coin flips for it.
- The trailing window (120 months, 156 weeks, 520 weekdays) includes the 2020 pandemic shock until the shock leaves the window, which widens the predictive distribution for those years.
- Monthly outcomes are ALFRED reconstructions of BLS headline figures (UNRATE as published, PAYEMS level minus the same vintage's prior month, CPIAUCNS 12-month change rounded half-up), not parsed BLS news releases.
- The live baseline uses the BLS v1 API (latest revised data); this backtest substitutes ALFRED vintages for the same series, limited to the same ten calendar years.
- October 2025 unemployment and CPI were never published (federal shutdown); those targets are skipped and the next target is forecast two months ahead.
- ICSA and DGS10 results use the current FRED vintage for history and outcomes and are approximations, not point in time.

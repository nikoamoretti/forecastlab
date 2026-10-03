# Conflict Panel V1: country-month political-violence baselines

Status: Phase 1. Statistical baselines, a rolling-origin scoreboard, a ViEWS benchmark and the first live panel. There are no AI-analyst adjustments and no protest or riot data yet. Nothing here is a claim about any particular group of people. The panel forecasts counts of deaths from organized violence that UCDP records, by country and month.

## Purpose and design

Long-horizon questions, such as whether a country will see large-scale political violence within ten years, cannot be scored for years. This phase therefore builds a panel that forecasts political-violence outcomes for every country every month. Those forecasts resolve monthly and can be scored thousands of times. A method that proves skill there can later be applied to individual countries and to longer questions.

Code: `packages/forecasting/forecastlab/conflict/`. Command-line interface: `scripts/conflict_panel.py`. Committed outputs: `artifacts/conflict_panel_v1/`.

| Module | Role |
| --- | --- |
| `sources.py` | UCDP release discovery and selection, an HTTPS download cache restricted to `ucdp.uu.se` and `api.viewsforecasting.org`, SHA-256 manifest |
| `ged.py`, `panel.py`, `countries.py` | Event parsing, aggregation to the zero-filled country-month panel, country identifiers |
| `features.py`, `models.py`, `forecaster.py` | Causal features, models, fitting and forecasting at one origin |
| `scoring.py`, `backtest.py` | Scoring rules, rolling-origin backtest, scoreboard |
| `views.py`, `vintage.py` | ViEWS benchmark and as-of (real-time vintage) panels |
| `live.py` | Live forecast artifact and `score_live_forecasts` |

numpy is the only new dependency. It is in a new `conflict` extra and in `dev`, so the API and worker install is unchanged. Nothing outside this package imports it.

## Data sources, attribution and license

- **UCDP Georeferenced Event Dataset (GED) Global 26.1**, covering 1989-01 to 2025-12, `https://ucdp.uu.se/downloads/ged/ged261-csv.zip`. It has 417,968 events.
- **UCDP Candidate Events Dataset 26.0.X**, which extends GED to recent months: `GEDEvent_v26_01_26_06.csv` (January–June 2026), `GEDEvent_v26_0_7.csv` and `GEDEvent_v26_0_8.csv`. After de-duplication, 13,659 Candidate events are used. The panel ends in **August 2026**.
- **Vintage reference:** `GEDEvent_v25_01_25_12.csv`, the full-year 2025 Candidate file. It is used only for the vintage check below. The ViEWS comparison also uses historical releases (GED 21.1–25.1 and the 2021–2026 Candidate files).
- **ViEWS** forecasts and ViEWS's own country-month UCDP aggregation (`predictors_fatalities003_0000_00`), taken from the public API at `https://api.viewsforecasting.org/`.

UCDP data are licensed CC BY 4.0. Please cite Davies, Pettersson and Öberg (2026), *Organized violence 1989–2025, and violent political protests*, Journal of Peace Research; Sundberg and Melander (2013), *Introducing the UCDP Georeferenced Event Dataset*, Journal of Peace Research 50(4); and Hegre, Croicu, Eck and Högbladh (2020), *Introducing the UCDP Candidate Events Dataset*, Research & Politics. ViEWS (Uppsala University and the Peace Research Institute Oslo; Hegre et al. 2019, *ViEWS: A political violence early-warning system*, Journal of Peace Research 56(2)) is credited as the benchmark. Use of its forecasts follows the terms published by the ViEWS project. Only bulk files and the public API are used, never the token-protected UCDP API.

Raw downloads stay in the gitignored `data/cache/conflict/` directory: about 410 MB, most of it historical releases for the as-of comparison. Each file's URL, SHA-256, size, retrieval time and `Last-Modified` header is recorded in `raw/raw_manifest.json`, and these hashes are copied into `artifacts/conflict_panel_v1/data_manifest.json`. The hashes of the files used are:

| File | SHA-256 |
| --- | --- |
| `ged261-csv.zip` | `8c941d84954e555ee2e54f40fa04d9203bf1e2f962203d0a9930966c4947c667` |
| `GEDEvent_v26_01_26_06.csv` | `68dd3d363b8edf4808bd6bf6f5d0412c3c722ffb1db367aeb8eff48bab80589a` |
| `GEDEvent_v26_0_7.csv` | `9fc2c6dd85eee91845512e8ef1055281d9fd028fb98748a6f2a27c6e8aa8c562` |
| `GEDEvent_v26_0_8.csv` | `2ad6e0b2bfdbaa31873716a3455096923a8539519d69d96aeeea2d0f44a34593` |
| `GEDEvent_v25_01_25_12.csv` (vintage check only) | `04191644956606b3a1305c427171c265c25fbdb314b9ef28b86503c981c5c24b` |

## The panel

- **Countries:** every `country_id` that appears in the selected GED and Candidate files, which gives 130 countries. Four of them (Belize, Chile, the Dominican Republic and Oman) appear only in 2026 Candidate data. The key is GED `country_id`, a Gleditsch–Ward code, together with UCDP's country name, GED region and the ISO 3166-1 alpha-3 code of the present-day state (`countries.py`). Countries with no recorded event since 1989 are not in the panel. Their forecasts would be near zero, and leaving them out avoids inflating scores with easy zeros.
- **Months:** 1989-01 to 2026-08. States that entered the system after 1989 (for example South Sudan from 2011-07, and the former Soviet republics from 1991-12) are only in the panel from their entry month, so earlier months are not counted as observed zeros. The panel has 58,018 valid country-months, and every one without an event is an explicit zero.
- **Series:** the UCDP `best` fatality estimate summed per country-month for state-based (sb), non-state (ns) and one-sided (os) violence, plus their total. Each event is assigned to the month of its `date_start`.
- **Release selection** (`fetch`): the latest final GED, then for each later year the latest cumulative Candidate file followed by monthly files. Coverage stops at the first missing month, so a missing file is never read as a month without events. An event that appears in several Candidate files is counted once, from the latest file. All Candidate events are kept, including those flagged `Check …`. `--candidate-status clear` keeps only events with status `Clear`.
- **Vintage:** final GED is revised every year, and Candidate data are preliminary. Backtests use today's vintage for all history, so they carry a vintage effect, quantified below.

## Targets, horizons and models

The **origin** `t` is the last month of data a forecast may use. Horizon `h` (1, 3, 6 or 12) targets the fatalities in the **single calendar month `t + h`**. It is not a cumulative window. For example, forecasts made from data through August 2026 target September 2026, November 2026, February 2027 and August 2027. The September 2026 month has already passed, but its data are unpublished and the forecast uses no information from it.

The targets are binary indicators that total fatalities are ≥ 1, ≥ 25 and ≥ 100, plus the count target `log1p(fatalities)`. A ViEWS-comparable state-based version is also run.

The models are:

- **Climatology:** each country's historical frequency over its months up to `t`, with a Jeffreys prior: `(hits + 0.5) / (months + 1)`. Its count forecast is the country's empirical `log1p` distribution.
- **Persistence:** the state of month `t`, as a 0/1 probability and as a point count.
- **Logistic baseline:** one L2-penalised logistic regression (Newton's method, λ = 1, standardised features) per threshold and horizon. Monotonicity is enforced with `P(≥25) = min(P(≥25), P(≥1))` and `P(≥100) = min(P(≥100), P(≥25))`. In the backtest the rule changed 1.2% of rows, by at most 0.054. The features are `log1p` fatalities over the last 1, 3, 6, 12 and 24 months; `log1p` months since the last month with a fatality, capped at 120; the share of the last 12 months with any fatality; the mean `log1p` 12-month fatalities of the other countries in the same GED region; and the global share of country-months with a fatality over the last 12 months.
- **Count distribution:** a mixture over the threshold bins. It puts mass at zero with probability `1 − P(≥1)`; inside the bins 1–24, 25–99 and ≥100 it uses a ridge-regression location plus 20 residual quantiles, clipped to the bin. The bin probabilities therefore equal the threshold probabilities exactly. Quantiles and the mean come from this mixture.

**No leakage.** Features at month `s` use only months up to `s`, and the model for origin `t` is fitted only on rows whose outcome month `s + h` is at or before `t`. Training rows start in 1990-12, so that 24 months of history exist. The tests check that features and forecasts at `t` are unchanged when later data are removed or altered.

## Rolling-origin backtest

Origins run from 2015-01 to the latest month with an observed target, which is 2026-07 for `h = 1` and 2025-08 for `h = 12`. This gives 69,940 country forecasts. Every model is refitted at every origin. The scores are the Brier score, log loss (probabilities clipped to [1e-4, 1 − 1e-4]), the Brier skill score (BSS) against climatology, AUC and calibration tables over ten equal-width probability bins. For counts the scores are MAE (median) and MSE (mean) on `log1p` and an exact CRPS. The 95% intervals come from a bootstrap over countries with 1,000 replicates, which keeps each country's correlated months together. "Top 20" means the 20 countries with the most fatalities over the target months 2015-02 to 2026-08: Ukraine, Ethiopia, Syria, Afghanistan, Sudan, Mexico, Israel, Yemen, DR Congo, Nigeria, Iraq, Somalia, Iran, Burkina Faso, Brazil, Myanmar, Mali, Pakistan, Russia and South Sudan. This slice is chosen using outcomes, so it is descriptive. Full results are in `artifacts/conflict_panel_v1/backtest_summary.json`.

### Brier skill against climatology, logistic baseline

| Slice | h | ≥1 | ≥25 | ≥100 |
| --- | --- | --- | --- | --- |
| All 130 countries (n = 18,070) | 1 | 0.563 [0.474, 0.643] | 0.619 [0.530, 0.690] | 0.578 [0.503, 0.640] |
| All 130 countries (n = 16,640) | 12 | 0.463 [0.355, 0.560] | 0.513 [0.416, 0.590] | 0.435 [0.350, 0.505] |
| Top 20 | 1 | 0.777 [0.585, 0.911] | 0.719 [0.595, 0.812] | 0.622 [0.544, 0.689] |
| Top 20 | 12 | 0.721 [0.493, 0.877] | 0.613 [0.471, 0.717] | 0.465 [0.379, 0.539] |
| France (n = 139 / 128) | 1 | 0.297 | −0.049 | −0.011 |
| France | 12 | −0.301 | −0.128 | −4.81 (no event; both Briers < 1e-4) |

Persistence also beats climatology, but by less: overall at `h = 1` its BSS is 0.29, 0.42 and 0.42, and at `h = 12` it is 0.17, 0.30 and 0.26. The baseline's AUC is 0.97–0.98 at `h = 1` and 0.96–0.97 at `h = 12`. Its log-loss skill against climatology is 0.51–0.57 at `h = 1` and 0.41–0.47 at `h = 12`. Skill declines steadily with horizon, with BSS at `h = 3` of 0.53–0.58 and at `h = 6` of 0.48–0.55.

**Counts, `log1p` scale, all countries.** At `h = 1`, CRPS is 0.236 for the baseline, 0.552 for climatology and 0.351 for persistence, a CRPS skill of 0.57. MSE of the mean is 0.50 (climatology 2.15, persistence 0.68), and MAE of the median is 0.31. At `h = 12`, CRPS is 0.308 against 0.572 for climatology (skill 0.46), and MSE is 0.80. The 5–95% predictive interval covers 96–97% of outcomes, so it is somewhat too wide.

### Calibration

Calibration-in-the-large is close. At `h = 1` the mean forecast is 0.302, 0.162 and 0.086 against observed rates of 0.311, 0.171 and 0.091. In the Murphy decomposition, reliability is small next to resolution: at `h = 1`, 0.0004–0.0017 against 0.06–0.16, and at `h = 12`, 0.0011–0.0035 against 0.05–0.15. The decile tables show a consistent pattern. Below 0.1, where most rows fall, forecasts are well calibrated. Between 0.1 and 0.2 for ≥25 and ≥100 they are too high; for example, forecasts of 0.14 for ≥25 at `h = 1` saw 0.077. Between about 0.4 and 0.9 for ≥25 and ≥100 they are **underconfident**: forecasts of 0.65 for ≥100 at `h = 1` saw 0.86, and forecasts of 0.45 for ≥25 at `h = 12` saw 0.59. High-intensity violence was more persistent from 2015 to 2026 than the linear-logit baseline implies. The model specification was fixed before the results were seen and was not tuned on them.

### France

In this panel France has 25 months with at least one recorded fatality since 1989. Two months had 25 or more: 2015-11 with 133 and 2016-07 with 87. The other months each have 1–11 deaths. Most fall in 2015–2018, and in 2023–2024 there is a run of 15 months with non-state violence. The August 2026 Candidate file adds one non-state fatality. In the backtest at `h = 1` there are 22 positive months for ≥1, and the baseline beats climatology for ≥1 (BSS 0.30, Brier 0.105 against 0.149) because it raises probabilities during the 2023–2024 run. The ≥25 and ≥100 outcomes happened once or twice, so their skill is noise around zero: the baseline gave the two large months probabilities of 0.002 and 0.011 for ≥25. At `h = 12` the baseline is worse than climatology for France, because it carries the elevated 2023–2024 level into later quiet months. Riots and unrest that cause no death recorded by UCDP are not in the data at all, which is why ACLED is planned next.

## Vintage check: Candidate data versus final GED

The full-year 2025 Candidate file (published January 2026) was compared with GED 26.1 for 2025 over 948 country-months (79 countries).

- Candidate data record 28,396 events and 181,331 deaths; the final data record 25,770 events and 245,464 deaths. Final totals are 35% higher.
- **≥1 fatality:** 488 country-months in both, 107 only in the Candidate file and 10 only in the final file. Candidate data include events flagged for checking and events below UCDP's annual inclusion threshold.
- **≥25:** 292, 33 and 11. **≥100:** 173, 15 and 5.
- The mean absolute difference in `log1p` fatalities is 0.35 per country-month.
- Keeping only `Clear` Candidate events reverses the bias. For ≥1 the split becomes 443 in both, 29 Candidate-only and 55 final-only.

In practice, the panel's 2026 months and the live scoring targets are preliminary. They will tend to show more months with at least one fatality, and fewer deaths, than the final release will. Several low-violence countries, including the United States and France, have 2026 Candidate months with 1–4 recorded deaths, mostly flagged for checking.

## ViEWS benchmark

ViEWS publishes monthly `fatalities00N_YYYY_MM_tKK` runs. Run `YYYY_MM` uses data through that month, and step `s` targets month `YYYY-MM + s`, which is the same convention as this panel. Its country-month forecasts cover state-based violence only. They give `main_mean_ln`, a point prediction of `ln(sb + 1)` (`sc_cm_sb_main` in `fatalities001`), and `main_dich`, the probability of at least 25 battle-related deaths (`sc_cm_sb_dich_main`). For a like-for-like comparison, the baseline is re-run on the **sb series** at the ViEWS origins and both are scored on identical rows.

- **Runs:** 56, one per origin from 2021-12 to 2026-07. They comprise `fatalities001` for 2021-12 to 2023-03, `fatalities002` for 2023-04 to 2025-09 and `fatalities003` for 2025-10 to 2026-07. Where two runs exist for one origin, the newest model family and then the latest attempt is used. The runs left out are `fatalities002_2023_09_t01`, `2023_10_t01` and `2025_10_t01`. Runs with month `00` are ignored.
- **Rows:** the countries in this panel that are also in ViEWS (all 130), at horizons 1, 3, 6 and 12 where the target month is at or before 2026-08. ViEWS countries with no UCDP event ever are left out. Their outcomes are all zero.
- **Metrics:** MSE on `log1p(sb)`, comparing the baseline's predictive mean with `main_mean_ln`, and the Brier score for sb ≥ 25. Each is scored against two outcome definitions: this panel's aggregation and ViEWS's own aggregation of UCDP, which assigns events to countries differently in about 4% of country-months.

**Primary comparison: as-of data (vintage-matched).** For each origin `t`, the panel is rebuilt only from UCDP files whose `Last-Modified` date is on or before the last day of month `t + 1`. That is the earliest time a forecast using month `t`'s Candidate file could be made. For example, origin 2024-01 uses GED 23.1, the 2023 Candidate file and Candidate 24.0.1. UCDP's files for June to September 2022 carry a `Last-Modified` date of 2022-12-20, so origins 2022-06 to 2022-10 are skipped rather than given guessed dates. This leaves 51 origins and 24,180 matched rows.

| Metric (24,180 rows) | Baseline | ViEWS | Baseline improvement [95% CI] |
| --- | --- | --- | --- |
| MSE `log1p(sb)`, panel outcomes | 0.602 | 0.664 | 9.4% [−0.3%, 18.6%] |
| Brier sb ≥ 25, panel outcomes | 0.0362 | 0.0381 | 5.0% [−1.7%, 9.9%] |
| MSE `log1p(sb)`, ViEWS outcomes | 0.602 | 0.662 | 8.9% [−0.8%, 18.3%] |
| Brier sb ≥ 25, ViEWS outcomes | 0.0355 | 0.0372 | 4.7% [−2.4%, 9.6%] |

By horizon, against panel outcomes, the MSE improvement is 5.8%, 7.5%, 13.2% and 10.0% at `h` = 1, 3, 6 and 12. The Brier improvement is 7.1% [1.1%, 11.9%] at `h = 1` and 3–5% with intervals spanning zero at longer horizons. For reference, climatology's MSE is 2.22 and persistence's is 0.76.

**On the as-of basis, the simple baseline is at least on par with ViEWS on these metrics. Its point estimates are slightly better, but only the `h = 1` Brier difference has an interval that excludes zero.**

**Secondary comparison: revised data.** Feeding the baseline today's GED 26.1 history instead gives an MSE of 0.563 against 0.671 for ViEWS, a 16.1% improvement [6.5%, 25.4%]. The Brier improvement is 5.9% [−1.5%, 11.4%]. On the same 24,180 rows the baseline's own MSE is 0.555 with revised data and 0.602 with as-of data, and its ≥1 Brier is 0.0409 against 0.0469. **Revised data therefore improve this baseline by about 8% on MSE.** This is the vintage effect that also makes the main 2015–2026 backtest somewhat optimistic.

**Caveats.**

- The exact data ViEWS used for each run are unknown. The as-of rule is an approximation based on UCDP publication dates.
- ViEWS may have run before or after the cutoff.
- The API is assumed to serve forecasts as originally published.
- `fatalities003` probabilities are rounded to four decimals, and many are exactly 0 or 1.
- Outcomes for 2026 are preliminary Candidate data for both forecasters.
- Only the sb series and these two metrics are compared.

Details are in `artifacts/conflict_panel_v1/views_comparison.json`.

## Live panel (origin 2026-08)

`artifacts/conflict_panel_v1/live_forecast_2026-08.json` and its `.csv` hold every country at horizons 1, 3, 6 and 12. Each row has P(≥1), P(≥25) and P(≥100), climatology, count quantiles (5, 25, 50, 75 and 95%) and the stored count-distribution parameters. The file also records data vintage (releases and SHA-256), code identity (Git commit, dirty flag, SHA-256 of the conflict package), configuration and training sizes.

Next-month (2026-09) P(≥25), top 10: Ukraine 0.993, Nigeria 0.982, Sudan 0.980, Yemen 0.972, Pakistan 0.966, Ethiopia 0.955, DR Congo 0.940, Mexico 0.938, Israel 0.931, Somalia 0.928.

The top risers are measured as the change in next-month P(≥25) against the forecast from data through July 2026: Afghanistan 0.51 → 0.72, Chad 0.22 → 0.36, South Africa 0.26 → 0.37, South Sudan 0.69 → 0.78 and Yemen 0.89 → 0.97.

| France, target month | P(≥1) | P(≥25) | P(≥100) | Climatology P(≥1) / P(≥25) | Median / 95th pct deaths |
| --- | --- | --- | --- | --- | --- |
| 2026-09 (h = 1) | 0.183 | 0.0021 | 0.00018 | 0.056 / 0.0055 | 0 / 3.7 |
| 2026-11 (h = 3) | 0.167 | 0.0026 | 0.00036 | 0.056 / 0.0055 | 0 / 3.8 |
| 2027-02 (h = 6) | 0.170 | 0.0036 | 0.00053 | 0.056 / 0.0055 | 0 / 4.1 |
| 2027-08 (h = 12) | 0.187 | 0.0064 | 0.0012 | 0.056 / 0.0055 | 0 / 5.2 |

France's P(≥1) is about three times climatology because of the one fatality recorded in the August 2026 Candidate file. These are statistical baselines, not assessments.

**Scoring.** UCDP releases Candidate 26.0.9 (September 2026) around 20 October 2026. When it is out, `scripts/conflict_panel.py score --fetch` downloads it, rebuilds the panel and calls `score_live_forecasts`. That function scores every stored forecast whose target month is now covered, using the backtest's scoring functions, and writes `live_score_<origin>_data_<month>.json`. Target months not yet covered are listed as pending. Scores based on Candidate data can change when the final GED is published.

## Reproduction

```bash
uv sync --extra dev --frozen          # or --extra conflict for numpy without dev tools
P="uv run --frozen --no-sync python scripts/conflict_panel.py"
$P fetch      # discover the latest UCDP files, download, hash, build the panel
$P backtest   # about 1 minute; artifacts/conflict_panel_v1/backtest_summary.json
$P views      # ViEWS API and historical UCDP releases (about 400 MB the first time)
$P forecast   # live_forecast_<origin>.json and .csv
$P score --fetch
```

`--offline` uses only cached files. Tests use small fixtures and mocked HTTP and need no network: `tests/test_conflict_data.py`, `tests/test_conflict_models.py` and `tests/test_conflict_backtest.py`.

## Limitations

- UCDP covers only organized violence that causes at least one recorded death. Protests, riots, strikes, unrest without deaths and violence that UCDP cannot attribute to an organized actor are absent. For France and other low-violence countries, most politically relevant unrest is therefore invisible to this panel.
- Data revisions: backtests use the latest vintage, and the vintage effect is about 8% of MSE on 2021–2026 origins. Recent months come from Candidate data, which tend to show more months with at least one fatality and fewer deaths than the final release (see the vintage check).
- Granularity is country-month. Events are assigned to the month of `date_start`, and multi-month events are not split. There is no subnational detail.
- The country list is the GED country list. Countries with no recorded event since 1989 are not forecast.
- Calibration: P(≥25) and P(≥100) are underconfident between about 0.4 and 0.9. The 90% count intervals are slightly too wide.
- These are statistical baselines only. There are no AI-analyst adjustments, no covariates beyond fatality history, and no structural or economic data.
- The ViEWS comparison uses an approximate as-of rule, covers only the sb series and two metrics, and on vintage-matched data most differences are not statistically clear.

## Next phases

1. **AI-analyst adjustments:** bounded, logged adjustments to the baseline probabilities from evidence-based country analysis, scored against this baseline on the same panel.
2. **ACLED protests and riots**, with a licence review, so that unrest without UCDP-recorded deaths becomes observable. The UCDP Violent Political Protest dataset (2011–2025) is another candidate source.
3. **A France long-term signpost dashboard** that links the monthly panel, protest and riot indicators and long-horizon questions, with each signpost resolved from observable data.

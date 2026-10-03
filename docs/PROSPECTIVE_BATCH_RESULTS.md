# Prospective batch results

Live prospective batches run so far, their operational results, and how they are scored. No outcome-based score exists yet for these batches; nothing here is an accuracy or calibration claim.

Both batches ran in ephemeral local instances, whose databases were discarded. Their exports under `artifacts/prospective_*` are the durable record:

- `cohort_report.json`: per-question cells with status, probability, cost and latency.
- `frozen_manifest.json`: the frozen manifest, including contracts, cutoffs, profiles and the source hash.
- `runs.json`: per-run status, error and cost.

## Batches

### `prospective_macro_batch_20261002`

- **Questions:** 10 BLS questions across 3 release events.
  - CPI for 2026-09 (released 2026-10-14) above 3.3, 3.4 and 3.5%.
  - Employment for 2026-10 (released 2026-11-06): unemployment above 4.1, 4.2 and 4.3%; payroll change above 29,000 and 100,000.
  - Employment for 2026-11 (released 2026-12-04): unemployment above 4.2%; payroll change above 29,000.
- **Selection rule:** thresholds are the latest published value plus or minus one step, fixed before execution.
- **Run:** commit `18caea5`, OpenAI `gpt-5-mini` with Tavily. Manifest `c38a7b02…`. Estimated cost $7.72.

| Method | Forecasts | Withheld | Failed | Notes |
| --- | ---: | ---: | ---: | --- |
| `root_event_ensemble_v1` | 4 | 6 | 0 | All withholdings were `missing_resolution_evidence`, because cohort freezes did not attach the verified official schedule that Autopilot uses. Fixed in #25 for later cohorts. |
| `single_model_forecaster_v1` | 9 | 0 | 1 | Threshold forecasts are not monotone: CPI above 3.3% got 65% while above 3.4% got 70%, and payrolls above 29,000 got 45% while above 100,000 got 65%. |
| `three_track_strict_forecaster_v1` | 4 | 0 | 6 | All failures were `invalid_structured_output:research_plan` (revision 3). Fixed in revision 4 (#24). |

### `prospective_fast_fred_batch_20261003`

- **Questions:** 7 fast questions, chosen by the fixed `fast_fred_question_selection_v1` rule.
  - Initial jobless claims for the weeks ending 2026-10-03 and 2026-10-10 above 197,000.
  - The 10-year Treasury yield on 2026-10-05 through 2026-10-09 above 5.24%.
- **Run:** commit `b6b273d`. Estimated cost $5.16.

| Method | Forecasts | Withheld | Failed | Notes |
| --- | ---: | ---: | ---: | --- |
| `root_event_ensemble_v1` | 1 | 6 | 0 | No verified schedule evidence exists for FRED series, so the method withholds unless research supplies resolution evidence. |
| `single_model_forecaster_v1` | 7 | 0 | 0 | Day-to-day estimates for the same yield threshold range from 45% to 80%. |
| `three_track_strict_forecaster_v1` | 6 | 0 | 1 | Estimates range from 40.9% to 53%. |

## Scoring

`python scripts/score_prospective_artifacts.py` resolves every exported entry and writes `scores.json` next to each artifact. Questions that have not been published stay `pending`.

- **FRED questions** use the in-app first-vintage rule, `official_fred_outcomes.find_initial_release`.
- **Monthly BLS questions** are reconstructed from the ALFRED vintage dated on the scheduled release day. The value must be present in that vintage and absent from the previous day's vintage.
  - Unemployment uses `UNRATE` as published.
  - Payrolls use the `PAYEMS` level minus the revised prior month, in jobs.
  - CPI uses the `CPIAUCNS` 12-month change, rounded half-up to one decimal.

  These are reconstructions of the BLS headline figures, not parsed news-release documents. For example, the September 2026 unemployment rate reconstructs to 4.2% from the 2026-10-02 vintage.
- **Scores** are Brier score and log loss per method, alongside the 0.25 Brier of an uninformed 50% forecast.

The sample is small and the questions are correlated: several share a release event, and the thresholds are nested. Report coverage, withholdings and failures together with any score.

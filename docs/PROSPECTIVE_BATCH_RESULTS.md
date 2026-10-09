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
| `root_event_ensemble_v1` | 1 | 6 | 0 | No verified schedule evidence exists for FRED series, so the method withholds unless research supplies resolution evidence. Later cohorts attach the official DOL or H.15 publication document at freeze ([details](FAST_RESOLVING_QUESTIONS.md#resolution-evidence-at-cohort-freeze)). |
| `single_model_forecaster_v1` | 7 | 0 | 0 | Day-to-day estimates for the same yield threshold range from 45% to 80%. |
| `three_track_strict_forecaster_v1` | 6 | 0 | 1 | Estimates range from 40.9% to 53%. |

## Fast batch results (scored 2026-10-09)

Four of the seven fast questions have resolved from the first FRED vintage (ALFRED). The yield questions for Oct 8 and Oct 9 and the claims question for the week ending Oct 10 are still pending.

| Question | Actual | Outcome | root ensemble | single model | strict three-track | statistical baseline | Claude Code |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: |
| Claims, week ending Oct 3, above 197,000 | 197,000 | No | withheld | 43% | 42% | 44% | 50% |
| 10-year yield Oct 5 above 5.24% | 5.31% | Yes | withheld | 45% | 41% | 50% | 68% |
| 10-year yield Oct 6 above 5.24% | 5.27% | Yes | withheld | 55% | 53% | 52% | 64% |
| 10-year yield Oct 7 above 5.24% | 5.28% | Yes | 50% | 55% | 46% | 53% | 61% |
| **Mean Brier (lower is better; 0.25 = coin flip)** | | | 0.245 (1 forecast) | 0.223 | 0.259 | 0.223 | 0.159 |

- Claude Code was closest on all three yield days because it started from Treasury's official 5.28% for Oct 2, already above the line. It was the furthest on claims, where it said 50% and the number landed exactly on 197,000, which does not count as "above".
- The strict three-track method did worse than a coin flip, mainly by leaning below 50% on the yield days.
- This proves little. It is four questions, three of them the same yield threshold on consecutive days, so they mostly count as one bet. Claude's forecasts were also made after the cohort froze (about 14 hours later), so it saw slightly more news than the frozen methods.

## Statistical baseline supplement

Both batches were frozen before `statistical_baseline_v1` existed. `scripts/baseline_supplement.py` computed the deterministic baseline for every entry on 2026-10-03, after the freezes but before any release. It used the same latest observations the cohorts saw: DGS10 through 2026-10-01, ICSA through the week ending 2026-09-26, unemployment and payrolls through 2026-09, and CPI through 2026-08. The results are in `baseline_supplement.json` in each artifact directory. Each file records the snapshot hash and last observation.

The scorer reports these forecasts as a separate method, `statistical_baseline_v1 (post-freeze supplement)`. They are not part of the frozen manifests.

| Batch | Question | Baseline probability | 80% range of the published value |
| --- | --- | ---: | --- |
| `prospective_macro_batch_20261002` | unemployment 2026-10 > 4.1 | 52.6% | 3.9 to 4.3 |
| `prospective_macro_batch_20261002` | cpi 2026-09 > 3.3 | 57.4% | 2.9 to 3.9 |
| `prospective_macro_batch_20261002` | cpi 2026-09 > 3.5 | 35.8% | 2.9 to 3.9 |
| `prospective_macro_batch_20261002` | unemployment 2026-11 > 4.2 | 25.9% | 3.7 to 4.4 |
| `prospective_macro_batch_20261002` | unemployment 2026-10 > 4.2 | 25.7% | 3.9 to 4.3 |
| `prospective_macro_batch_20261002` | unemployment 2026-10 > 4.3 | 9.1% | 3.9 to 4.3 |
| `prospective_macro_batch_20261002` | payrolls 2026-10 > 100,000 | 32.9% | -167000 to 244000 |
| `prospective_macro_batch_20261002` | payrolls 2026-10 > 29,000 | 53.1% | -167000 to 244000 |
| `prospective_macro_batch_20261002` | cpi 2026-09 > 3.4 | 47.5% | 2.9 to 3.9 |
| `prospective_macro_batch_20261002` | payrolls 2026-11 > 29,000 | 52.7% | -160000 to 218000 |
| `prospective_fast_fred_batch_20261003` | treasury_10y 2026-10-09 > 5.24 | 53.0% | 5.12 to 5.39 |
| `prospective_fast_fred_batch_20261003` | treasury_10y 2026-10-06 > 5.24 | 52.4% | 5.15 to 5.35 |
| `prospective_fast_fred_batch_20261003` | jobless_claims 2026-10-10 > 197,000 | 45.5% | 183000 to 214000 |
| `prospective_fast_fred_batch_20261003` | treasury_10y 2026-10-08 > 5.24 | 53.3% | 5.13 to 5.37 |
| `prospective_fast_fred_batch_20261003` | jobless_claims 2026-10-03 > 197,000 | 43.9% | 185000 to 210000 |
| `prospective_fast_fred_batch_20261003` | treasury_10y 2026-10-05 > 5.24 | 49.8% | 5.16 to 5.33 |
| `prospective_fast_fred_batch_20261003` | treasury_10y 2026-10-07 > 5.24 | 53.0% | 5.13 to 5.37 |

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

## GPU questions (general binary, 2026-10-03)

Five binary candidates from `docs/research/gpu_scouting_questions_2026-10.json` ran through the three AI methods on 2026-10-03, each against the same approved contract. They are not a frozen cohort and the statistical baseline does not apply to them. The record is in `artifacts/prospective_gpu_questions_20261003/forecasts.json`, with outcomes still to be recorded from each named source after its resolution date. Estimated cost was about $4.30.

| Question | Resolves | root ensemble | single model | strict three-track |
| --- | --- | --- | ---: | ---: |
| USTR extends the Section 301 exclusion for graphics cards beyond 2026-11-10 | 2026-11-10 | withheld | 28% | 20% |
| A Moore Threads Lushan card is on retail sale | 2026-12-31 | withheld | 25% | 25% |
| NVIDIA announces an RTX 50 Super card with a US price | 2027-01-31 | withheld | 20% | 7% |
| NVIDIA raises an official RTX 50 US MSRP | 2027-03-31 | withheld | 10% | 12% |
| Sony, Microsoft or Nintendo raises a named console's US MSRP again | 2027-06-30 | withheld | 12% | 7% |

The root method withheld all five because its evidence gate did not find primary sources for the resolution, reference-class or current-conditions sections. On general questions about company and policy announcements, it currently produces no forecast.

The USTR estimates are open to question. USTR has extended these exclusions several times before, so the historical base rate looks higher than 20–28%. The resolution will show whether the AI methods underweighted that history.

## Claude Code forecaster (2026-10-03)

`claude_code_forecaster_v1` is Claude working in a Claude Code session with web search, at no API cost. It forecast all 22 open questions at 2026-10-03T19:26:53Z, before any of them resolved. Each forecast has its rationale and dated sources in `claude_code_supplement.json` for the two macro batches, and in `forecasts.json` for the GPU questions. The scorer treats every `*_supplement.json` as a separate, labeled post-freeze method.

Where it differs most from the other methods:

- **September CPI:** Claude forecasts 96%, 91% and 74% for thresholds of 3.3, 3.4 and 3.5%. The statistical baseline gives 57%, 48% and 36%. Claude cites the Cleveland Fed nowcast of 3.60% and a 3.7% consensus.
- **10-year yield, Oct 5–9:** 58–68% for above 5.24, against about 50–53% from the baseline. Claude cites Treasury's official 5.28 for Oct 2.
- **The USTR graphics-card exclusion:** 85%, against 20–28% from the other AI methods. Claude cites the US–China truce extension to 2027-01-10 and USTR's record of extending these exclusions before the deadline.

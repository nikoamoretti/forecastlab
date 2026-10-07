# Track record

The app's home page answers three questions: which forecasts have been decided, whether each call was right, and how good the forecasting is overall.

## Where the data comes from

`scripts/build_track_record.py` reads every `artifacts/prospective_*` directory and writes `data/track_record.json`. `scripts/score_prospective_artifacts.py` runs it after scoring, so the daily run refreshes it. `GET /api/track-record` serves the file, because the API deployment does not include `artifacts/`.

Inputs:

- Cohorts with `frozen_manifest.json` and `scores.json`. These are the macro and fast batches, the daily sets, and `prospective_autopilot_pilot_202609`.
- `forecasts.json` files for general questions, such as the GPU set. These stay open until an outcome is recorded in the file.

`prospective_autopilot_pilot_202609` holds the first three Autopilot forecasts, made on 2026-09-05 in the live database. They were exported unchanged on 2026-10-07 so that they are scored with the same first-release rules. Pipeline smoke tests, drafts and failed runs are not part of the record. They remain under **All runs**.

## Rules

- **Our call.** A question's call is the forecast of the first forecaster in this order that answered it: Claude, research pipeline, single AI model, three-track AI, statistical baseline.
- **Right or wrong.** Probability above 50% means we called yes; below 50% means we called no. A call is right when that side happened. Exactly 50% is a toss-up: it counts in the accuracy score but not in the right/wrong tally.
- **Accuracy score.** This is the Brier score: the mean squared difference between the probability and the outcome, 0 to 1, lower is better. Always answering 50% scores 0.25. The page says "better than guessing" when the score is at least 10% below 0.25, and "worse than guessing" when it is at least 10% above.
- **Cancelled.** A 10-year-yield question on a U.S. bond-market holiday is cancelled, because no value is published. It never counts.
- **Final results.** A resolved first-release value is never re-fetched. A later source error therefore cannot reopen a decided question.
- **Sample size.** Each forecaster's row only counts questions that forecaster answered. Below 30 results the page says that luck dominates.

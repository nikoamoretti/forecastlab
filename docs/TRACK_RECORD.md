# Track record

The app's home page answers three questions: which forecasts have been decided, whether each call was right, and how good the forecasting is overall.

## Where the data comes from

`scripts/build_track_record.py` reads every `artifacts/prospective_*` directory and writes `data/track_record.json`. `scripts/score_prospective_artifacts.py` runs it after scoring, so the daily run refreshes it. `GET /api/track-record` serves the file, because the API deployment does not include `artifacts/`.

Inputs:

- Cohorts with `frozen_manifest.json` and `scores.json`. These are the macro and fast batches, the daily sets, and `prospective_autopilot_pilot_202609`.
- `forecasts.json` files for general questions:
  - The GPU set.
  - `prospective_requests_<date>`: questions the owner asked on **Ask a question**, answered by Claude in the daily run. Their ids match the request ids, so an answered request links to `/q/<id>`.

  These stay open until an outcome is recorded in the file with `scripts/question_requests.py outcome`.
- `prospective_markets_<date>/forecasts.json`: questions borrowed from Polymarket by `scripts/market_questions.py` under `polymarket_selection_v4` (v3 until 2026-10-27, v2 until 2026-10-25, v1 for the 2026-10-07 batch). v3 takes up to 18 a day, closing within 10 days. v2 also skips "next prime minister/president" markets, which settle only when someone takes office. v4 also skips markets whose rules allow settling more than 30 days after trading closes (for example an election that may go to a runoff). Polymarket decides them. `scripts/score_prospective_artifacts.py` records the result once the market is closed, its UMA resolution is final, and one side pays 1. A 50/50 settlement is cancelled.

`prospective_autopilot_pilot_202609` holds the first three Autopilot forecasts, made on 2026-09-05 in the live database. They were exported unchanged on 2026-10-07 so that they are scored with the same first-release rules. Pipeline smoke tests, drafts and failed runs are not part of the record. They remain under **All runs**.

## Pages

- **`/` Track record.** The headline score and the market comparison, progress toward 30 results, the next result, the latest results and the open questions, five at a time. Every row opens the question page.
- **`/q/<id>` Question page.** Our call and what happened side by side, Claude's confidence-corrected probability when one was frozen ("Claude said 72%. Corrected for its track record: 78%."), why we made the call (the forecaster's own rationale and dated sources), what each forecaster said, and how the question is decided.
- **`/ask` Ask a question.** Owner questions wait in `app_settings` rows keyed `question_request:<id>` (`GET/POST /api/question-requests`, `DELETE` to withdraw, `POST …/answered`) until the daily run answers them. The paid research pipeline remains at `/new`.

## Rules

- **Our call.** A question's call is the combined forecast: the median of every forecaster's probability on it (`combined_median_v1`), leaving out the market benchmark. With one forecaster it is that forecaster's probability. This rule replaced "Claude first, then the research pipeline, single AI model, three-track AI, statistical baseline" on 2026-10-07, after four questions had been decided. Under the old rule they scored 3 of 4; under the median they score 2 of 4, because on the 2026-10-05 10-year yield question Claude said yes at 68% but the other three forecasters leaned no. The median was adopted anyway, because one rule for every question beats trusting a single forecaster, and four results cannot tell the two rules apart. Every forecaster keeps its own row, so the page shows whether the combination beats its members. Weighting forecasters by their records waits for about 30 results and a rule written down before it is applied.
- **Right or wrong.** Probability above 50% means we called yes; below 50% means we called no. A call is right when that side happened. Exactly 50% is a toss-up: it counts in the accuracy score but not in the right/wrong tally.
- **Headline score.** The home page's main number is one score with its coverage: "Score 0.20 on all 56 decided questions". It is the Brier score of **our call** over every decided question that counts (`summary.brier` over `summary.resolved`; cancelled and unscored questions are out). Lower is better; always saying 50% scores 0.25. The "35 of 55 calls right" line is secondary; a toss-up (exactly 50%) is in the score but not in the right/wrong count, which is why it is 55 there and 56 in the score.
- **Versus the market.** Directly under it: "On the 37 questions the market also priced: us 0.17, market 0.15." This is the fair comparison. It uses the same forecaster as the headline (our call, not Claude alone) on only the decided questions that carry a market price (Polymarket's, else Manifold's), so both sides are scored on identical questions. It is `summary.vs_market` = `{questions, ours, market}`, so the website and any other reader share one source. Scores on different sets of questions, such as a forecaster's own row, are not comparable with it, and each row says how many questions it covers.
- **Accuracy score.** This is the Brier score: the mean squared difference between the probability and the outcome, 0 to 1, lower is better. Always answering 50% scores 0.25. The page says "better than guessing" when the score is at least 10% below 0.25, and "worse than guessing" when it is at least 10% above.
- **Judge panel.** Cheap open-weights models forecast from Claude's evidence brief ([JUDGE_PANEL.md](JUDGE_PANEL.md)). Each one is a forecaster in the median, with its own row.
- **Integrity notes.** A question's `integrity_note` discloses anything that could have compromised a forecast, for example market odds seen in a search result while researching. It is shown on the question's page, written before the outcome was known, and the forecast still counts.
- **Market benchmark.** On a Polymarket question, the market's price when the question was selected is shown as its own forecaster, "Prediction market (benchmark)" (`market_price_v1`). It is never part of our call. Our forecasters do not look at it. Beating it is the hardest test the record has.
- **Confidence correction (`claude_calibrated_v1`, "Claude, confidence-corrected").** Claude's probability adjusted by how its past calls at each confidence level turned out (Platt scaling). Shown for comparison; it is scored on the record like a benchmark and is never part of our call or of any forecaster's weight. Rules:
  - The fit is a logistic regression of the outcome on logit(Claude's probability), over Claude's own decided, scored forecasts on questions whose result date is **strictly before the forecast day**. A result from the forecast day or later is never used.
  - A prior pulls (slope, intercept) toward (1, 0), "Claude's stated probabilities are right", as if by 20 extra observations (spread evenly from 5% to 95%) that agree with Claude. With few results the correction is mild; with many it follows the data.
  - No corrected forecast until at least 30 such results exist. The corrected probability is kept within 2% to 98%.
  - It starts on 2026-10-31. A question forecast earlier never gets one, so no value is made with hindsight.
  - It is computed once, the first time the question is built after Claude's forecast exists, and frozen in the directory's `calibrated.json` (`{question id: {probability, raw, slope, intercept, results, as_of}}`). Later results never recompute it. The record also carries `calibration`: the fit as of the build date (slope, intercept, results), for reading only.
- **Cancelled.** A 10-year-yield question on a U.S. bond-market holiday is cancelled, because no value is published. It never counts.
- **Final results.** A resolved first-release value is never re-fetched. A later source error therefore cannot reopen a decided question.
- **Sample size.** Each forecaster's row only counts questions that forecaster answered. Below 30 results the page says that luck dominates.

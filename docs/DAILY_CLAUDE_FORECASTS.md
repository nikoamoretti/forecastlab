# Daily Claude forecasts

A scheduled Claude Code routine adds zero-cost forecasts every weekday. The research runs in the owner's Claude Code session. The only metered calls are the judge panel's, capped at $1 a day and currently made to free models ([JUDGE_PANEL.md](JUDGE_PANEL.md)), and data comes from keyless official sources and Polymarket's public API.

## What one run does

1. **Merge yesterday's run.** Merge any open `claude/daily-forecasts-*` pull request whose CI is green.
2. **Prepare questions.** Run `python scripts/daily_forecasts.py prepare`. It applies the current fast selection rule, `fast_fred_question_selection_v3` (at most one jobless claims question and one week-ahead 10-year yield question, thresholds at the latest observed value, none for an indicator that still has an unreleased question) and skips targets already in any artifact. It writes `artifacts/prospective_daily_<date>/` with the statistical baseline's forecasts and `questions.json`. If there is nothing new, skip to step 4.
3. **Forecast.** Claude forecasts every question in `questions.json`.
   - Start with the base rate: the baseline probability and the recent history.
   - Then look for news: consensus forecasts, recent releases, market moves.
   - Use web search, but only information published before now.
   - Keep probabilities coherent across nested thresholds and within [0.02, 0.98].
   - Write a 2–4 sentence rationale for each forecast, with dated source URLs.
4. **Answer the owner's questions.** The owner asks questions on the app's **Ask a question** page. With `FORECASTLAB_PASSPHRASE` set to the owner passphrase, `python scripts/question_requests.py waiting` lists the waiting ones.
   - For each, write a precise resolution rule: what counts as yes, the resolution date, and the source that will decide it. Then forecast it as in step 3, with a short topic such as "Tech" or "Interest rates".
   - Save the answers as `{"forecast_made_at", "answers": [{"id", "question", "topic", "resolution_criteria", "resolution_date", "resolution_source", "probability", "rationale", "sources"}]}` and run `python scripts/question_requests.py record <file>`. The ids must be the request ids.
   - After the pull request is pushed, run `python scripts/question_requests.py answered <id> ...`.
   - For an asked question whose resolution date has passed, check the named source and run `python scripts/question_requests.py outcome <artifact_dir> <id> yes|no --note "<what happened>" --source <url>`.
5. **Forecast market questions.** Run `python scripts/market_questions.py prepare`. It applies `polymarket_selection_v2` and writes `artifacts/prospective_markets_<date>/`, with up to 8 Polymarket questions:
   - closing in 2–21 days, and not a "next prime minister/president" market, which settles only when someone takes office;
   - at least $10,000 traded;
   - priced between 5% and 95%;
   - one per event and at most two per topic.

   It also adds up to 4 Manifold questions (`manifold_selection_v1`) from the economics, politics, technology and world topics, under the same window, price band and topic cap. Manifold trades play money, so a market needs at least 15 traders and 1,000 mana traded, and personal markets ("Will I …") are skipped. Its price is a separate benchmark, `manifold_price_v1`, and is not part of our call either. If Manifold can't be reached, the Polymarket questions still run.

   Forecast `questions.json` as in step 3, reading each question's `resolution_criteria` closely.
   - **Never look up the question's market odds:** not Polymarket, Kalshi, Manifold or any betting site. The market price is the benchmark we try to beat, so it must not leak into our forecast.
   - `market_snapshot.json` holds that price. Don't open it before recording.
   - Search summaries sometimes quote odds anyway. Add `-polymarket -kalshi -manifold -odds` to searches about a market question. If odds still appear, don't use them, and disclose it in that forecast's `integrity_note`. The note is shown on the question's page.
   - Save `{"forecast_made_at", "forecasts": [{"id", "probability", "rationale", "sources"}]}`. Then run `python scripts/market_questions.py record <dir> <file>`.
6. **Record the step 3 forecasts.** Run `python scripts/daily_forecasts.py record <dir> <forecasts.json>`. It refuses forecasts made at or after an entry's cutoff, extreme probabilities, and any mismatch with the entries.
7. **Judge panel.** Write `briefs.json` in each directory that got new questions today: the daily set, the market set and the asked set.
   - The format is `{"written_at", "briefs": {"<question id>": "<brief>"}}`.
   - A brief is 4–8 sentences of the evidence found in steps 3–5: dated facts, the base rate and recent history, and source URLs.
   - It contains no probability, no lean and no market odds.
   - Then load `OPENROUTER_API_KEY` without printing it and run `python scripts/judge_panel.py run <dir> ...`.
   - Failed or rate-limited members are logged and do not stop the run. See [JUDGE_PANEL.md](JUDGE_PANEL.md).
8. **Score.** Run `python scripts/score_prospective_artifacts.py`. It rescores every artifact against first releases, records final Polymarket results for market questions, and rebuilds `data/track_record.json`, the data behind the app's Track record page. Commit that file with the artifacts.
9. **Commit.** Commit to `claude/daily-forecasts-<date>` and open a pull request. The pull request's server timestamp shows the forecasts existed before the releases. The next run merges it once CI is green.
10. **Notify.** Send the owner a short, verdict-first note only when something resolved or a run failed.

## Honesty rules

- Never use information published after the forecast time.
- Never edit a recorded forecast. A later view goes in a new day's artifact.
- Holidays without a published value resolve as cancelled, not as yes or no. So does a Polymarket market settled 50/50.
- Market questions are forecast without looking at any market's odds.
- Judges see only the question, its rule and Claude's brief. They never see Claude's probability.
- The scorer reports Claude, the statistical baseline and every earlier method side by side, with the number of scored questions.

## Running from a locked-down container

A cloud session may not reach FRED, Polymarket, Manifold or OpenRouter, may have lost the OpenRouter key when its container was recycled, and cannot dispatch workflows through the Claude GitHub App. The **Daily run steps** workflow (`.github/workflows/daily-run.yml`) runs those steps in GitHub Actions with the repository's secrets and commits each result to `claude/daily-forecasts-<date>`:

- **prepare** (steps 2 and 5) runs on a weekday schedule at 10:05 UTC. It creates the branch from `grok/forecastlab-mvp` and runs both `prepare` commands.
- **finish** (steps 7 and 8) runs the judge panel on the listed directories, scores, and starts CI on the branch, since pushes made by the workflow start no CI.

To run a step, push a commit to the daily branch that adds `daily-run-request.json`, for example `{"step": "finish", "dirs": ["artifacts/prospective_markets_<date>"]}`. The workflow removes the file when it is done. Between steps, `git pull` the branch, forecast, run the `record` commands (they need no network), write `briefs.json`, and push. People can also start any step, or `check-key`, from the Actions tab.

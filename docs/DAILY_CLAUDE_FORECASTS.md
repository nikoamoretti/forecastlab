# Daily Claude forecasts

A scheduled Claude Code routine adds zero-cost forecasts every weekday. No paid API is called. The model work runs in the owner's Claude Code session, and data comes from keyless official sources.

## What one run does

1. **Merge yesterday's run.** Merge any open `claude/daily-forecasts-*` pull request whose CI is green.
2. **Prepare questions.** Run `python scripts/daily_forecasts.py prepare`. It applies the current fast selection rule, `fast_fred_question_selection_v2` (jobless claims and the 10-year yield, thresholds at the latest observed value, skipping U.S. bond-market holidays) and skips targets already in any artifact. It writes `artifacts/prospective_daily_<date>/` with the statistical baseline's forecasts and `questions.json`. If there is nothing new, skip to step 4.
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
5. **Record.** Run `python scripts/daily_forecasts.py record <dir> <forecasts.json>`. It refuses forecasts made at or after an entry's cutoff, extreme probabilities, and any mismatch with the entries.
6. **Score.** Run `python scripts/score_prospective_artifacts.py`, which rescores every artifact against first releases and rebuilds `data/track_record.json`, the data behind the app's Track record page. Commit that file with the artifacts.
7. **Commit.** Commit to `claude/daily-forecasts-<date>` and open a pull request. The pull request's server timestamp shows the forecasts existed before the releases. The next run merges it once CI is green.
8. **Notify.** Send the owner a short, verdict-first note only when something resolved or a run failed.

## Honesty rules

- Never use information published after the forecast time.
- Never edit a recorded forecast. A later view goes in a new day's artifact.
- Holidays without a published value resolve as cancelled, not as yes or no.
- The scorer reports Claude, the statistical baseline and every earlier method side by side, with the number of scored questions.

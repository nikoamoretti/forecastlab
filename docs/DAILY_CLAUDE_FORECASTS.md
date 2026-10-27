# Daily Claude forecasts

A scheduled Claude Code routine adds forecasts every weekday. The research runs on the owner's Cursor subscription, on his Mac; questions Cursor cannot research (its weekly limit, a timeout) are researched by a separate headless Claude Sonnet run (owner's call, 2026-10-23), never by the forecaster itself. The only metered calls are the judge panel's, capped at $2 a day (about $1.30 a day expected with the larger daily set from 2026-10-26; one free and three paid open-weights models plus Claude Sonnet 5.5, [JUDGE_PANEL.md](JUDGE_PANEL.md)), and data comes from keyless official sources and Polymarket's public API.

## What one run does

1. **Merge yesterday's run.** Merge any open `claude/daily-forecasts-*` pull request whose CI is green.
2. **Prepare questions.** Run `python scripts/daily_forecasts.py prepare`. It applies the current fast selection rule, `fast_fred_question_selection_v3` (at most one jobless claims question and one week-ahead 10-year yield question, thresholds at the latest observed value, none for an indicator that still has an unreleased question) and skips targets already in any artifact. It writes `artifacts/prospective_daily_<date>/` with the statistical baseline's forecasts and `questions.json`. If there is nothing new, skip to step 4.
3. **Read the research, then forecast.** The research is not done in Claude's session and costs no Claude tokens. A LaunchAgent on the owner's Mac (`scripts/mac_research.sh`, see [CURSOR_RESEARCH.md](CURSOR_RESEARCH.md)) runs cursor-agent on a non-Claude model for every new question and pushes `research.json`; the **download** step then fetches every cited page and checks the quotes. Before forecasting any question set:
   - Check that `research.json` is on the daily branch. If it is missing, or lists questions under `failed`, run `python scripts/cursor_research.py fill` (today's branch; `--branch` for another): a headless Claude Sonnet run with only web search and fetch researches every question still without research, pushes `research.json` and asks for **download**. Wait for that run, then pull. If there is still no research by 15:00 UTC, **skip the day**: forecast nothing, run no judges, and say so in one line. The forecaster itself never searches the web.
   - Run `python scripts/cursor_research.py digest <dir> ...` and read each question's evidence: dated facts with their source, the base rate and the open unknowns. Skip any question the digest says has no research (research failed for it), and the same for one where `research.json` lists it under `failed`. Do not forecast it; step 6 records it as skipped.
   - Do not search the web. Forecast from the digest alone.

   Claude then forecasts every researched question in `questions.json`, once per question at high reasoning effort (owner's call, 2026-10-24: reasoning effort is the best-supported accuracy gain in the published AI forecasting results).
   - Start with the base rate in the digest: the reference class and counts, then the baseline probability and recent history, then what the digest says is different this time.
   - Adjust only for evidence in the digest, and prefer sources whose quotes were checked.
   - Keep probabilities coherent across nested thresholds and within [0.02, 0.98].
   - Write a 2–4 sentence rationale for each forecast, with dated source URLs from the digest.
4. **Answer the owner's questions.** The owner asks questions on the app's **Ask a question** page. With `FORECASTLAB_PASSPHRASE` set to the owner passphrase, `python scripts/question_requests.py waiting` lists the waiting ones.
   - For each, write a precise resolution rule: what counts as yes, the resolution date, and the source that will decide it. Then forecast it as in step 3, with a short topic such as "Tech" or "Interest rates".
   - Save the answers as `{"forecast_made_at", "answers": [{"id", "question", "topic", "resolution_criteria", "resolution_date", "resolution_source", "probability", "rationale", "sources"}]}` and run `python scripts/question_requests.py record <file>`. The ids must be the request ids.
   - After the pull request is pushed, run `python scripts/question_requests.py answered <id> ...`.
   - For an asked question whose resolution date has passed, check the named source and run `python scripts/question_requests.py outcome <artifact_dir> <id> yes|no --note "<what happened>" --source <url>`.
5. **Forecast market questions.** Run `python scripts/market_questions.py prepare`. It applies `polymarket_selection_v4` (v3 plus: skip markets whose rules allow settling more than 30 days after trading closes) and writes `artifacts/prospective_markets_<date>/`, with up to 18 Polymarket questions:
   - closing in 2–10 days, so results come in quickly, and not a "next prime minister/president" market, which settles only when someone takes office;
   - at least $10,000 traded;
   - priced between 5% and 95%;
   - one per event and at most four per topic.

   It also adds up to 10 Manifold questions (`manifold_selection_v2`) from the economics, politics, technology and world topics, under the same window, price band and topic cap. Manifold trades play money, so a market needs at least 15 traders and 1,000 mana traded, and personal markets ("Will I …") are skipped. Its price is a separate benchmark, `manifold_price_v1`, and is not part of our call either. If Manifold can't be reached, the Polymarket questions still run.

   Forecast `questions.json` as in step 3, reading each question's `resolution_criteria` closely.
   - **Never look up the question's market odds:** not Polymarket, Kalshi, Manifold or any betting site. The market price is the benchmark we try to beat, so it must not leak into our forecast.
   - `market_snapshot.json` holds that price. Don't open it before recording.
   - The research tasks name no market, and answers that cite a market site or odds are cleaned before they are saved. If odds still appear in the digest, don't use them, and disclose it in that forecast's `integrity_note`. The note is shown on the question's page.
   - Save `{"forecast_made_at", "forecasts": [{"id", "probability", "rationale", "sources"}]}`. Then run `python scripts/market_questions.py record <dir> <file>`. Questions the digest has no research for (or `research.json` lists under `failed`) are left out of the file and passed with `--skip <id>[,<id>...]` or `--skip-failed`; they are printed and listed under `skipped` in `forecasts.json`, and any other uncovered question is still refused.
6. **Record the step 3 forecasts.** Run `python scripts/daily_forecasts.py record <dir> <forecasts.json>`. It refuses forecasts made at or after an entry's cutoff, extreme probabilities, and any mismatch with the entries.
   - Questions skipped in step 3 are passed with `--skip <id>[,<id>...]`, or `--skip-failed` to skip every id under `failed` in `research.json`. They are printed as skipped and listed under `skipped` in the supplement with a reason ("no research" or "research failed or timed out"). Never record through a temporary directory or edit the manifest. A question that is neither forecast nor skipped is still refused, and the scorer and site simply show no Claude forecast for a skipped one.
7. **Judge panel.** Write `briefs.json` in each directory that got new questions today: the daily set, the market set and the asked set.
   - The format is `{"written_at", "briefs": {"<question id>": "<brief>"}}`.
   - A brief is 8–15 sentences built from the digest: the dated facts that decide the question, each with its source; the base rate and how it was worked out; and what would change the answer. Prefer the primary source over a write-up of it.
   - The judges also read excerpts of the `research.json` sources, so a brief need not restate them word for word.
   - It contains no probability, no lean and no market odds.
   - Judges never come from a research model's family (`model_family` and `fallback.model_family` in `configs/research.json`); `judge_panel.py` refuses to run if one does.
   - Then load `OPENROUTER_API_KEY` without printing it and run `python scripts/judge_panel.py run <dir> ...`.
   - Failed or rate-limited members are logged and do not stop the run. See [JUDGE_PANEL.md](JUDGE_PANEL.md).
8. **Score.** Run `python scripts/score_prospective_artifacts.py`. It rescores every artifact against first releases, records final Polymarket results for market questions, and rebuilds `data/track_record.json`, the data behind the app's Track record page. Commit that file with the artifacts.
9. **Commit.** Commit to `claude/daily-forecasts-<date>` and open a pull request. The pull request's server timestamp shows the forecasts existed before the releases. The next run merges it once CI is green.
10. **Notify.** Send the owner a short, verdict-first note only when something resolved or a run failed.

**Learning from results.** `cursor_research.py digest` starts with the forecaster's own record so far: how often it was right at each confidence level, by topic, and its latest misses with what it said. Read it before forecasting and use it to set how sure to be, never to override the evidence. From Oct 24 our call is a weighted median: each forecaster counts by its record on the questions decided by then (Brier score shrunk toward a coin flip's by 10 results). The call is saved in the directory's `calls.json` the first time it is built and never recomputed, so later results cannot rewrite it.

**What each question page shows.** `build_track_record.py` also writes `data/research/<directory>.json`: per question, who researched it and when, the dated facts, base rate, open questions and sources (with whether their quotes were found on the page), the brief the judges read, and every judge call including failures. Downloaded page text and market odds are left out. The question page shows it as "How this forecast was made" and "The research".

**Results every day.** Scoring must not wait for a forecasting day. A separate scheduled check runs every evening (weekends included): it creates `claude/daily-forecasts-<date>-results` from the base, pushes `{"step":"finish","dirs":[]}` so Actions only re-scores, and merges the pull request if anything changed. A Polymarket market counts once it is closed and pays exactly 1 and 0, with no dispute and more than six hours past its end (UMA's challenge window is two hours). The site groups past-due questions under "Waiting for results" using today's date, not the date the record was built.

## Honesty rules

- Never use information published after the forecast time.
- Never edit a recorded forecast. A later view goes in a new day's artifact.
- Holidays without a published value resolve as cancelled, not as yes or no. So does a Polymarket market settled 50/50.
- Market questions are forecast without looking at any market's odds.
- Judges see only the question, its rule, Claude's brief and excerpts of the fetched sources. They never see Claude's probability or a market price.
- The scorer reports Claude, the statistical baseline and every earlier method side by side, with the number of scored questions.

## Running from a locked-down container

A cloud session may not reach FRED, Polymarket, Manifold or OpenRouter, may have lost the OpenRouter key when its container was recycled, and cannot dispatch workflows through the Claude GitHub App. The **Daily run steps** workflow (`.github/workflows/daily-run.yml`) runs those steps in GitHub Actions with the repository's secrets and commits each result to `claude/daily-forecasts-<date>`:

- **prepare** (steps 2 and 5) creates the branch from `grok/forecastlab-mvp` if needed and runs both `prepare` commands. It has no schedule: the Mac research job asks for it, and so does the routine if the Mac has not.
- **download** (step 3) runs `research_download.py` on the listed directories after the Mac job pushes `research.json`: it downloads each cited page, checks the quotes against it and keeps the page text around them.
- **research** is retired (Tavily search through `research_pack.py`); it is kept only for hand runs.
- **conflict-score** runs `scripts/conflict_panel.py score --fetch` (UCDP's host is blocked in locked-down containers) and commits `artifacts/conflict_panel_v1/live_score_*.json`. It needs no secrets and logs a clear message, committing nothing, if UCDP has not published the target month yet.
- **finish** (steps 7 and 8) runs the judge panel on the listed directories, scores, and starts CI on the branch, since pushes made by the workflow start no CI.

To run a step, push a commit to the daily branch that adds `daily-run-request.json`, for example `{"step": "finish", "dirs": ["artifacts/prospective_markets_<date>"]}`. The workflow removes the file when it is done. The order is prepare, research (on the Mac), download, then finish. Between steps, `git pull` the branch, read the digests, forecast, run the `record` commands (they need no network), write `briefs.json`, and push. People can also start any step, or `check-key`, from the Actions tab.

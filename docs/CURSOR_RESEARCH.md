# Cursor research

Research for the daily forecasts is done by `cursor-agent` on the owner's Mac, on his Cursor plan, with a non-Claude model. Claude never searches the web for a forecast: it reads a digest of the evidence and forecasts. This keeps Claude's token use small and the researcher separate from the forecaster and the judges.

## The daily flow (weekdays, UTC)

1. A LaunchAgent (`ai.forecastlab.research`) runs `scripts/mac_research.sh` every 30 minutes. The script updates its clone to `grok/forecastlab-mvp` and runs `scripts/cursor_research.py daily`, which does nothing unless it is a weekday between the `window_utc` hours in `configs/research.json` and today's research has not been pushed.
2. It creates `claude/daily-forecasts-<date>` (with a `-dryrun` suffix while `mode` is `dry_run`) and asks the **Daily run steps** workflow to `prepare` the questions, then waits for it.
3. It runs `cursor-agent -p --trust --force --mode ask --model <model>` once per new question, in an empty temporary folder, `parallel` at a time. The prompt (`forecastlab.cursor_research.prompt`) asks for evidence only: sources with dates and word-for-word quotes, dated facts, a base rate and open unknowns. It carries no market name, link or price, and tells the model never to use prediction markets or odds.
4. Each answer is checked (`validate`): valid JSON, at least 2 sources and 2 facts, no market site, no sentence naming a market or odds. Failed questions are logged under `failed` and skipped; they do not stop the run.
5. It saves `research.json` and pushes it with a `download` request. The workflow runs `scripts/research_download.py`, which downloads each cited page (HTML or PDF), keeps the text around the quotes, and marks each source `verified` (every quote found), `false` (page read, quote missing) or `null` (page not downloadable).
6. The routine reads the evidence with `python scripts/cursor_research.py digest <dir>`, forecasts, writes briefs, and asks for `finish`. Judges read the same excerpts.

## Limits and failure

- **Cost:** none beyond the Cursor plan. Each run uses about 100,000 tokens on Cursor's side and shows as Included. The CLI cannot read the plan's usage meter, so the job keeps its own count in `~/.forecastlab/ledger.json` and stops at `daily_run_cap` runs a day and `weekly_run_cap` a week. Questions are researched in file order, so a capped day researches the first ones.
- **Mac asleep or job failed:** the LaunchAgent catches up when the Mac wakes. If `research.json` has not landed by 15:00 UTC, the routine skips the day: no forecasts, no judge spend.
- **Claude fills the gaps (owner's call, 2026-10-23):** when Cursor fails for a question (its weekly limit, a timeout) or the Mac job never ran, the daily routine runs `scripts/cursor_research.py fill`. It sends the same prompt to a headless `claude -p` run (`fallback` in `configs/research.json`: Sonnet, low effort, only the WebSearch and WebFetch tools, in an empty folder) for every question still without research, checks the answers the same way, records the model in `researched_by`, and pushes with a `download` request. The forecaster is a different run that only reads the digest, and Claude models may not judge.
- **Independence:** `model_family` (and `fallback.model_family`) in `configs/research.json` name the researchers' families. `judge_panel.py` refuses to run when a judge's model id contains it, and the research model is never a forecaster. Change the model and the family together.
- **Dry run:** while `mode` is `dry_run` the branch is `claude/daily-forecasts-<date>-dryrun`, the routine forecasts nothing from it, and no judge money is spent. Set `mode` to `live` to go live. The first three dry-run days (Oct 14 to 16, 2026) researched every question with no failures; 45 to 63 percent of the cited sources had every quote found on the downloaded page, and the rest were either unreachable from Actions or did not contain the quoted sentence. `mode` is `live` from 2026-10-17.

## Setup on the Mac

The job needs `cursor-agent` signed in, `git` with push access to `nikoamoretti/forecastlab`, and Python 3.9+ (`python3`) on the LaunchAgent's `PATH`. The job uses its own clone at `~/.forecastlab/repo` (set `FORECASTLAB_REPO` to change it) and a scratch worktree at `~/.forecastlab/work`.

Hand test of three questions, nothing pushed: `scripts/mac_research.sh --dry-run --branch claude/daily-forecasts-<date>-dryrun`.

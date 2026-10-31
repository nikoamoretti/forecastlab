# Judge panel

Research is the expensive part of forecasting. Reading many pages costs money when a pay-per-use model does it. The judge panel splits the work:

- **Claude researches.** This runs in the owner's Claude Code session, so it costs nothing per question. It first fetches the source documents for each question (`scripts/research_pack.py`, Tavily search with full page text, prediction-market sites and odds removed), then writes a neutral *evidence brief* from them: dated facts, base rates and source URLs.
- **Other models judge.** Each one reads the question, its resolution rule, the brief and excerpts of the fetched sources (up to about 12,000 characters, `judge_prompt_v2`), and states a probability. Since 2026-10-31 the panel is Claude Sonnet 5.5 alone (below).

Each panel member is a forecaster of its own. Its probability goes into the median that makes our call, next to Claude and the statistical baseline. Its own row on the Track record page shows whether it adds anything.

## Members

`configs/judge_panel.json` lists the members (`judge_panel_v6` since 2026-10-31). Each member's label is in `forecastlab_api.track_record.FORECASTERS`.

| Method | Model | Reached through |
| --- | --- | --- |
| `judge_claude_sonnet55_v1` | `claude-sonnet-5-5` | the `claude` command in the routine's Claude Code session (`"transport": "claude_cli"`) |

The Claude judge runs as a headless Claude Code call with no tools, no settings, no MCP servers and no session history, in an empty folder (`forecastlab.judge_panel.claude_cli_post`), so it sees only the system prompt and the question. It bills the owner's Claude account, not OpenRouter. A smoke test on 2026-10-31 took 8 seconds and reported $0.06 for one question; later calls in a run reuse the cached system prompt and cost less. `usd_per_mtok` (2 / 10) bounds each call's worst case for the daily cap, and the cost Claude Code reports is what is charged. `parallel` (4) is how many questions it answers at once. It keeps the method of the OpenRouter-run Claude Sonnet 5.5 judge it replaces, so its record carries on: same model, same prompt, same brief and excerpts. `reasoning_max_tokens` (3000) caps each member's thinking inside `max_tokens` (6000): before it, reasoning models often used the whole budget thinking and sent back no answer. OpenRouter members answer at temperature 0, except Claude (`"temperature": null`), because Anthropic models accept no temperature but 1 while they think.

**History.** `judge_panel_v1` (2026-10-07, while the OpenRouter account had no credit) ran the two Nemotron models and `judge_gemma_v1` (`google/gemma-4-31b-it:free`), which was rate-limited on most calls. v2 dropped Gemma once credit was added the same day. Gemma's recorded forecasts stay on the record under its own method. `judge_nemotron_super_v1` (`nvidia/nemotron-3-super-120b-a12b:free`) was removed on 2026-10-21 because OpenRouter returned HTTP 404 for it on every run since at least 2026-10-19; its earlier forecasts stay on the record under its own method. `judge_panel_v3` (2026-10-23) dropped `judge_kimi_k26_v1` (`moonshotai/kimi-k2.6`): 25 of its 87 calls ran out of tokens before answering, 40% of its spend bought nothing, and it was the panel's most expensive member per answer. Its earlier forecasts stay on the record. `judge_panel_v4` (2026-10-24) added `judge_gemini38_flash_v1` (`google/gemini-3.8-flash`); `judge_panel_v5` replaced it the same day, before it answered any question, with `judge_claude_sonnet55_v1` at the owner's request. `judge_panel_v6` (2026-10-31) dropped the four OpenRouter members (Nemotron Ultra, DeepSeek V4 Pro, Qwen 3.8 27B, MiniMax M3) and moved the Claude judge off OpenRouter. The OpenRouter credit ran out on 2026-10-31 (HTTP 402 on most calls), the free Nemotron Ultra had failed every call since 2026-10-26, and the owner asked to use Claude as the judge with Cursor doing the research. Their earlier forecasts stay on the record under their own methods. The tests use the frozen v1 panel in `fixtures/judge_panel_v1.json`.

## Rules

- **Briefs are evidence, not forecasts.**
  - A brief holds no probability, no lean and no market odds.
  - `scripts/judge_panel.py` refuses a brief that mentions Polymarket, Kalshi, Manifold, Metaculus, betting odds or implied probability.
- **Independence.** Members see only the question, its rule, the brief and the source excerpts. They never see Claude's forecast, each other's answers or the market price. No member may come from the family that does the research (Grok, `configs/research.json`); `scripts/judge_panel.py` refuses to run if one does. Claude fills the questions Cursor could not research, so the Claude judge sits out exactly those questions (`researched_by` in `research.json`); the run counts them as `skipped_own_research`.
- **Before the cutoff.** A member is asked only while the question's forecast cutoff is ahead: the entry cutoff for FRED questions, the market close for Polymarket questions, and the resolution date for asked questions.
- **One answer per member.**
  - A member is asked at most once a day per question. A failed call can be retried on a later day, before the cutoff.
  - An answer, once recorded, is final.
- **Probability clamp.** Probabilities are clamped to [0.02, 0.98], the range every recorded forecast uses. The raw answer stays in the log.
- **Budget.**
  - `daily_budget_usd` (2.00 since 2026-10-25, when the daily question set roughly doubled; was 1.00) caps spending per UTC day.
  - A call starts only if its worst case fits in what is left today. The worst case comes from OpenRouter's published prices, the prompt length and `max_tokens`.
  - The amount OpenRouter reports for the call is what gets charged against the cap.
  - Free models cost 0.
- **Audit trail.** Every attempt goes to `judge_panel_log.json` in the artifact directory. Each entry records:
  - the model requested and the model that served it;
  - the prompt version and the hashes of the brief and the source excerpts;
  - tokens, cost, and the answer or the error.

  `python scripts/judge_panel.py spend` prints today's spend and this month's.

## Where the forecasts go

- **FRED cohorts** (`frozen_manifest.json`): each member gets `<method>_supplement.json`. The scorer scores it as its own method.
- **Market and asked question sets** (`forecasts.json`): each member's forecast is added to the question's `forecasts` list.

Both are rebuilt from the log on every run, so a rerun never duplicates a forecast.

## Running it

- `python scripts/judge_panel.py run --local <dir> ...` asks the `claude_cli` members through the `claude` command on this machine. The daily routine runs it in its own session before asking Actions for **finish**. It needs no key; without the `claude` command it prints a note and does nothing.
- `python scripts/judge_panel.py run <dir> ...` asks the OpenRouter members, in the **finish** step. It reads `OPENROUTER_API_KEY` from the environment; without it, or with no OpenRouter members in the panel, it prints a note and does nothing. The key is never logged or committed.
- Either run rebuilds every member's forecasts from the log, so the two never undo each other.

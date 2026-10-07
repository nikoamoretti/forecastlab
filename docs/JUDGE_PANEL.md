# Judge panel

Research is the expensive part of forecasting. Reading many pages costs money when a pay-per-use model does it. The judge panel splits the work:

- **Claude researches.** This runs in the owner's Claude Code session, so it costs nothing per question. For each question it writes a neutral *evidence brief*: dated facts, base rates and source URLs.
- **Cheap open-weights models judge.** Each one reads the question, its resolution rule and the brief, and states a probability.

Each panel member is a forecaster of its own. Its probability goes into the median that makes our call, next to Claude and the statistical baseline. Its own row on the Track record page shows whether it adds anything.

## Members

`configs/judge_panel.json` lists the members (`judge_panel_v2` since 2026-10-07). Each member's label is in `forecastlab_api.track_record.FORECASTERS`.

| Method | OpenRouter model | Cost (USD per million tokens, in / out, 2026-10-07) |
| --- | --- | --- |
| `judge_nemotron_ultra_v1` | `nvidia/nemotron-3-ultra-550b-a55b:free` | free |
| `judge_nemotron_super_v1` | `nvidia/nemotron-3-super-120b-a12b:free` | free |
| `judge_deepseek_v4_pro_v1` | `deepseek/deepseek-v4-pro` | 0.21 / 0.42 |
| `judge_kimi_k26_v1` | `moonshotai/kimi-k2.6` | 0.47 / 2.45 |
| `judge_qwen38_27b_v1` | `qwen/qwen3.8-27b` | 0.43 / 2.55 |
| `judge_minimax_m3_v1` | `minimax/minimax-m3` | 0.30 / 1.20 |

All six are open weights from five labs. The four paid members cost about a cent per question together; the $1 daily cap still bounds the worst case.

**History.** `judge_panel_v1` (2026-10-07, while the OpenRouter account had no credit) ran the two Nemotron models and `judge_gemma_v1` (`google/gemma-4-31b-it:free`), which was rate-limited on most calls. v2 dropped Gemma once credit was added the same day. Gemma's recorded forecasts stay on the record under its own method. The tests use the frozen v1 panel in `fixtures/judge_panel_v1.json`.

## Rules

- **Briefs are evidence, not forecasts.**
  - A brief holds no probability, no lean and no market odds.
  - `scripts/judge_panel.py` refuses a brief that mentions Polymarket, Kalshi, Manifold, Metaculus, betting odds or implied probability.
- **Independence.** Members see only the question, its rule and the brief. They never see Claude's forecast, each other's answers or the market price.
- **Before the cutoff.** A member is asked only while the question's forecast cutoff is ahead: the entry cutoff for FRED questions, the market close for Polymarket questions, and the resolution date for asked questions.
- **One answer per member.**
  - A member is asked at most once a day per question. A failed call can be retried on a later day, before the cutoff.
  - An answer, once recorded, is final.
- **Probability clamp.** Probabilities are clamped to [0.02, 0.98], the range every recorded forecast uses. The raw answer stays in the log.
- **Budget.**
  - `daily_budget_usd` (1.00) caps spending per UTC day.
  - A call starts only if its worst case fits in what is left today. The worst case comes from OpenRouter's published prices, the prompt length and `max_tokens`.
  - The amount OpenRouter reports for the call is what gets charged against the cap.
  - Free models cost 0.
- **Audit trail.** Every attempt goes to `judge_panel_log.json` in the artifact directory. Each entry records:
  - the model requested and the model that served it;
  - the prompt version and the brief's hash;
  - tokens, cost, and the answer or the error.

  `python scripts/judge_panel.py spend` prints today's spend and this month's.

## Where the forecasts go

- **FRED cohorts** (`frozen_manifest.json`): each member gets `<method>_supplement.json`. The scorer scores it as its own method.
- **Market and asked question sets** (`forecasts.json`): each member's forecast is added to the question's `forecasts` list.

Both are rebuilt from the log on every run, so a rerun never duplicates a forecast.

## Key

The run reads `OPENROUTER_API_KEY` from the environment. Without it the run prints a note and does nothing. The key is never logged or committed.

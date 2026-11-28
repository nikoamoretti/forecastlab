#!/usr/bin/env python3
"""Run the Metaculus FutureEval binary-question bot once (rule: forecastlab.metaculus_bot, docs/metaculus-bot.md).

  python scripts/metaculus_bot.py [--dry-run] [--tournament ID_OR_SLUG ...] [--max-questions N]

Reads METACULUS_TOKEN, OPENROUTER_API_KEY and TAVILY_API_KEY from the environment. Without METACULUS_TOKEN it
prints one line and exits 0. Writes a run log JSON to --out-dir (never committed).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.metaculus_bot import (  # noqa: E402
    DEFAULT_MODEL,
    FALL_2026_TOURNAMENT,
    RunConfig,
    run,
    write_run_log,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="do everything except the forecast and comment POSTs")
    parser.add_argument(
        "--tournament",
        action="append",
        help=f"tournament id or slug, repeatable (default {FALL_2026_TOURNAMENT}; also 'bot-testing-area', 'minibench')",
    )
    parser.add_argument("--max-questions", type=int, default=5)
    parser.add_argument("--max-per-24h", type=int, default=30)
    parser.add_argument("--cost-ceiling", type=float, default=1.5, help="USD per run")
    parser.add_argument("--model", default=os.environ.get("METACULUS_BOT_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--out-dir", default="data/local/metaculus-bot")
    args = parser.parse_args(argv)

    token = os.environ.get("METACULUS_TOKEN", "").strip()
    if not token:
        print("METACULUS_TOKEN not set; skipping")
        return 0
    tournaments = [t.strip() for raw in (args.tournament or [FALL_2026_TOURNAMENT]) for t in raw.split(",") if t.strip()]
    config = RunConfig(
        tournaments=tournaments or [FALL_2026_TOURNAMENT],
        max_questions=args.max_questions,
        max_per_24h=args.max_per_24h,
        cost_ceiling_usd=args.cost_ceiling,
        dry_run=args.dry_run,
        model=args.model,
    )
    log = run(
        config,
        metaculus_token=token,
        openrouter_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        tavily_key=os.environ.get("TAVILY_API_KEY", "").strip(),
    )
    path = write_run_log(log, args.out_dir)
    done = [r for r in log["results"] if r.get("probability") is not None]
    errors = [r for r in log["results"] if r.get("error")]
    usage = log.get("usage", {})
    print(
        f"{'dry run: ' if config.dry_run else ''}open binary={log.get('open_binary_questions', 0)} "
        f"forecast={len(done)} posted={sum(1 for r in done if r['posted'])} errors={len(errors)} "
        f"cost=${usage.get('cost_usd', 0):.4f} stopped={log.get('stopped', 'no')} log={path}"
    )
    if log.get("skipped"):
        print(log["skipped"])
    if log.get("error"):
        print(log["error"])
    return 1 if log.get("fatal") else 0


if __name__ == "__main__":
    raise SystemExit(main())

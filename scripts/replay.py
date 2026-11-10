#!/usr/bin/env python3
"""Replay lab: score a variant forecaster against Claude on decided questions.

    scripts/replay.py questions [--topic T] [--json]
    scripts/replay.py score artifacts/replay/price_model_v1.json

A variant file is {question id: probability}. `score` prints the variant's Brier
score next to Claude's own (claude_code_forecaster_v1) on exactly the questions
the variant covers. The market benchmark from the track record is shown for the
same subset as a comparison only; it is never an input to a variant.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRACK = ROOT / "data" / "track_record.json"
CLAUDE = "claude_code_forecaster_v1"
MARKET_METHODS = ("market_price_v1", "manifold_price_v1", "kalshi_price_v1")


def forecast_day(source: str) -> str | None:
    """prospective_markets_20261029 -> 2026-10-29."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", source or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _prob(question: dict, method: str) -> float | None:
    for f in question.get("forecasts") or []:
        if f.get("method") == method and f.get("probability") is not None:
            return float(f["probability"])
    return None


def load_questions(path: Path = TRACK) -> list[dict[str, Any]]:
    """Decided, scored questions with Claude's probability (market price kept
    separately under 'market' for comparison columns only)."""
    data = json.loads(path.read_text())
    rows = []
    for q in data["questions"]:
        if q.get("status") != "resolved" or not q.get("scored") or q.get("outcome") is None:
            continue
        market = next((p for m in MARKET_METHODS if (p := _prob(q, m)) is not None), None)
        rows.append({
            "id": q["id"],
            "title": q["title"],
            "detail": q.get("detail") or "",
            "topic": q.get("topic"),
            "forecast_day": forecast_day(q.get("source", "")),
            "resolves_on": q.get("resolves_on"),
            "outcome": int(q["outcome"]),
            "claude": _prob(q, CLAUDE),
            "market": market,
        })
    return rows


def brier(pairs: list[tuple[float, int]]) -> float:
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def cmd_questions(args: argparse.Namespace) -> int:
    rows = load_questions()
    if args.topic:
        rows = [r for r in rows if (r["topic"] or "").lower() == args.topic.lower()]
    if args.json:
        for r in rows:
            r.pop("market")  # not for model building
        json.dump(rows, sys.stdout, indent=1)
        print()
        return 0
    for r in rows:
        c = "  -  " if r["claude"] is None else f"{r['claude']:.2f}"
        print(f"{r['id'][:22]:22} {r['forecast_day']} -> {r['resolves_on']} out={r['outcome']} "
              f"claude={c} [{r['topic']}] {r['title']}")
    print(f"\n{len(rows)} decided, scored questions", file=sys.stderr)
    return 0


def score(variant: dict[str, float], rows: list[dict]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    used = [(by_id[i], float(p)) for i, p in variant.items() if i in by_id and by_id[i]["claude"] is not None]
    skipped = [i for i in variant if i not in by_id or by_id[i]["claude"] is None]
    out: dict[str, Any] = {"n": len(used), "skipped": skipped}
    if not used:
        return out
    out["variant"] = brier([(p, r["outcome"]) for r, p in used])
    out["claude"] = brier([(r["claude"], r["outcome"]) for r, _ in used])
    out["blend"] = brier([((r["claude"] + p) / 2, r["outcome"]) for r, p in used])
    priced = [(r, p) for r, p in used if r["market"] is not None]
    out["market_n"] = len(priced)
    if priced:
        out["market"] = brier([(r["market"], r["outcome"]) for r, _ in priced])
        out["variant_on_market_subset"] = brier([(p, r["outcome"]) for r, p in priced])
        out["claude_on_market_subset"] = brier([(r["claude"], r["outcome"]) for r, _ in priced])
    return out


def cmd_score(args: argparse.Namespace) -> int:
    variant = json.loads(Path(args.variant).read_text())
    if "probabilities" in variant:  # tolerate a wrapped file
        variant = variant["probabilities"]
    s = score(variant, load_questions())
    print(f"variant file: {args.variant}  ({len(variant)} probabilities)")
    print(f"questions scored (decided, Claude forecast present): {s['n']}")
    if s["skipped"]:
        print(f"ignored ids (not decided/scored or no Claude forecast): {len(s['skipped'])}")
    if not s["n"]:
        return 1
    print(f"  variant Brier   {s['variant']:.4f}")
    print(f"  Claude Brier    {s['claude']:.4f}   (same {s['n']} questions)")
    print(f"  blend (mean)    {s['blend']:.4f}   (average of Claude and variant)")
    if s["market_n"]:
        print(f"  market Brier    {s['market']:.4f}   (comparison only; {s['market_n']} of them were market-priced)")
        print(f"    on that subset: variant {s['variant_on_market_subset']:.4f}, Claude {s['claude_on_market_subset']:.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("questions", help="list decided, scored questions")
    q.add_argument("--topic")
    q.add_argument("--json", action="store_true", help="full rows as JSON (without market price)")
    q.set_defaults(fn=cmd_questions)
    s = sub.add_parser("score", help="score a {id: probability} file")
    s.add_argument("variant")
    s.set_defaults(fn=cmd_score)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

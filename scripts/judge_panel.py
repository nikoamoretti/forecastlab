#!/usr/bin/env python3
"""Run the cheap-model judge panel (configs/judge_panel.json) on today's question sets.

  python scripts/judge_panel.py run <artifact_dir> [<artifact_dir> ...]
  python scripts/judge_panel.py spend                 # today's and this month's spend

Each directory needs ``briefs.json``: ``{"written_at", "briefs": {"<question id>": "<brief>"}}``,
written by Claude after its research. A brief holds dated facts, base rates and source URLs,
and no probability or lean. It never quotes prediction-market odds.

For every question with a brief whose forecast cutoff is still ahead, every panel member
that has not answered yet is asked once. Each attempt, with its cost and the model that
served it, goes to ``judge_panel_log.json`` in the directory. The forecasts are then written
where the track record reads them. Cohorts with ``frozen_manifest.json`` get one
``<method>_supplement.json`` per member. General question sets get the judges added to
``forecasts.json``. A run stops starting calls once the next call's worst case would push
today's spend past ``daily_budget_usd``. Without ``OPENROUTER_API_KEY`` it does nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.judge_panel import (  # noqa: E402
    Budget,
    PanelConfig,
    PanelQuestion,
    Post,
    Price,
    ask,
    fetch_models,
    load_config,
    openrouter_post,
    prices,
    prompt,
    worst_case_cost,
)
from forecastlab.timeutil import as_utc, utcnow  # noqa: E402

CONFIG = ROOT / "configs" / "judge_panel.json"
LOG = "judge_panel_log.json"
MIN_PROBABILITY, MAX_PROBABILITY = 0.02, 0.98
# A brief is evidence for the judges, never a forecast to copy.
FORBIDDEN_IN_BRIEF = re.compile(r"polymarket|kalshi|manifold|metaculus|betting odds|implied probability", re.IGNORECASE)


def _parse(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def is_cohort(directory: Path) -> bool:
    return (directory / "frozen_manifest.json").exists()


def load_questions(directory: Path) -> list[PanelQuestion]:
    briefs_path = directory / "briefs.json"
    if not briefs_path.exists():
        raise SystemExit(f"{directory.name}: briefs.json is missing")
    briefs: dict[str, str] = json.loads(briefs_path.read_text())["briefs"]
    for qid, brief in briefs.items():
        if not brief.strip():
            raise SystemExit(f"{directory.name}/{qid}: the brief is empty")
        if FORBIDDEN_IN_BRIEF.search(brief):
            raise SystemExit(f"{directory.name}/{qid}: the brief mentions a prediction market or odds")
    questions = []
    if is_cohort(directory):
        for entry in json.loads((directory / "questions.json").read_text()):
            if entry["entry_id"] in briefs:
                questions.append(PanelQuestion(
                    id=entry["entry_id"], question=entry["question"],
                    criteria=(f"{entry['question']} It resolves on the first published value, released around "
                              f"{entry['release_at'][:10]}; later revisions do not count."),
                    resolves_on=entry["release_at"][:10], cutoff=_parse(entry["cutoff"]), brief=briefs[entry["entry_id"]]))
        return questions
    closes = {}
    if (directory / "questions.json").exists():  # market questions: the forecast must precede the close
        closes = {q["id"]: q["closes_at"] for q in json.loads((directory / "questions.json").read_text())["questions"]}
    for question in json.loads((directory / "forecasts.json").read_text())["questions"]:
        if question["id"] in briefs and question.get("outcome") is None and not question.get("cancelled"):
            questions.append(PanelQuestion(
                id=question["id"], question=question["question"], criteria=question["resolution_criteria"],
                resolves_on=question["resolution_date"],
                cutoff=_parse(closes.get(question["id"]) or f"{question['resolution_date']}T00:00:00Z"),
                brief=briefs[question["id"]]))
    return questions


def spent_on(artifacts: Path, day: str) -> float:
    total = 0.0
    for path in artifacts.glob(f"*/{LOG}"):
        total += sum(a["cost_usd"] for a in json.loads(path.read_text())["attempts"] if a["at"].startswith(day))
    return total


def _clamp(probability: float) -> float:
    return min(MAX_PROBABILITY, max(MIN_PROBABILITY, probability))


def write_forecasts(directory: Path, config: PanelConfig, attempts: list[dict[str, Any]]) -> None:
    """Rewrite the judges' forecasts from the log, so a rerun never duplicates them."""
    answered = {(a["id"], a["method"]): a for a in attempts if a["ok"]}
    if is_cohort(directory):
        report = json.loads((directory / "cohort_report.json").read_text())
        events = {q["id"]: q["release_event"] for q in report["questions"]}
        for member in config.members:
            cells = [{"entry_id": qid, "release_event": events[qid], "status": "forecasted",
                      "probability": _clamp(a["probability"]), "computed_at": a["at"], "rationale": a["reasoning"],
                      "model": a.get("served_model") or a["model"]}
                     for (qid, method), a in sorted(answered.items()) if method == member.method]
            path = directory / f"{member.method}_supplement.json"
            if cells:
                _write(path, {"cohort_id": report["id"], "manifest_hash": report["manifest_hash"], "method": member.method,
                              "generated_at": max(c["computed_at"] for c in cells), "panel": config.version,
                              "note": "Judge panel member forecasting from Claude's evidence brief before each cutoff; "
                                      "scored as a separate method.", "cells": cells})
        return
    path = directory / "forecasts.json"
    data = json.loads(path.read_text())
    methods = {m.method for m in config.members}
    for question in data["questions"]:
        question["forecasts"] = [f for f in question["forecasts"] if f["method"] not in methods] + [
            {"method": member.method, "probability": _clamp(answered[(question["id"], member.method)]["probability"]),
             "created_at": answered[(question["id"], member.method)]["at"],
             "rationale": answered[(question["id"], member.method)]["reasoning"]}
            for member in config.members if (question["id"], member.method) in answered]
    _write(path, data)


def run(directories: list[Path], *, post: Post, price_list: dict[str, Price], now: datetime | None = None,
        config: PanelConfig | None = None, artifacts: Path = ROOT / "artifacts") -> dict[str, Any]:
    config = config or load_config(CONFIG)
    now = as_utc(now or utcnow())
    budget = Budget(config.daily_budget_usd, spent_on(artifacts, now.date().isoformat()))
    summary: dict[str, Any] = {"asked": 0, "answered": 0, "skipped_budget": 0, "spent_before_usd": round(budget.spent, 4)}
    for directory in directories:
        log_path = directory / LOG
        log: dict[str, Any] = json.loads(log_path.read_text()) if log_path.exists() else {
            "panel": config.version, "prompt_version": config.prompt_version, "attempts": []}
        done = {(a["id"], a["method"]) for a in log["attempts"] if a["ok"]}
        tried = {(a["id"], a["method"]) for a in log["attempts"]}
        for question in load_questions(directory):
            if now >= question.cutoff:
                continue
            brief_hash = hashlib.sha256(question.brief.encode()).hexdigest()[:16]
            for member in config.members:
                key = (question.id, member.method)
                # One attempt per member and question per day; a failed one may be retried on a later day.
                if key in done or (key in tried and any(a["at"][:10] == now.date().isoformat()
                                                        for a in log["attempts"] if (a["id"], a["method"]) == key)):
                    continue
                price = price_list.get(member.model)
                if price is None:
                    print(f"{member.model}: no published price, skipped")
                    continue
                if not budget.allows(worst_case_cost(len(prompt(question, now)) + 1000, config.max_tokens, price)):
                    summary["skipped_budget"] += 1
                    continue
                attempt = ask(member, question, now=now, post=post, max_tokens=config.max_tokens, price=price)
                attempt |= {"brief_sha256": brief_hash, "prompt_version": config.prompt_version}
                budget.charge(attempt["cost_usd"])
                log["attempts"].append(attempt)
                summary["asked"] += 1
                summary["answered"] += attempt["ok"]
                if not attempt["ok"]:
                    print(f"{question.id} {member.method}: {attempt['error']}")
        _write(log_path, log)
        write_forecasts(directory, config, log["attempts"])
    summary["spent_today_usd"] = round(budget.spent, 4)
    print(json.dumps(summary))
    if summary["skipped_budget"]:
        print(f"daily cap of ${config.daily_budget_usd:.2f} reached: {summary['skipped_budget']} calls not made")
    return summary


def main(argv: list[str]) -> int:
    if argv[:1] == ["run"] and len(argv) > 1:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            print("OPENROUTER_API_KEY is not set: judge panel skipped")
            return 0
        run([Path(p) for p in argv[1:]], post=openrouter_post(key), price_list=prices(fetch_models()))
    elif argv[:1] == ["spend"]:
        today = utcnow().date().isoformat()
        month = spent_on(ROOT / "artifacts", today[:7])
        print(f"judge panel spend: today ${spent_on(ROOT / 'artifacts', today):.4f}, this month ${month:.4f}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

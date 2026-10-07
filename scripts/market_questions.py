#!/usr/bin/env python3
"""Daily questions borrowed from Polymarket (rule: forecastlab.market_questions).

  python scripts/market_questions.py prepare                   # artifacts/prospective_markets_<date>/
  python scripts/market_questions.py record <dir> <forecasts.json>
  python scripts/market_questions.py resolve                   # every open market question

``prepare`` writes ``questions.json`` (question, rules, close time) and, separately,
``market_snapshot.json`` with each market's price at selection. Forecasters read only
``questions.json`` and must not look up the market's odds, so our forecasts stay
independent of the benchmark.

``record`` takes ``{"method", "forecast_made_at", "forecasts": [{"id", "probability",
"rationale", "sources", "integrity_note"?}]}`` (``integrity_note`` discloses, for example,
market odds seen in a search result; such a question is marked ``unscored``, so it stays on
the page but counts in no score), checks that every question is covered, that probabilities lie
in [0.02, 0.98] and that the forecast was made before each market closes, and writes
``forecasts.json``, which the track record reads.

``resolve`` records Polymarket's final result for questions whose market has closed.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.market_questions import (  # noqa: E402
    MARKET_METHOD,
    SELECTION_VERSION,
    Fetch,
    candidates,
    fetch_json,
    resolution,
    select,
)
from forecastlab.timeutil import as_utc, utcnow  # noqa: E402

CLAUDE_METHOD = "claude_code_forecaster_v1"


def _parse(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def known_ids(artifacts: Path) -> set[str]:
    seen: set[str] = set()
    for path in [*artifacts.glob("prospective_markets_*/questions.json"), *artifacts.glob("prospective_markets_*/forecasts.json")]:
        data = json.loads(path.read_text())
        seen |= {q["id"] for q in (data["questions"] if isinstance(data, dict) else data)}
    return seen


def prepare(now: datetime | None = None, artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json) -> Path | None:
    now = as_utc(now or utcnow())
    chosen = select(candidates(now, fetch), known_ids(artifacts))
    if not chosen:
        print("no new market questions today")
        return None
    directory = artifacts / f"prospective_markets_{now.strftime('%Y%m%d')}"
    directory.mkdir(parents=True, exist_ok=False)
    keep = ("id", "question", "topic", "resolution_criteria", "closes_at", "market_url")
    (directory / "questions.json").write_text(json.dumps(
        {"selection": SELECTION_VERSION, "selected_at": now.isoformat(), "questions": [{k: c[k] for k in keep} for c in chosen]},
        indent=2, sort_keys=True) + "\n")
    (directory / "market_snapshot.json").write_text(json.dumps(
        {"taken_at": now.isoformat(), "markets": [{"id": c["id"], "market_id": c["market_id"], "yes_price": c["market_price"],
                                                   "volume": c["volume"]} for c in chosen]}, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: {len(chosen)} questions")
    return directory


def record(directory: Path, forecasts_path: Path) -> Path:
    questions = {q["id"]: q for q in json.loads((directory / "questions.json").read_text())["questions"]}
    snapshot = json.loads((directory / "market_snapshot.json").read_text())
    prices = {m["id"]: m for m in snapshot["markets"]}
    data = json.loads(forecasts_path.read_text())
    made_at = _parse(data["forecast_made_at"])
    forecasts = {f["id"]: f for f in data["forecasts"]}
    if set(forecasts) != set(questions):
        raise SystemExit(f"forecasts must cover exactly the questions: missing {sorted(set(questions) - set(forecasts))}, "
                         f"unknown {sorted(set(forecasts) - set(questions))}")
    out = []
    for qid, question in questions.items():
        forecast = forecasts[qid]
        probability = float(forecast["probability"])
        if not 0.02 <= probability <= 0.98:
            raise SystemExit(f"{qid}: probability {probability} outside [0.02, 0.98]")
        if made_at >= _parse(question["closes_at"]):
            raise SystemExit(f"{qid}: forecast made at {made_at.isoformat()} is not before the market closes")
        out.append({
            "id": qid, "question": question["question"], "topic": question["topic"],
            "resolution_criteria": question["resolution_criteria"], "resolution_date": question["closes_at"][:10],
            "resolution_source": question["market_url"], "market_id": prices[qid]["market_id"], "outcome": None,
            **({"integrity_note": forecast["integrity_note"], "unscored": True} if forecast.get("integrity_note") else {}),
            "forecasts": [
                {"method": MARKET_METHOD, "probability": prices[qid]["yes_price"], "created_at": snapshot["taken_at"]},
                {"method": data.get("method", CLAUDE_METHOD), "probability": probability, "created_at": made_at.isoformat(),
                 "rationale": forecast.get("rationale", ""), "sources": forecast.get("sources", [])}]})
    target = directory / "forecasts.json"
    target.write_text(json.dumps({"schema": "market_questions_v1", "selection": SELECTION_VERSION, "note": (
        "Questions borrowed from Polymarket and resolved by Polymarket. The market price at selection is a benchmark, "
        "not part of our call; our forecasters did not look at it."), "questions": out}, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: recorded {len(out)} forecasts")
    return target


def resolve(artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json, today: datetime | None = None) -> int:
    """Record final Polymarket results; network errors leave a question open for the next run."""
    today = as_utc(today or utcnow())
    changed = 0
    for path in sorted(artifacts.glob("prospective_markets_*/forecasts.json")):
        data = json.loads(path.read_text())
        before = changed
        for question in data["questions"]:
            if question["outcome"] is not None or question.get("cancelled") or question["resolution_date"] > today.date().isoformat():
                continue
            try:
                result = resolution(question["market_id"], fetch)
            except Exception as exc:  # noqa: BLE001 - one unreachable market must not stop the others
                print(f"{question['id']}: still open ({type(exc).__name__})")
                continue
            if result["status"] == "resolved":
                question |= {"outcome": result["outcome"], "outcome_source": question["resolution_source"],
                             "outcome_note": f"Polymarket: {'Yes' if result['outcome'] else 'No'}"}
                changed += 1
            elif result["status"] == "cancelled":
                question |= {"cancelled": True, "outcome_note": "Polymarket settled it 50/50"}
                changed += 1
        if changed > before:
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"market questions: {changed} newly resolved")
    return changed


def main(argv: list[str]) -> int:
    if argv[:1] == ["prepare"]:
        prepare()
    elif argv[:1] == ["record"] and len(argv) == 3:
        record(Path(argv[1]), Path(argv[2]))
    elif argv[:1] == ["resolve"]:
        resolve()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Daily questions borrowed from Polymarket and Manifold (rule: forecastlab.market_questions).

  python scripts/market_questions.py prepare                   # artifacts/prospective_markets_<date>/
  python scripts/market_questions.py pool                      # read-only: the selection funnel per source, writes nothing
  python scripts/market_questions.py record <dir> <forecasts.json> [--skip <id>[,<id>...]] [--skip-failed]
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

``--skip`` names questions that get no forecast today; ``--skip-failed`` adds the ids under
``failed`` in the directory's ``research.json``. Skipped questions are printed and listed under
``skipped`` in ``forecasts.json`` with a reason; any other uncovered question is still refused.

``resolve`` records the market's final result (Polymarket or Manifold) for questions whose market has closed.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.live_counts import NO_TALLY  # noqa: E402
from forecastlab.market_questions import (  # noqa: E402
    DAILY_LIMIT,
    MANIFOLD_DAILY_LIMIT,
    MANIFOLD_SELECTION_VERSION,
    MARKET_METHOD,
    SELECTION_VERSION,
    Fetch,
    Stats,
    candidates,
    fetch_json,
    manifold_candidates,
    manifold_resolution,
    manifold_rules,
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


def pending_questions(artifacts: Path) -> list[dict[str, Any]]:
    """Questions not yet resolved: recorded ones without an outcome (and not cancelled), plus the
    questions of a day whose forecasts are not recorded yet. Skipped questions are not tracked. Each carries its
    event (``market_url``, or ``resolution_source`` once recorded) so selection can avoid re-picking an open event."""
    pending: list[dict[str, Any]] = []
    for directory in sorted(artifacts.glob("prospective_markets_*")):
        recorded, selected = directory / "forecasts.json", directory / "questions.json"
        if recorded.exists():
            pending += [q for q in json.loads(recorded.read_text())["questions"]
                        if q.get("outcome") is None and not q.get("cancelled")]
        elif selected.exists():
            pending += [{"id": q["id"], "topic": q["topic"], "question": q["question"], "resolution_date": q["closes_at"][:10],
                            "market_url": q.get("market_url")}
                        for q in json.loads(selected.read_text())["questions"]]
    return pending


def prepare(now: datetime | None = None, artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json) -> Path | None:
    now = as_utc(now or utcnow())
    seen = known_ids(artifacts)
    pending = pending_questions(artifacts)
    chosen = select(candidates(now, fetch), seen, pending=pending)
    try:  # Manifold adds questions; if it is unreachable, Polymarket's still run
        chosen += [manifold_rules(item, fetch) for item in select(manifold_candidates(now, fetch), seen, MANIFOLD_DAILY_LIMIT, pending + chosen)]
    except Exception as exc:  # noqa: BLE001
        print(f"manifold questions skipped today ({type(exc).__name__})")
    if not chosen:
        print("no new market questions today")
        return None
    directory = artifacts / f"prospective_markets_{now.strftime('%Y%m%d')}"
    directory.mkdir(parents=True, exist_ok=False)
    keep = ("id", "question", "topic", "resolution_criteria", "closes_at", "market_url")
    (directory / "questions.json").write_text(json.dumps(
        {"selection": f"{SELECTION_VERSION}+{MANIFOLD_SELECTION_VERSION}", "selected_at": now.isoformat(), "questions": [{k: c[k] for k in keep} for c in chosen]},
        indent=2, sort_keys=True) + "\n")
    (directory / "market_snapshot.json").write_text(json.dumps(
        {"taken_at": now.isoformat(), "markets": [{"id": c["id"], "market_id": c["market_id"], "yes_price": c["market_price"],
                                                   "volume": c["volume"], "method": c.get("method", MARKET_METHOD)} for c in chosen]}, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: {len(chosen)} questions")
    return directory


def _funnel(stats: Stats, prefixes: tuple[str, ...]) -> list[str]:
    return [f"    {key} = {value}" for key, value in sorted(stats.items()) if key.startswith(prefixes)]


def pool(now: datetime | None = None, artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json) -> dict[str, Stats]:
    """Read-only diagnostic: fetch and select exactly as ``prepare`` does, print the funnel per source, write nothing."""
    now = as_utc(now or utcnow())
    seen, held = known_ids(artifacts), pending_questions(artifacts)
    print(f"pool at {now.isoformat()}: {len(seen)} questions seen before, {len(held)} pending")
    by_day: dict[tuple[str, str], int] = {}
    for q in held:
        key = (str(q.get("topic")), str(q.get("resolution_date") or q.get("closes_at"))[:10])
        by_day[key] = by_day.get(key, 0) + 1
    print("  pending by topic and resolution date: " + ", ".join(f"{t}/{d}={n}" for (t, d), n in sorted(by_day.items())))
    result: dict[str, Stats] = {}
    chosen: list[dict[str, Any]] = []
    for source, find, limit in (("polymarket", candidates, DAILY_LIMIT), ("manifold", manifold_candidates, MANIFOLD_DAILY_LIMIT)):
        stats: Stats = {}
        result[source] = stats
        last: list[str] = []

        def tracked(url: str, last: list[str] = last) -> Any:
            last[:] = [url]
            return fetch(url)

        try:
            found = find(now, tracked, stats)
            picked = select(found, seen, limit, held + chosen, stats)
        except Exception as exc:  # noqa: BLE001 - the point is to see which call fails
            print(f"{source.upper()}: ERROR {type(exc).__name__}: {exc} (last url: {last[0] if last else 'none'})")
            continue
        chosen += picked
        print(f"{source.upper()} funnel (limit {limit}):")
        print("\n".join(_funnel(stats, ("events:", "markets:", "fetched:", "reach:", "volume_band:")) + _funnel(stats, ("drop:",)) + _funnel(stats, ("candidates:",))
                        + _funnel(stats, ("select:",))))
        print(f"  chosen: {len(picked)}")
    return result


def record(directory: Path, forecasts_path: Path, skip: set[str] | None = None, skip_failed: bool = False) -> Path:
    selected = json.loads((directory / "questions.json").read_text())
    questions = {q["id"]: q for q in selected["questions"]}
    snapshot = json.loads((directory / "market_snapshot.json").read_text())
    prices = {m["id"]: m for m in snapshot["markets"]}
    data = json.loads(forecasts_path.read_text())
    made_at = _parse(data["forecast_made_at"])
    forecasts = {f["id"]: f for f in data["forecasts"]}
    skipped = {identifier: "no research" for identifier in skip or ()}
    if skip_failed:
        research = directory / "research.json"
        failed = json.loads(research.read_text()).get("failed", {}) if research.exists() else {}
        for identifier in failed:
            no_tally = isinstance(failed, dict) and failed[identifier] == NO_TALLY
            skipped.setdefault(identifier, "no live tally for a count/total question" if no_tally
                               else "research failed or timed out")
    if set(skipped) - set(questions):
        raise SystemExit(f"cannot skip unknown questions: {sorted(set(skipped) - set(questions))}")
    if set(skipped) & set(forecasts):
        raise SystemExit(f"questions both skipped and forecast: {sorted(set(skipped) & set(forecasts))}")
    if set(forecasts) | set(skipped) != set(questions) or set(forecasts) - set(questions):
        raise SystemExit(f"forecasts must cover exactly the questions: missing {sorted(set(questions) - set(forecasts) - set(skipped))}, "
                         f"unknown {sorted(set(forecasts) - set(questions))}")
    out = []
    for qid, question in questions.items():
        if qid in skipped:
            print(f"{directory.name}: skipped {qid} ({skipped[qid]})")
            continue
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
                {"method": prices[qid].get("method", MARKET_METHOD), "probability": prices[qid]["yes_price"],
                 "created_at": snapshot["taken_at"]},
                {"method": data.get("method", CLAUDE_METHOD), "probability": probability, "created_at": made_at.isoformat(),
                 "rationale": forecast.get("rationale", ""), "sources": forecast.get("sources", [])}]})
    target = directory / "forecasts.json"
    record_data = {"schema": "market_questions_v1", "selection": selected.get("selection", SELECTION_VERSION), "note": (
        "Questions borrowed from a prediction market (Polymarket or Manifold) and resolved by it. The market price at "
        "selection is a benchmark, "
        "not part of our call; our forecasters did not look at it."), "questions": out}
    if skipped:
        record_data["skipped"] = [{"id": i, "reason": r} for i, r in sorted(skipped.items())]
    target.write_text(json.dumps(record_data, indent=2, sort_keys=True) + "\n")
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
            manifold = question["id"].startswith("manifold-")
            try:
                result = (manifold_resolution(question["market_id"], fetch) if manifold
                          else resolution(question["market_id"], fetch, now=today))
            except Exception as exc:  # noqa: BLE001 - one unreachable market must not stop the others
                print(f"{question['id']}: still open ({type(exc).__name__})")
                continue
            if result["status"] == "resolved":
                question |= {"outcome": result["outcome"], "outcome_source": question["resolution_source"],
                             "outcome_note": f"{'Manifold' if manifold else 'Polymarket'}: {'Yes' if result['outcome'] else 'No'}"}
                changed += 1
            elif result["status"] == "cancelled":
                question |= {"cancelled": True, "outcome_note": "Manifold cancelled it" if manifold else "Polymarket settled it 50/50"}
                changed += 1
            elif not manifold:  # why a past-due market is still open; flags only, never a price
                print(f"{question['id']}: pending (closed={result.get('closed')}, uma={result.get('uma') or 'none'}, "
                      f"settled_prices={result.get('settled_prices')})")
        if changed > before:
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"market questions: {changed} newly resolved")
    return changed


def main(argv: list[str]) -> int:
    if argv[:1] == ["prepare"]:
        prepare()
    elif argv[:1] == ["pool"]:
        pool()
    elif argv[:1] == ["record"] and len(argv) >= 3:
        rest, skip, skip_failed = argv[3:], set(), False
        while rest:
            flag = rest.pop(0)
            if flag == "--skip-failed":
                skip_failed = True
            elif flag == "--skip" and rest:
                skip |= {i for i in rest.pop(0).split(",") if i}
            else:
                print(__doc__)
                return 2
        record(Path(argv[1]), Path(argv[2]), skip, skip_failed)
    elif argv[:1] == ["resolve"]:
        resolve()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

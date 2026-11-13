#!/usr/bin/env python3
"""Daily questions borrowed from Polymarket, Manifold and Kalshi (rule: forecastlab.market_questions).

  python scripts/market_questions.py prepare                   # artifacts/prospective_markets_<date>/
  python scripts/market_questions.py pool                      # read-only: the selection funnel per source, writes nothing
  python scripts/market_questions.py record <dir> <forecasts.json> [--skip <id>[,<id>...]] [--skip-failed]
  python scripts/market_questions.py resolve                   # every open market question
  python scripts/market_questions.py hold <dir> <id> --until YYYY-MM-DD --reason "..." [--source <url>]
  python scripts/market_questions.py second-look export <dir> --out <file> [--topics Tech[,Politics...]]
  python scripts/market_questions.py second-look record <dir> <answers.json> [--topics Tech[,Politics...]]

``prepare`` writes ``questions.json`` (question, rules, close time) and, separately,
``market_snapshot.json`` with each market's price at selection. Forecasters read only
``questions.json`` and must not look up the market's odds, so our forecasts stay
independent of the benchmark.
A source that cannot be read is skipped: the other sources' questions are still written, the
skipped source and its reason go under ``skipped_sources`` in ``questions.json``, and ``prepare`` exits 3
so the run is seen to have lost questions (``scripts/market_probe.py`` shows what each market API answers).

``record`` takes ``{"method", "forecast_made_at", "forecasts": [{"id", "probability",
"rationale", "sources", "integrity_note"?}]}`` (``integrity_note`` discloses, for example,
market odds seen in a search result; such a question is marked ``unscored``, so it stays on
the page but counts in no score), checks that every question is covered, that probabilities lie
in [0.02, 0.98] and that the forecast was made before each market closes, and writes
``forecasts.json``, which the track record reads.

``--skip`` names questions that get no forecast today; ``--skip-failed`` adds the ids under
``failed`` in the directory's ``research.json``. Skipped questions are printed and listed under
``skipped`` in ``forecasts.json`` with a reason; any other uncovered question is still refused.

``resolve`` records the market's final result (Polymarket, Manifold or Kalshi) for questions whose market has closed.

``hold`` sets one market's result aside until a day: for a market that was closed before the event it asks about was
decided (a Manifold creator may close a market early, for example before an election is called). It clears a result
``resolve`` already recorded and makes ``resolve`` skip the question until that day, when it reads the market again.
The reason and its source stay in ``forecasts.json`` under ``resolution_hold``; forecasts and our call are untouched.

``second-look export`` writes a packet for the "second look": a second Claude forecast made after seeing the research
digest, Claude's first (blind) forecast with its rationale, and the market's price. One item per eligible question: it has
both a Claude forecast and a market price in ``forecasts.json``, a topic in ``--topics`` (default: the topics of the
second-look rules in CALL_RULES), findings in ``research.json`` (and is not listed under ``failed``), is still open, and
holds no second look yet. The packet contains market prices, so it is refused inside ``artifacts/`` and never committed.
``second-look record`` reads ``{"<id>": {"probability", "note"}}`` and appends a ``claude_second_look_v1`` entry to each
question's ``forecasts`` (one per question, never overwritten; nothing else in the file changes). The second look is
never read by the blind forecaster: see ``prompts/second_look.md``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab.cursor_research import digest  # noqa: E402
from forecastlab.live_counts import NO_TALLY  # noqa: E402
from forecastlab.market_questions import (  # noqa: E402
    DAILY_LIMIT,
    KALSHI_DAILY_LIMIT,
    KALSHI_SELECTION_VERSION,
    MANIFOLD_DAILY_LIMIT,
    MANIFOLD_SELECTION_VERSION,
    MARKET_METHOD,
    SELECTION_VERSION,
    Fetch,
    Stats,
    candidates,
    fetch_json,
    kalshi_candidates,
    kalshi_resolution,
    manifold_candidates,
    manifold_resolution,
    manifold_rules,
    resolution,
    select,
)
from forecastlab.timeutil import as_utc, utcnow  # noqa: E402
from forecastlab_api.track_record import BENCHMARKS, CALL_RULES, SECOND_LOOK  # noqa: E402

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


def _why(exc: Exception) -> str:
    """Why a source was skipped. A network error or a refusal is given in full (it is the server's own text); any
    other error only by its type, because a parsing error can quote market data and this goes to a public log."""
    return f"{type(exc).__name__}: {exc}"[:400] if isinstance(exc, OSError) else type(exc).__name__


def prepare(now: datetime | None = None, artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json,
            skipped: dict[str, str] | None = None) -> Path | None:
    """Select today's questions. Each source is read on its own: one that cannot be reached or read is skipped and
    the others still run (on 2026-11-13 a refusal by Polymarket, then the only unguarded source, lost the whole day's
    selection without a failed run). A skipped source is named with its reason in ``skipped`` (filled in for the
    caller), in the log, as a warning on the Actions run, and in ``questions.json`` under ``skipped_sources``."""
    now = as_utc(now or utcnow())
    seen = known_ids(artifacts)
    pending = pending_questions(artifacts)
    skipped = {} if skipped is None else skipped
    chosen: list[dict[str, Any]] = []
    sources: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...] = (
        ("polymarket", lambda: select(candidates(now, fetch), seen, pending=pending)),
        ("manifold", lambda: [manifold_rules(item, fetch) for item in
                              select(manifold_candidates(now, fetch), seen, MANIFOLD_DAILY_LIMIT, pending + chosen)]),
        ("kalshi", lambda: select(kalshi_candidates(now, fetch), seen, KALSHI_DAILY_LIMIT, pending + chosen)),
    )
    for source, pick in sources:
        try:
            chosen += pick()  # in place, so the later sources see what the earlier ones chose
        except Exception as exc:  # noqa: BLE001 - whatever stops one source must not stop the others
            skipped[source] = _why(exc)
            print(f"{source} questions skipped today ({skipped[source]})")
            print(f"::warning title=Market source skipped::{source}: {skipped[source]}", file=sys.stderr)
    if not chosen:
        print(f"no market questions selected: {', '.join(skipped)} could not be read" if skipped else "no new market questions today")
        return None
    directory = artifacts / f"prospective_markets_{now.strftime('%Y%m%d')}"
    directory.mkdir(parents=True, exist_ok=False)
    keep = ("id", "question", "topic", "resolution_criteria", "closes_at", "market_url")
    (directory / "questions.json").write_text(json.dumps(
        {"selection": f"{SELECTION_VERSION}+{MANIFOLD_SELECTION_VERSION}+{KALSHI_SELECTION_VERSION}", "selected_at": now.isoformat(), "questions": [{k: c[k] for k in keep} for c in chosen],
         **({"skipped_sources": skipped} if skipped else {})},
        indent=2, sort_keys=True) + "\n")
    (directory / "market_snapshot.json").write_text(json.dumps(
        {"taken_at": now.isoformat(), "markets": [{"id": c["id"], "market_id": c["market_id"], "yes_price": c["market_price"],
                                                   "volume": c["volume"], "volume_floor": c["volume_floor"], "method": c.get("method", MARKET_METHOD),
                                                   **({"volume_unit": c["volume_unit"]} if "volume_unit" in c else {})} for c in chosen]}, indent=2, sort_keys=True) + "\n")
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
    for source, find, limit in (("polymarket", candidates, DAILY_LIMIT), ("manifold", manifold_candidates, MANIFOLD_DAILY_LIMIT),
                               ("kalshi", kalshi_candidates, KALSHI_DAILY_LIMIT)):
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
        print("  chosen ids: " + ", ".join(c["id"] for c in picked))
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
            # the floor it was chosen under and what had traded, so scores can be split by how thin the market was
            **({"volume_floor": prices[qid]["volume_floor"], "volume": prices[qid]["volume"]}
               if "volume_floor" in prices[qid] else {}),
            **({"volume_unit": prices[qid]["volume_unit"]} if "volume_unit" in prices[qid] else {}),
            **({"integrity_note": forecast["integrity_note"], "unscored": True} if forecast.get("integrity_note") else {}),
            "forecasts": [
                {"method": prices[qid].get("method", MARKET_METHOD), "probability": prices[qid]["yes_price"],
                 "created_at": snapshot["taken_at"]},
                {"method": data.get("method", CLAUDE_METHOD), "probability": probability, "created_at": made_at.isoformat(),
                 "rationale": forecast.get("rationale", ""), "sources": forecast.get("sources", [])}]})
    target = directory / "forecasts.json"
    record_data = {"schema": "market_questions_v1", "selection": selected.get("selection", SELECTION_VERSION), "note": (
        "Questions borrowed from a prediction market (Polymarket, Manifold or Kalshi) and resolved by it. The market price at "
        "selection is a benchmark, "
        "not part of our call; our forecasters did not look at it."), "questions": out}
    if skipped:
        record_data["skipped"] = [{"id": i, "reason": r} for i, r in sorted(skipped.items())]
    target.write_text(json.dumps(record_data, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: recorded {len(out)} forecasts")
    return target


def second_look_topics() -> set[str] | None:
    """The topics of the second-look rules in CALL_RULES (None when a rule covers every topic)."""
    rules = [r for r in CALL_RULES if r["forecaster"] == SECOND_LOOK]
    if any(r["topics"] is None for r in rules):
        return None
    return {topic for r in rules for topic in r["topics"] or ()}


def _price_entry(question: dict[str, Any]) -> dict[str, Any] | None:
    """The recorded market price: the first benchmark in BENCHMARKS that priced the question."""
    return next((f for method in BENCHMARKS for f in question["forecasts"]
                 if f["method"] == method and f.get("probability") is not None), None)


def _second_look_pool(directory: Path, topics: set[str] | None) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any]]:
    """(eligible questions by id, ids already holding a second look, research.json) for a market directory."""
    data = json.loads((directory / "forecasts.json").read_text())
    research_path = directory / "research.json"
    research = json.loads(research_path.read_text()) if research_path.exists() else {}
    wanted = second_look_topics() if topics is None else topics
    eligible: dict[str, dict[str, Any]] = {}
    done: set[str] = set()
    for question in data["questions"]:
        methods = {f["method"] for f in question["forecasts"]}
        if SECOND_LOOK in methods:
            done.add(question["id"])
            continue
        claude = next((f for f in question["forecasts"] if f["method"] == CLAUDE_METHOD and f.get("probability") is not None), None)
        if (claude is None or _price_entry(question) is None or question.get("outcome") is not None or question.get("cancelled")
                or (wanted is not None and question["topic"] not in wanted)
                or not research.get("findings", {}).get(question["id"]) or question["id"] in research.get("failed", {})):
            continue
        eligible[question["id"]] = question
    return eligible, done, research


def second_look_export(directory: Path, out: Path, topics: set[str] | None = None, today: str | None = None) -> int:
    """Write the second-look packet for ``directory`` to ``out`` and return how many questions it holds.

    The packet carries market prices, so ``out`` may not be inside the repository's ``artifacts/`` directory.
    """
    resolved = out.resolve()
    if resolved.is_relative_to((ROOT / "artifacts").resolve()):
        raise SystemExit(f"{out}: the packet contains market prices; write it outside artifacts/ and do not commit it")
    eligible, _, research = _second_look_pool(directory, topics)
    day = today or f"{utcnow():%Y-%m-%d}"
    packet = []
    for qid, question in eligible.items():
        claude = next(f for f in question["forecasts"] if f["method"] == CLAUDE_METHOD)
        price = _price_entry(question)
        assert price is not None
        task = {"id": qid, "question": question["question"], "criteria": question["resolution_criteria"],
                "resolves_on": question["resolution_date"], "topic": question["topic"]}
        packet.append({
            "id": qid, "question": question["question"], "resolution_criteria": question["resolution_criteria"],
            "topic": question["topic"], "resolves_on": question["resolution_date"], "digest": digest(research, qid, task, day),
            "first_forecast": {"probability": claude["probability"], "rationale": claude.get("rationale", "")},
            "market_price": price["probability"]})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: exported {len(packet)} questions for a second look")
    return len(packet)


def second_look_record(directory: Path, answers_path: Path, topics: set[str] | None = None, now: datetime | None = None) -> Path:
    """Append each answer as a ``claude_second_look_v1`` forecast; nothing else in forecasts.json changes."""
    now = as_utc(now or utcnow())
    answers = json.loads(answers_path.read_text())
    if not isinstance(answers, dict):
        raise SystemExit('answers must be {"<id>": {"probability": p, "note": "..."}}')
    path = directory / "forecasts.json"
    data = json.loads(path.read_text())
    eligible, done, _ = _second_look_pool(directory, topics)
    known = {q["id"] for q in data["questions"]}
    if unknown := sorted(set(answers) - known):
        raise SystemExit(f"unknown questions: {unknown}")
    if again := sorted(set(answers) & done):
        raise SystemExit(f"already hold a second look (one per question, never overwritten): {again}")
    if ineligible := sorted(set(answers) - set(eligible)):
        raise SystemExit(f"not eligible for a second look: {ineligible}")
    selected = directory / "questions.json"
    closes = {q["id"]: q["closes_at"] for q in json.loads(selected.read_text())["questions"]} if selected.exists() else {}
    for qid, answer in answers.items():
        probability = float(answer["probability"])
        if not 0.02 <= probability <= 0.98:
            raise SystemExit(f"{qid}: probability {probability} outside [0.02, 0.98]")
        closing = _parse(closes[qid]) if qid in closes else _parse(f"{eligible[qid]['resolution_date']}T00:00:00Z")
        if now >= closing:
            raise SystemExit(f"{qid}: second look made at {now.isoformat()} is not before the market closes")
    for question in data["questions"]:
        if question["id"] in answers:
            answer = answers[question["id"]]
            question["forecasts"].append({"method": SECOND_LOOK, "probability": float(answer["probability"]),
                                          "created_at": now.isoformat(), "rationale": str(answer.get("note", "")),
                                          "saw_market_price": True})
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: recorded {len(answers)} second looks")
    return path


def _held(question: dict[str, Any], today: datetime) -> bool:
    """True while the question's result is set aside (see ``hold``)."""
    until = (question.get("resolution_hold") or {}).get("until")
    return isinstance(until, str) and until > today.date().isoformat()


def hold(directory: Path, question_id: str, until: str, reason: str, source: str | None = None,
         today: datetime | None = None) -> Path:
    """Set one market's result aside until ``until`` (YYYY-MM-DD) because the market closed before the event was decided.

    A result ``resolve`` already recorded is cleared, so the question is open again and counts in no score; from
    ``until`` on, ``resolve`` reads the market again. Nothing else in the file changes.
    """
    date.fromisoformat(until)  # refuse a day that is not a date
    if not reason.strip():
        raise SystemExit("a hold needs a reason")
    path = directory / "forecasts.json"
    data = json.loads(path.read_text())
    question = next((q for q in data["questions"] if q["id"] == question_id), None)
    if question is None:
        raise SystemExit(f"{question_id} is not in {path}")
    if question.get("cancelled"):
        raise SystemExit(f"{question_id} was cancelled; there is no result to hold")
    question.pop("outcome_note", None)
    question.pop("outcome_source", None)
    question |= {"outcome": None, "resolution_hold": {
        "until": until, "reason": reason.strip(), "set_on": as_utc(today or utcnow()).date().isoformat(),
        **({"source": source} if source else {})}}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"{question_id}: result held until {until}")
    return path


def resolve(artifacts: Path = ROOT / "artifacts", fetch: Fetch = fetch_json, today: datetime | None = None) -> int:
    """Record final market results (Polymarket, Manifold, Kalshi); network errors leave a question open for the next run."""
    today = as_utc(today or utcnow())
    changed = 0
    for path in sorted(artifacts.glob("prospective_markets_*/forecasts.json")):
        data = json.loads(path.read_text())
        before = changed
        for question in data["questions"]:
            if question["outcome"] is not None or question.get("cancelled") or question["resolution_date"] > today.date().isoformat():
                continue
            if _held(question, today):
                continue
            manifold, kalshi = question["id"].startswith("manifold-"), question["id"].startswith("kalshi-")
            name = "Kalshi" if kalshi else "Manifold" if manifold else "Polymarket"
            try:
                result = (kalshi_resolution(question["market_id"], fetch) if kalshi
                          else manifold_resolution(question["market_id"], fetch) if manifold
                          else resolution(question["market_id"], fetch, now=today))
            except Exception as exc:  # noqa: BLE001 - one unreachable market must not stop the others
                print(f"{question['id']}: still open ({type(exc).__name__})")
                continue
            if result["status"] == "resolved":
                question |= {"outcome": result["outcome"], "outcome_source": question["resolution_source"],
                             "outcome_note": f"{name}: {'Yes' if result['outcome'] else 'No'}"}
                changed += 1
            elif result["status"] == "cancelled":
                question |= {"cancelled": True, "outcome_note": (
                    "Kalshi settled it without a yes or no" if kalshi else "Manifold cancelled it" if manifold
                    else "Polymarket settled it 50/50")}
                changed += 1
            elif not manifold and not kalshi:  # why a past-due market is still open; flags only, never a price
                print(f"{question['id']}: pending (closed={result.get('closed')}, uma={result.get('uma') or 'none'}, "
                      f"settled_prices={result.get('settled_prices')})")
        if changed > before:
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"market questions: {changed} newly resolved")
    return changed


def _second_look_cli(argv: list[str]) -> int:
    rest, topics, out, positional = argv[2:], None, None, []
    while rest:
        item = rest.pop(0)
        if item == "--topics" and rest:
            topics = {t.strip() for t in rest.pop(0).split(",") if t.strip()}
        elif item == "--out" and rest and argv[1] == "export":
            out = Path(rest.pop(0))
        elif item.startswith("--"):
            print(__doc__)
            return 2
        else:
            positional.append(item)
    if argv[1] == "export" and len(positional) == 1 and out is not None:
        second_look_export(Path(positional[0]), out, topics)
    elif argv[1] == "record" and len(positional) == 2:
        second_look_record(Path(positional[0]), Path(positional[1]), topics)
    else:
        print(__doc__)
        return 2
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["prepare"]:
        skipped: dict[str, str] = {}
        prepare(skipped=skipped)
        if skipped:  # what the other sources gave is written; the run still fails, so the loss is seen
            return 3
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
    elif argv[:2] in (["second-look", "export"], ["second-look", "record"]):
        return _second_look_cli(argv)
    elif argv[:1] == ["resolve"]:
        resolve()
    elif argv[:1] == ["hold"] and len(argv) >= 3:
        rest, options = argv[3:], {}
        while rest:
            flag = rest.pop(0)
            if flag in ("--until", "--reason", "--source") and rest:
                options[flag[2:]] = rest.pop(0)
            else:
                print(__doc__)
                return 2
        if "until" not in options or "reason" not in options:
            print(__doc__)
            return 2
        hold(Path(argv[1]), argv[2], options["until"], options["reason"], options.get("source"))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

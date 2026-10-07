"""The owner's track record: every recorded forecast, its outcome, and whether the call was right.

Built from the committed prospective artifacts (``artifacts/prospective_*``) and their
``scores.json``. The API deployment does not ship ``artifacts/``, so
``scripts/build_track_record.py`` writes the result to ``data/track_record.json`` and
``GET /api/track-record`` serves that file.

"Our call" for a question is the forecast of the first forecaster in ``PRIMARY_ORDER``
that answered it. A call is right when it put more than 50% on what happened; an exact
50% is a toss-up and counts in the accuracy score but not in the right/wrong tally.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from forecastlab.fast_questions import bond_market_holidays
from forecastlab.macro import MacroSpec
from forecastlab.plain_questions import TOPICS, format_value, question_text, question_title
from forecastlab.timeutil import utcnow

TRACK_RECORD_VERSION = "track_record_v1"
SUPPLEMENT_SUFFIX = " (post-freeze supplement)"
COIN_FLIP_BRIER = 0.25
FORECASTERS: dict[str, dict[str, str]] = {
    "claude_code_forecaster_v1": {
        "label": "Claude", "about": "Claude researches each question on the web and states a probability."},
    "root_event_ensemble_v1": {
        "label": "Research pipeline", "about": "Three AI estimates built from one shared, checked evidence packet."},
    "single_model_forecaster_v1": {"label": "Single AI model", "about": "One AI model with web search."},
    "three_track_strict_forecaster_v1": {"label": "Three-track AI", "about": "Three separate AI analyses, combined."},
    "three_track_forecaster": {"label": "Three-track AI (early)", "about": "Earlier version of the three-track AI."},
    "statistical_baseline_v1": {
        "label": "Statistical baseline", "about": "A fixed rule based on past data, with no AI. The bar to beat."},
}
PRIMARY_ORDER = list(FORECASTERS)


def _forecaster(method: str) -> str:
    return method.removesuffix(SUPPLEMENT_SUFFIX)


def _call(forecasts: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_method = {f["method"]: f for f in forecasts}
    return next((by_method[m] for m in PRIMARY_ORDER if m in by_method), forecasts[0] if forecasts else None)


def verdict(probability: float, outcome: int | None) -> str | None:
    """'right', 'wrong', 'toss_up', or None while the question is open."""
    if outcome is None:
        return None
    if probability == 0.5:
        return "toss_up"
    return "right" if (probability > 0.5) == bool(outcome) else "wrong"


def _macro_questions(directory: Path) -> list[dict[str, Any]]:
    scores = json.loads((directory / "scores.json").read_text())
    manifest = json.loads((directory / "frozen_manifest.json").read_text())["manifest"]
    questions = []
    for entry in manifest["entries"]:
        spec = MacroSpec.model_validate(entry["macro"])
        forecasts: dict[str, dict[str, Any]] = {}
        for cell in scores["cells"]:
            if cell["entry_id"] == entry["entry_id"] and cell["probability"] is not None:
                method = _forecaster(cell["method"])
                forecasts.setdefault(method, {"method": method, "probability": cell["probability"]})
        result = scores["outcomes"].get(entry["entry_id"], {})
        status, outcome, actual = "pending", None, None
        if result.get("status") == "resolved":
            status, outcome, actual = "resolved", result["outcome"], format_value(spec.indicator, result["value"])
        elif spec.indicator == "treasury_10y":
            day = date.fromisoformat(spec.observation_period)
            if day in bond_market_holidays(day.year):
                status = "cancelled"
        questions.append({
            "id": entry["entry_id"], "title": question_title(spec), "detail": question_text(spec),
            "topic": TOPICS[spec.indicator], "resolves_on": spec.release_at.date().isoformat(),
            "status": status, "outcome": outcome, "actual": actual,
            "forecasts": sorted(forecasts.values(), key=lambda f: PRIMARY_ORDER.index(f["method"])
                                if f["method"] in PRIMARY_ORDER else len(PRIMARY_ORDER)),
            "source": directory.name})
    return questions


def _general_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    questions = []
    for question in data["questions"]:
        forecasts = [{"method": f["method"], "probability": f["probability"]}
                     for f in question["forecasts"] if f.get("probability") is not None]
        outcome = question.get("outcome")
        questions.append({
            "id": question["id"], "title": question["question"], "detail": question["resolution_criteria"],
            "topic": "Tech", "resolves_on": question["resolution_date"],
            "status": "pending" if outcome is None else "resolved",
            "outcome": None if outcome is None else int(outcome), "actual": None,
            "forecasts": sorted(forecasts, key=lambda f: PRIMARY_ORDER.index(f["method"])
                                if f["method"] in PRIMARY_ORDER else len(PRIMARY_ORDER)),
            "source": path.parent.name})
    return questions


def _brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def _tally(rows: list[tuple[float, int]]) -> dict[str, Any]:
    marks = [verdict(p, o) for p, o in rows]
    briers = [_brier(p, o) for p, o in rows]
    return {"resolved": len(rows), "right": marks.count("right"), "wrong": marks.count("wrong"),
            "toss_ups": marks.count("toss_up"),
            "brier": sum(briers) / len(briers) if briers else None, "coin_flip_brier": COIN_FLIP_BRIER}


def build_track_record(artifacts: Path, *, today: date | None = None) -> dict[str, Any]:
    questions: list[dict[str, Any]] = []
    for directory in sorted(artifacts.glob("prospective_*")):
        if (directory / "scores.json").exists() and (directory / "frozen_manifest.json").exists():
            questions += _macro_questions(directory)
        elif (directory / "forecasts.json").exists():
            questions += _general_questions(directory / "forecasts.json")
    for question in questions:
        call = _call(question["forecasts"])
        question["call"] = call
        question["verdict"] = verdict(call["probability"], question["outcome"]) if call else None
    resolved = [q for q in questions if q["status"] == "resolved" and q["call"]]
    summary = _tally([(q["call"]["probability"], q["outcome"]) for q in resolved])
    summary |= {"pending": sum(q["status"] == "pending" for q in questions),
                "cancelled": sum(q["status"] == "cancelled" for q in questions), "total": len(questions)}
    forecasters = []
    for method in sorted({f["method"] for q in questions for f in q["forecasts"]},
                         key=lambda m: PRIMARY_ORDER.index(m) if m in PRIMARY_ORDER else len(PRIMARY_ORDER)):
        rows = [(f["probability"], q["outcome"]) for q in resolved for f in q["forecasts"] if f["method"] == method]
        info = FORECASTERS.get(method, {"label": method, "about": ""})
        forecasters.append({"method": method, **info,
                            "forecasts": sum(f["method"] == method for q in questions for f in q["forecasts"]),
                            **_tally(rows)})
    # Latest results first, then open questions by the date they resolve.
    questions = (sorted((q for q in questions if q["status"] == "resolved"), key=lambda q: q["resolves_on"], reverse=True)
                 + sorted((q for q in questions if q["status"] != "resolved"), key=lambda q: q["resolves_on"]))
    return {"version": TRACK_RECORD_VERSION, "generated_on": (today or utcnow().date()).isoformat(),
            "summary": summary, "forecasters": forecasters, "questions": questions}

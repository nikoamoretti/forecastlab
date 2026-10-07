from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from forecastlab.macro import MacroSpec
from forecastlab.plain_questions import format_value, question_title
from forecastlab_api.track_record import build_track_record, verdict

UNEMPLOYMENT = {"indicator": "unemployment", "observation_period": "2026-09", "threshold": 4.1, "comparison": "gt",
                "release_at": "2026-10-02T12:30:00Z", "revision_policy": "first_release"}
HOLIDAY_YIELD = {"indicator": "treasury_10y", "observation_period": "2026-10-12", "threshold": 5.24, "comparison": "gt",
                 "release_at": "2026-10-13T20:00:00Z", "revision_policy": "first_release"}
OPEN_YIELD = HOLIDAY_YIELD | {"observation_period": "2026-10-13", "release_at": "2026-10-14T20:00:00Z"}


def _cohort(directory: Path) -> None:
    directory.mkdir()
    entries = [{"entry_id": "u", "macro": UNEMPLOYMENT}, {"entry_id": "h", "macro": HOLIDAY_YIELD},
               {"entry_id": "y", "macro": OPEN_YIELD}]
    (directory / "frozen_manifest.json").write_text(json.dumps({"manifest": {"entries": entries}}))
    cells = [
        {"entry_id": "u", "method": "root_event_ensemble_v1", "probability": 0.37},
        {"entry_id": "u", "method": "statistical_baseline_v1", "probability": 0.6},
        {"entry_id": "u", "method": "single_model_forecaster_v1", "probability": None},
        {"entry_id": "h", "method": "claude_code_forecaster_v1 (post-freeze supplement)", "probability": 0.58},
        {"entry_id": "y", "method": "statistical_baseline_v1", "probability": 0.54},
        {"entry_id": "y", "method": "claude_code_forecaster_v1 (post-freeze supplement)", "probability": 0.5},
    ]
    outcomes = {"u": {"status": "resolved", "value": "4.2", "outcome": 1},
                "h": {"status": "pending", "reason": "fred_initial_release_not_yet_published"},
                "y": {"status": "pending", "reason": "fred_initial_release_not_yet_published"}}
    (directory / "scores.json").write_text(json.dumps({"cells": cells, "outcomes": outcomes}))


def test_track_record_marks_calls_and_counts_each_forecaster(tmp_path: Path) -> None:
    _cohort(tmp_path / "prospective_test")
    (tmp_path / "prospective_gpu").mkdir()
    (tmp_path / "prospective_gpu" / "forecasts.json").write_text(json.dumps({"questions": [{
        "id": "gpu", "question": "Will a GPU launch?", "resolution_criteria": "YES if it launches.",
        "resolution_date": "2027-01-31", "outcome": None,
        "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.38}]}]}))

    record = build_track_record(tmp_path, today=date(2026, 10, 7))

    by_id = {q["id"]: q for q in record["questions"]}
    unemployment = by_id["u"]
    assert unemployment["title"] == "Will the U.S. unemployment rate for September 2026 come in above 4.1%?"
    # Our call is the median of the forecasters that answered: (0.37 + 0.6) / 2.
    assert unemployment["call"] == {"method": "combined_median_v1", "probability": pytest.approx(0.485), "members": 2}
    assert (unemployment["status"], unemployment["actual"], unemployment["verdict"]) == ("resolved", "4.2%", "wrong")
    assert [f["method"] for f in unemployment["forecasts"]] == ["root_event_ensemble_v1", "statistical_baseline_v1"]
    assert by_id["h"]["status"] == "cancelled"  # Columbus Day: no 10-year yield is published.
    assert by_id["y"]["call"]["probability"] == pytest.approx(0.52)  # supplement suffix removed before combining
    assert by_id["y"]["forecasts"][0]["method"] == "claude_code_forecaster_v1"
    assert by_id["gpu"]["status"] == "pending" and by_id["gpu"]["topic"] == "Tech"
    assert [q["id"] for q in record["questions"]] == ["u", "h", "y", "gpu"]

    summary = record["summary"]
    assert (summary["resolved"], summary["right"], summary["wrong"], summary["pending"], summary["cancelled"]) == (1, 0, 1, 2, 1)
    assert summary["brier"] == pytest.approx(0.515 ** 2)
    assert record["forecasters"][0]["method"] == "combined_median_v1"
    assert (record["forecasters"][0]["resolved"], record["forecasters"][0]["wrong"]) == (1, 1)
    baseline = next(f for f in record["forecasters"] if f["method"] == "statistical_baseline_v1")
    assert (baseline["forecasts"], baseline["resolved"], baseline["right"]) == (2, 1, 1)
    assert baseline["label"] == "Statistical baseline"


@pytest.mark.parametrize(("probability", "outcome", "expected"), [
    (0.63, 1, "right"), (0.37, 1, "wrong"), (0.37, 0, "right"), (0.5, 1, "toss_up"), (0.7, None, None)])
def test_verdict(probability: float, outcome: int | None, expected: str | None) -> None:
    assert verdict(probability, outcome) == expected


def test_plain_question_titles_and_values() -> None:
    payrolls = MacroSpec.model_validate(UNEMPLOYMENT | {"indicator": "payrolls", "threshold": 162000})
    assert question_title(payrolls) == "Will the U.S. economy add more than 162,000 jobs in September 2026?"
    assert question_title(MacroSpec.model_validate(OPEN_YIELD)) == "Will the 10-year U.S. Treasury yield on Oct 13, 2026 be above 5.24%?"
    assert format_value("payrolls", "29000") == "+29,000 jobs"
    assert format_value("jobless_claims", 231000) == "231,000 claims"
    assert format_value("cpi", "3.4") == "3.4%"


def test_track_record_endpoint_serves_the_built_file(client) -> None:
    response = client.get("/api/track-record")
    assert response.status_code == 200
    body = response.json()
    assert {"summary", "forecasters", "questions"} <= set(body)
    assert body["summary"]["resolved"] == body["summary"]["right"] + body["summary"]["wrong"] + body["summary"]["toss_ups"]

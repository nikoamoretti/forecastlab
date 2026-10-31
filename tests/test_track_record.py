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


def test_unscored_question_is_shown_but_counts_in_no_score(tmp_path: Path) -> None:
    (tmp_path / "prospective_markets_x").mkdir()
    question = {"resolution_criteria": "Polymarket rules.", "resolution_date": "2026-10-05", "outcome": 1,
                "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.8},
                              {"method": "market_price_v1", "probability": 0.7}]}
    (tmp_path / "prospective_markets_x" / "forecasts.json").write_text(json.dumps({"questions": [
        question | {"id": "clean", "question": "Clean?"},
        question | {"id": "leaked", "question": "Leaked?", "integrity_note": "Saw the odds.", "unscored": True}]}))

    record = build_track_record(tmp_path, today=date(2026, 10, 7))

    by_id = {q["id"]: q for q in record["questions"]}
    assert (by_id["leaked"]["scored"], by_id["leaked"]["verdict"], by_id["leaked"]["note"]) == (False, "right", "Saw the odds.")
    assert by_id["clean"]["scored"] is True
    assert (record["summary"]["resolved"], record["summary"]["unscored"]) == (1, 1)
    for forecaster in record["forecasters"]:
        assert forecaster["resolved"] == 1, forecaster["method"]


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


def _markets(directory: Path, questions: list[dict]) -> None:
    directory.mkdir()
    (directory / "forecasts.json").write_text(json.dumps({"questions": [
        {"question": q["id"], "resolution_criteria": "Rules.", "resolution_date": q.get("date", "2026-10-30"),
         "outcome": q.get("outcome"), **q} for q in questions]}))


def test_from_oct_24_better_records_count_for_more_and_the_call_is_frozen(tmp_path: Path) -> None:
    good, bad = "claude_code_forecaster_v1", "statistical_baseline_v1"
    # Before the switch: the plain median, and a record where one forecaster is much better.
    _markets(tmp_path / "prospective_markets_20261020", [
        {"id": f"old{n}", "date": "2026-10-21", "outcome": n % 2,
         "forecasts": [{"method": good, "probability": 0.9 if n % 2 else 0.1},
                       {"method": bad, "probability": 0.2 if n % 2 else 0.8}]} for n in range(10)])
    _markets(tmp_path / "prospective_markets_20261024", [
        {"id": "new", "forecasts": [{"method": good, "probability": 0.7}, {"method": bad, "probability": 0.3},
                                    {"method": "market_price_v1", "probability": 0.1}]}])

    record = build_track_record(tmp_path, today=date(2026, 10, 24), freeze=True)
    by_id = {q["id"]: q for q in record["questions"]}
    assert by_id["old0"]["call"]["method"] == "combined_median_v1"
    call = by_id["new"]["call"]
    assert (call["method"], call["probability"], call["members"]) == ("combined_weighted_v1", 0.7, 2)
    assert call["weights"][good] > 2 * call["weights"][bad]  # the market is a benchmark, never a member
    frozen = json.loads((tmp_path / "prospective_markets_20261024" / "calls.json").read_text())
    assert frozen == {"new": call}

    # Later results that would change the weights do not rewrite a call already made.
    (tmp_path / "prospective_markets_20261024" / "calls.json").write_text(json.dumps({"new": call | {"probability": 0.3}}))
    assert {q["id"]: q for q in build_track_record(tmp_path, freeze=True)["questions"]}["new"]["call"]["probability"] == 0.3


def test_weighted_call_without_any_record_is_the_plain_median_of_the_middle_member(tmp_path: Path) -> None:
    _markets(tmp_path / "prospective_markets_20261025", [
        {"id": "q", "forecasts": [{"method": m, "probability": p} for m, p in
                                  [("claude_code_forecaster_v1", 0.2), ("judge_kimi_k26_v1", 0.5), ("judge_qwen38_27b_v1", 0.9)]]}])
    call = build_track_record(tmp_path)["questions"][0]["call"]
    assert (call["method"], call["probability"]) == ("combined_weighted_v1", 0.5)
    assert not (tmp_path / "prospective_markets_20261025" / "calls.json").exists()  # only saved with freeze


# ---- Claude's confidence correction (claude_calibrated_v1) and the headline score ----

CLAUDE = "claude_code_forecaster_v1"


def _history(root: Path, count: int, *, late_date: str | None = None) -> None:
    """``count`` decided Claude forecasts from before the correction starts: 70% said, 40% came true."""
    _markets(root / "prospective_markets_20261001", [
        {"id": f"h{n}", "date": "2026-10-05", "outcome": 1 if n % 10 < 4 else 0,
         "forecasts": [{"method": CLAUDE, "probability": 0.7}]} for n in range(count)])
    if late_date:
        _markets(root / "prospective_markets_20261002", [
            {"id": "late", "date": late_date, "outcome": 0, "forecasts": [{"method": CLAUDE, "probability": 0.7}]}])


def _today_question(root: Path, probability: float = 0.7, day: str = "20261031") -> None:
    _markets(root / f"prospective_markets_{day}", [
        {"id": f"new{day}", "date": "2026-11-05", "forecasts": [
            {"method": CLAUDE, "probability": probability}, {"method": "statistical_baseline_v1", "probability": 0.5},
            {"method": "market_price_v1", "probability": 0.2}]}])


def _forecast(record: dict, question_id: str, method: str) -> dict | None:
    question = next(q for q in record["questions"] if q["id"] == question_id)
    return next((f for f in question["forecasts"] if f["method"] == method), None)


def test_fit_starts_at_the_identity_and_follows_the_data() -> None:
    from forecastlab_api.track_record import fit_confidence_correction

    slope, intercept = fit_confidence_correction([])
    assert (round(slope, 6), round(intercept, 6)) == (1.0, 0.0)
    # Said 90% and 10% but each came true only 60% / 40% of the time: overconfident, so slope < 1.
    rows = [(0.9, 1)] * 60 + [(0.9, 0)] * 40 + [(0.1, 1)] * 40 + [(0.1, 0)] * 60
    over_slope, over_intercept = fit_confidence_correction(rows * 3)
    assert over_slope < 0.6 and abs(over_intercept) < 0.05
    # Always right when stated: stays near the identity.
    honest = [(0.8, 1)] * 80 + [(0.8, 0)] * 20 + [(0.2, 1)] * 20 + [(0.2, 0)] * 80
    honest_slope, honest_intercept = fit_confidence_correction(honest)
    assert abs(honest_slope - 1) < 0.1 and abs(honest_intercept) < 0.05


def test_the_prior_pulls_a_short_record_toward_the_identity() -> None:
    from forecastlab_api.track_record import fit_confidence_correction

    pattern = [(0.9, 1)] * 6 + [(0.9, 0)] * 4 + [(0.1, 1)] * 4 + [(0.1, 0)] * 6
    short, _ = fit_confidence_correction(pattern)
    long, _ = fit_confidence_correction(pattern * 10)
    assert 0.1 < long < short < 1  # the same pattern moves the fit further the more results there are
    # 20 pseudo-observations: a pure-prior fit with a few results never leaves the neighbourhood of (1, 0).
    slope, intercept = fit_confidence_correction([(0.7, 0)] * 3)
    assert 0.7 < slope < 1.1 and -1.0 < intercept < 0


def test_corrected_forecast_waits_for_thirty_results(tmp_path: Path) -> None:
    _history(tmp_path, 29)
    _today_question(tmp_path)
    record = build_track_record(tmp_path, today=date(2026, 10, 31), freeze=True)
    assert _forecast(record, "new20261031", "claude_calibrated_v1") is None
    assert record["calibration"] is None
    assert not (tmp_path / "prospective_markets_20261031" / "calibrated.json").exists()

    enough = tmp_path / "enough"
    enough.mkdir()
    _history(enough, 30)
    assert build_track_record(enough, today=date(2026, 10, 31))["calibration"]["results"] == 30


def test_corrected_forecast_is_frozen_with_its_fit_and_uses_only_results_known_before_the_day(tmp_path: Path) -> None:
    _history(tmp_path, 40, late_date="2026-10-31")  # decided on the forecast day itself: not known beforehand
    _today_question(tmp_path)
    record = build_track_record(tmp_path, today=date(2026, 10, 31), freeze=True)
    corrected = _forecast(record, "new20261031", "claude_calibrated_v1")
    assert corrected is not None
    assert corrected["results"] == 40 and corrected["raw"] == 0.7 and corrected["as_of"] == "2026-10-31"
    # Said 70% but only 40% came true: the correction moves it down.
    assert 0.02 <= corrected["probability"] < 0.7
    frozen = json.loads((tmp_path / "prospective_markets_20261031" / "calibrated.json").read_text())
    assert frozen == {"new20261031": {k: v for k, v in corrected.items() if k != "method"}}
    assert {"slope", "intercept", "results"} <= set(frozen["new20261031"])

    # Results that arrive later never rewrite it.
    path = tmp_path / "prospective_markets_20261031" / "calibrated.json"
    path.write_text(json.dumps({"new20261031": frozen["new20261031"] | {"probability": 0.5}}))
    later = build_track_record(tmp_path, today=date(2026, 11, 3), freeze=True)
    assert _forecast(later, "new20261031", "claude_calibrated_v1")["probability"] == 0.5


def test_the_correction_ignores_a_result_decided_after_the_forecast_day(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    _today_question(tmp_path)
    flipped = tmp_path / "prospective_markets_20261002"
    _markets(flipped, [{"id": "after", "date": "2026-11-02", "outcome": 1, "forecasts": [{"method": CLAUDE, "probability": 0.05}]}])
    with_after = _forecast(build_track_record(tmp_path), "new20261031", "claude_calibrated_v1")
    (flipped / "forecasts.json").unlink()
    flipped.rmdir()
    without = _forecast(build_track_record(tmp_path), "new20261031", "claude_calibrated_v1")
    assert with_after == without


def test_questions_forecast_before_the_start_day_get_no_corrected_value(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    _today_question(tmp_path, day="20261030")
    record = build_track_record(tmp_path, today=date(2026, 10, 31), freeze=True)
    assert _forecast(record, "new20261030", "claude_calibrated_v1") is None
    assert not (tmp_path / "prospective_markets_20261030" / "calibrated.json").exists()


def test_the_corrected_forecast_is_scored_but_never_part_of_our_call(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    _today_question(tmp_path)
    # Our call on the open question: members are Claude and the baseline only (median of 0.7, 0.5).
    record = build_track_record(tmp_path, today=date(2026, 10, 31))
    question = next(q for q in record["questions"] if q["id"] == "new20261031")
    assert question["call"]["members"] == 2
    assert question["call"]["probability"] == 0.5 and set(question["call"]["weights"]) == {CLAUDE, "statistical_baseline_v1"}
    corrected = _forecast(record, "new20261031", "claude_calibrated_v1")
    assert corrected is not None and corrected["probability"] != question["call"]["probability"]
    # It has its own row on the record, with its label.
    row = next(f for f in record["forecasters"] if f["method"] == "claude_calibrated_v1")
    assert row["label"] == "Claude, confidence-corrected" and row["forecasts"] == 1
    # And it carries no weight in the weights used for later calls.
    from forecastlab_api.track_record import _weights
    assert "claude_calibrated_v1" not in _weights([{**q, "outcome": 1} for q in record["questions"]])


def test_headline_score_and_the_fair_market_comparison(tmp_path: Path) -> None:
    _markets(tmp_path / "prospective_markets_20261001", [
        {"id": "a", "date": "2026-10-05", "outcome": 1, "forecasts": [
            {"method": CLAUDE, "probability": 0.8}, {"method": "market_price_v1", "probability": 0.6}]},
        {"id": "b", "date": "2026-10-05", "outcome": 0, "forecasts": [
            {"method": CLAUDE, "probability": 0.4}, {"method": "manifold_price_v1", "probability": 0.1}]},
        {"id": "c", "date": "2026-10-05", "outcome": 1, "forecasts": [{"method": CLAUDE, "probability": 0.5}]}])  # no market
    summary = build_track_record(tmp_path, today=date(2026, 10, 31))["summary"]
    assert summary["resolved"] == 3
    assert summary["brier"] == pytest.approx((0.04 + 0.16 + 0.25) / 3)
    versus = summary["vs_market"]
    assert versus["questions"] == 2
    assert versus["ours"] == pytest.approx((0.04 + 0.16) / 2)
    assert versus["market"] == pytest.approx((0.16 + 0.01) / 2)

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from forecastlab.macro import MacroSpec
from forecastlab.plain_questions import format_value, question_title
from forecastlab_api import track_record
from forecastlab_api.learned_model import fit_learned_model, learned_probability
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


def test_each_forecaster_carries_the_day_of_its_latest_forecast(tmp_path: Path) -> None:
    """The page lists a forecaster with no recent forecast under "earlier forecasters"."""
    judge = "judge_qwen38_27b_v1"
    _markets(tmp_path / "prospective_markets_20261020", [
        {"id": "old", "forecasts": [{"method": CLAUDE, "probability": 0.7}, {"method": judge, "probability": 0.6}]}])
    _markets(tmp_path / "prospective_markets_20261110", [
        {"id": "new", "forecasts": [{"method": CLAUDE, "probability": 0.7}, {"method": "market_price_v1", "probability": 0.4}]}])
    _cohort(tmp_path / "prospective_test")  # a directory without a day in its name

    last = {f["method"]: f["last_forecast_on"] for f in build_track_record(tmp_path, today=date(2026, 11, 10))["forecasters"]}

    assert (last["combined_median_v1"], last[CLAUDE], last["market_price_v1"]) == ("2026-11-10", "2026-11-10", "2026-11-10")
    assert last[judge] == "2026-10-20"
    assert last["root_event_ensemble_v1"] is None


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


def test_from_nov_5_our_call_is_claudes_own_forecast_frozen_and_earlier_calls_stay(tmp_path: Path) -> None:
    _markets(tmp_path / "prospective_markets_20261104", [
        {"id": "before", "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.2},
                                       {"method": "judge_claude_sonnet55_v1", "probability": 0.6},
                                       {"method": "judge_qwen38_27b_v1", "probability": 0.7}]}])
    _markets(tmp_path / "prospective_markets_20261105", [
        {"id": "claude", "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.2},
                                       {"method": "judge_claude_sonnet55_v1", "probability": 0.6},
                                       {"method": "market_price_v1", "probability": 0.9}]},
        {"id": "no-claude", "forecasts": [{"method": "judge_claude_sonnet55_v1", "probability": 0.6}]}])
    by_id = {q["id"]: q for q in build_track_record(tmp_path, today=date(2026, 11, 5), freeze=True)["questions"]}
    assert (by_id["before"]["call"]["method"], by_id["before"]["call"]["probability"]) == ("combined_weighted_v1", 0.6)
    assert by_id["claude"]["call"] == {"method": "claude_call_v1", "probability": 0.2, "members": 1}
    assert by_id["no-claude"]["call"]["method"] == "combined_weighted_v1"
    frozen = json.loads((tmp_path / "prospective_markets_20261105" / "calls.json").read_text())
    assert frozen["claude"] == by_id["claude"]["call"]


# ---- Claude's confidence correction (claude_calibrated_v1) and the headline score ----

CLAUDE = "claude_code_forecaster_v1"
SECOND_LOOK = "claude_second_look_v1"
LEARNED = "learned_model_v1"


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


def test_from_nov_6_our_call_is_the_corrected_forecast_frozen(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    _today_question(tmp_path, day="20261106")
    record = build_track_record(tmp_path, today=date(2026, 11, 6), freeze=True)
    question = next(q for q in record["questions"] if q["id"] == "new20261106")
    corrected = _forecast(record, "new20261106", "claude_calibrated_v1")
    assert corrected is not None and corrected["probability"] != 0.7
    assert question["call"] == {"method": "claude_corrected_call_v1", "probability": corrected["probability"], "members": 1}
    frozen = json.loads((tmp_path / "prospective_markets_20261106" / "calls.json").read_text())
    assert frozen["new20261106"] == question["call"]


def _research(directory: Path, *, good: list[str] = (), warned: list[str] = (), failed: list[str] = ()) -> None:
    """A research.json as the download step writes it: findings per question, some with warnings, some failed."""
    (directory / "research.json").write_text(json.dumps({
        "findings": {**{i: {"facts": [{"fact": "x", "source": 0}]} for i in good},
                     **{i: {"facts": [{"fact": "x", "source": 0}], "warnings": ["only 1 primary source(s) with quotes (need 2): rely on the facts less than on official data"]}
                        for i in warned}},
        "failed": {i: "ValueError: only 0 usable sources" for i in failed}}))


def _anchored_day(root: Path, day: str, questions: dict[str, tuple[float, float | None]], **research: list[str]) -> Path:
    """A market directory: id -> (Claude's probability, the market's price or None)."""
    directory = root / f"prospective_markets_{day}"
    _markets(directory, [
        {"id": qid, "date": "2026-11-20", "forecasts": [{"method": CLAUDE, "probability": claude}]
         + ([{"method": "market_price_v1", "probability": price}] if price is not None else [])}
        for qid, (claude, price) in questions.items()])
    _research(directory, **research)
    return directory


def test_from_nov_10_the_call_is_the_market_price_until_an_edge_is_proven(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    nov9 = _anchored_day(tmp_path, "20261109", {"n9": (0.7, 0.2)}, good=["n9"])
    nov10 = _anchored_day(tmp_path, "20261110", {
        "up": (0.9, 0.5), "down": (0.1, 0.5), "inside": (0.55, 0.5), "high": (0.99, 0.95), "low": (0.0, 0.03),
        "none": (0.7, None)}, good=["up", "down", "inside", "high", "low", "none"])
    record = build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    # No rule is promoted, so the call is the price itself wherever Claude stands. Both numbers are kept on the call,
    # with the move it was allowed (none); no rule is named and nothing is marked as held.
    assert call["up"] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9, "market": 0.5,
                          "max_move": 0.0}
    assert call["down"] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.1, "market": 0.5,
                            "max_move": 0.0}
    assert call["inside"]["probability"] == 0.5
    assert call["high"]["probability"] == 0.95 and call["low"]["probability"] == 0.03
    # No market price: Claude's own forecast, as before.
    assert call["none"] == {"method": "claude_call_v1", "probability": 0.7, "members": 1}
    # Nov 9 keeps the corrected-forecast rule (claude_calibrated_v1), not the anchor.
    assert call["n9"]["method"] == "claude_corrected_call_v1"
    # The independent forecast stays tracked beside the call.
    assert _forecast(record, "up", CLAUDE)["probability"] == 0.9
    assert _forecast(record, "up", "market_price_v1")["probability"] == 0.5
    assert json.loads((nov10 / "calls.json").read_text())["up"] == call["up"]
    # Rebuilding changes nothing.
    frozen = {path: path.read_text() for path in (nov9 / "calls.json", nov10 / "calls.json")}
    again = build_track_record(tmp_path, today=date(2026, 11, 12), freeze=True)
    assert {q["id"]: q["call"] for q in again["questions"]} == call
    assert {path: path.read_text() for path in frozen} == frozen


def test_a_promoted_rule_moves_the_call_toward_claude_by_at_most_its_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(every_question_move_v1="promoted"))
    _history(tmp_path, 40)
    nov9 = _anchored_day(tmp_path, "20261109", {"n9": (0.7, 0.2)}, good=["n9"])
    nov10 = _anchored_day(tmp_path, "20261110", {
        "up": (0.9, 0.5), "down": (0.1, 0.5), "inside": (0.55, 0.5), "high": (0.99, 0.95), "low": (0.0, 0.03),
        "none": (0.7, None)}, good=["up", "down", "inside", "high", "low", "none"])
    record = build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    # Moved toward Claude by at most 0.10, on both sides; both numbers are kept on the call,
    # with the move it was allowed and the rule that allowed it.
    assert call["up"] == {"method": "market_anchored_call_v1", "probability": 0.6, "members": 1, "claude": 0.9, "market": 0.5,
                          "max_move": 0.1, "rule": "every_question_move_v1"}
    assert call["down"]["probability"] == pytest.approx(0.4) and call["down"]["claude"] == 0.1
    assert call["inside"]["probability"] == 0.55  # within the cap: Claude's number
    assert call["high"]["probability"] == 0.98 and call["low"]["probability"] == 0.02  # kept within 2% to 98%
    # No market price: Claude's own forecast, whatever the rules say.
    assert call["none"] == {"method": "claude_call_v1", "probability": 0.7, "members": 1}
    # Nov 9 keeps the corrected-forecast rule (claude_calibrated_v1), not the anchor.
    assert call["n9"]["method"] == "claude_corrected_call_v1"
    assert json.loads((nov10 / "calls.json").read_text())["up"] == call["up"]
    # Rebuilding changes nothing, even when the inputs behind a frozen call have moved.
    frozen = {path: path.read_text() for path in (nov9 / "calls.json", nov10 / "calls.json")}
    _research(nov10, failed=["up"])
    again = build_track_record(tmp_path, today=date(2026, 11, 12), freeze=True)
    assert {q["id"]: q["call"] for q in again["questions"]} == call
    assert {path: path.read_text() for path in frozen} == frozen


def test_calls_frozen_before_nov_10_are_byte_identical_to_the_rules_they_were_made_under(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    for day in ("20261104", "20261105", "20261106", "20261109"):
        _anchored_day(tmp_path, day, {f"q{day}": (0.7, 0.2)}, good=[f"q{day}"])
    # Same data once with the Nov 10 code path unreachable (today before it), then after: the frozen files do not differ.
    build_track_record(tmp_path, today=date(2026, 11, 9), freeze=True)
    before = {p.parent.name: p.read_bytes() for p in sorted(tmp_path.glob("prospective_markets_*/calls.json"))}
    _anchored_day(tmp_path, "20261110", {"q20261110": (0.7, 0.2)}, good=["q20261110"])
    build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    after = {p.parent.name: p.read_bytes() for p in sorted(tmp_path.glob("prospective_markets_*/calls.json"))}
    assert {k: v for k, v in after.items() if k < "prospective_markets_20261110"} == before
    assert json.loads(before["prospective_markets_20261104"])["q20261104"]["method"] == "combined_weighted_v1"
    assert json.loads(before["prospective_markets_20261105"])["q20261105"] == {"method": "claude_call_v1", "probability": 0.7, "members": 1}
    assert json.loads(before["prospective_markets_20261109"])["q20261109"]["method"] == "claude_corrected_call_v1"
    assert json.loads(after["prospective_markets_20261110"])["q20261110"]["probability"] == 0.2  # the market price


def test_weak_research_holds_a_call_that_may_move_at_the_market_price(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(every_question_move_v1="promoted"))
    _anchored_day(tmp_path, "20261110", {"ok": (0.9, 0.5), "warned": (0.9, 0.5), "failed": (0.9, 0.5), "empty": (0.9, 0.5)},
                  good=["ok"], warned=["warned"], failed=["failed"])
    call = {q["id"]: q["call"] for q in build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)["questions"]}
    assert call["ok"]["probability"] == pytest.approx(0.6) and "held" not in call["ok"]
    for held in ("warned", "failed", "empty"):  # a primary-source warning, a failed question, no findings at all
        assert call[held] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9,
                              "market": 0.5, "max_move": 0.1, "rule": "every_question_move_v1", "held": "weak research"}
    # Claude's own forecast is still tracked on the held question.
    assert _forecast(build_track_record(tmp_path, today=date(2026, 11, 10)), "warned", CLAUDE)["probability"] == 0.9


def test_a_market_price_without_a_claude_forecast_follows_the_old_fallback(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_markets_20261110"
    _markets(directory, [{"id": "q", "date": "2026-11-20", "forecasts": [
        {"method": "judge_qwen38_27b_v1", "probability": 0.6}, {"method": "market_price_v1", "probability": 0.2}]}])
    _research(directory, good=["q"])
    call = build_track_record(tmp_path, today=date(2026, 11, 10))["questions"][0]["call"]
    assert call["method"] == "combined_weighted_v1" and call["probability"] == 0.6


def test_the_record_scores_the_call_claude_alone_and_the_market_on_the_same_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A promoted rule, so that the call, Claude alone and the market are three different numbers.
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(every_question_move_v1="promoted"))
    directory = tmp_path / "prospective_markets_20261110"
    _markets(directory, [
        {"id": "a", "date": "2026-11-12", "outcome": 1, "forecasts": [
            {"method": CLAUDE, "probability": 0.9}, {"method": "market_price_v1", "probability": 0.5}]},
        {"id": "b", "date": "2026-11-12", "outcome": 0, "forecasts": [
            {"method": CLAUDE, "probability": 0.2}, {"method": "manifold_price_v1", "probability": 0.2}]},
        {"id": "no-price", "date": "2026-11-12", "outcome": 1, "forecasts": [{"method": CLAUDE, "probability": 0.8}]}])
    _research(directory, good=["a", "b", "no-price"])
    record = build_track_record(tmp_path, today=date(2026, 11, 13), freeze=True)
    # a: call 0.6; b: call 0.2 (already at the market). Questions without a price are in no market comparison.
    versus = record["summary"]["vs_market"]
    assert versus["questions"] == 2 and versus["claude_questions"] == 2
    assert versus["ours"] == pytest.approx((0.4 ** 2 + 0.2 ** 2) / 2)
    assert versus["claude"] == pytest.approx((0.1 ** 2 + 0.2 ** 2) / 2)
    assert versus["market"] == pytest.approx((0.5 ** 2 + 0.2 ** 2) / 2)
    assert versus["since_anchored"] == {"date": "2026-11-10", "questions": 2, "ours": versus["ours"],
                                        "claude": versus["claude"], "market": versus["market"]}
    week = record["summary"]["progress"]["weeks"][0]
    assert week["claude"] == pytest.approx(versus["claude"]) and week["ours"] == pytest.approx(versus["ours"])
    assert record["summary"]["progress"]["changes"][-1] == {"date": "2026-11-10", "label": "Call anchored to market"}
    claude = next(f for f in record["forecasters"] if f["method"] == CLAUDE)
    assert claude["label"] == "Claude alone" and claude["resolved"] == 3


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


def test_progress_groups_by_forecast_week_and_splits_before_and_after_nov_6(tmp_path: Path) -> None:
    def q(name: str, outcome: int, ours: float, price: float | None) -> dict:
        forecasts = [{"method": CLAUDE, "probability": ours}]
        if price is not None:
            forecasts.append({"method": "market_price_v1", "probability": price})
        return {"id": name, "date": "2026-11-10", "outcome": outcome, "forecasts": forecasts}
    # 2026-11-02 is a Monday: Nov 2 and Nov 8 share a week, Nov 9 starts the next.
    _markets(tmp_path / "prospective_markets_20261102", [q("a", 1, 0.8, 0.6), q("nomarket", 1, 0.9, None)])
    _markets(tmp_path / "prospective_markets_20261108", [q("b", 0, 0.4, 0.1)])
    _markets(tmp_path / "prospective_markets_20261109", [q("c", 1, 0.5, 0.9)])
    progress = build_track_record(tmp_path, today=date(2026, 11, 12))["summary"]["progress"]
    first, second = progress["weeks"]
    assert (first["start"], first["questions"]) == ("2026-11-02", 2)
    assert first["ours"] == pytest.approx((0.04 + 0.16) / 2) and first["market"] == pytest.approx((0.16 + 0.01) / 2)
    assert (first["right"], first["wrong"]) == (2, 0)
    assert (second["start"], second["questions"]) == ("2026-11-09", 1)
    assert second["ours"] == pytest.approx(0.25) and second["market"] == pytest.approx(0.01)
    assert (second["right"], second["wrong"]) == (0, 0)  # an exact 50% is a toss-up
    assert [c["date"] for c in progress["changes"]] == ["2026-10-24", "2026-11-06", "2026-11-09", "2026-11-10"]
    split = progress["since_last_change"]
    assert split["date"] == "2026-11-06"
    assert split["before"]["questions"] == 1 and split["before"]["ours"] == pytest.approx(0.04)
    assert split["after"]["questions"] == 2 and split["after"]["ours"] == pytest.approx((0.16 + 0.25) / 2)
    assert split["after"]["market"] == pytest.approx(0.01)


# --- Call rules and edge tests -------------------------------------------------------------------------------------


def _rules(**status: str) -> tuple[dict, ...]:
    """The registered rules with the given statuses (rule id -> status) set."""
    return tuple(r | {"status": status.get(r["id"], r["status"])} for r in track_record.CALL_RULES)


def _tech_and_other(root: Path, day: str = "20261110") -> Path:
    directory = root / f"prospective_markets_{day}"
    _markets(directory, [
        {"id": "tech", "topic": "Tech", "forecasts": [{"method": CLAUDE, "probability": 0.9}, {"method": "market_price_v1", "probability": 0.5}]},
        {"id": "other", "topic": "Politics", "forecasts": [{"method": CLAUDE, "probability": 0.9}, {"method": "market_price_v1", "probability": 0.5}]}])
    _research(directory, good=["tech", "other"])
    return directory


def test_the_registry_starts_with_four_testing_rules_and_no_default_move() -> None:
    assert track_record.DEFAULT_MOVE == 0.0  # owner's decision of 2026-11-10: the market price until an edge is proven
    assert [(r["id"], r["label"], r["registered"], r["topics"], r["move"], r["status"], r["forecaster"]) for r in track_record.CALL_RULES] == [
        ("tech_move_v1", "Tech questions", date(2026, 11, 10), ("Tech",), 0.10, "testing", None),
        ("every_question_move_v1", "Every question", date(2026, 11, 10), None, 0.10, "testing", None),
        ("tech_second_look_v1", "Second look on Tech", date(2026, 11, 11), ("Tech",), 1.0, "testing", SECOND_LOOK),
        ("learned_model_call_v1", "Learned from results", date(2026, 11, 11), None, 1.0, "testing", LEARNED)]
    assert track_record.SECOND_LOOK == "claude_second_look_v1" and SECOND_LOOK in track_record.EXCLUDED
    assert track_record.LEARNED == "learned_model_v1" and LEARNED in track_record.EXCLUDED
    assert (track_record.PROMOTION_QUESTIONS, track_record.PROMOTION_DATES) == (40, 10)


def test_a_testing_rule_changes_no_call_and_a_default_move_is_what_moves_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "DEFAULT_MOVE", 0.10)
    _tech_and_other(tmp_path)
    record = build_track_record(tmp_path, today=date(2026, 11, 10))
    call = {q["id"]: q["call"] for q in record["questions"]}
    for qid in ("tech", "other"):
        assert call[qid] == {"method": "market_anchored_call_v1", "probability": 0.6, "members": 1, "claude": 0.9,
                             "market": 0.5, "max_move": 0.1}
    assert record["summary"]["call_policy"] == {"default_move": 0.1, "promoted": [], "bar": {"questions": 40, "dates": 10, "interval": 0.9}}
    ours = next(f for f in record["forecasters"] if f["method"] == "combined_median_v1")
    assert "moves at most 10 points toward Claude's forecast" in ours["about"]
    market = next(f for f in record["forecasters"] if f["method"] == "market_price_v1")
    assert "moves at most 10 points" in market["about"]


def test_by_default_the_call_is_the_market_price_and_claude_alone_is_still_scored(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_markets_20261110"
    _markets(directory, [
        {"id": "a", "date": "2026-11-12", "outcome": 1, "forecasts": [{"method": CLAUDE, "probability": 0.9}, {"method": "market_price_v1", "probability": 0.5}]},
        {"id": "weak", "date": "2026-11-12", "outcome": 0, "forecasts": [{"method": CLAUDE, "probability": 0.1}, {"method": "market_price_v1", "probability": 0.4}]}])
    _research(directory, good=["a"])
    record = build_track_record(tmp_path, today=date(2026, 11, 13), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    # No move: the price itself, with no rule named and no "held" mark (nothing was held back).
    assert call["a"] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9, "market": 0.5, "max_move": 0.0}
    assert call["weak"] == {"method": "market_anchored_call_v1", "probability": 0.4, "members": 1, "claude": 0.1, "market": 0.4, "max_move": 0.0}
    versus = record["summary"]["vs_market"]
    assert versus["ours"] == pytest.approx(versus["market"]) and versus["claude"] == pytest.approx((0.1 ** 2 + 0.1 ** 2) / 2)
    assert next(f for f in record["forecasters"] if f["method"] == CLAUDE)["resolved"] == 2
    assert record["summary"]["call_policy"]["default_move"] == 0.0
    ours = next(f for f in record["forecasters"] if f["method"] == "combined_median_v1")
    assert "is the market's price unless a tested edge applies" in ours["about"] and "10 points" not in ours["about"]
    assert "is this price unless a tested edge applies" in next(f for f in record["forecasters"] if f["method"] == "market_price_v1")["about"]


def test_a_promoted_rule_moves_the_questions_it_matches_and_names_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "DEFAULT_MOVE", 0.0)
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(tech_move_v1="promoted"))
    _tech_and_other(tmp_path)
    record = build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    assert call["tech"] == {"method": "market_anchored_call_v1", "probability": 0.6, "members": 1, "claude": 0.9, "market": 0.5,
                            "max_move": 0.1, "rule": "tech_move_v1"}
    assert call["other"] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9, "market": 0.5, "max_move": 0.0}
    assert record["summary"]["call_policy"]["promoted"] == ["Tech questions"]
    assert [(e["id"], e["status"]) for e in record["summary"]["edge_tests"]] == [
        ("tech_move_v1", "promoted"), ("every_question_move_v1", "testing"), ("tech_second_look_v1", "testing"),
        ("learned_model_call_v1", "testing")]


def test_the_largest_move_among_matching_promoted_rules_applies_and_weak_research_still_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = _rules(tech_move_v1="promoted", every_question_move_v1="promoted")
    rules = (rules[0] | {"move": 0.05}, rules[1] | {"move": 0.2})
    monkeypatch.setattr(track_record, "DEFAULT_MOVE", 0.0)
    monkeypatch.setattr(track_record, "CALL_RULES", rules)
    directory = _tech_and_other(tmp_path)
    _research(directory, good=["other"], warned=["tech"])
    call = {q["id"]: q["call"] for q in build_track_record(tmp_path, today=date(2026, 11, 10))["questions"]}
    assert call["other"]["probability"] == pytest.approx(0.7) and call["other"]["rule"] == "every_question_move_v1" and call["other"]["max_move"] == 0.2
    assert call["tech"]["probability"] == 0.5 and call["tech"]["held"] == "weak research" and call["tech"]["rule"] == "every_question_move_v1"


def test_changing_the_policy_never_rewrites_a_frozen_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _history(tmp_path, 40)
    for day in ("20261104", "20261105", "20261106", "20261109"):
        _anchored_day(tmp_path, day, {f"q{day}": (0.7, 0.2)}, good=[f"q{day}"])
    _tech_and_other(tmp_path)
    build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    frozen = {p: p.read_bytes() for p in sorted(tmp_path.glob("prospective_*/calls.json"))}
    assert len(frozen) == 5
    monkeypatch.setattr(track_record, "DEFAULT_MOVE", 0.0)
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(every_question_move_v1="promoted"))
    build_track_record(tmp_path, today=date(2026, 11, 12), freeze=True)
    assert {p: p.read_bytes() for p in sorted(tmp_path.glob("prospective_*/calls.json"))} == frozen


def test_a_question_priced_only_by_kalshi_is_anchored_compared_and_counted_in_the_tech_edge_test(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_markets_20261110"
    _markets(directory, [{"id": "kalshi-KXGPT-ASTRA61", "topic": "Tech", "date": "2026-11-13", "outcome": 1,
                          "resolution_source": "https://kalshi.com/events/KXGPT-ASTRA61", "forecasts": [
                              {"method": CLAUDE, "probability": 0.9}, {"method": "kalshi_price_v1", "probability": 0.5}]}])
    _research(directory, good=["kalshi-KXGPT-ASTRA61"])
    record = build_track_record(tmp_path, today=date(2026, 11, 20))
    call = record["questions"][0]["call"]
    assert call["method"] == "market_anchored_call_v1" and call["probability"] == 0.5 and call["market"] == 0.5 and call["claude"] == 0.9
    versus = record["summary"]["vs_market"]
    assert versus["questions"] == 1 and versus["market"] == pytest.approx(0.25) and versus["ours"] == pytest.approx(0.25)
    kalshi = next(f for f in record["forecasters"] if f["method"] == "kalshi_price_v1")
    assert kalshi["label"] == "Kalshi (benchmark)" and kalshi["resolved"] == 1
    tech = next(t for t in record["summary"]["edge_tests"] if t["id"] == "tech_move_v1")
    assert tech["counted"]["questions"] == 1 and tech["counted"]["market"] == pytest.approx(0.25)
    assert tech["counted"]["rule"] == pytest.approx(0.16)  # the price moved 0.10 toward Claude: 0.6 against a yes


def _decided(root: Path, day: str, items: list[tuple[str, str, float, float, int, str]], **extra: object) -> None:
    """A directory of decided questions: (id, topic, Claude, market price, outcome, resolution date)."""
    directory = root / f"prospective_markets_{day}"
    _markets(directory, [
        {"id": qid, "topic": topic, "date": when, "outcome": outcome, **extra,
         "forecasts": [{"method": CLAUDE, "probability": claude}, {"method": "market_price_v1", "probability": price}]}
        for qid, topic, claude, price, outcome, when in items])
    _research(directory, good=[item[0] for item in items])


def test_edge_tests_count_only_questions_first_forecast_on_or_after_the_registered_day(tmp_path: Path) -> None:
    # Before the rule: tech, price 0.5, Claude 0.9, yes -> rule 0.6 (0.16) against market 0.25.
    _decided(tmp_path, "20261109", [("old-tech", "Tech", 0.9, 0.5, 1, "2026-11-12"), ("old-other", "Politics", 0.9, 0.5, 1, "2026-11-12")])
    # Counted: tech 0.9 vs 0.5, no -> rule 0.6 (0.36) against 0.25; politics 0.1 vs 0.5, no -> rule 0.4 (0.16) against 0.25.
    _decided(tmp_path, "20261110", [("tech", "Tech", 0.9, 0.5, 0, "2026-11-13"), ("pol", "Politics", 0.1, 0.5, 0, "2026-11-14"),
                                    ("open", "Tech", 0.9, 0.5, None, "2026-11-30")])
    _decided(tmp_path, "20261111", [("noprice", "Tech", 0.9, 0.5, 1, "2026-11-13")])
    (tmp_path / "prospective_markets_20261110" / "research.json").write_text(json.dumps({"findings": {
        "tech": {"facts": [{"fact": "x", "source": 0}]}, "pol": {"facts": [{"fact": "x", "source": 0}], "warnings": ["thin"]}}, "failed": {}}))
    forecasts = json.loads((tmp_path / "prospective_markets_20261111" / "forecasts.json").read_text())
    forecasts["questions"][0]["forecasts"] = [f for f in forecasts["questions"][0]["forecasts"] if f["method"] == CLAUDE]
    (tmp_path / "prospective_markets_20261111" / "forecasts.json").write_text(json.dumps(forecasts))
    tests = {t["id"]: t for t in build_track_record(tmp_path, today=date(2026, 11, 20))["summary"]["edge_tests"]}
    tech = tests["tech_move_v1"]
    assert (tech["label"], tech["status"], tech["registered"]) == ("Tech questions", "testing", "2026-11-10")
    assert tech["counted"] == {"questions": 1, "dates": 1, "rule": pytest.approx(0.36), "market": pytest.approx(0.25),
                               "difference": pytest.approx(0.11), "interval": None, "meets_bar": False}
    assert tech["before"] == {"questions": 1, "dates": 1, "rule": pytest.approx(0.16), "market": pytest.approx(0.25),
                              "difference": pytest.approx(-0.09), "interval": None}
    every = tests["every_question_move_v1"]
    # "pol" has weak research (a warning), so the rule holds it at the price: Brier equals the market's.
    assert every["counted"]["questions"] == 2 and every["counted"]["dates"] == 2
    assert every["counted"]["rule"] == pytest.approx((0.36 + 0.25) / 2) and every["counted"]["market"] == pytest.approx(0.25)
    assert every["counted"]["interval"] is not None and every["counted"]["meets_bar"] is False
    assert every["before"]["questions"] == 2 and every["before"]["rule"] == pytest.approx(0.16)


def test_older_questions_with_no_research_file_are_moved_by_the_plain_cap(tmp_path: Path) -> None:
    _decided(tmp_path, "20261105", [("t", "Tech", 0.9, 0.5, 1, "2026-11-12")])
    (tmp_path / "prospective_markets_20261105" / "research.json").unlink()
    test = next(t for t in build_track_record(tmp_path, today=date(2026, 11, 20))["summary"]["edge_tests"] if t["id"] == "tech_move_v1")
    assert test["before"]["rule"] == pytest.approx(0.16) and test["counted"]["questions"] == 0 and test["counted"]["rule"] is None


def _winning_days(root: Path, days: int, per_day: int) -> None:
    items = [(f"w{d}-{n}", "Tech", 0.9, 0.5, 1, f"2026-11-{11 + d:02d}") for d in range(days) for n in range(per_day)]
    _decided(root, "20261110", items)


def test_edge_tests_are_deterministic(tmp_path: Path) -> None:
    _winning_days(tmp_path, 6, 3)
    _decided(tmp_path, "20261111", [("x", "Tech", 0.2, 0.6, 1, "2026-11-20"), ("y", "Tech", 0.8, 0.3, 0, "2026-11-21")])
    first = build_track_record(tmp_path, today=date(2026, 11, 25))["summary"]["edge_tests"]
    second = build_track_record(tmp_path, today=date(2026, 11, 25))["summary"]["edge_tests"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    low, high = first[0]["counted"]["interval"]
    assert low <= first[0]["counted"]["difference"] <= high


@pytest.mark.parametrize(("days", "per_day", "meets"), [(12, 4, True), (12, 3, False), (9, 5, False)])
def test_a_rule_meets_the_bar_only_with_enough_questions_on_enough_days_and_an_interval_below_zero(
        tmp_path: Path, days: int, per_day: int, meets: bool) -> None:
    _winning_days(tmp_path, days, per_day)
    counted = next(t for t in build_track_record(tmp_path, today=date(2026, 12, 15))["summary"]["edge_tests"] if t["id"] == "tech_move_v1")["counted"]
    assert (counted["questions"], counted["dates"]) == (days * per_day, days)
    assert counted["difference"] == pytest.approx(-0.09) and counted["interval"][1] < 0
    assert counted["meets_bar"] is meets


def test_a_rule_that_beats_the_market_only_by_luck_does_not_meet_the_bar(tmp_path: Path) -> None:
    # Half the days the move helps, half it hurts by the same amount: the interval straddles zero.
    items = [(f"m{d}-{n}", "Tech", 0.9, 0.5, d % 2, f"2026-11-{11 + d:02d}") for d in range(12) for n in range(4)]
    _decided(tmp_path, "20261110", items)
    counted = next(t for t in build_track_record(tmp_path, today=date(2026, 12, 15))["summary"]["edge_tests"] if t["id"] == "tech_move_v1")["counted"]
    assert counted["questions"] == 48 and counted["dates"] == 12
    assert counted["interval"][0] < 0 < counted["interval"][1] and counted["meets_bar"] is False


# --- The second look (claude_second_look_v1) and the rule that uses it ----------------------------------------------


def _looks(root: Path, day: str, items: list[tuple], research: bool = True) -> Path:
    """A market directory: (id, topic, Claude, market price, second look or None, outcome, resolution date) per question."""
    directory = root / f"prospective_markets_{day}"
    _markets(directory, [
        {"id": qid, "topic": topic, "date": when, "outcome": outcome,
         "forecasts": [{"method": CLAUDE, "probability": claude}, {"method": "market_price_v1", "probability": price}]
         + ([{"method": SECOND_LOOK, "probability": second}] if second is not None else [])}
        for qid, topic, claude, price, second, outcome, when in items])
    if research:
        _research(directory, good=[item[0] for item in items])
    return directory


def _calls(root: Path, **kwargs: object) -> dict[str, dict]:
    return {q["id"]: q["call"] for q in build_track_record(root, today=date(2026, 11, 11), **kwargs)["questions"]}


def test_the_second_look_is_scored_on_its_own_row_but_is_in_no_older_call(tmp_path: Path) -> None:
    _markets(tmp_path / "prospective_markets_20261020", [
        {"id": "median", "date": "2026-10-25", "outcome": 1, "forecasts": [
            {"method": CLAUDE, "probability": 0.6}, {"method": "statistical_baseline_v1", "probability": 0.4},
            {"method": SECOND_LOOK, "probability": 0.98}]}])
    _markets(tmp_path / "prospective_markets_20261024", [
        {"id": "weighted", "date": "2026-10-25", "outcome": 1, "forecasts": [
            {"method": CLAUDE, "probability": 0.6}, {"method": "statistical_baseline_v1", "probability": 0.4},
            {"method": SECOND_LOOK, "probability": 0.98}]}])
    # No Claude forecast: the call falls back to the weighted call, and the second look is no member of it.
    _markets(tmp_path / "prospective_markets_20261105", [
        {"id": "fallback", "date": "2026-11-12", "forecasts": [
            {"method": "statistical_baseline_v1", "probability": 0.3}, {"method": SECOND_LOOK, "probability": 0.9}]}])
    _markets(tmp_path / "prospective_markets_20261110", [
        {"id": "nothing", "date": "2026-11-12", "forecasts": [
            {"method": "market_price_v1", "probability": 0.3}, {"method": SECOND_LOOK, "probability": 0.9}]}])
    record = build_track_record(tmp_path, today=date(2026, 11, 11), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    assert (call["median"]["method"], call["median"]["probability"], call["median"]["members"]) == ("combined_median_v1", 0.5, 2)
    assert (call["weighted"]["method"], call["weighted"]["members"]) == ("combined_weighted_v1", 2)
    assert SECOND_LOOK not in call["weighted"]["weights"]
    assert (call["fallback"]["method"], call["fallback"]["probability"], call["fallback"]["members"]) == ("combined_weighted_v1", 0.3, 1)
    assert call["nothing"] is None  # a price and a second look but no Claude and no other member: no call at all
    assert SECOND_LOOK not in track_record._weights([{"outcome": 1, "forecasts": [{"method": SECOND_LOOK, "probability": 0.9}]}])
    row = next(f for f in record["forecasters"] if f["method"] == SECOND_LOOK)
    assert row["label"] == "Claude, second look" and row["forecasts"] == 4 and row["resolved"] == 2
    assert row["about"].startswith("Claude's forecast after also seeing the market's price and its own first forecast.")
    assert [f["method"] for f in record["forecasters"]][:3] == ["combined_median_v1", CLAUDE, SECOND_LOOK]


def test_the_second_look_never_enters_the_confidence_correction(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    _markets(tmp_path / "prospective_markets_20261015", [
        {"id": "looked", "date": "2026-10-20", "outcome": 0, "forecasts": [
            {"method": CLAUDE, "probability": 0.7}, {"method": SECOND_LOOK, "probability": 0.95}]}])
    questions = build_track_record(tmp_path, today=date(2026, 11, 1))["questions"]
    rows = track_record._claude_results_before(questions, date(2026, 11, 6))
    assert len(rows) == 41 and (0.95, 0) not in rows and (0.7, 0) in rows
    # A question that has only a second look gets no corrected forecast, however many results are known.
    _markets(tmp_path / "prospective_markets_20261106", [
        {"id": "only", "date": "2026-11-12", "forecasts": [{"method": SECOND_LOOK, "probability": 0.9}]}])
    record = build_track_record(tmp_path, today=date(2026, 11, 6), freeze=True)
    assert _forecast(record, "only", "claude_calibrated_v1") is None
    assert not (tmp_path / "prospective_markets_20261106" / "calibrated.json").exists()
    # And the fit is the same with or without the second looks.
    without = tmp_path / "without"
    without.mkdir()
    _history(without, 40)
    _markets(without / "prospective_markets_20261015", [
        {"id": "looked", "date": "2026-10-20", "outcome": 0, "forecasts": [{"method": CLAUDE, "probability": 0.7}]}])
    assert build_track_record(tmp_path, today=date(2026, 11, 6))["calibration"] == build_track_record(without, today=date(2026, 11, 6))["calibration"]


def test_a_testing_forecaster_rule_changes_no_call(tmp_path: Path) -> None:
    _looks(tmp_path, "20261111", [("tech", "Tech", 0.9, 0.5, 0.2, None, "2026-11-20"), ("other", "Politics", 0.9, 0.5, 0.2, None, "2026-11-20")])
    for call in _calls(tmp_path).values():
        assert call == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9, "market": 0.5, "max_move": 0.0}


def test_a_promoted_forecaster_rule_sets_the_call_and_records_where_it_came_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(tech_second_look_v1="promoted"))
    directory = _looks(tmp_path, "20261111", [
        ("tech", "Tech", 0.9, 0.5, 0.2, None, "2026-11-20"), ("nolook", "Tech", 0.9, 0.5, None, None, "2026-11-20"),
        ("other", "Politics", 0.9, 0.5, 0.2, None, "2026-11-20"), ("weak", "Tech", 0.9, 0.5, 0.2, None, "2026-11-20")])
    _research(directory, good=["tech", "nolook", "other"], warned=["weak"])
    call = _calls(tmp_path, freeze=True)
    assert call["tech"] == {"method": "market_anchored_call_v1", "probability": 0.2, "members": 1, "claude": 0.9, "market": 0.5,
                            "max_move": 1.0, "rule": "tech_second_look_v1", "forecaster": SECOND_LOOK, "target": 0.2}
    # No second look: the rule does not apply, and nothing else is promoted, so the call is the price.
    assert call["nolook"] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9, "market": 0.5, "max_move": 0.0}
    assert call["other"]["probability"] == 0.5 and "rule" not in call["other"]  # not a Tech question
    assert call["weak"]["probability"] == 0.5 and call["weak"]["held"] == "weak research"
    assert call["weak"]["rule"] == "tech_second_look_v1" and call["weak"]["target"] == 0.2
    assert json.loads((directory / "calls.json").read_text())["tech"] == call["tech"]


def test_the_rules_move_caps_how_far_the_second_look_can_pull_the_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = _rules(tech_second_look_v1="promoted")
    monkeypatch.setattr(track_record, "CALL_RULES", rules[:2] + (rules[2] | {"move": 0.15},))
    _looks(tmp_path, "20261111", [("down", "Tech", 0.9, 0.5, 0.1, None, "2026-11-20"), ("up", "Tech", 0.1, 0.5, 0.9, None, "2026-11-20"),
                                  ("near", "Tech", 0.5, 0.5, 0.58, None, "2026-11-20"), ("edge", "Tech", 0.5, 0.95, 0.99, None, "2026-11-20")])
    call = _calls(tmp_path)
    assert (call["down"]["probability"], call["down"]["max_move"], call["down"]["target"]) == (pytest.approx(0.35), 0.15, 0.1)
    assert call["up"]["probability"] == pytest.approx(0.65)
    assert call["near"]["probability"] == pytest.approx(0.58)
    assert call["edge"]["probability"] == 0.98  # within the call bounds


def test_a_promoted_forecaster_rule_takes_precedence_over_a_promoted_move_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(tech_move_v1="promoted", every_question_move_v1="promoted", tech_second_look_v1="promoted"))
    _looks(tmp_path, "20261111", [("tech", "Tech", 0.9, 0.5, 0.3, None, "2026-11-20"), ("nolook", "Tech", 0.9, 0.5, None, None, "2026-11-20"),
                                  ("other", "Politics", 0.9, 0.5, 0.3, None, "2026-11-20")])
    call = _calls(tmp_path)
    assert call["tech"]["rule"] == "tech_second_look_v1" and call["tech"]["probability"] == pytest.approx(0.3)
    # Without a second look (or off Tech) the move rules apply as before: the Claude target, no forecaster fields.
    assert call["nolook"]["rule"] == "tech_move_v1" and call["nolook"]["probability"] == pytest.approx(0.6)  # equal moves: the first rule
    assert "forecaster" not in call["nolook"] and "target" not in call["nolook"]
    assert call["other"]["rule"] == "every_question_move_v1" and call["other"]["probability"] == pytest.approx(0.6)


def test_the_edge_test_for_a_forecaster_rule_scores_the_second_look_against_the_market(tmp_path: Path) -> None:
    # Before the rule's day: counted only for information. Brier of 0.2 against 1 is 0.64; the market's 0.5 gives 0.25.
    _looks(tmp_path, "20261110", [("early", "Tech", 0.9, 0.5, 0.2, 1, "2026-11-12")])
    directory = _looks(tmp_path, "20261111", [
        ("win", "Tech", 0.1, 0.5, 0.9, 1, "2026-11-13"),       # rule 0.9 -> 0.01 against 0.25
        ("nolook", "Tech", 0.9, 0.5, None, 0, "2026-11-13"),   # no second look: not counted
        ("other", "Politics", 0.9, 0.5, 0.9, 0, "2026-11-13"), # not Tech: not counted
        ("open", "Tech", 0.9, 0.5, 0.9, None, "2026-11-30"),   # undecided: not counted
        ("weak", "Tech", 0.9, 0.5, 0.9, 0, "2026-11-14")])     # weak research: held at the price, 0.25 against 0.25
    _research(directory, good=["win", "nolook", "other", "open"], warned=["weak"])
    tests = {t["id"]: t for t in build_track_record(tmp_path, today=date(2026, 11, 20))["summary"]["edge_tests"]}
    test = tests["tech_second_look_v1"]
    assert (test["label"], test["status"], test["registered"]) == ("Second look on Tech", "testing", "2026-11-11")
    assert test["counted"]["questions"] == 2 and test["counted"]["dates"] == 2
    assert test["counted"]["rule"] == pytest.approx((0.01 + 0.25) / 2) and test["counted"]["market"] == pytest.approx(0.25)
    assert test["counted"]["difference"] == pytest.approx(-0.12) and test["counted"]["meets_bar"] is False
    assert test["before"]["questions"] == 1 and test["before"]["rule"] == pytest.approx(0.64)
    # The move rules still use Claude's blind forecast: the second look changes nothing about them.
    assert tests["every_question_move_v1"]["counted"]["questions"] == 5  # early, win, nolook, other, weak (open is undecided)


def test_a_second_look_is_never_a_target_of_the_move_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(tech_move_v1="promoted"))
    _looks(tmp_path, "20261111", [("tech", "Tech", 0.9, 0.5, 0.0, None, "2026-11-20")])
    call = _calls(tmp_path)["tech"]
    assert call["probability"] == pytest.approx(0.6) and call["rule"] == "tech_move_v1" and "forecaster" not in call


def test_promoting_the_second_look_rule_never_rewrites_a_call_already_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _looks(tmp_path, "20261111", [("tech", "Tech", 0.9, 0.5, 0.2, None, "2026-11-20")])
    before = _calls(tmp_path, freeze=True)
    frozen = (tmp_path / "prospective_markets_20261111" / "calls.json").read_bytes()
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(tech_second_look_v1="promoted"))
    assert _calls(tmp_path, freeze=True) == before
    assert (tmp_path / "prospective_markets_20261111" / "calls.json").read_bytes() == frozen
    # A question first built after the promotion does use it.
    _looks(tmp_path, "20261112", [("later", "Tech", 0.9, 0.5, 0.2, None, "2026-11-20")])
    assert _calls(tmp_path, freeze=True)["later"]["rule"] == "tech_second_look_v1"


# --- The learned model (learned_model_v1) and the rule that tests it -------------------------------------------------

def _lessons(count: int) -> list[tuple[str, str, float, float, int, str]]:
    """``count`` decided questions, market 50% and Claude 80%: on Tech yes happens 4 times in 5, on Politics half the time."""
    return [(f"l{n}", "Tech", 0.8, 0.5, 0 if n % 10 == 0 else 1, "2026-11-05") if n % 2 == 0
            else (f"l{n}", "Politics", 0.8, 0.5, n % 4 // 2, "2026-11-05") for n in range(count)]


def _fit_of(items: list[tuple[str, str, float, float, int, str]]) -> dict:
    fit = fit_learned_model([(price, claude, topic, outcome) for _, topic, claude, price, outcome, _ in items])
    return {"intercept": round(fit["intercept"], 6), "slope": round(fit["slope"], 6),
            "weights": {topic: round(weight, 6) for topic, weight in fit["weights"].items()}}


def test_the_learned_forecast_waits_for_a_hundred_results_and_for_its_first_day(tmp_path: Path) -> None:
    _decided(tmp_path, "20261101", _lessons(99))
    _looks(tmp_path, "20261111", [("new", "Tech", 0.7, 0.4, None, None, "2026-11-20")])
    record = build_track_record(tmp_path, today=date(2026, 11, 11), freeze=True)
    assert _forecast(record, "new", LEARNED) is None and record["learned_model"] is None
    assert not (tmp_path / "prospective_markets_20261111" / "learned.json").exists()
    # Enough results, but a question first forecast the day before the model starts gets none.
    _decided(tmp_path, "20261102", [("extra", "Tech", 0.8, 0.5, 1, "2026-11-05")])
    _looks(tmp_path, "20261110", [("early", "Tech", 0.7, 0.4, None, None, "2026-11-20")])
    record = build_track_record(tmp_path, today=date(2026, 11, 11), freeze=True)
    assert _forecast(record, "early", LEARNED) is None and _forecast(record, "new", LEARNED) is not None
    assert not (tmp_path / "prospective_markets_20261110" / "learned.json").exists()


def test_the_learned_forecast_is_frozen_with_its_fit_and_uses_only_results_known_before_the_day(tmp_path: Path) -> None:
    lessons = _lessons(120)
    _decided(tmp_path, "20261101", lessons)
    # Decided on the forecast day itself: not known before it, so not part of the fit.
    _decided(tmp_path, "20261102", [("sameday", "Tech", 0.8, 0.5, 0, "2026-11-11")])
    directory = _looks(tmp_path, "20261111", [
        ("tech", "Tech", 0.7, 0.4, None, None, "2026-11-20"), ("pol", "Politics", 0.7, 0.4, None, None, "2026-11-20"),
        ("crypto", "Crypto", 0.7, 0.4, None, None, "2026-11-20")])
    forecasts = json.loads((directory / "forecasts.json").read_text())
    forecasts["questions"].append({"id": "noprice", "question": "noprice", "resolution_criteria": "Rules.", "resolution_date": "2026-11-20",
                                   "outcome": None, "topic": "Tech", "forecasts": [{"method": CLAUDE, "probability": 0.7}]})
    (directory / "forecasts.json").write_text(json.dumps(forecasts))
    record = build_track_record(tmp_path, today=date(2026, 11, 11), freeze=True)
    fit = _fit_of(lessons)
    saved = json.loads((directory / "learned.json").read_text())
    assert set(saved) == {"tech", "pol", "crypto"}  # no market price, no learned forecast
    assert saved["tech"] == {
        "probability": round(learned_probability(0.4, 0.7, "Tech", fit), 6), "market": 0.4, "claude": 0.7,
        "intercept": fit["intercept"], "slope": fit["slope"], "claude_weight": fit["weights"]["Tech"],
        "results": 120, "as_of": "2026-11-11"}
    assert _forecast(record, "tech", LEARNED) == {"method": LEARNED, **saved["tech"]}
    # It follows Claude further on the topic where Claude's disagreement has paid, and not at all on a topic never seen.
    assert saved["tech"]["probability"] > saved["pol"]["probability"]
    assert saved["crypto"]["claude_weight"] == 0.0
    assert saved["crypto"]["probability"] == round(learned_probability(0.4, 0.4, "Crypto", fit), 6)
    assert record["learned_model"] == {**fit, "results": 120, "as_of": "2026-11-11"}
    # A later result changes the fit of later days, never a forecast already frozen.
    _decided(tmp_path, "20261103", [(f"more{n}", "Tech", 0.8, 0.5, 0, "2026-11-12") for n in range(40)])
    later = build_track_record(tmp_path, today=date(2026, 11, 15), freeze=True)
    assert json.loads((directory / "learned.json").read_text()) == saved
    assert _forecast(later, "tech", LEARNED) == {"method": LEARNED, **saved["tech"]}
    assert later["learned_model"]["results"] == 161 and later["learned_model"]["weights"]["Tech"] < fit["weights"]["Tech"]


def test_the_learned_forecast_has_its_own_row_and_changes_no_call_while_it_is_tested(tmp_path: Path) -> None:
    _decided(tmp_path, "20261101", _lessons(120))
    _looks(tmp_path, "20261111", [("tech", "Tech", 0.7, 0.4, None, None, "2026-11-20")])
    record = build_track_record(tmp_path, today=date(2026, 11, 11))
    question = next(q for q in record["questions"] if q["id"] == "tech")
    assert question["call"] == {"method": "market_anchored_call_v1", "probability": 0.4, "members": 1, "claude": 0.7, "market": 0.4, "max_move": 0.0}
    row = next(f for f in record["forecasters"] if f["method"] == LEARNED)
    assert (row["label"], row["forecasts"], row["resolved"]) == ("Learned model", 1, 0)
    # Never a member of a combined call or of a forecaster's weight.
    assert LEARNED not in track_record._weights([q for q in record["questions"] if q["status"] == "resolved"])


def test_the_edge_test_scores_the_learned_forecast_against_the_market(tmp_path: Path) -> None:
    lessons = _lessons(120)
    _decided(tmp_path, "20261101", lessons)
    directory = _looks(tmp_path, "20261111", [
        ("win", "Tech", 0.8, 0.5, None, 1, "2026-11-13"), ("weak", "Tech", 0.8, 0.5, None, 1, "2026-11-14"),
        ("open", "Tech", 0.8, 0.5, None, None, "2026-11-30")])
    _research(directory, good=["win", "open"], warned=["weak"])
    record = build_track_record(tmp_path, today=date(2026, 11, 20))
    test = next(t for t in record["summary"]["edge_tests"] if t["id"] == "learned_model_call_v1")
    assert (test["label"], test["status"], test["registered"]) == ("Learned from results", "testing", "2026-11-11")
    learned = learned_probability(0.5, 0.8, "Tech", _fit_of(lessons))
    assert learned > 0.6
    # "weak" has thin research, so the rule holds it at the price like every other rule.
    assert test["counted"]["questions"] == 2 and test["counted"]["dates"] == 2
    assert test["counted"]["rule"] == pytest.approx(((1 - learned) ** 2 + 0.25) / 2, abs=1e-6)
    assert test["counted"]["market"] == pytest.approx(0.25) and test["counted"]["meets_bar"] is False
    # Questions forecast before the model started have no learned forecast, so nothing is shown for them.
    assert test["before"]["questions"] == 0


def test_a_promoted_learned_rule_sets_the_call_and_records_where_it_came_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_record, "CALL_RULES", _rules(learned_model_call_v1="promoted"))
    lessons = _lessons(120)
    _decided(tmp_path, "20261101", lessons)
    directory = _looks(tmp_path, "20261111", [("tech", "Tech", 0.8, 0.5, None, None, "2026-11-20"), ("weak", "Politics", 0.8, 0.5, None, None, "2026-11-20")])
    _research(directory, good=["tech"], warned=["weak"])
    call = _calls(tmp_path, freeze=True)
    learned = round(learned_probability(0.5, 0.8, "Tech", _fit_of(lessons)), 6)
    assert call["tech"] == {"method": "market_anchored_call_v1", "probability": learned, "members": 1, "claude": 0.8, "market": 0.5,
                            "max_move": 1.0, "rule": "learned_model_call_v1", "forecaster": LEARNED, "target": learned}
    assert call["weak"]["probability"] == 0.5 and call["weak"]["held"] == "weak research"
    assert json.loads((directory / "calls.json").read_text())["tech"] == call["tech"]

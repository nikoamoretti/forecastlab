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


def test_from_nov_10_the_call_starts_from_the_market_and_moves_at_most_ten_points(tmp_path: Path) -> None:
    _history(tmp_path, 40)
    nov9 = _anchored_day(tmp_path, "20261109", {"n9": (0.7, 0.2)}, good=["n9"])
    nov10 = _anchored_day(tmp_path, "20261110", {
        "up": (0.9, 0.5), "down": (0.1, 0.5), "inside": (0.55, 0.5), "high": (0.99, 0.95), "low": (0.0, 0.03),
        "none": (0.7, None)}, good=["up", "down", "inside", "high", "low", "none"])
    record = build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)
    call = {q["id"]: q["call"] for q in record["questions"]}
    # Moved toward Claude by at most 0.10, on both sides; both numbers are kept on the call.
    assert call["up"] == {"method": "market_anchored_call_v1", "probability": 0.6, "members": 1, "claude": 0.9, "market": 0.5}
    assert call["down"]["probability"] == pytest.approx(0.4) and call["down"]["claude"] == 0.1
    assert call["inside"]["probability"] == 0.55  # within the cap: Claude's number
    assert call["high"]["probability"] == 0.98 and call["low"]["probability"] == 0.02  # kept within 2% to 98%
    # No market price: Claude's own forecast, as before.
    assert call["none"] == {"method": "claude_call_v1", "probability": 0.7, "members": 1}
    # Nov 9 keeps the corrected-forecast rule (claude_calibrated_v1), not the anchor.
    assert call["n9"]["method"] == "claude_corrected_call_v1"
    # The independent forecast stays tracked beside the call.
    assert _forecast(record, "up", CLAUDE)["probability"] == 0.9
    assert _forecast(record, "up", "market_price_v1")["probability"] == 0.5
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
    assert json.loads(after["prospective_markets_20261110"])["q20261110"]["probability"] == pytest.approx(0.3)


def test_weak_research_holds_the_call_at_the_market_price(tmp_path: Path) -> None:
    _anchored_day(tmp_path, "20261110", {"ok": (0.9, 0.5), "warned": (0.9, 0.5), "failed": (0.9, 0.5), "empty": (0.9, 0.5)},
                  good=["ok"], warned=["warned"], failed=["failed"])
    call = {q["id"]: q["call"] for q in build_track_record(tmp_path, today=date(2026, 11, 10), freeze=True)["questions"]}
    assert call["ok"]["probability"] == pytest.approx(0.6) and "held" not in call["ok"]
    for held in ("warned", "failed", "empty"):  # a primary-source warning, a failed question, no findings at all
        assert call[held] == {"method": "market_anchored_call_v1", "probability": 0.5, "members": 1, "claude": 0.9,
                              "market": 0.5, "held": "weak research"}
    # Claude's own forecast is still tracked on the held question.
    assert _forecast(build_track_record(tmp_path, today=date(2026, 11, 10)), "warned", CLAUDE)["probability"] == 0.9


def test_a_market_price_without_a_claude_forecast_follows_the_old_fallback(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_markets_20261110"
    _markets(directory, [{"id": "q", "date": "2026-11-20", "forecasts": [
        {"method": "judge_qwen38_27b_v1", "probability": 0.6}, {"method": "market_price_v1", "probability": 0.2}]}])
    _research(directory, good=["q"])
    call = build_track_record(tmp_path, today=date(2026, 11, 10))["questions"][0]["call"]
    assert call["method"] == "combined_weighted_v1" and call["probability"] == 0.6


def test_the_record_scores_the_call_claude_alone_and_the_market_on_the_same_questions(tmp_path: Path) -> None:
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

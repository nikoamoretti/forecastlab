from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from forecastlab.macro import MacroSpec
from forecastlab_api import artifact_scoring
from forecastlab_api.artifact_scoring import monthly_first_release, outcome_for, score_artifact


def _fake_alfred(vintages: dict[str, dict[str, str]], *, latest: str):
    """Return a fetch() serving ALFRED-style CSVs; vintages map date -> {obs: value}."""

    def fetch(url: str) -> str:
        query = parse_qs(urlsplit(url).query)
        series, vintage = query["id"][0], query["vintage_date"][0]
        served = min(vintage, latest)  # ALFRED clamps future vintages to its current date.
        rows = vintages.get(served, {})
        start, end = query["cosd"][0], query["coed"][0]
        lines = [f"observation_date,{series}_{served.replace('-', '')}"]
        lines += [f"{obs},{value}" for obs, value in sorted(rows.items()) if start <= obs <= end]
        return "\n".join(lines) + "\n"

    return fetch


def _spec(indicator: str, period: str, threshold: float, release: datetime) -> MacroSpec:
    return MacroSpec(indicator=indicator, observation_period=period, threshold=threshold, release_at=release)


CPI_RELEASE = datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
CPI_ROWS = {"2025-09-01": "324.800", "2026-08-01": "334.900"}


def test_cpi_yoy_reconstructed_from_release_day_vintage_and_rounded_half_up() -> None:
    spec = _spec("cpi", "2026-09", 3.4, CPI_RELEASE)
    vintages = {"2026-10-13": dict(CPI_ROWS), "2026-10-14": {**CPI_ROWS, "2026-09-01": "335.843"}}
    result = monthly_first_release(spec, today=date(2026, 10, 15), fetch=_fake_alfred(vintages, latest="2026-10-15"))

    # 335.843 / 324.800 - 1 = 3.39994% -> 3.4 (half-up to one decimal); 3.4 is not > 3.4.
    assert result["status"] == "resolved"
    assert result["value"] == "3.4"
    assert outcome_for(spec, result["value"]) == 0
    assert "vintage_date=2026-10-14" in result["vintage_url"]


def test_payroll_change_uses_revised_prior_month_in_release_vintage() -> None:
    spec = _spec("payrolls", "2026-10", 29000, datetime(2026, 11, 6, 13, 30, tzinfo=UTC))
    vintages = {"2026-11-05": {"2026-09-01": "159600"},
                "2026-11-06": {"2026-09-01": "159620", "2026-10-01": "159700"}}
    result = monthly_first_release(spec, today=date(2026, 11, 7), fetch=_fake_alfred(vintages, latest="2026-11-07"))

    assert result["status"] == "resolved"
    assert result["value"] == "80000"  # (159700 - 159620) thousand jobs
    assert outcome_for(spec, result["value"]) == 1


def test_monthly_resolution_stays_pending_before_publication() -> None:
    spec = _spec("unemployment", "2026-10", 4.2, datetime(2026, 11, 6, 13, 30, tzinfo=UTC))
    fetch = _fake_alfred({"2026-11-03": {"2026-09-01": "4.2"}}, latest="2026-11-03")

    assert monthly_first_release(spec, today=date(2026, 11, 3), fetch=fetch)["reason"] == "release_in_future"
    # The release day arrived (by the scorer's clock) but ALFRED has not published that vintage yet.
    assert monthly_first_release(spec, today=date(2026, 11, 6), fetch=fetch)["reason"] == "release_vintage_not_available"


def test_monthly_value_already_in_prior_vintage_is_an_exception() -> None:
    spec = _spec("unemployment", "2026-09", 4.1, datetime(2026, 10, 2, 12, 30, tzinfo=UTC))
    vintages = {"2026-10-01": {"2026-09-01": "4.2"}, "2026-10-02": {"2026-09-01": "4.2"}}
    result = monthly_first_release(spec, today=date(2026, 10, 3), fetch=_fake_alfred(vintages, latest="2026-10-03"))

    assert result["status"] == "exception"
    assert result["reason"] == "observation_published_before_scheduled_release"


@pytest.mark.parametrize(("comparison", "value", "expected"), [
    ("gt", "4.2", 1), ("gt", "4.1", 0), ("ge", "4.1", 1), ("lt", "4.0", 1), ("le", "4.2", 0)])
def test_outcome_comparisons_are_exact_decimals(comparison: str, value: str, expected: int) -> None:
    spec = MacroSpec(indicator="unemployment", observation_period="2026-09", threshold=4.1, comparison=comparison,
                     release_at=datetime(2026, 10, 2, 12, 30, tzinfo=UTC))
    assert outcome_for(spec, value) == expected


def _write_artifact(directory: Path) -> None:
    spec = _spec("unemployment", "2026-09", 4.1, datetime(2026, 10, 2, 12, 30, tzinfo=UTC))
    fast = _spec("treasury_10y", "2026-10-05", 5.24, datetime(2026, 10, 6, 21, 0, tzinfo=UTC))
    methods = ["root_event_ensemble_v1", "single_model_forecaster_v1"]
    manifest = {"methods": methods, "entries": [
        {"entry_id": "e1", "macro": spec.model_dump(mode="json")},
        {"entry_id": "e2", "macro": fast.model_dump(mode="json")}]}
    report = {"id": "cohort", "manifest_hash": "h", "questions": [
        {"id": "e1", "release_event": "employment-2026-10-02", "cells": [
            {"method": methods[0], "status": "forecasted", "probability": 0.374},
            {"method": methods[1], "status": "forecasted", "probability": 0.3}]},
        {"id": "e2", "release_event": "dgs10-2026-10-05", "cells": [
            {"method": methods[0], "status": "insufficient_evidence", "probability": None},
            {"method": methods[1], "status": "forecasted", "probability": 0.45}]}]}
    (directory / "frozen_manifest.json").write_text(json.dumps({"manifest": manifest}))
    (directory / "cohort_report.json").write_text(json.dumps(report))


def test_score_artifact_scores_resolved_and_keeps_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_artifact(tmp_path)
    vintages = {"2026-10-01": {"2026-08-01": "4.1"}, "2026-10-02": {"2026-08-01": "4.1", "2026-09-01": "4.2"}}

    def not_ready(*_args, **_kwargs):
        raise artifact_scoring.OfficialMacroOutcomeError(artifact_scoring.NOT_READY)

    monkeypatch.setattr(artifact_scoring.official_fred_outcomes, "find_initial_release", not_ready)
    scores = score_artifact(tmp_path, today=date(2026, 10, 3), fetch=_fake_alfred(vintages, latest="2026-10-03"))

    assert scores["outcomes"]["e1"]["outcome"] == 1
    assert scores["outcomes"]["e2"]["status"] == "pending"
    root = scores["methods"]["root_event_ensemble_v1"]
    assert root["resolved_forecasts"] == 1
    assert root["mean_brier"] == pytest.approx((1 - 0.374) ** 2)
    single = scores["methods"]["single_model_forecaster_v1"]
    assert single["forecasted"] == 2 and single["resolved_forecasts"] == 1
    assert single["mean_brier"] == pytest.approx(0.49)

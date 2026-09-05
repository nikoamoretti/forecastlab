from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from tests.test_forecast_experiments import _create_experiment, _freeze_dataset

from forecastlab_api.forecast_analysis import FAILURE_CATEGORIES
from forecastlab_api.forecast_experiments import CONTROLLED_FORECAST_PROFILES
from forecastlab_api.models import (
    ForecastExperimentResult,
    ForecastExperimentRun,
    ForecastFailure,
    ForecastVersion,
)


def _profile_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["profile_id"]: item for item in payload["profiles"]}


def test_research_analysis_reports_performance_operations_and_research_without_mutation(
    client,
) -> None:
    dataset = _freeze_dataset(client, name="Research analysis fixture")
    created = _create_experiment(client, dataset["id"])
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 3
    comparison = client.get(
        f"/api/forecast-experiments/{created['id']}/report"
    ).json()

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        before_results = {
            item.experiment_run_id: (
                item.probability,
                item.brier_score,
                item.log_loss,
                item.completion_status,
            )
            for item in session.scalars(select(ForecastExperimentResult)).all()
        }
        before_versions = session.scalar(
            select(func.count()).select_from(ForecastVersion)
        )

    response = client.get(f"/api/forecast-experiments/{created['id']}/analysis")
    assert response.status_code == 200
    analysis = response.json()
    profiles = _profile_map(analysis)
    comparison_profiles = _profile_map(comparison)

    assert analysis["status"] == "completed_with_failures"
    assert analysis["configuration_hash"] == created["configuration_hash"]
    assert list(profiles) == list(CONTROLLED_FORECAST_PROFILES)
    assert analysis["notice"].startswith("Internal research measurements only")
    assert analysis["failure_taxonomy"] == [
        {"category": category, "label": label}
        for category, label in FAILURE_CATEGORIES.items()
    ]
    for profile_id, profile in profiles.items():
        assert profile["performance"]["brier_score"] == comparison_profiles[profile_id][
            "brier_score"
        ]
        assert profile["performance"]["log_loss"] == comparison_profiles[profile_id][
            "log_loss"
        ]
        assert profile["performance"]["calibration_buckets"]["available"] is False
        graph_profile = profile_id == "graph_forecaster_v1"
        assert profile["performance"]["calibration_buckets"]["sample_count"] == (
            0 if graph_profile else 1
        )
        assert profile["operations"]["assigned_questions"] == 1
        assert profile["operations"]["completed"] == (0 if graph_profile else 1)
        assert profile["operations"]["failed"] == (1 if graph_profile else 0)
        assert profile["operations"]["completion_rate"] == (
            0.0 if graph_profile else 1.0
        )
        assert profile["operations"]["failure_rate"] == (
            1.0 if graph_profile else 0.0
        )
        assert profile["operations"]["total_cost_usd"] == 0.0
        assert profile["operations"]["mean_latency_ms"] >= 0
        assert profile["research"]["evidence_coverage"] is not None
        assert profile["research"]["eligible_evidence_items"] > 0
        assert profile["research"]["distinct_sources"] > 0
        assert profile["research"]["source_quality"]["source_class_counts"]

    assert profiles["single_model_forecaster_v1"]["research"]["claim_count"] == 0
    assert profiles["three_track_forecaster"]["research"]["claim_count"] == 0
    graph_research = profiles["graph_forecaster_v1"]["research"]
    assert graph_research["claim_count"] > 0
    assert (
        graph_research["source_quality"]["claim_quality_assessed_count"]
        == graph_research["claim_count"]
    )
    assert 0 <= graph_research["source_quality"]["mean_claim_source_quality"] <= 1
    assert 0 <= graph_research["source_quality"]["primary_claim_rate"] <= 1

    with main_mod.SessionLocal() as session:
        after_results = {
            item.experiment_run_id: (
                item.probability,
                item.brier_score,
                item.log_loss,
                item.completion_status,
            )
            for item in session.scalars(select(ForecastExperimentResult)).all()
        }
        after_versions = session.scalar(
            select(func.count()).select_from(ForecastVersion)
        )
    assert after_results == before_results
    assert after_versions == before_versions


def test_failure_classification_and_annotation_are_persisted(client) -> None:
    dataset = _freeze_dataset(client, name="Failure classification fixture")
    created = _create_experiment(client, dataset["id"])
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 3
    progress = client.get(f"/api/forecast-experiments/{created['id']}").json()
    graph_run = next(
        item for item in progress["runs"] if item["profile_id"] == "graph_forecaster_v1"
    )
    first = client.post(
        f"/api/forecast-experiment-runs/{graph_run['id']}/failures",
        json={
            "category": "bad evidence",
            "annotation": "  The retained source did not support the decisive claim.  ",
            "created_by": "research-reviewer",
        },
    )
    assert first.status_code == 201
    failure = first.json()
    assert failure["category"] == "bad_evidence"
    assert failure["label"] == "Bad evidence"
    assert failure["annotation"] == "The retained source did not support the decisive claim."
    assert failure["created_by"] == "research-reviewer"

    repeated = client.post(
        f"/api/forecast-experiment-runs/{graph_run['id']}/failures",
        json={
            "category": "bad_evidence",
            "annotation": "The excerpt was relevant but insufficient for the conclusion.",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == failure["id"]

    patched = client.patch(
        f"/api/forecast-failures/{failure['id']}",
        json={"annotation": "Final internal evidence review annotation."},
    )
    assert patched.status_code == 200
    assert patched.json()["annotation"] == "Final internal evidence review annotation."

    listed = client.get(
        f"/api/forecast-experiment-runs/{graph_run['id']}/failures"
    ).json()
    assert len(listed["failures"]) == 1
    assert listed["failures"][0]["id"] == failure["id"]

    invalid = client.post(
        f"/api/forecast-experiment-runs/{graph_run['id']}/failures",
        json={"category": "bad_luck", "annotation": "Unsupported category."},
    )
    assert invalid.status_code == 400
    assert "invalid_forecast_failure_category" in str(invalid.json())

    analysis = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    ).json()
    assert analysis["failure_summary"]["total_classifications"] == 1
    assert analysis["failure_summary"]["classified_runs"] == 1
    assert analysis["failure_summary"]["by_category"]["bad_evidence"] == 1
    graph_row = next(
        item
        for item in analysis["rows"]
        if item["forecast_experiment_run_id"] == graph_run["id"]
    )
    assert graph_row["failure_classifications"][0]["annotation"] == (
        "Final internal evidence review annotation."
    )

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastFailure)) == 1
        stored = session.get(ForecastFailure, failure["id"])
        assert stored is not None
        assert stored.experiment_id == created["id"]


def test_analysis_reports_operational_failures_without_scoring_them(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _freeze_dataset(client, name="Operational analysis fixture")
    created = _create_experiment(client, dataset["id"])

    def fail_execution(*_args, **_kwargs):
        raise RuntimeError("analysis_operation_failed")

    monkeypatch.setattr("forecastlab_api.forecast_experiments.execute_run", fail_execution)
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 3
    analysis = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    ).json()

    assert analysis["status"] == "failed"
    for profile in analysis["profiles"]:
        assert profile["performance"]["brier_score"] is None
        assert profile["performance"]["log_loss"] is None
        assert profile["performance"]["calibration_buckets"]["sample_count"] == 0
        assert profile["operations"]["completed"] == 0
        assert profile["operations"]["failed"] == 1
        assert profile["operations"]["failure_rate"] == 1.0
        assert profile["operations"]["failure_categories"] == {
            "PermanentProviderError": 1
        }
    assert all(row["probability"] is None for row in analysis["rows"])
    assert all(row["brier_score"] is None for row in analysis["rows"])


def test_failure_classification_rejects_nonterminal_run(client) -> None:
    dataset = _freeze_dataset(client, name="Pending classification fixture")
    created = _create_experiment(client, dataset["id"])
    run = created["runs"][0]

    response = client.post(
        f"/api/forecast-experiment-runs/{run['id']}/failures",
        json={"category": "bad_contract", "annotation": "Too early."},
    )

    assert response.status_code == 400
    assert "forecast_experiment_run_not_terminal" in str(response.json())
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        stored = session.get(ForecastExperimentRun, run["id"])
        assert stored is not None
        assert stored.status == "pending"

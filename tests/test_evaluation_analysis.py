from __future__ import annotations

import csv
import io

from sqlalchemy import func, select

from forecastlab_api.evaluation_experiments import CONTROLLED_COMPARISON_PROFILES
from forecastlab_api.models import ForecastFailure


def _evaluation_csv() -> bytes:
    row = {
        "question": "Will synthetic series A exceed threshold 1 before 31 December 2025?",
        "yes_condition": "Synthetic series A exceeds threshold 1 before 2025-12-31 23:59 UTC.",
        "no_condition": "Synthetic series A does not exceed threshold 1 before 2025-12-31 23:59 UTC.",
        "forecast_date": "2024-08-01T00:00:00Z",
        "resolution_date": "2026-01-01T00:00:00Z",
        "outcome": 1,
        "resolution_source": "https://fixtures.forecastlab.local/synthetic-a",
        "domain": "synthetic",
        "category": "test",
    }
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return stream.getvalue().encode()


def _completed_experiment(client) -> tuple[str, dict]:
    imported = client.post(
        "/api/evaluation/datasets/import",
        data={
            "name": "Synthetic analysis test dataset",
            "version": "1.0.0",
            "description": "Synthetic historical-shaped row for analysis tests only.",
            "provenance": "Synthetic test fixture; not forecasting-quality evidence.",
        },
        files={"file": ("analysis.csv", _evaluation_csv(), "text/csv")},
    )
    assert imported.status_code == 201
    created = client.post(
        "/api/evaluation/experiments",
        json={
            "dataset_id": imported.json()["id"],
            "profile_ids": list(CONTROLLED_COMPARISON_PROFILES),
            "synthetic_test": True,
        },
    )
    assert created.status_code == 201
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 2
    report = client.get(f"/api/evaluation/experiments/{created.json()['id']}/report")
    assert report.status_code == 200
    assert report.json()["status"] == "completed"
    return created.json()["id"], report.json()


def test_failure_classification_storage_and_annotation_persistence(client) -> None:
    experiment_id, report = _completed_experiment(client)
    evaluation_run_id = report["rows"][0]["run_id"]
    created = client.post(
        f"/api/evaluation/runs/{evaluation_run_id}/failures",
        json={
            "category": "overconfidence",
            "annotation": "  Probability was too extreme for the retained uncertainty.  ",
            "created_by": "method_reviewer",
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["failure_group"] == "reasoning"
    assert payload["category"] == "overconfidence"
    assert payload["annotation"] == "Probability was too extreme for the retained uncertainty."
    assert payload["created_by"] == "method_reviewer"

    updated = client.patch(
        f"/api/evaluation/failures/{payload['id']}",
        json={"annotation": "Reviewed uncertainty did not justify the forecast extremity."},
    )
    assert updated.status_code == 200
    assert updated.json()["annotation"] == "Reviewed uncertainty did not justify the forecast extremity."
    assert updated.json()["id"] == payload["id"]

    duplicate = client.post(
        f"/api/evaluation/runs/{evaluation_run_id}/failures",
        json={
            "category": "overconfidence",
            "annotation": "Final internal annotation.",
            "created_by": "method_reviewer",
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == payload["id"]
    assert duplicate.json()["annotation"] == "Final internal annotation."

    analysis = client.get(f"/api/evaluation/experiments/{experiment_id}/analysis").json()
    reviewed = next(item for item in analysis["rows"] if item["evaluation_run_id"] == evaluation_run_id)
    assert reviewed["failures"][0]["annotation"] == "Final internal annotation."

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastFailure)) == 1


def test_analysis_report_aggregates_performance_reliability_research_and_cost(client) -> None:
    experiment_id, comparison = _completed_experiment(client)
    response = client.get(f"/api/evaluation/experiments/{experiment_id}/analysis")

    assert response.status_code == 200
    analysis = response.json()
    assert "do not establish profile superiority" in analysis["notice"]
    assert set(analysis["failure_taxonomy"]) == {
        "question",
        "research",
        "reasoning",
        "aggregation",
        "operational",
    }
    comparison_by_profile = {item["profile_id"]: item for item in comparison["profiles"]}
    analysis_by_profile = {item["profile_id"]: item for item in analysis["profiles"]}
    assert set(analysis_by_profile) == set(CONTROLLED_COMPARISON_PROFILES)
    for profile_id, profile in analysis_by_profile.items():
        assert profile["performance"]["brier_score"] == comparison_by_profile[profile_id]["brier_score"]
        assert profile["performance"]["log_loss"] == comparison_by_profile[profile_id]["log_loss"]
        assert profile["performance"]["calibration_buckets"]["sample_count"] == 1
        assert profile["performance"]["calibration_buckets"]["available"] is False
        assert sum(item["count"] for item in profile["performance"]["probability_distribution"]) == 1
        assert profile["reliability"] == {
            "assigned_questions": 1,
            "completed": 1,
            "partial": 0,
            "failures": 0,
            "completion_rate": 1.0,
            "partial_rate": 0.0,
            "failure_rate": 0.0,
        }
        assert profile["research"]["evidence_coverage"] == 1.0
        assert profile["research"]["source_count"] > 0
        assert profile["cost"] == {"total_cost": 0.0, "cost_per_question": 0.0}
    assert analysis_by_profile["three_track_forecaster"]["research"]["claim_count"] == 0
    assert analysis_by_profile["graph_forecaster_v1"]["research"]["claim_count"] > 0
    assert analysis["failure_summary"]["total_classifications"] == 0


def test_analysis_report_groups_multiple_internal_failure_classifications(client) -> None:
    experiment_id, report = _completed_experiment(client)
    runs = {item["profile_id"]: item["run_id"] for item in report["rows"]}
    first = client.post(
        f"/api/evaluation/runs/{runs['three_track_forecaster']}/failures",
        json={"category": "wrong_resolver", "annotation": "Resolver did not match the reviewed record."},
    )
    second = client.post(
        f"/api/evaluation/runs/{runs['graph_forecaster_v1']}/failures",
        json={"category": "timeout", "annotation": "Research exceeded the recorded provider timeout."},
    )
    invalid = client.post(
        f"/api/evaluation/runs/{runs['graph_forecaster_v1']}/failures",
        json={"category": "made_up_failure", "annotation": "This must be rejected."},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert invalid.status_code == 400
    analysis = client.get(f"/api/evaluation/experiments/{experiment_id}/analysis").json()
    assert analysis["failure_summary"]["total_classifications"] == 2
    assert analysis["failure_summary"]["classified_runs"] == 2
    assert analysis["failure_summary"]["by_group"]["question"] == 1
    assert analysis["failure_summary"]["by_group"]["operational"] == 1
    assert analysis["failure_summary"]["by_category"]["wrong_resolver"] == 1
    assert analysis["failure_summary"]["by_category"]["timeout"] == 1
    assert sum(len(item["failures"]) for item in analysis["rows"]) == 2

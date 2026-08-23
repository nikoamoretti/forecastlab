from __future__ import annotations

from sqlalchemy import select

from forecastlab_api.experiments import V1_EVALUATION_METRICS, V1_EVALUATION_PROFILES
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkResult,
    BenchmarkTask,
    ForecastContractRow,
    ForecastRun,
)


def test_three_track_experiment_alias_preserves_existing_execution_settings() -> None:
    from forecastlab.profiles import load_profile

    existing = load_profile("three_track_ensemble").model_dump(
        exclude={"id", "label", "description"}
    )
    alias = load_profile("three_track_forecaster").model_dump(
        exclude={"id", "label", "description"}
    )

    assert alias == existing


def test_v1_evaluation_dataset_seed_is_idempotent(client) -> None:
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.experiments import V1_EVALUATION_DATASET_KEY, V1_EVALUATION_DATASET_VERSION
    from forecastlab_api.seed import seed_v1_evaluation_benchmarks

    with SessionLocal() as session:
        first = seed_v1_evaluation_benchmarks(session)
        second = seed_v1_evaluation_benchmarks(session)
        session.commit()
        assert first.id == second.id
        assert first.question_count == 10
        matches = session.scalars(
            select(BenchmarkDataset).where(
                BenchmarkDataset.builtin_key == V1_EVALUATION_DATASET_KEY,
                BenchmarkDataset.builtin_version == V1_EVALUATION_DATASET_VERSION,
            )
        ).all()
        assert len(matches) == 1


def test_v1_evaluation_workflow_runs_ten_question_mock_comparison(client) -> None:
    workflow_response = client.get("/api/evaluations/v1")

    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    assert workflow["question_count"] == 10
    assert workflow["task_count"] == 20
    assert workflow["profiles"] == list(V1_EVALUATION_PROFILES)
    assert workflow["metrics"] == list(V1_EVALUATION_METRICS)
    assert len(workflow["questions"]) == 10
    assert "do not establish profile superiority" in workflow["notice"]

    created_response = client.post("/api/evaluations/v1")

    assert created_response.status_code == 200
    created = created_response.json()
    assert created["total_tasks"] == 20
    assert created["is_synthetic"] is True
    assert created["workflow"]["question_count"] == 10

    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=30) == 20

    progress = client.get(f"/api/experiments/{created['id']}").json()
    assert progress["status"] == "completed"
    assert progress["completed_tasks"] == 20
    assert progress["failed_tasks"] == 0

    summary = client.get(f"/api/experiments/{created['id']}/summary").json()
    assert summary["sample_size"] == 20
    assert summary["model_provider"] == "mock"
    assert summary["search_provider"] == "mock"
    assert {item["profile_id"] for item in summary["profiles"]} == set(V1_EVALUATION_PROFILES)
    assert "do not establish profile superiority" in summary["comparison_interpretation"]
    assert set(summary["metric_definitions"]) == set(V1_EVALUATION_METRICS)
    assert len(summary["paired_comparisons_all_valid"]) == 1
    comparison = summary["paired_comparisons_all_valid"][0]
    assert comparison["n"] == 10
    assert comparison["mean_paired_brier_difference"] is not None
    assert comparison["mean_log_loss_difference"] is not None
    assert comparison["mean_cost_difference"] is not None
    assert comparison["mean_latency_difference"] is not None
    assert comparison["mean_evidence_coverage_difference"] is not None
    assert comparison["evidence_coverage_pair_count"] == 10
    for profile in summary["profiles"]:
        assert profile["total_count"] == 10
        assert profile["completion_rate"] == 1.0
        assert profile["all_valid"]["n"] == 10
        assert profile["all_valid"]["brier"] is not None
        assert profile["all_valid"]["log_loss"] is not None
        assert profile["all_valid"]["mean_cost_usd"] == 0.0
        assert profile["all_valid"]["mean_latency_ms"] >= 0
        assert profile["all_valid"]["mean_evidence_coverage"] == 1.0
        assert profile["all_valid"]["evidence_coverage_n"] == 10
    assert all(row["evidence_coverage"] == 1.0 for row in summary["rows"])
    assert {row["evidence_total_units"] for row in summary["rows"]} == {3, 7}

    export = client.get(f"/api/experiments/{created['id']}/export.csv")
    assert export.status_code == 200
    assert "evidence_coverage" in export.text.splitlines()[0]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        tasks = session.scalars(
            select(BenchmarkTask).where(
                BenchmarkTask.experiment_id == created["id"],
                BenchmarkTask.profile_id == "graph_forecaster_v1",
            )
        ).all()
        assert len(tasks) == 10
        for task in tasks:
            assert task.question_id is not None
            contract = session.scalar(
                select(ForecastContractRow).where(
                    ForecastContractRow.question_id == task.question_id,
                    ForecastContractRow.status == "approved",
                )
            )
            assert contract is not None
        results = session.scalars(
            select(BenchmarkResult).where(BenchmarkResult.experiment_id == created["id"])
        ).all()
        assert len(results) == 20
        graph_runs = session.scalars(
            select(ForecastRun).where(
                ForecastRun.benchmark_task_id.in_([task.id for task in tasks]),
            )
        ).all()
        assert len(graph_runs) == 10

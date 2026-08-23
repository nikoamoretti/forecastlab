from __future__ import annotations

from sqlalchemy import select

from forecastlab_api.experiments import V1_EVALUATION_METRICS, V1_EVALUATION_PROFILES
from forecastlab_api.models import (
    BenchmarkDataset,
    BenchmarkProfileSnapshot,
    BenchmarkResult,
    BenchmarkTask,
    ForecastAggregationRow,
    ForecastContractRow,
    ForecastNodeRunRow,
    ForecastRun,
    ProviderCallLedger,
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


def test_v1_comparison_profiles_share_resource_ceilings() -> None:
    from forecastlab.profiles import load_profile

    baseline = load_profile("three_track_forecaster")
    graph = load_profile("graph_forecaster_v1")
    single = load_profile("single_model_forecaster_v1")
    ceiling_fields = (
        "max_model_calls",
        "max_search_calls",
        "max_fetched_documents",
        "max_tokens",
        "max_estimated_cost_usd",
        "max_wall_clock_seconds",
    )

    assert {field: getattr(graph, field) for field in ceiling_fields} == {
        field: getattr(baseline, field) for field in ceiling_fields
    } == {field: getattr(single, field) for field in ceiling_fields}
    assert graph.graph_generation_enabled is True
    assert graph.evidence_claims_enabled is True
    assert graph.node_forecasting_enabled is True
    assert graph.graph_aggregation_enabled is True


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
    assert workflow["task_count"] == 30
    assert workflow["profiles"] == list(V1_EVALUATION_PROFILES)
    assert workflow["metrics"] == list(V1_EVALUATION_METRICS)
    assert len(workflow["questions"]) == 10
    assert "do not establish profile superiority" in workflow["notice"]

    created_response = client.post("/api/evaluations/v1")

    assert created_response.status_code == 200
    created = created_response.json()
    assert created["total_tasks"] == 30
    assert created["is_synthetic"] is True
    assert created["workflow"]["question_count"] == 10

    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=40) == 30

    progress = client.get(f"/api/experiments/{created['id']}").json()
    assert progress["status"] == "completed"
    assert progress["completed_tasks"] == 30
    assert progress["failed_tasks"] == 0

    summary = client.get(f"/api/experiments/{created['id']}/summary").json()
    assert summary["sample_size"] == 30
    assert summary["model_provider"] == "mock"
    assert summary["search_provider"] == "mock"
    assert {item["profile_id"] for item in summary["profiles"]} == set(V1_EVALUATION_PROFILES)
    assert "do not establish profile superiority" in summary["comparison_interpretation"]
    assert set(summary["metric_definitions"]) == set(V1_EVALUATION_METRICS)
    assert len(summary["paired_comparisons_all_valid"]) == 3
    for comparison in summary["paired_comparisons_all_valid"]:
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
    assert {row["evidence_total_units"] for row in summary["rows"]} == {1, 3, 7}

    export = client.get(f"/api/experiments/{created['id']}/export.csv")
    assert export.status_code == 200
    assert "evidence_coverage" in export.text.splitlines()[0]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        all_tasks = session.scalars(
            select(BenchmarkTask).where(BenchmarkTask.experiment_id == created["id"])
        ).all()
        paired: dict[str, list[BenchmarkTask]] = {}
        for task in all_tasks:
            paired.setdefault(task.benchmark_question_id, []).append(task)
        assert len(paired) == 10
        for pair in paired.values():
            assert {task.profile_id for task in pair} == set(V1_EVALUATION_PROFILES)
            runs = [session.get(ForecastRun, task.run_id) for task in pair]
            assert all(run is not None for run in runs)
            assert len({run.as_of for run in runs if run is not None}) == 1
            contracts = [
                session.scalar(
                    select(ForecastContractRow).where(
                        ForecastContractRow.question_id == task.question_id,
                        ForecastContractRow.status == "approved",
                    )
                )
                for task in pair
            ]
            assert all(contract is not None for contract in contracts)
            frozen_contracts = {
                (
                    contract.original_question,
                    contract.normalized_question,
                    contract.yes_condition,
                    contract.no_condition,
                    contract.resolution_date,
                    contract.authoritative_source,
                    contract.resolution_method,
                )
                for contract in contracts
                if contract is not None
            }
            assert len(frozen_contracts) == 1

        snapshots = session.scalars(
            select(BenchmarkProfileSnapshot).where(
                BenchmarkProfileSnapshot.experiment_id == created["id"]
            )
        ).all()
        assert len(snapshots) == 3
        assert len(
            {
                (
                    item.effective_max_cost_usd,
                    item.effective_max_tokens,
                    item.effective_max_model_calls,
                    item.effective_max_search_calls,
                    item.effective_max_fetched_documents,
                    item.effective_max_wall_clock_seconds,
                )
                for item in snapshots
            }
        ) == 1

        graph_tasks = session.scalars(
            select(BenchmarkTask).where(
                BenchmarkTask.experiment_id == created["id"],
                BenchmarkTask.profile_id == "graph_forecaster_v1",
            )
        ).all()
        assert len(graph_tasks) == 10
        for task in graph_tasks:
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
        assert len(results) == 30
        graph_runs = session.scalars(
            select(ForecastRun).where(
                ForecastRun.benchmark_task_id.in_([task.id for task in graph_tasks]),
            )
        ).all()
        assert len(graph_runs) == 10

        single_tasks = session.scalars(
            select(BenchmarkTask).where(
                BenchmarkTask.experiment_id == created["id"],
                BenchmarkTask.profile_id == "single_model_forecaster_v1",
            )
        ).all()
        assert len(single_tasks) == 10
        single_runs = session.scalars(
            select(ForecastRun).where(
                ForecastRun.benchmark_task_id.in_([task.id for task in single_tasks]),
            )
        ).all()
        assert len(single_runs) == 10
        for run in single_runs:
            model_calls = session.scalars(
                select(ProviderCallLedger).where(
                    ProviderCallLedger.run_id == run.id,
                    ProviderCallLedger.provider_type == "model",
                )
            ).all()
            assert len(model_calls) == 1
            assert model_calls[0].stage == "single_model_forecast"
            assert session.scalar(
                select(ForecastNodeRunRow).where(ForecastNodeRunRow.forecast_run_id == run.id)
            ) is None
            assert session.scalar(
                select(ForecastAggregationRow).where(
                    ForecastAggregationRow.forecast_run_id == run.id
                )
            ) is None

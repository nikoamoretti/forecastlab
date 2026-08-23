from __future__ import annotations

import csv
import io
import json
import math

import pytest
from sqlalchemy import select

from forecastlab.evaluation import log_loss
from forecastlab.execution import configuration_hash
from forecastlab_api.evaluation_experiments import CONTROLLED_COMPARISON_PROFILES
from forecastlab_api.models import (
    EvaluationExperiment,
    EvaluationResult,
    EvaluationRun,
    ForecastRun,
    FrozenEvaluationExperimentError,
)


def _row(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
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
    payload.update(overrides)
    return payload


def _csv(rows: list[dict[str, object]]) -> bytes:
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "question",
            "yes_condition",
            "no_condition",
            "forecast_date",
            "resolution_date",
            "outcome",
            "resolution_source",
            "domain",
            "category",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def _dataset(client, rows: list[dict[str, object]] | None = None) -> dict:
    response = client.post(
        "/api/evaluation/datasets/import",
        data={
            "name": "Synthetic controlled-runner test dataset",
            "version": "1.0.0",
            "description": "Historical-shaped synthetic data used only in automated tests.",
            "provenance": "Synthetic test fixture; not a real quality benchmark.",
        },
        files={"file": ("synthetic-evaluation.csv", _csv(rows or [_row()]), "text/csv")},
    )
    assert response.status_code == 201
    return response.json()


def _experiment(client, dataset_id: str) -> dict:
    response = client.post(
        "/api/evaluation/experiments",
        json={
            "dataset_id": dataset_id,
            "profile_ids": list(reversed(CONTROLLED_COMPARISON_PROFILES)),
            "synthetic_test": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_same_question_is_assigned_to_both_profiles_and_configuration_is_frozen(client) -> None:
    dataset = _dataset(client)
    created = _experiment(client, dataset["id"])

    assert created["profiles"] == list(CONTROLLED_COMPARISON_PROFILES)
    assert created["total_runs"] == 2
    assert created["configuration"]["dataset"]["version"] == "1.0.0"
    assert created["configuration"]["dataset"]["dataset_hash"] == dataset["dataset_hash"]
    assert created["configuration"]["provider"]["model_provider"] == "mock"
    assert created["configuration"]["provider"]["search_provider"] == "mock"
    assert created["configuration"]["synthetic_test"] is True

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        experiment = session.get(EvaluationExperiment, created["id"])
        assert experiment is not None
        frozen = json.loads(experiment.configuration_json)
        assert experiment.configuration_hash == configuration_hash(frozen)
        assert frozen["profile_ids"] == list(CONTROLLED_COMPARISON_PROFILES)
        assert frozen["questions"][0]["evidence_cutoff"] == "2024-08-01T00:00:00+00:00"
        assert len({json.dumps(item["budget"], sort_keys=True) for item in frozen["profiles"].values()}) == 1
        runs = session.scalars(
            select(EvaluationRun).where(EvaluationRun.experiment_id == experiment.id)
        ).all()
        assert len(runs) == 2
        assert len({item.question_id for item in runs}) == 1
        assert {item.profile_id for item in runs} == set(CONTROLLED_COMPARISON_PROFILES)
        experiment.configuration_json = "{}"
        with pytest.raises(
            FrozenEvaluationExperimentError,
            match="evaluation_experiment_configuration_immutable",
        ):
            session.flush()
        session.rollback()


def test_results_and_metrics_are_persisted_for_both_forecasting_profiles(client) -> None:
    dataset = _dataset(client)
    created = _experiment(client, dataset["id"])

    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 2
    progress = client.get(f"/api/evaluation/experiments/{created['id']}")
    assert progress.status_code == 200
    assert progress.json()["status"] == "completed"
    assert progress.json()["completed_runs"] == 2

    response = client.get(f"/api/evaluation/experiments/{created['id']}/report")
    assert response.status_code == 200
    report = response.json()
    assert report["notice"].endswith("does not establish that either profile is superior.")
    assert {item["profile_id"] for item in report["profiles"]} == set(CONTROLLED_COMPARISON_PROFILES)
    assert len(report["rows"]) == 2
    for row in report["rows"]:
        assert row["completion_status"] == "completed"
        assert 0 <= row["probability"] <= 1
        assert math.isclose(row["brier_score"], (row["probability"] - row["outcome"]) ** 2)
        assert math.isclose(row["log_loss"], log_loss(row["probability"], row["outcome"]))
        assert row["cost"] == 0
        assert row["latency"] >= 0
        assert row["evidence_coverage"] == 1

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        results = session.scalars(
            select(EvaluationResult)
            .join(EvaluationRun, EvaluationResult.run_id == EvaluationRun.id)
            .where(EvaluationRun.experiment_id == created["id"])
        ).all()
        assert len(results) == 2
        evaluation_runs = session.scalars(
            select(EvaluationRun).where(EvaluationRun.experiment_id == created["id"])
        ).all()
        forecast_runs = [session.get(ForecastRun, item.forecast_run_id) for item in evaluation_runs]
        assert all(item is not None for item in forecast_runs)
        assert len({item.as_of for item in forecast_runs if item is not None}) == 1
        assert {item.profile_id for item in forecast_runs if item is not None} == set(
            CONTROLLED_COMPARISON_PROFILES
        )


def test_execution_uses_the_frozen_profile_prompt_and_provider_snapshot(client, monkeypatch) -> None:
    dataset = _dataset(client)
    created = _experiment(client, dataset["id"])
    saved = client.put(
        "/api/settings",
        json={
            "model_provider": "openai_compatible",
            "model_name": "mutated-after-freeze",
            "model_base_url": "https://mutated.invalid/v1",
            "model_api_key": "sk-test-only-mutated-key",
            "search_provider": "tavily",
            "search_api_key": "tvly-test-only-mutated-key",
        },
    )
    assert saved.status_code == 200

    def mutable_source_loaded(*_args, **_kwargs):
        raise AssertionError("mutable profile or prompt source loaded during evaluation execution")

    monkeypatch.setattr("forecastlab_api.evaluation_experiments.load_profile", mutable_source_loaded)
    monkeypatch.setattr("forecastlab_api.evaluation_experiments.load_prompt_bundle", mutable_source_loaded)
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 2
    report = client.get(f"/api/evaluation/experiments/{created['id']}/report").json()
    assert report["status"] == "completed"
    assert report["configuration"]["provider"]["model_provider"] == "mock"
    assert report["configuration"]["provider"]["model"] == "mock-forecast-v1"
    assert report["configuration"]["provider"]["search_provider"] == "mock"

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_runs = session.scalars(
            select(EvaluationRun).where(EvaluationRun.experiment_id == created["id"])
        ).all()
        forecast_runs = [session.get(ForecastRun, item.forecast_run_id) for item in evaluation_runs]
        for forecast_run in forecast_runs:
            assert forecast_run is not None
            context = json.loads(forecast_run.execution_context_json)
            assert context["model_provider"] == "mock"
            assert context["search_provider"] == "mock"
            assert context["model_name"] != "mutated-after-freeze"


def test_failure_is_recorded_without_synthesizing_a_probability(client, monkeypatch) -> None:
    dataset = _dataset(client)
    created = _experiment(client, dataset["id"])

    def fail_execution(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr("forecastlab_api.evaluation_experiments.execute_run", fail_execution)
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 2
    progress = client.get(f"/api/evaluation/experiments/{created['id']}").json()
    assert progress["status"] == "failed"
    assert progress["failed_runs"] == 2

    report = client.get(f"/api/evaluation/experiments/{created['id']}/report").json()
    assert len(report["rows"]) == 2
    assert all(item["completion_status"] == "failed" for item in report["rows"])
    assert all(item["probability"] is None for item in report["rows"])
    assert all("synthetic execution failure" in item["error"] for item in report["rows"])
    assert all(item["failure_rate"] == 1 for item in report["profiles"])


def test_controlled_runner_rejects_any_other_profile_set(client) -> None:
    dataset = _dataset(client)
    response = client.post(
        "/api/evaluation/experiments",
        json={
            "dataset_id": dataset["id"],
            "profile_ids": ["three_track_forecaster"],
            "synthetic_test": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "controlled_profiles_required"

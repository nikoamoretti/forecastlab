from __future__ import annotations

import csv
import io
import json
from collections import defaultdict

import pytest
from sqlalchemy import select

from forecastlab.evaluation import brier_score, log_loss
from forecastlab.execution import configuration_hash
from forecastlab_api.forecast_experiments import CONTROLLED_FORECAST_PROFILES
from forecastlab_api.models import (
    ForecastExperiment,
    ForecastExperimentResult,
    ForecastExperimentRun,
    ForecastRun,
    FrozenForecastExperimentError,
)


def _resolved_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "question": "Did US unemployment reach 5% before 31 December 2025?",
        "yes_condition": (
            "The BLS seasonally adjusted U-3 unemployment rate reached at least 5.0% "
            "by 2025-12-31."
        ),
        "no_condition": (
            "The BLS seasonally adjusted U-3 unemployment rate remained below 5.0% "
            "through 2025-12-31."
        ),
        "forecast_date": "2025-01-01T00:00:00Z",
        "resolution_date": "2025-12-31T00:00:00Z",
        "outcome": 0,
        "resolution_source": "https://fixtures.forecastlab.local/bls-employment-situation",
        "authoritative_resolver": "US Bureau of Labor Statistics",
        "domain": "economics",
    }
    row.update(overrides)
    return row


def _csv_bytes(rows: list[dict[str, object]]) -> bytes:
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
            "authoritative_resolver",
            "domain",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def _import_dataset(
    client,
    *,
    name: str = "Controlled comparison fixture",
    version: str = "1.0.0",
) -> dict:
    response = client.post(
        "/api/evaluation/datasets/import",
        data={
            "name": name,
            "version": version,
            "description": "Resolved fixture used only to test experiment orchestration.",
            "provenance": "ForecastLab deterministic mock-provider test fixture.",
        },
        files={"file": ("resolved.csv", _csv_bytes([_resolved_row()]), "text/csv")},
    )
    assert response.status_code == 201
    return response.json()


def _freeze_dataset(client, *, name: str = "Controlled comparison fixture") -> dict:
    imported = _import_dataset(client, name=name)
    reviewed = client.post(f"/api/evaluation/datasets/{imported['id']}/review")
    assert reviewed.status_code == 200
    frozen = client.post(f"/api/evaluation/datasets/{imported['id']}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    return frozen.json()


def _create_experiment(client, dataset_id: str):
    response = client.post(
        "/api/forecast-experiments",
        json={
            "dataset_id": dataset_id,
            "profile_ids": list(CONTROLLED_FORECAST_PROFILES),
            "synthetic_test": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_controlled_experiment_freezes_inputs_and_assigns_same_questions(client) -> None:
    dataset = _freeze_dataset(client)
    created = _create_experiment(client, dataset["id"])

    assert created["profiles"] == list(CONTROLLED_FORECAST_PROFILES)
    assert created["total_runs"] == 3
    assignments: dict[str, set[str]] = defaultdict(set)
    for run in created["runs"]:
        assignments[run["evaluation_question_id"]].add(run["profile_id"])
    assert list(assignments.values()) == [set(CONTROLLED_FORECAST_PROFILES)]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        experiment = session.get(ForecastExperiment, created["id"])
        assert experiment is not None
        frozen = json.loads(experiment.configuration_json)
        assert experiment.configuration_hash == configuration_hash(frozen)
        assert frozen["dataset"]["hash"] == dataset["hash"]
        assert frozen["dataset"]["status"] == "frozen"
        assert frozen["questions"][0]["evidence_cutoff"] == "2025-01-01T00:00:00+00:00"
        assert frozen["profile_ids"] == list(CONTROLLED_FORECAST_PROFILES)
        assert frozen["provider"]["model_provider"] == "mock"
        assert frozen["provider"]["search_provider"] == "mock"
        assert "model_api_key" not in frozen["provider"]
        assert "search_api_key" not in frozen["provider"]
        assert frozen["prompts"]["bundle"]
        assert frozen["prompts"]["hashes"]
        assert frozen["code"]["git_commit"]
        assert frozen["code"]["tracked_source_hash"]
        assert frozen["code"]["dependency_hash"]
        assert all(
            snapshot["budget"] == frozen["common_budget"]
            for snapshot in frozen["profiles"].values()
        )

        experiment.configuration_json = "{}"
        with pytest.raises(
            FrozenForecastExperimentError,
            match="forecast_experiment_configuration_immutable",
        ):
            session.flush()
        session.rollback()


def test_all_profiles_execute_from_frozen_configuration_and_report_metrics(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _freeze_dataset(client, name="Frozen execution fixture")
    created = _create_experiment(client, dataset["id"])

    client.put(
        "/api/settings",
        json={
            "model_provider": "openai_compatible",
            "model_name": "changed-after-freeze",
            "model_base_url": "https://changed.invalid/v1",
            "search_provider": "tavily",
            "model_api_key": "test-key-never-used",
            "search_api_key": "test-search-key-never-used",
        },
    )

    def mutable_source_loaded(*_args, **_kwargs):
        raise AssertionError("mutable configuration loaded during controlled execution")

    monkeypatch.setattr(
        "forecastlab_api.forecast_experiments.load_profile",
        mutable_source_loaded,
    )
    monkeypatch.setattr(
        "forecastlab_api.forecast_experiments.load_prompt_bundle",
        mutable_source_loaded,
    )
    monkeypatch.setattr(
        "forecastlab_api.forecast_experiments.resolve_execution_context",
        mutable_source_loaded,
    )

    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 3
    report_response = client.get(f"/api/forecast-experiments/{created['id']}/report")
    assert report_response.status_code == 200
    report = report_response.json()

    assert report["status"] == "completed_with_failures"
    assert report["notice"].startswith("Measurements only")
    assert [row["profile_id"] for row in report["profiles"]] == list(
        CONTROLLED_FORECAST_PROFILES
    )
    assert len(report["rows"]) == 3
    for row in report["rows"]:
        if row["profile_id"] == "graph_forecaster_v1":
            assert row["completion_status"] == "failed"
            assert row["probability"] is None
            assert row["brier_score"] is None
            assert row["log_loss"] is None
            assert row["error"] == "two_distinct_source_hosts_required"
        else:
            assert row["completion_status"] == "completed"
            assert row["probability"] is not None
            assert row["brier_score"] == pytest.approx(
                brier_score(row["probability"], row["outcome"])
            )
            assert row["log_loss"] == pytest.approx(
                log_loss(row["probability"], row["outcome"])
            )
        assert row["cost_usd"] == 0.0
        assert row["latency_ms"] >= 0
        assert row["evidence_coverage"] is not None
    for profile in report["profiles"]:
        assert profile["assigned_questions"] == 1
        graph_profile = profile["profile_id"] == "graph_forecaster_v1"
        assert profile["scored_questions"] == (0 if graph_profile else 1)
        assert profile["completion_rate"] == (0.0 if graph_profile else 1.0)
        assert profile["failure_rate"] == (1.0 if graph_profile else 0.0)
        assert profile["total_cost_usd"] == 0.0

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        runs = session.scalars(
            select(ForecastRun).where(
                ForecastRun.id.in_(
                    [row["forecast_run_id"] for row in report["rows"]]
                )
            )
        ).all()
        assert len(runs) == 3
        contexts = [json.loads(run.execution_context_json) for run in runs]
        assert {item["profile_id"] for item in contexts} == set(
            CONTROLLED_FORECAST_PROFILES
        )
        assert {item["model_provider"] for item in contexts} == {"mock"}
        assert {item["search_provider"] for item in contexts} == {"mock"}
        assert {run.as_of.date().isoformat() for run in runs if run.as_of} == {"2025-01-01"}


def test_experiment_failure_is_persisted_without_a_probability(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _freeze_dataset(client, name="Failure handling fixture")
    created = _create_experiment(client, dataset["id"])

    def fail_execution(*_args, **_kwargs):
        raise RuntimeError("controlled_test_failure")

    monkeypatch.setattr("forecastlab_api.forecast_experiments.execute_run", fail_execution)
    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == 3
    report = client.get(f"/api/forecast-experiments/{created['id']}/report").json()

    assert report["status"] == "failed"
    assert all(row["status"] == "failed" for row in report["rows"])
    assert all(row["probability"] is None for row in report["rows"])
    assert all(row["brier_score"] is None for row in report["rows"])
    assert all(row["log_loss"] is None for row in report["rows"])
    assert all(row["error"] == "controlled_test_failure" for row in report["rows"])
    assert all(profile["failure_rate"] == 1.0 for profile in report["profiles"])

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        runs = session.scalars(
            select(ForecastExperimentRun).where(
                ForecastExperimentRun.experiment_id == created["id"]
            )
        ).all()
        results = session.scalars(
            select(ForecastExperimentResult).where(
                ForecastExperimentResult.experiment_run_id.in_([item.id for item in runs])
            )
        ).all()
        assert len(results) == 3
        assert all(item.completion_status == "failed" for item in results)
        assert all(item.probability is None for item in results)


def test_controlled_experiment_requires_frozen_dataset_and_all_profiles(client) -> None:
    draft = _import_dataset(client, name="Draft experiment fixture")

    not_frozen = client.post(
        "/api/forecast-experiments",
        json={
            "dataset_id": draft["id"],
            "profile_ids": list(CONTROLLED_FORECAST_PROFILES),
            "synthetic_test": True,
        },
    )
    assert not_frozen.status_code == 400
    assert "evaluation_dataset_must_be_frozen" in str(not_frozen.json())

    client.post(f"/api/evaluation/datasets/{draft['id']}/review")
    client.post(f"/api/evaluation/datasets/{draft['id']}/freeze")
    incomplete = client.post(
        "/api/forecast-experiments",
        json={
            "dataset_id": draft["id"],
            "profile_ids": ["single_model_forecaster_v1", "three_track_forecaster"],
            "synthetic_test": True,
        },
    )
    assert incomplete.status_code == 400
    assert "controlled_forecast_profiles_required" in str(incomplete.json())

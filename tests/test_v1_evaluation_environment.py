from __future__ import annotations

import json
from collections import defaultdict

from tests.test_forecast_experiments import _create_experiment, _freeze_dataset

from forecastlab.execution import configuration_hash
from forecastlab.profiles import load_profile
from forecastlab_api.forecast_analysis import PAIRED_PROFILE_COMPARISONS
from forecastlab_api.forecast_experiments import CONTROLLED_FORECAST_PROFILES
from forecastlab_api.models import ForecastExperiment

EXPECTED_PROFILE_VERSIONS = {
    "single_model_forecaster_v1": 1,
    "three_track_forecaster": 1,
    "graph_forecaster_v1": 9,
}
EXPECTED_EXECUTION_STRATEGIES = {
    "single_model_forecaster_v1": "single_model",
    "three_track_forecaster": "legacy_tracks",
    "graph_forecaster_v1": "graph_nodes",
}


def test_v1_evaluation_profiles_are_present_and_versioned() -> None:
    assert list(CONTROLLED_FORECAST_PROFILES) == [
        "single_model_forecaster_v1",
        "three_track_forecaster",
        "graph_forecaster_v1",
    ]

    for profile_id in CONTROLLED_FORECAST_PROFILES:
        profile = load_profile(profile_id)
        assert profile.id == profile_id
        assert profile.version == EXPECTED_PROFILE_VERSIONS[profile_id]
        assert profile.execution_strategy == EXPECTED_EXECUTION_STRATEGIES[profile_id]
        assert profile.prompt_versions


def test_v1_evaluation_environment_runs_and_reports_from_one_frozen_manifest(
    client,
) -> None:
    dataset = _freeze_dataset(client, name="Integrated V1 evaluation fixture")
    created = _create_experiment(client, dataset["id"])

    assert created["dataset_id"] == dataset["id"]
    assert created["profiles"] == list(CONTROLLED_FORECAST_PROFILES)
    assert created["created_at"]
    assignments: dict[str, set[str]] = defaultdict(set)
    for run in created["runs"]:
        assignments[run["evaluation_question_id"]].add(run["profile_id"])
    assert list(assignments.values()) == [set(CONTROLLED_FORECAST_PROFILES)]

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        experiment = session.get(ForecastExperiment, created["id"])
        assert experiment is not None
        frozen = json.loads(experiment.configuration_json)

        assert frozen["dataset"]["id"] == dataset["id"]
        assert frozen["dataset"]["version"] == dataset["version"]
        assert frozen["dataset"]["hash"] == dataset["hash"]
        assert frozen["dataset"]["status"] == "frozen"
        assert frozen["profile_ids"] == list(CONTROLLED_FORECAST_PROFILES)
        assert experiment.configuration_hash == configuration_hash(frozen)
        assert experiment.created_at is not None

        assert frozen["prompts"]["bundle"]
        assert frozen["prompts"]["versions"]
        assert frozen["prompts"]["hashes"]
        assert frozen["prompts"]["bundle_hash"]
        assert frozen["provider"]["model_provider"] == "mock"
        assert frozen["provider"]["model"] == "mock-forecast-v1"
        assert frozen["provider"]["search_provider"] == "mock"
        assert frozen["provider"]["evidence_policy"] == "synthetic_historical_fixtures"
        assert frozen["code"]["git_commit"]
        assert frozen["code"]["tracked_source_hash"]
        assert frozen["code"]["dependency_hash"]

        for profile_id in CONTROLLED_FORECAST_PROFILES:
            snapshot = frozen["profiles"][profile_id]
            context = snapshot["execution_context"]
            assert snapshot["profile_id"] == profile_id
            assert snapshot["source_profile"]["version"] == EXPECTED_PROFILE_VERSIONS[profile_id]
            assert snapshot["effective_profile"]["id"] == profile_id
            assert snapshot["budget"] == frozen["common_budget"]
            assert context["profile_id"] == profile_id
            assert context["profile_version"] == EXPECTED_PROFILE_VERSIONS[profile_id]
            assert context["model_provider"] == frozen["provider"]["model_provider"]
            assert context["model_name"] == frozen["provider"]["model"]
            assert context["search_provider"] == frozen["provider"]["search_provider"]
            assert context["evidence_policy"] == frozen["provider"]["evidence_policy"]
            assert context["prompt_versions"] == frozen["prompts"]["versions"]
            assert context["prompt_hashes"] == frozen["prompts"]["hashes"]
            assert context["code_commit"] == frozen["code"]["git_commit"]

    from forecastlab_api.worker import drain_jobs

    assert drain_jobs(max_steps=10) == len(CONTROLLED_FORECAST_PROFILES)

    report_response = client.get(f"/api/forecast-experiments/{created['id']}/report")
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["status"] == "completed_with_failures"
    assert report["dataset"]["id"] == dataset["id"]
    assert report["dataset"]["version"] == dataset["version"]
    assert report["configuration_hash"] == created["configuration_hash"]
    assert report["configuration"]["profiles"] == list(CONTROLLED_FORECAST_PROFILES)
    assert len(report["rows"]) == len(CONTROLLED_FORECAST_PROFILES)
    assert {row["profile_id"] for row in report["rows"]} == set(
        CONTROLLED_FORECAST_PROFILES
    )
    assert {
        row["profile_id"]: row["completion_status"] for row in report["rows"]
    } == {
        "single_model_forecaster_v1": "completed",
        "three_track_forecaster": "completed",
        "graph_forecaster_v1": "failed",
    }
    assert all(row["forecast_run_id"] for row in report["rows"])
    assert all(
        (row["probability"] is None) == (row["profile_id"] == "graph_forecaster_v1")
        for row in report["rows"]
    )

    analysis_response = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    )
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    assert analysis["experiment_id"] == created["id"]
    assert analysis["configuration_hash"] == created["configuration_hash"]
    assert [item["profile_id"] for item in analysis["profiles"]] == list(
        CONTROLLED_FORECAST_PROFILES
    )
    assert [
        (item["profile_a"], item["profile_b"]) for item in analysis["comparisons"]
    ] == list(PAIRED_PROFILE_COMPARISONS)
    assert [item["question_count"] for item in analysis["comparisons"]] == [1, 0, 0]
    assert all(
        item["evidence_status"] == "insufficient evidence"
        for item in analysis["comparisons"]
    )
    assert len(analysis["rows"]) == len(CONTROLLED_FORECAST_PROFILES)

from __future__ import annotations

import math

import pytest
from sqlalchemy import select
from tests.test_forecast_experiments import (
    _create_experiment,
    _csv_bytes,
    _resolved_row,
)

from forecastlab.evaluation import brier_score, log_loss
from forecastlab.timeutil import utcnow
from forecastlab_api.forecast_analysis import (
    CALIBRATION_BUCKET_EDGES,
    PAIRED_PROFILE_COMPARISONS,
    STATISTICAL_BOOTSTRAP_SAMPLES,
    STATISTICAL_MIN_PAIRED_QUESTIONS,
    STATISTICAL_RANDOM_SEED,
    calibration_bucket_report,
    deterministic_bootstrap_confidence_interval,
)
from forecastlab_api.forecast_experiments import CONTROLLED_FORECAST_PROFILES
from forecastlab_api.models import (
    EvaluationQuestion,
    ForecastExperiment,
    ForecastExperimentResult,
    ForecastExperimentRun,
)

PROFILE_PROBABILITIES = {
    "single_model_forecaster_v1": (0.45, 0.55),
    "three_track_forecaster": (0.35, 0.65),
    "graph_forecaster_v1": (0.25, 0.75),
}
PROFILE_COSTS = {
    "single_model_forecaster_v1": 1.0,
    "three_track_forecaster": 2.0,
    "graph_forecaster_v1": 3.0,
}
PROFILE_LATENCIES = {
    "single_model_forecaster_v1": 100,
    "three_track_forecaster": 200,
    "graph_forecaster_v1": 300,
}


def _completed_statistical_experiment(client, *, question_count: int, name: str) -> dict:
    rows = [
        _resolved_row(
            question=(
                f"Did official fixture indicator {index} reach its threshold before "
                "31 December 2025?"
            ),
            yes_condition=(
                f"Official fixture indicator {index} reached its declared threshold by "
                "2025-12-31."
            ),
            no_condition=(
                f"Official fixture indicator {index} remained below its declared threshold "
                "through 2025-12-31."
            ),
            outcome=index % 2,
        )
        for index in range(question_count)
    ]
    imported = client.post(
        "/api/evaluation/datasets/import",
        data={
            "name": name,
            "version": "1.0.0",
            "description": "Resolved rows for deterministic statistical analysis tests.",
            "provenance": "ForecastLab statistical-method fixture.",
        },
        files={"file": ("statistical.csv", _csv_bytes(rows), "text/csv")},
    )
    assert imported.status_code == 201, imported.text
    dataset_id = imported.json()["id"]
    assert client.post(f"/api/evaluation/datasets/{dataset_id}/review").status_code == 200
    assert client.post(f"/api/evaluation/datasets/{dataset_id}/freeze").status_code == 200
    created = _create_experiment(client, dataset_id)

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        experiment = session.get(ForecastExperiment, created["id"])
        assert experiment is not None
        questions = {
            item.id: item
            for item in session.scalars(
                select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == dataset_id)
            ).all()
        }
        runs = session.scalars(
            select(ForecastExperimentRun).where(
                ForecastExperimentRun.experiment_id == experiment.id
            )
        ).all()
        for run in runs:
            question = questions[run.evaluation_question_id]
            probability = PROFILE_PROBABILITIES[run.profile_id][question.outcome]
            run.status = "completed"
            run.started_at = utcnow()
            run.completed_at = utcnow()
            session.add(
                ForecastExperimentResult(
                    id=f"result-{run.id}",
                    experiment_run_id=run.id,
                    probability=probability,
                    outcome=question.outcome,
                    brier_score=brier_score(probability, question.outcome),
                    log_loss=log_loss(probability, question.outcome),
                    cost_usd=PROFILE_COSTS[run.profile_id],
                    latency_ms=PROFILE_LATENCIES[run.profile_id],
                    evidence_coverage=0.5,
                    evidence_covered_units=1,
                    evidence_total_units=2,
                    completion_status="completed",
                )
            )
        experiment.status = "completed"
        experiment.completed_at = utcnow()
        session.commit()
    return created


def test_paired_comparison_and_confidence_intervals_use_identical_questions(client) -> None:
    created = _completed_statistical_experiment(
        client,
        question_count=25,
        name="Paired statistical comparison",
    )

    response = client.get(f"/api/forecast-experiments/{created['id']}/analysis")
    assert response.status_code == 200
    analysis = response.json()
    comparisons = analysis["comparisons"]

    assert [(item["profile_a"], item["profile_b"]) for item in comparisons] == list(
        PAIRED_PROFILE_COMPARISONS
    )
    assert all(item["experiment_id"] == created["id"] for item in comparisons)
    assert all(item["question_count"] == 25 for item in comparisons)
    assert all(
        item["bootstrap_samples"] == STATISTICAL_BOOTSTRAP_SAMPLES
        for item in comparisons
    )
    assert all(item["random_seed"] == STATISTICAL_RANDOM_SEED for item in comparisons)
    assert all(item["evidence_status"] == "interval estimate available" for item in comparisons)

    single_vs_three = comparisons[0]
    assert single_vs_three["mean_brier_difference"] == pytest.approx(0.08)
    assert single_vs_three["mean_log_loss_difference"] == pytest.approx(
        -math.log(0.55) + math.log(0.65)
    )
    assert single_vs_three["brier_confidence_interval"] == pytest.approx([0.08, 0.08])
    assert single_vs_three["log_loss_confidence_interval"][0] == pytest.approx(
        single_vs_three["mean_log_loss_difference"]
    )
    assert single_vs_three["log_loss_confidence_interval"][1] == pytest.approx(
        single_vs_three["mean_log_loss_difference"]
    )
    assert single_vs_three["mean_cost_difference"] == -1.0
    assert single_vs_three["mean_latency_difference"] == -100.0
    assert single_vs_three["brier_interval_excludes_zero"] is True
    assert single_vs_three["log_loss_interval_excludes_zero"] is True
    assert analysis["methodology"] == {
        "version": "paired_bootstrap_v1",
        "paired_population": "completed_questions_available_for_both_profiles",
        "difference_direction": "profile_a_minus_profile_b",
        "confidence_level": 0.95,
        "bootstrap_method": "paired_percentile_bootstrap",
        "bootstrap_samples": 2000,
        "random_seed": 20260823,
        "minimum_paired_questions": 20,
        "calibration_bucket_edges": list(CALIBRATION_BUCKET_EDGES),
        "calibration_adjustment": "none",
        "multiple_comparisons": (
            "Three profile pairs and two loss metrics are reported without multiplicity "
            "adjustment; intervals are unadjusted and must be interpreted together."
        ),
    }


def test_bootstrap_is_reproducible_and_generates_ordered_interval() -> None:
    differences = [(-0.12 + index * 0.013) for index in range(25)]

    first = deterministic_bootstrap_confidence_interval(differences)
    second = deterministic_bootstrap_confidence_interval(differences)

    assert first == second
    assert first is not None
    assert first[0] < first[1]
    assert first == pytest.approx([-0.0004, 0.07188], abs=1e-12)


def test_calibration_uses_five_fixed_probability_buckets() -> None:
    report = calibration_bucket_report(
        [
            (0.0, 0),
            (0.199, 1),
            (0.2, 0),
            (0.399, 1),
            (0.4, 1),
            (0.6, 0),
            (0.8, 1),
            (1.0, 1),
        ]
    )

    assert [item["label"] for item in report["buckets"]] == [
        "0-20%",
        "20-40%",
        "40-60%",
        "60-80%",
        "80-100%",
    ]
    assert [item["forecast_count"] for item in report["buckets"]] == [2, 2, 1, 1, 2]
    assert report["buckets"][0]["average_predicted_probability"] == pytest.approx(0.0995)
    assert report["buckets"][0]["actual_outcome_frequency"] == 0.5
    assert report["buckets"][-1]["actual_outcome_frequency"] == 1.0
    assert report["available"] is False
    assert report["message"] == "insufficient evidence"


def test_cost_efficiency_reports_recorded_cost_loss_and_latency(client) -> None:
    created = _completed_statistical_experiment(
        client,
        question_count=25,
        name="Cost efficiency comparison",
    )

    analysis = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    ).json()
    efficiency = {item["profile_id"]: item for item in analysis["cost_efficiency"]}

    single = efficiency["single_model_forecaster_v1"]
    assert single["question_count"] == 25
    assert single["scored_question_count"] == 25
    assert single["total_cost"] == 25.0
    assert single["cost_per_question"] == 1.0
    assert single["brier_per_dollar"] == pytest.approx(0.2025)
    assert single["log_loss_per_dollar"] == pytest.approx(-math.log(0.55))
    assert single["latency_per_question"] == 100.0
    assert single["evidence_status"] == "ratio available"
    assert efficiency["three_track_forecaster"]["cost_per_question"] == 2.0
    assert efficiency["graph_forecaster_v1"]["cost_per_question"] == 3.0


def test_small_paired_sample_is_labeled_insufficient_evidence(client) -> None:
    created = _completed_statistical_experiment(
        client,
        question_count=STATISTICAL_MIN_PAIRED_QUESTIONS - 1,
        name="Insufficient paired sample",
    )

    analysis = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    ).json()

    for comparison in analysis["comparisons"]:
        assert comparison["question_count"] == 19
        assert comparison["mean_brier_difference"] is not None
        assert comparison["brier_confidence_interval"] is None
        assert comparison["log_loss_confidence_interval"] is None
        assert comparison["brier_interval_excludes_zero"] is None
        assert comparison["log_loss_interval_excludes_zero"] is None
        assert comparison["evidence_status"] == "insufficient evidence"
        assert comparison["message"] == "insufficient evidence"
    for profile in analysis["profiles"]:
        calibration = profile["performance"]["calibration_buckets"]
        assert calibration["sample_count"] == 19
        assert calibration["available"] is False
        assert calibration["message"] == "insufficient evidence"


def test_paired_comparison_excludes_missing_profile_question(client) -> None:
    created = _completed_statistical_experiment(
        client,
        question_count=25,
        name="Missing paired question",
    )
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        missing = session.scalar(
            select(ForecastExperimentRun).where(
                ForecastExperimentRun.experiment_id == created["id"],
                ForecastExperimentRun.profile_id == "three_track_forecaster",
            )
        )
        assert missing is not None
        missing.status = "failed"
        result = missing.result
        assert result is not None
        result.completion_status = "failed"
        result.probability = None
        result.brier_score = None
        result.log_loss = None
        session.commit()

    analysis = client.get(
        f"/api/forecast-experiments/{created['id']}/analysis"
    ).json()
    comparisons = {
        (item["profile_a"], item["profile_b"]): item
        for item in analysis["comparisons"]
    }
    assert comparisons[("single_model_forecaster_v1", "three_track_forecaster")][
        "question_count"
    ] == 24
    assert comparisons[("three_track_forecaster", "graph_forecaster_v1")][
        "question_count"
    ] == 24
    assert comparisons[("single_model_forecaster_v1", "graph_forecaster_v1")][
        "question_count"
    ] == 25
    assert all(
        comparison["question_count"] <= 25 for comparison in comparisons.values()
    )
    assert set(PROFILE_PROBABILITIES) == set(CONTROLLED_FORECAST_PROFILES)

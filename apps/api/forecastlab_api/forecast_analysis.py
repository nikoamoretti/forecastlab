from __future__ import annotations

import math
import random
import uuid
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evaluation import mean, median
from forecastlab.timeutil import utcnow
from forecastlab_api.forecast_experiments import (
    CONTROLLED_FORECAST_PROFILES,
    forecast_experiment_report,
)
from forecastlab_api.models import (
    EvaluationQuestion,
    EvidenceClaimRow,
    EvidenceItem,
    ForecastExperiment,
    ForecastExperimentResult,
    ForecastExperimentRun,
    ForecastFailure,
)

FAILURE_CATEGORIES: dict[str, str] = {
    "bad_contract": "Bad contract",
    "bad_evidence": "Bad evidence",
    "bad_decomposition": "Bad decomposition",
    "bad_node_forecast": "Bad node forecast",
    "bad_aggregation": "Bad aggregation",
    "operational_failure": "Operational failure",
}
PAIRED_PROFILE_COMPARISONS = (
    ("single_model_forecaster_v1", "three_track_forecaster"),
    ("three_track_forecaster", "graph_forecaster_v1"),
    ("single_model_forecaster_v1", "graph_forecaster_v1"),
)
STATISTICAL_BOOTSTRAP_SAMPLES = 2000
STATISTICAL_RANDOM_SEED = 20260823
STATISTICAL_CONFIDENCE_LEVEL = 0.95
STATISTICAL_MIN_PAIRED_QUESTIONS = 20
CALIBRATION_MIN_QUESTIONS = 20
CALIBRATION_BUCKET_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
ANALYSIS_NOTICE = (
    "Internal research measurements only. Reported intervals are paired observed differences, "
    "not a system ranking, and this analysis does not modify any forecast, profile, prompt, "
    "or execution result."
)


class ForecastComparisonResult(BaseModel):
    experiment_id: str
    profile_a: str
    profile_b: str
    question_count: int
    mean_brier_difference: float | None
    mean_log_loss_difference: float | None
    brier_confidence_interval: list[float] | None
    log_loss_confidence_interval: list[float] | None
    bootstrap_samples: int
    random_seed: int
    confidence_level: float = STATISTICAL_CONFIDENCE_LEVEL
    method: str = "paired_percentile_bootstrap"
    paired_population: str = "completed_questions_available_for_both_profiles"
    mean_cost_difference: float | None = None
    mean_latency_difference: float | None = None
    brier_interval_excludes_zero: bool | None = None
    log_loss_interval_excludes_zero: bool | None = None
    evidence_status: str
    message: str


class CostEfficiencyReport(BaseModel):
    profile_id: str
    question_count: int
    scored_question_count: int
    total_cost: float
    cost_per_question: float | None
    brier_per_dollar: float | None
    log_loss_per_dollar: float | None
    latency_per_question: float | None
    evidence_status: str


class ForecastResearchAnalysis(BaseModel):
    experiment_id: str
    dataset: dict[str, Any]
    status: str
    configuration_hash: str
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    comparisons: list[ForecastComparisonResult] = Field(default_factory=list)
    cost_efficiency: list[CostEfficiencyReport] = Field(default_factory=list)
    methodology: dict[str, Any]
    failure_taxonomy: list[dict[str, str]] = Field(default_factory=list)
    failure_summary: dict[str, Any]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    notice: str = ANALYSIS_NOTICE


def serialize_forecast_failure(row: ForecastFailure) -> dict[str, Any]:
    return {
        "id": row.id,
        "experiment_id": row.experiment_id,
        "forecast_experiment_run_id": row.forecast_experiment_run_id,
        "category": row.category,
        "label": FAILURE_CATEGORIES[row.category],
        "annotation": row.annotation,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _normalize_category(category: str) -> str:
    return category.strip().casefold().replace("-", "_").replace(" ", "_")


def classify_forecast_failure(
    session: Session,
    *,
    forecast_experiment_run_id: str,
    category: str,
    annotation: str,
    created_by: str = "internal_reviewer",
) -> ForecastFailure:
    normalized_category = _normalize_category(category)
    if normalized_category not in FAILURE_CATEGORIES:
        raise ValueError("invalid_forecast_failure_category")
    normalized_annotation = " ".join(annotation.split()).strip()
    if not normalized_annotation:
        raise ValueError("forecast_failure_annotation_required")
    normalized_reviewer = " ".join(created_by.split()).strip() or "internal_reviewer"
    experiment_run = session.get(ForecastExperimentRun, forecast_experiment_run_id)
    if experiment_run is None:
        raise ValueError("forecast_experiment_run_not_found")
    if experiment_run.status not in {"completed", "partial", "failed"}:
        raise ValueError("forecast_experiment_run_not_terminal")
    existing = session.scalar(
        select(ForecastFailure).where(
            ForecastFailure.forecast_experiment_run_id == experiment_run.id,
            ForecastFailure.category == normalized_category,
        )
    )
    if existing is not None:
        existing.annotation = normalized_annotation
        existing.updated_at = utcnow()
        return existing
    row = ForecastFailure(
        id=str(uuid.uuid4()),
        experiment_id=experiment_run.experiment_id,
        forecast_experiment_run_id=experiment_run.id,
        category=normalized_category,
        annotation=normalized_annotation,
        created_by=normalized_reviewer,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def update_forecast_failure_annotation(
    session: Session,
    *,
    failure_id: str,
    annotation: str,
) -> ForecastFailure:
    row = session.get(ForecastFailure, failure_id)
    if row is None:
        raise ValueError("forecast_failure_not_found")
    normalized_annotation = " ".join(annotation.split()).strip()
    if not normalized_annotation:
        raise ValueError("forecast_failure_annotation_required")
    row.annotation = normalized_annotation
    row.updated_at = utcnow()
    session.flush()
    return row


def failures_for_forecast_experiment_run(
    session: Session,
    forecast_experiment_run_id: str,
) -> list[ForecastFailure]:
    return list(
        session.scalars(
            select(ForecastFailure)
            .where(
                ForecastFailure.forecast_experiment_run_id
                == forecast_experiment_run_id
            )
            .order_by(ForecastFailure.category, ForecastFailure.created_at)
        ).all()
    )


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("bootstrap_sample_empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    upper_weight = position - lower_index
    return (
        ordered[lower_index] * (1.0 - upper_weight)
        + ordered[upper_index] * upper_weight
    )


def deterministic_bootstrap_confidence_interval(
    paired_differences: list[float],
    *,
    samples: int = STATISTICAL_BOOTSTRAP_SAMPLES,
    random_seed: int = STATISTICAL_RANDOM_SEED,
    minimum_questions: int = STATISTICAL_MIN_PAIRED_QUESTIONS,
) -> list[float] | None:
    """Return a deterministic 95% paired percentile-bootstrap interval.

    Resampling operates on question-level paired differences. The interval is
    withheld below the predeclared evidence threshold even though a numerical
    bootstrap could be computed from fewer rows.
    """

    if len(paired_differences) < minimum_questions:
        return None
    if samples <= 0:
        raise ValueError("bootstrap_samples_must_be_positive")
    rng = random.Random(random_seed)
    row_count = len(paired_differences)
    sampled_means: list[float] = []
    for _ in range(samples):
        sampled_means.append(
            sum(paired_differences[rng.randrange(row_count)] for _ in range(row_count))
            / row_count
        )
    alpha = (1.0 - STATISTICAL_CONFIDENCE_LEVEL) / 2.0
    return [
        _percentile(sampled_means, alpha),
        _percentile(sampled_means, 1.0 - alpha),
    ]


def calibration_bucket_report(
    probability_outcomes: list[tuple[float, int]],
) -> dict[str, Any]:
    buckets: list[dict[str, Any]] = []
    for index, lower in enumerate(CALIBRATION_BUCKET_EDGES[:-1]):
        upper = CALIBRATION_BUCKET_EDGES[index + 1]
        members = [
            (probability, outcome)
            for probability, outcome in probability_outcomes
            if (lower <= probability < upper)
            or (index == len(CALIBRATION_BUCKET_EDGES) - 2 and probability == 1.0)
        ]
        probabilities = [probability for probability, _ in members]
        outcomes = [float(outcome) for _, outcome in members]
        buckets.append(
            {
                "label": f"{round(lower * 100)}-{round(upper * 100)}%",
                "lower_bound": lower,
                "upper_bound": upper,
                "forecast_count": len(members),
                "average_predicted_probability": mean(probabilities),
                "actual_outcome_frequency": mean(outcomes),
            }
        )
    sample_count = len(probability_outcomes)
    sufficient = sample_count >= CALIBRATION_MIN_QUESTIONS
    return {
        "available": sufficient,
        "sample_count": sample_count,
        "minimum_required": CALIBRATION_MIN_QUESTIONS,
        "evidence_status": (
            "interval estimate available" if sufficient else "insufficient evidence"
        ),
        "message": (
            "Five fixed probability buckets; no calibration adjustment is performed."
            if sufficient
            else "insufficient evidence"
        ),
        "buckets": buckets,
    }


def _completed_results_by_question(
    *,
    profile_id: str,
    runs: list[ForecastExperimentRun],
    results: dict[str, ForecastExperimentResult],
) -> dict[str, ForecastExperimentResult]:
    paired: dict[str, ForecastExperimentResult] = {}
    for run in runs:
        if run.profile_id != profile_id or run.status != "completed":
            continue
        result = results.get(run.id)
        if (
            result is None
            or result.completion_status != "completed"
            or result.probability is None
            or result.brier_score is None
            or result.log_loss is None
        ):
            continue
        paired[run.evaluation_question_id] = result
    return paired


def _interval_excludes_zero(interval: list[float] | None) -> bool | None:
    if interval is None:
        return None
    return interval[1] < 0.0 or interval[0] > 0.0


def build_forecast_comparison_result(
    *,
    experiment_id: str,
    profile_a: str,
    profile_b: str,
    runs: list[ForecastExperimentRun],
    results: dict[str, ForecastExperimentResult],
    bootstrap_samples: int = STATISTICAL_BOOTSTRAP_SAMPLES,
    random_seed: int = STATISTICAL_RANDOM_SEED,
) -> ForecastComparisonResult:
    profile_a_results = _completed_results_by_question(
        profile_id=profile_a,
        runs=runs,
        results=results,
    )
    profile_b_results = _completed_results_by_question(
        profile_id=profile_b,
        runs=runs,
        results=results,
    )
    paired_question_ids = sorted(set(profile_a_results) & set(profile_b_results))
    brier_differences = [
        float(profile_a_results[item].brier_score)
        - float(profile_b_results[item].brier_score)
        for item in paired_question_ids
    ]
    log_loss_differences = [
        float(profile_a_results[item].log_loss)
        - float(profile_b_results[item].log_loss)
        for item in paired_question_ids
    ]
    cost_differences = [
        profile_a_results[item].cost_usd - profile_b_results[item].cost_usd
        for item in paired_question_ids
    ]
    latency_differences = [
        float(profile_a_results[item].latency_ms)
        - float(profile_b_results[item].latency_ms)
        for item in paired_question_ids
    ]
    brier_interval = deterministic_bootstrap_confidence_interval(
        brier_differences,
        samples=bootstrap_samples,
        random_seed=random_seed,
    )
    log_loss_interval = deterministic_bootstrap_confidence_interval(
        log_loss_differences,
        samples=bootstrap_samples,
        random_seed=random_seed,
    )
    sufficient = len(paired_question_ids) >= STATISTICAL_MIN_PAIRED_QUESTIONS
    return ForecastComparisonResult(
        experiment_id=experiment_id,
        profile_a=profile_a,
        profile_b=profile_b,
        question_count=len(paired_question_ids),
        mean_brier_difference=mean(brier_differences),
        mean_log_loss_difference=mean(log_loss_differences),
        brier_confidence_interval=brier_interval,
        log_loss_confidence_interval=log_loss_interval,
        bootstrap_samples=bootstrap_samples,
        random_seed=random_seed,
        mean_cost_difference=mean(cost_differences),
        mean_latency_difference=mean(latency_differences),
        brier_interval_excludes_zero=_interval_excludes_zero(brier_interval),
        log_loss_interval_excludes_zero=_interval_excludes_zero(log_loss_interval),
        evidence_status=(
            "interval estimate available" if sufficient else "insufficient evidence"
        ),
        message=(
            "Observed differences are profile A minus profile B. The unadjusted intervals "
            "use paired question-level resampling."
            if sufficient
            else "insufficient evidence"
        ),
    )


def build_cost_efficiency_report(
    *,
    profile_id: str,
    runs: list[ForecastExperimentRun],
    results: dict[str, ForecastExperimentResult],
) -> CostEfficiencyReport:
    profile_runs = [run for run in runs if run.profile_id == profile_id]
    profile_results = [results[run.id] for run in profile_runs if run.id in results]
    scored_results = [
        result
        for run in profile_runs
        if run.status == "completed"
        and (result := results.get(run.id)) is not None
        and result.completion_status == "completed"
        and result.brier_score is not None
        and result.log_loss is not None
    ]
    total_cost = sum(result.cost_usd for result in profile_results)
    total_latency = sum(result.latency_ms for result in profile_results)
    question_count = len(profile_runs)
    denominator_available = total_cost > 0.0 and bool(scored_results)
    return CostEfficiencyReport(
        profile_id=profile_id,
        question_count=question_count,
        scored_question_count=len(scored_results),
        total_cost=total_cost,
        cost_per_question=(total_cost / question_count if question_count else None),
        brier_per_dollar=(
            sum(float(result.brier_score) for result in scored_results) / total_cost
            if denominator_available
            else None
        ),
        log_loss_per_dollar=(
            sum(float(result.log_loss) for result in scored_results) / total_cost
            if denominator_available
            else None
        ),
        latency_per_question=(
            float(total_latency) / question_count if question_count else None
        ),
        evidence_status=(
            "ratio available"
            if denominator_available
            else "unavailable: total cost is zero or no completed scores exist"
        ),
    )


def _research_metrics(session: Session, forecast_run_ids: list[str]) -> dict[str, Any]:
    if not forecast_run_ids:
        return {
            "eligible_evidence_items": 0,
            "distinct_sources": 0,
            "claim_count": 0,
            "source_quality": {
                "source_class_counts": {},
                "claim_quality_assessed_count": 0,
                "mean_claim_source_quality": None,
                "primary_claim_rate": None,
            },
        }
    evidence = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.run_id.in_(forecast_run_ids),
                EvidenceItem.rejected.is_(False),
                EvidenceItem.as_of_eligible.is_(True),
            )
        ).all()
    )
    claims = list(
        session.scalars(
            select(EvidenceClaimRow)
            .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
            .where(
                EvidenceItem.run_id.in_(forecast_run_ids),
                EvidenceItem.rejected.is_(False),
                EvidenceItem.as_of_eligible.is_(True),
                EvidenceClaimRow.as_of_eligible.is_(True),
                EvidenceClaimRow.cutoff_verified.is_(True),
            )
        ).all()
    )
    source_classes = Counter(item.source_class or "unclassified" for item in evidence)
    claim_qualities = [float(item.source_quality) for item in claims]
    primary_claims = sum(bool(item.primary_source) for item in claims)
    return {
        "eligible_evidence_items": len(evidence),
        "distinct_sources": len({item.url for item in evidence}),
        "claim_count": len(claims),
        "source_quality": {
            "source_class_counts": dict(sorted(source_classes.items())),
            "claim_quality_assessed_count": len(claims),
            "mean_claim_source_quality": mean(claim_qualities),
            "primary_claim_rate": primary_claims / len(claims) if claims else None,
        },
    }


def _failure_summary(failures: list[ForecastFailure]) -> dict[str, Any]:
    counts = {category: 0 for category in FAILURE_CATEGORIES}
    for failure in failures:
        counts[failure.category] = counts.get(failure.category, 0) + 1
    return {
        "total_classifications": len(failures),
        "classified_runs": len(
            {failure.forecast_experiment_run_id for failure in failures}
        ),
        "by_category": counts,
    }


def _profile_analysis(
    session: Session,
    *,
    profile_id: str,
    runs: list[ForecastExperimentRun],
    results: dict[str, ForecastExperimentResult],
    comparison_profile: dict[str, Any],
) -> dict[str, Any]:
    profile_runs = [run for run in runs if run.profile_id == profile_id]
    profile_results = [results[run.id] for run in profile_runs if run.id in results]
    valid = [
        result
        for run in profile_runs
        if run.status == "completed"
        and (result := results.get(run.id)) is not None
        and result.completion_status == "completed"
        and result.probability is not None
        and result.brier_score is not None
        and result.log_loss is not None
    ]
    pairs = [
        (float(result.probability), result.outcome)
        for result in valid
        if result.probability is not None
    ]
    assigned = len(profile_runs)
    completed = sum(run.status == "completed" for run in profile_runs)
    partial = sum(run.status == "partial" for run in profile_runs)
    failed = sum(run.status == "failed" for run in profile_runs)
    costs = [result.cost_usd for result in profile_results]
    latencies = [float(result.latency_ms) for result in profile_results]
    errors = Counter(
        (run.error_category or "unclassified")
        for run in profile_runs
        if run.status == "failed"
    )
    research = _research_metrics(
        session,
        [run.forecast_run_id for run in profile_runs if run.forecast_run_id],
    )
    research["evidence_coverage"] = comparison_profile.get("evidence_coverage")
    return {
        "profile_id": profile_id,
        "performance": {
            "brier_score": mean(
                [float(result.brier_score) for result in valid]
            ),
            "log_loss": mean([float(result.log_loss) for result in valid]),
            "calibration_buckets": calibration_bucket_report(pairs),
            "scored_questions": len(valid),
            "analysis_population": "completed_only",
        },
        "operations": {
            "assigned_questions": assigned,
            "completed": completed,
            "partial": partial,
            "failed": failed,
            "completion_rate": completed / assigned if assigned else None,
            "partial_rate": partial / assigned if assigned else None,
            "failure_rate": failed / assigned if assigned else None,
            "total_cost_usd": sum(costs),
            "cost_per_question_usd": sum(costs) / assigned if assigned else None,
            "mean_latency_ms": mean(latencies),
            "median_latency_ms": median(latencies),
            "failure_categories": dict(sorted(errors.items())),
        },
        "research": research,
    }


def build_forecast_research_analysis(
    session: Session,
    experiment: ForecastExperiment,
) -> ForecastResearchAnalysis:
    comparison = forecast_experiment_report(session, experiment).model_dump(mode="json")
    runs = list(
        session.scalars(
            select(ForecastExperimentRun)
            .where(ForecastExperimentRun.experiment_id == experiment.id)
            .order_by(
                ForecastExperimentRun.evaluation_question_id,
                ForecastExperimentRun.profile_id,
            )
        ).all()
    )
    results = {
        item.experiment_run_id: item
        for item in session.scalars(
            select(ForecastExperimentResult).where(
                ForecastExperimentResult.experiment_run_id.in_(
                    [run.id for run in runs] or [""]
                )
            )
        ).all()
    }
    questions = {
        item.id: item
        for item in session.scalars(
            select(EvaluationQuestion).where(
                EvaluationQuestion.dataset_id == experiment.dataset_id
            )
        ).all()
    }
    failures = list(
        session.scalars(
            select(ForecastFailure)
            .where(ForecastFailure.experiment_id == experiment.id)
            .order_by(ForecastFailure.created_at, ForecastFailure.id)
        ).all()
    )
    failures_by_run: dict[str, list[ForecastFailure]] = {}
    for failure in failures:
        failures_by_run.setdefault(
            failure.forecast_experiment_run_id,
            [],
        ).append(failure)
    comparison_profiles = {
        item["profile_id"]: item for item in comparison.get("profiles") or []
    }
    profile_ids = comparison.get("configuration", {}).get("profiles") or list(
        CONTROLLED_FORECAST_PROFILES
    )
    profiles = [
        _profile_analysis(
            session,
            profile_id=profile_id,
            runs=runs,
            results=results,
            comparison_profile=comparison_profiles.get(profile_id) or {},
        )
        for profile_id in profile_ids
    ]
    comparisons = [
        build_forecast_comparison_result(
            experiment_id=experiment.id,
            profile_a=profile_a,
            profile_b=profile_b,
            runs=runs,
            results=results,
        )
        for profile_a, profile_b in PAIRED_PROFILE_COMPARISONS
    ]
    cost_efficiency = [
        build_cost_efficiency_report(
            profile_id=profile_id,
            runs=runs,
            results=results,
        )
        for profile_id in profile_ids
    ]
    rows: list[dict[str, Any]] = []
    for run in runs:
        result = results.get(run.id)
        question = questions.get(run.evaluation_question_id)
        rows.append(
            {
                "forecast_experiment_run_id": run.id,
                "forecast_run_id": run.forecast_run_id,
                "evaluation_question_id": run.evaluation_question_id,
                "question": question.question if question else None,
                "profile_id": run.profile_id,
                "status": run.status,
                "error": run.error,
                "error_category": run.error_category,
                "probability": result.probability if result else None,
                "outcome": result.outcome if result else (question.outcome if question else None),
                "brier_score": result.brier_score if result else None,
                "log_loss": result.log_loss if result else None,
                "cost_usd": result.cost_usd if result else 0.0,
                "latency_ms": result.latency_ms if result else 0,
                "evidence_coverage": result.evidence_coverage if result else None,
                "failure_classifications": [
                    serialize_forecast_failure(item)
                    for item in failures_by_run.get(run.id, [])
                ],
            }
        )
    return ForecastResearchAnalysis(
        experiment_id=experiment.id,
        dataset=comparison["dataset"],
        status=experiment.status,
        configuration_hash=experiment.configuration_hash,
        profiles=profiles,
        comparisons=comparisons,
        cost_efficiency=cost_efficiency,
        methodology={
            "version": "paired_bootstrap_v1",
            "paired_population": "completed_questions_available_for_both_profiles",
            "difference_direction": "profile_a_minus_profile_b",
            "confidence_level": STATISTICAL_CONFIDENCE_LEVEL,
            "bootstrap_method": "paired_percentile_bootstrap",
            "bootstrap_samples": STATISTICAL_BOOTSTRAP_SAMPLES,
            "random_seed": STATISTICAL_RANDOM_SEED,
            "minimum_paired_questions": STATISTICAL_MIN_PAIRED_QUESTIONS,
            "calibration_bucket_edges": list(CALIBRATION_BUCKET_EDGES),
            "calibration_adjustment": "none",
            "multiple_comparisons": (
                "Three profile pairs and two loss metrics are reported without multiplicity "
                "adjustment; intervals are unadjusted and must be interpreted together."
            ),
        },
        failure_taxonomy=[
            {"category": category, "label": label}
            for category, label in FAILURE_CATEGORIES.items()
        ],
        failure_summary=_failure_summary(failures),
        rows=rows,
    )

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evaluation import mean, median, reliability_bins
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
ANALYSIS_NOTICE = (
    "Descriptive internal research measurements only. This analysis does not establish "
    "profile superiority and does not modify any forecast, profile, prompt, or execution result."
)


class ForecastResearchAnalysis(BaseModel):
    experiment_id: str
    dataset: dict[str, Any]
    status: str
    configuration_hash: str
    profiles: list[dict[str, Any]] = Field(default_factory=list)
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
        for result in profile_results
        if result.probability is not None and result.completion_status != "failed"
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
            "brier_score": comparison_profile.get("brier_score"),
            "log_loss": comparison_profile.get("log_loss"),
            "calibration_buckets": reliability_bins(pairs),
            "scored_questions": len(valid),
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
        failure_taxonomy=[
            {"category": category, "label": label}
            for category, label in FAILURE_CATEGORIES.items()
        ],
        failure_summary=_failure_summary(failures),
        rows=rows,
    )

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evaluation import reliability_bins
from forecastlab.timeutil import utcnow
from forecastlab_api.evaluation_experiments import evaluation_comparison_report
from forecastlab_api.models import (
    EvaluationExperiment,
    EvaluationQuestion,
    EvaluationResult,
    EvaluationRun,
    EvidenceClaimRow,
    EvidenceItem,
    ForecastFailure,
)

FAILURE_TAXONOMY: dict[str, tuple[str, ...]] = {
    "question": (
        "ambiguous_resolution",
        "incorrect_contract",
        "wrong_resolver",
    ),
    "research": (
        "missing_evidence",
        "poor_source_quality",
        "cutoff_failure",
    ),
    "reasoning": (
        "bad_prior",
        "overconfidence",
        "ignored_counterargument",
        "narrative_bias",
    ),
    "aggregation": (
        "incorrect_weighting",
        "dependency_failure",
    ),
    "operational": (
        "provider_failure",
        "timeout",
        "budget_failure",
    ),
}
FAILURE_GROUP_BY_CATEGORY = {
    category: group
    for group, categories in FAILURE_TAXONOMY.items()
    for category in categories
}
ANALYSIS_NOTICE = (
    "Internal research diagnostics only. Failure labels and descriptive metrics do not establish profile superiority "
    "and do not change any stored forecast."
)


class ForecastResearchAnalysis(BaseModel):
    experiment_id: str
    dataset: dict[str, Any]
    status: str
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    failure_taxonomy: dict[str, list[str]]
    failure_summary: dict[str, Any]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    notice: str = ANALYSIS_NOTICE


def serialize_forecast_failure(row: ForecastFailure) -> dict[str, Any]:
    return {
        "id": row.id,
        "experiment_id": row.experiment_id,
        "evaluation_run_id": row.evaluation_run_id,
        "failure_group": row.failure_group,
        "category": row.category,
        "annotation": row.annotation,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def classify_forecast_failure(
    session: Session,
    *,
    evaluation_run_id: str,
    category: str,
    annotation: str,
    created_by: str = "internal_reviewer",
) -> ForecastFailure:
    normalized_category = category.strip().casefold()
    group = FAILURE_GROUP_BY_CATEGORY.get(normalized_category)
    if group is None:
        raise ValueError("invalid_forecast_failure_category")
    normalized_annotation = " ".join(annotation.split()).strip()
    if not normalized_annotation:
        raise ValueError("forecast_failure_annotation_required")
    normalized_reviewer = " ".join(created_by.split()).strip() or "internal_reviewer"
    evaluation_run = session.get(EvaluationRun, evaluation_run_id)
    if evaluation_run is None:
        raise ValueError("evaluation_run_not_found")
    existing = session.scalar(
        select(ForecastFailure).where(
            ForecastFailure.evaluation_run_id == evaluation_run.id,
            ForecastFailure.category == normalized_category,
        )
    )
    if existing is not None:
        existing.annotation = normalized_annotation
        existing.created_by = normalized_reviewer
        existing.updated_at = utcnow()
        return existing
    row = ForecastFailure(
        id=str(uuid.uuid4()),
        experiment_id=evaluation_run.experiment_id,
        evaluation_run_id=evaluation_run.id,
        failure_group=group,
        category=normalized_category,
        annotation=normalized_annotation,
        created_by=normalized_reviewer,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def update_failure_annotation(
    session: Session,
    *,
    failure_id: str,
    annotation: str,
) -> ForecastFailure:
    row = session.get(ForecastFailure, failure_id)
    if row is None:
        raise ValueError("forecast_failure_not_found")
    normalized = " ".join(annotation.split()).strip()
    if not normalized:
        raise ValueError("forecast_failure_annotation_required")
    row.annotation = normalized
    row.updated_at = utcnow()
    session.flush()
    return row


def failures_for_evaluation_run(session: Session, evaluation_run_id: str) -> list[ForecastFailure]:
    return list(
        session.scalars(
            select(ForecastFailure)
            .where(ForecastFailure.evaluation_run_id == evaluation_run_id)
            .order_by(ForecastFailure.failure_group, ForecastFailure.category, ForecastFailure.created_at)
        ).all()
    )


def _probability_distribution(probabilities: list[float], *, bin_count: int = 10) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for index in range(bin_count):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        count = sum(
            1
            for probability in probabilities
            if (lower <= probability < upper) or (index == bin_count - 1 and probability == 1.0)
        )
        bins.append(
            {
                "bin_start": lower,
                "bin_end": upper,
                "count": count,
            }
        )
    return bins


def _research_counts(session: Session, forecast_run_ids: list[str]) -> tuple[int, int]:
    if not forecast_run_ids:
        return 0, 0
    evidence = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.run_id.in_(forecast_run_ids),
            EvidenceItem.rejected.is_(False),
            EvidenceItem.as_of_eligible.is_(True),
        )
    ).all()
    source_count = len({item.url for item in evidence})
    claims = session.scalars(
        select(EvidenceClaimRow)
        .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.run_id.in_(forecast_run_ids))
    ).all()
    return source_count, len(claims)


def _failure_summary(failures: list[ForecastFailure]) -> dict[str, Any]:
    by_group = {group: 0 for group in FAILURE_TAXONOMY}
    by_category = {category: 0 for category in FAILURE_GROUP_BY_CATEGORY}
    for failure in failures:
        by_group[failure.failure_group] = by_group.get(failure.failure_group, 0) + 1
        by_category[failure.category] = by_category.get(failure.category, 0) + 1
    return {
        "total_classifications": len(failures),
        "classified_runs": len({failure.evaluation_run_id for failure in failures}),
        "by_group": by_group,
        "by_category": by_category,
    }


def build_forecast_research_analysis(
    session: Session,
    experiment: EvaluationExperiment,
) -> ForecastResearchAnalysis:
    comparison = evaluation_comparison_report(session, experiment).model_dump(mode="json")
    runs = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.experiment_id == experiment.id)
        .order_by(EvaluationRun.question_id, EvaluationRun.profile_id)
    ).all()
    results = {
        item.run_id: item
        for item in session.scalars(
            select(EvaluationResult).where(EvaluationResult.run_id.in_([run.id for run in runs] or [""]))
        ).all()
    }
    questions = {
        item.id: item
        for item in session.scalars(
            select(EvaluationQuestion).where(EvaluationQuestion.dataset_id == experiment.dataset_id)
        ).all()
    }
    failures = session.scalars(
        select(ForecastFailure)
        .where(ForecastFailure.experiment_id == experiment.id)
        .order_by(ForecastFailure.created_at, ForecastFailure.id)
    ).all()
    failures_by_run: dict[str, list[ForecastFailure]] = {}
    for failure in failures:
        failures_by_run.setdefault(failure.evaluation_run_id, []).append(failure)
    comparison_profiles = {
        item["profile_id"]: item for item in comparison.get("profiles") or []
    }
    profile_analyses: list[dict[str, Any]] = []
    for profile_id in comparison.get("configuration", {}).get("profiles") or []:
        profile_runs = [run for run in runs if run.profile_id == profile_id]
        profile_results = [results[run.id] for run in profile_runs if run.id in results]
        valid = [
            result
            for result in profile_results
            if result.probability is not None and result.completion_status != "failed"
        ]
        pairs = [(float(result.probability), result.outcome) for result in valid if result.probability is not None]
        probabilities = [probability for probability, _ in pairs]
        forecast_run_ids = [run.forecast_run_id for run in profile_runs if run.forecast_run_id]
        source_count, claim_count = _research_counts(session, forecast_run_ids)
        comparison_profile = comparison_profiles.get(profile_id) or {}
        assigned = len(profile_runs)
        total_cost = sum(result.cost for result in profile_results)
        completed = sum(run.status == "completed" for run in profile_runs)
        partial = sum(run.status == "partial" for run in profile_runs)
        failed = sum(run.status == "failed" for run in profile_runs)
        profile_analyses.append(
            {
                "profile_id": profile_id,
                "performance": {
                    "brier_score": comparison_profile.get("brier_score"),
                    "log_loss": comparison_profile.get("log_loss"),
                    "calibration_buckets": reliability_bins(pairs),
                    "probability_distribution": _probability_distribution(probabilities),
                    "scored_questions": len(valid),
                },
                "reliability": {
                    "assigned_questions": assigned,
                    "completed": completed,
                    "partial": partial,
                    "failures": failed,
                    "completion_rate": completed / assigned if assigned else None,
                    "partial_rate": partial / assigned if assigned else None,
                    "failure_rate": failed / assigned if assigned else None,
                },
                "research": {
                    "evidence_coverage": comparison_profile.get("evidence_coverage"),
                    "source_count": source_count,
                    "claim_count": claim_count,
                },
                "cost": {
                    "total_cost": total_cost,
                    "cost_per_question": total_cost / assigned if assigned else None,
                },
            }
        )
    rows: list[dict[str, Any]] = []
    for run in runs:
        result = results.get(run.id)
        question = questions.get(run.question_id)
        rows.append(
            {
                "evaluation_run_id": run.id,
                "forecast_run_id": run.forecast_run_id,
                "question_id": run.question_id,
                "question": question.question_text if question else None,
                "profile_id": run.profile_id,
                "status": run.status,
                "error": run.error,
                "probability": result.probability if result else None,
                "outcome": result.outcome if result else (question.outcome if question else None),
                "brier_score": result.brier_score if result else None,
                "failures": [
                    serialize_forecast_failure(item)
                    for item in failures_by_run.get(run.id, [])
                ],
            }
        )
    return ForecastResearchAnalysis(
        experiment_id=experiment.id,
        dataset=comparison["dataset"],
        status=experiment.status,
        profiles=profile_analyses,
        failure_taxonomy={group: list(categories) for group, categories in FAILURE_TAXONOMY.items()},
        failure_summary=_failure_summary(failures),
        rows=rows,
    )

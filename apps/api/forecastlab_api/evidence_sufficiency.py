from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evidence_sufficiency import (
    EvidenceSufficiencyAssessment,
    NodeEvidenceSufficiencyAssessment,
)
from forecastlab.timeutil import as_utc
from forecastlab_api.models import EvidenceSufficiencyAssessmentRow


class EvidenceSufficiencyStoreError(ValueError):
    """Raised when immutable evidence-assessment input changes for one run."""


def evidence_sufficiency_from_row(
    row: EvidenceSufficiencyAssessmentRow,
) -> EvidenceSufficiencyAssessment:
    return EvidenceSufficiencyAssessment(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        policy_version=row.policy_version,
        policy_snapshot=json.loads(row.policy_snapshot_json),
        status=row.status,  # type: ignore[arg-type]
        reasons=json.loads(row.reasons_json),
        warnings=json.loads(row.warnings_json),
        selected_node_count=row.selected_node_count,
        included_node_count=row.included_node_count,
        critical_node_count=row.critical_node_count,
        selected_coverage_numerator=row.selected_coverage_numerator,
        selected_coverage_denominator=row.selected_coverage_denominator,
        selected_node_coverage=row.selected_node_coverage,
        graph_coverage_numerator=row.graph_coverage_numerator,
        graph_coverage_denominator=row.graph_coverage_denominator,
        graph_node_coverage=row.graph_node_coverage,
        included_graph_weight=row.included_graph_weight,
        total_graph_weight=row.total_graph_weight,
        graph_weight_coverage=row.graph_weight_coverage,
        cited_claim_count=row.cited_claim_count,
        cited_item_count=row.cited_item_count,
        cited_source_count=row.cited_source_count,
        distinct_host_count=row.distinct_host_count,
        distinct_hosts=json.loads(row.distinct_hosts_json),
        primary_claim_count=row.primary_claim_count,
        primary_node_count=row.primary_node_count,
        structured_claim_count=row.structured_claim_count,
        fallback_claim_count=row.fallback_claim_count,
        included_node_ids=json.loads(row.included_node_ids_json),
        excluded_node_ids=json.loads(row.excluded_node_ids_json),
        insufficient_node_ids=json.loads(row.insufficient_node_ids_json),
        per_node=[
            NodeEvidenceSufficiencyAssessment.model_validate(value)
            for value in json.loads(row.per_node_json)
        ],
        assessment_input_hash=row.assessment_input_hash,
        created_at=as_utc(row.created_at),
    )


def store_evidence_sufficiency_assessment(
    session: Session,
    assessment: EvidenceSufficiencyAssessment,
) -> EvidenceSufficiencyAssessmentRow:
    existing = session.scalar(
        select(EvidenceSufficiencyAssessmentRow).where(
            EvidenceSufficiencyAssessmentRow.forecast_run_id
            == assessment.forecast_run_id
        )
    )
    if existing is not None:
        if existing.assessment_input_hash != assessment.assessment_input_hash:
            raise EvidenceSufficiencyStoreError(
                "conflicting_immutable_evidence_sufficiency_assessment"
            )
        return existing

    row = EvidenceSufficiencyAssessmentRow(
        id=assessment.id,
        forecast_run_id=assessment.forecast_run_id,
        policy_version=assessment.policy_version,
        policy_snapshot_json=json.dumps(assessment.policy_snapshot, sort_keys=True),
        status=assessment.status,
        reasons_json=json.dumps(assessment.reasons),
        warnings_json=json.dumps(assessment.warnings),
        selected_node_count=assessment.selected_node_count,
        included_node_count=assessment.included_node_count,
        critical_node_count=assessment.critical_node_count,
        selected_coverage_numerator=assessment.selected_coverage_numerator,
        selected_coverage_denominator=assessment.selected_coverage_denominator,
        selected_node_coverage=assessment.selected_node_coverage,
        graph_coverage_numerator=assessment.graph_coverage_numerator,
        graph_coverage_denominator=assessment.graph_coverage_denominator,
        graph_node_coverage=assessment.graph_node_coverage,
        included_graph_weight=assessment.included_graph_weight,
        total_graph_weight=assessment.total_graph_weight,
        graph_weight_coverage=assessment.graph_weight_coverage,
        cited_claim_count=assessment.cited_claim_count,
        cited_item_count=assessment.cited_item_count,
        cited_source_count=assessment.cited_source_count,
        distinct_host_count=assessment.distinct_host_count,
        distinct_hosts_json=json.dumps(assessment.distinct_hosts),
        primary_claim_count=assessment.primary_claim_count,
        primary_node_count=assessment.primary_node_count,
        structured_claim_count=assessment.structured_claim_count,
        fallback_claim_count=assessment.fallback_claim_count,
        included_node_ids_json=json.dumps(assessment.included_node_ids),
        excluded_node_ids_json=json.dumps(assessment.excluded_node_ids),
        insufficient_node_ids_json=json.dumps(assessment.insufficient_node_ids),
        per_node_json=json.dumps(
            [value.model_dump(mode="json") for value in assessment.per_node],
            sort_keys=True,
        ),
        assessment_input_hash=assessment.assessment_input_hash,
        created_at=assessment.created_at,
    )
    session.add(row)
    session.flush()
    return row

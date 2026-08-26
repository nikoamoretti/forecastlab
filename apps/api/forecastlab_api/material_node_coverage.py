from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.material_node_coverage import (
    MaterialNodeCoverageAssessment,
    MissingDependencyRelationship,
    MissingParentRelationship,
)
from forecastlab.timeutil import as_utc
from forecastlab_api.models import MaterialNodeCoverageAssessmentRow


class MaterialNodeCoverageStoreError(ValueError):
    """Raised when immutable material-node inputs change for one run."""


def material_node_coverage_from_row(
    row: MaterialNodeCoverageAssessmentRow,
) -> MaterialNodeCoverageAssessment:
    return MaterialNodeCoverageAssessment(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        policy_version=row.policy_version,
        status=row.status,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        graph_id=row.graph_id,
        graph_version=row.graph_version,
        research_plan_id=row.research_plan_id,
        evidence_sufficiency_assessment_id=(
            row.evidence_sufficiency_assessment_id
        ),
        evidence_sufficiency_assessment_hash=(
            row.evidence_sufficiency_assessment_hash
        ),
        graph_node_count=row.graph_node_count,
        selected_node_count=row.selected_node_count,
        included_node_count=row.included_node_count,
        excluded_node_count=row.excluded_node_count,
        total_graph_weight=row.total_graph_weight,
        included_graph_weight=row.included_graph_weight,
        excluded_graph_weight=row.excluded_graph_weight,
        included_frontier_weight=row.included_frontier_weight,
        maximum_excluded_weight=row.maximum_excluded_weight,
        selected_node_ids=json.loads(row.selected_node_ids_json),
        included_node_ids=json.loads(row.included_node_ids_json),
        excluded_node_ids=json.loads(row.excluded_node_ids_json),
        higher_importance_excluded_node_ids=json.loads(
            row.higher_importance_excluded_node_ids_json
        ),
        frontier_tie_excluded_node_ids=json.loads(
            row.frontier_tie_excluded_node_ids_json
        ),
        missing_parent_relationships=[
            MissingParentRelationship.model_validate(value)
            for value in json.loads(row.missing_parent_relationships_json)
        ],
        missing_dependency_relationships=[
            MissingDependencyRelationship.model_validate(value)
            for value in json.loads(row.missing_dependency_relationships_json)
        ],
        reasons=json.loads(row.reasons_json),
        warnings=json.loads(row.warnings_json),
        policy_snapshot=json.loads(row.policy_snapshot_json),
        assessment_input_hash=row.assessment_input_hash,
    )


def store_material_node_coverage_assessment(
    session: Session,
    assessment: MaterialNodeCoverageAssessment,
) -> MaterialNodeCoverageAssessmentRow:
    existing = session.scalar(
        select(MaterialNodeCoverageAssessmentRow).where(
            MaterialNodeCoverageAssessmentRow.forecast_run_id
            == assessment.forecast_run_id
        )
    )
    if existing is not None:
        if existing.assessment_input_hash != assessment.assessment_input_hash:
            raise MaterialNodeCoverageStoreError(
                "conflicting_immutable_material_node_coverage_assessment"
            )
        return existing

    row = MaterialNodeCoverageAssessmentRow(
        id=assessment.id,
        forecast_run_id=assessment.forecast_run_id,
        policy_version=assessment.policy_version,
        status=assessment.status,
        created_at=assessment.created_at,
        graph_id=assessment.graph_id,
        graph_version=assessment.graph_version,
        research_plan_id=assessment.research_plan_id,
        evidence_sufficiency_assessment_id=(
            assessment.evidence_sufficiency_assessment_id
        ),
        evidence_sufficiency_assessment_hash=(
            assessment.evidence_sufficiency_assessment_hash
        ),
        graph_node_count=assessment.graph_node_count,
        selected_node_count=assessment.selected_node_count,
        included_node_count=assessment.included_node_count,
        excluded_node_count=assessment.excluded_node_count,
        total_graph_weight=assessment.total_graph_weight,
        included_graph_weight=assessment.included_graph_weight,
        excluded_graph_weight=assessment.excluded_graph_weight,
        included_frontier_weight=assessment.included_frontier_weight,
        maximum_excluded_weight=assessment.maximum_excluded_weight,
        selected_node_ids_json=json.dumps(assessment.selected_node_ids),
        included_node_ids_json=json.dumps(assessment.included_node_ids),
        excluded_node_ids_json=json.dumps(assessment.excluded_node_ids),
        higher_importance_excluded_node_ids_json=json.dumps(
            assessment.higher_importance_excluded_node_ids
        ),
        frontier_tie_excluded_node_ids_json=json.dumps(
            assessment.frontier_tie_excluded_node_ids
        ),
        missing_parent_relationships_json=json.dumps(
            [
                value.model_dump(mode="json")
                for value in assessment.missing_parent_relationships
            ],
            sort_keys=True,
        ),
        missing_dependency_relationships_json=json.dumps(
            [
                value.model_dump(mode="json")
                for value in assessment.missing_dependency_relationships
            ],
            sort_keys=True,
        ),
        reasons_json=json.dumps(assessment.reasons),
        warnings_json=json.dumps(assessment.warnings),
        policy_snapshot_json=json.dumps(
            assessment.policy_snapshot,
            sort_keys=True,
        ),
        assessment_input_hash=assessment.assessment_input_hash,
    )
    session.add(row)
    session.flush()
    return row

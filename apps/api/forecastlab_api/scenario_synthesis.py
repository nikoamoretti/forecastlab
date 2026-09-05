from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.scenario_synthesis import (
    ScenarioCoverageAudit,
    ScenarioPathway,
    ScenarioSynthesis,
)
from forecastlab.timeutil import as_utc
from forecastlab_api.models import ScenarioSynthesisRow


class ScenarioSynthesisStoreError(ValueError):
    """Raised when immutable scenario input changes for an assessed run."""


def scenario_synthesis_from_row(row: ScenarioSynthesisRow) -> ScenarioSynthesis:
    return ScenarioSynthesis(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        policy_version=row.policy_version,
        policy_snapshot=json.loads(row.policy_snapshot_json),
        status=row.status,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        completed_at=as_utc(row.completed_at),
        prompt_version=row.prompt_version,
        provider=row.provider,
        model=row.model,
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        scenarios=[
            ScenarioPathway.model_validate(value)
            for value in json.loads(row.scenarios_json)
        ],
        coverage_audit=ScenarioCoverageAudit.model_validate(
            json.loads(row.coverage_audit_json)
        ),
        failure_reasons=json.loads(row.failure_reasons_json),
        diagnostics=json.loads(row.diagnostics_json),
        evidence_sufficiency_assessment_id=(
            row.evidence_sufficiency_assessment_id
        ),
        evidence_sufficiency_assessment_hash=(
            row.evidence_sufficiency_assessment_hash
        ),
        material_node_coverage_assessment_id=(
            row.material_node_coverage_assessment_id
        ),
        material_node_coverage_assessment_hash=(
            row.material_node_coverage_assessment_hash
        ),
    )


def scenario_synthesis_for_run(
    session: Session,
    forecast_run_id: str,
) -> ScenarioSynthesisRow | None:
    return session.scalar(
        select(ScenarioSynthesisRow).where(
            ScenarioSynthesisRow.forecast_run_id == forecast_run_id
        )
    )


def store_scenario_synthesis(
    session: Session,
    synthesis: ScenarioSynthesis,
) -> ScenarioSynthesisRow:
    existing = scenario_synthesis_for_run(session, synthesis.forecast_run_id)
    if existing is not None:
        if existing.input_hash != synthesis.input_hash:
            raise ScenarioSynthesisStoreError(
                "conflicting_immutable_scenario_synthesis"
            )
        return existing
    row = ScenarioSynthesisRow(
        id=synthesis.id,
        forecast_run_id=synthesis.forecast_run_id,
        policy_version=synthesis.policy_version,
        policy_snapshot_json=json.dumps(
            synthesis.policy_snapshot,
            sort_keys=True,
        ),
        status=synthesis.status,
        created_at=synthesis.created_at,
        completed_at=synthesis.completed_at,
        prompt_version=synthesis.prompt_version,
        provider=synthesis.provider,
        model=synthesis.model,
        input_hash=synthesis.input_hash,
        output_hash=synthesis.output_hash,
        scenarios_json=json.dumps(
            [scenario.model_dump(mode="json") for scenario in synthesis.scenarios],
            sort_keys=True,
        ),
        coverage_audit_json=json.dumps(
            synthesis.coverage_audit.model_dump(mode="json"),
            sort_keys=True,
        ),
        failure_reasons_json=json.dumps(synthesis.failure_reasons),
        diagnostics_json=json.dumps(synthesis.diagnostics, sort_keys=True),
        evidence_sufficiency_assessment_id=(
            synthesis.evidence_sufficiency_assessment_id
        ),
        evidence_sufficiency_assessment_hash=(
            synthesis.evidence_sufficiency_assessment_hash
        ),
        material_node_coverage_assessment_id=(
            synthesis.material_node_coverage_assessment_id
        ),
        material_node_coverage_assessment_hash=(
            synthesis.material_node_coverage_assessment_hash
        ),
    )
    session.add(row)
    session.flush()
    return row

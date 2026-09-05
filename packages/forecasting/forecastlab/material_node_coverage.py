from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.research_planning import ResearchPlan
from forecastlab.schemas import ForecastGraph, ForecastNodeRun
from forecastlab.timeutil import utcnow

POLICY_VERSION = "private_v1_material_node_gate_v1"


class MaterialNodePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["private_v1_material_node_gate_v1"] = POLICY_VERSION
    comparison: Literal["strict_greater_than_frontier"] = (
        "strict_greater_than_frontier"
    )
    canonical_weight_source: Literal["forecast_node_importance_weight"] = (
        "forecast_node_importance_weight"
    )
    decimal_conversion: Literal["decimal_from_canonical_string"] = (
        "decimal_from_canonical_string"
    )
    frontier_ties_allowed: bool = True
    zero_total_graph_weight_allowed: bool = False
    relationship_findings_enforced: bool = False


PRIVATE_V1_MATERIAL_NODE_GATE_V1 = MaterialNodePolicy()


class MaterialNodePlanAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str
    status: Literal["passed", "failed"]
    selected_node_ids: list[str]
    skipped_node_ids: list[str]
    selected_node_weights: dict[str, str]
    skipped_node_weights: dict[str, str]
    selected_frontier_weight: str | None
    maximum_skipped_weight: str | None
    higher_importance_skipped_node_ids: list[str]
    frontier_tie_skipped_node_ids: list[str]
    reasons: list[str]
    warnings: list[str]
    policy_snapshot: dict[str, Any]
    audit_input_hash: str


class MaterialNodeFailureIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    node_id: str | None
    stage: str
    error_code: str
    impact: str


class MissingParentRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    excluded_parent_node_id: str


class MissingDependencyRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    excluded_dependency_node_id: str


class MaterialNodeCoverageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    forecast_run_id: str
    policy_version: str
    status: Literal["passed", "failed"]
    created_at: datetime = Field(default_factory=utcnow)
    graph_id: str
    graph_version: int
    research_plan_id: str
    evidence_sufficiency_assessment_id: str | None
    evidence_sufficiency_assessment_hash: str | None
    graph_node_count: int
    selected_node_count: int
    included_node_count: int
    excluded_node_count: int
    total_graph_weight: str
    included_graph_weight: str
    excluded_graph_weight: str
    included_frontier_weight: str | None
    maximum_excluded_weight: str | None
    selected_node_ids: list[str]
    included_node_ids: list[str]
    excluded_node_ids: list[str]
    higher_importance_excluded_node_ids: list[str]
    frontier_tie_excluded_node_ids: list[str]
    missing_parent_relationships: list[MissingParentRelationship]
    missing_dependency_relationships: list[MissingDependencyRelationship]
    reasons: list[str]
    warnings: list[str]
    policy_snapshot: dict[str, Any]
    assessment_input_hash: str


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _decimal_weight(value: float) -> Decimal:
    return Decimal(str(value))


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _sorted_ids_by_weight(
    node_ids: set[str],
    weights: dict[str, Decimal],
) -> list[str]:
    return sorted(node_ids, key=lambda node_id: (-weights[node_id], node_id))


def _graph_weights(
    graph: ForecastGraph,
) -> tuple[dict[str, Decimal], list[str]]:
    node_ids = [node.id for node in graph.nodes]
    reasons: list[str] = []
    if len(set(node_ids)) != len(node_ids):
        reasons.append("duplicate_graph_node_id")
    weights = {
        node.id: _decimal_weight(node.importance_weight)
        for node in graph.nodes
    }
    if not weights:
        reasons.append("forecast_graph_has_no_nodes")
    elif all(weight == 0 for weight in weights.values()):
        reasons.append("all_graph_importance_weights_zero")
    return weights, reasons


def _assessment_id(forecast_run_id: str, policy_version: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"forecastlab:{forecast_run_id}:{policy_version}",
        )
    )


def assess_material_node_plan(
    *,
    graph: ForecastGraph,
    plan: ResearchPlan,
    policy: MaterialNodePolicy = PRIVATE_V1_MATERIAL_NODE_GATE_V1,
) -> MaterialNodePlanAudit:
    """Audit a frozen ResearchPlan without changing its node selection or order."""

    weights, reasons = _graph_weights(graph)
    known_ids = set(weights)
    selected_set = set(plan.selected_nodes)
    skipped_set = set(plan.skipped_nodes)
    unknown_selected = sorted(selected_set - known_ids)
    unknown_skipped = sorted(skipped_set - known_ids)
    omitted_from_plan = sorted(known_ids - selected_set - skipped_set)
    for node_id in unknown_selected:
        reasons.append(f"selected_node_absent_from_graph:{node_id}")
    for node_id in unknown_skipped:
        reasons.append(f"skipped_node_absent_from_graph:{node_id}")
    if omitted_from_plan:
        reasons.append(
            "research_plan_graph_partition_incomplete:"
            + ",".join(omitted_from_plan)
        )

    selected_known = selected_set & known_ids
    skipped_known = skipped_set & known_ids
    selected_frontier = (
        min(weights[node_id] for node_id in selected_known)
        if selected_known
        else None
    )
    maximum_skipped = (
        max(weights[node_id] for node_id in skipped_known)
        if skipped_known
        else None
    )
    higher = (
        {
            node_id
            for node_id in skipped_known
            if weights[node_id] > selected_frontier
        }
        if selected_frontier is not None
        else set()
    )
    ties = (
        {
            node_id
            for node_id in skipped_known
            if weights[node_id] == selected_frontier
        }
        if selected_frontier is not None
        else set()
    )
    higher_ids = _sorted_ids_by_weight(higher, weights)
    tie_ids = _sorted_ids_by_weight(ties, weights)
    if not selected_known:
        reasons.append("material_node_plan_has_no_known_selected_nodes")
    if higher_ids:
        reasons.append("private_v1_plan_omits_higher_importance_node")
    warnings = (
        ["equal_weight_nodes_skipped_at_selected_frontier"]
        if tie_ids
        else []
    )

    selected_ids = _sorted_ids_by_weight(selected_known, weights)
    skipped_ids = _sorted_ids_by_weight(skipped_known, weights)
    policy_snapshot = policy.model_dump(mode="json")
    hash_payload = {
        "policy": policy_snapshot,
        "graph": {
            "id": graph.id,
            "version": graph.version,
            "node_weights": {
                node_id: _decimal_text(weights[node_id])
                for node_id in sorted(weights)
            },
        },
        "research_plan": {
            "id": plan.id,
            "selected_node_ids": sorted(plan.selected_nodes),
            "skipped_node_ids": sorted(plan.skipped_nodes),
        },
    }
    return MaterialNodePlanAudit(
        policy_version=policy.version,
        status="failed" if reasons else "passed",
        selected_node_ids=selected_ids,
        skipped_node_ids=skipped_ids,
        selected_node_weights={
            node_id: _decimal_text(weights[node_id]) for node_id in selected_ids
        },
        skipped_node_weights={
            node_id: _decimal_text(weights[node_id]) for node_id in skipped_ids
        },
        selected_frontier_weight=(
            _decimal_text(selected_frontier)
            if selected_frontier is not None
            else None
        ),
        maximum_skipped_weight=(
            _decimal_text(maximum_skipped)
            if maximum_skipped is not None
            else None
        ),
        higher_importance_skipped_node_ids=higher_ids,
        frontier_tie_skipped_node_ids=tie_ids,
        reasons=_ordered_unique(reasons),
        warnings=warnings,
        policy_snapshot=policy_snapshot,
        audit_input_hash=sha256_text(canonical_json(hash_payload)),
    )


def assess_material_node_coverage(
    *,
    forecast_run_id: str,
    graph: ForecastGraph,
    plan: ResearchPlan,
    node_runs: list[ForecastNodeRun],
    evidence_sufficiency_assessment_id: str | None,
    evidence_sufficiency_assessment_hash: str | None,
    node_failures: list[MaterialNodeFailureIdentity],
    policy: MaterialNodePolicy = PRIVATE_V1_MATERIAL_NODE_GATE_V1,
) -> MaterialNodeCoverageAssessment:
    """Audit included node materiality using canonical weights and identities only."""

    weights, reasons = _graph_weights(graph)
    known_ids = set(weights)
    selected_set = set(plan.selected_nodes)
    included_node_ids_raw = [run.node_id for run in node_runs]
    included_set = set(included_node_ids_raw)
    if len(included_set) != len(included_node_ids_raw):
        reasons.append("duplicate_included_node_forecast")
    unknown_included = sorted(included_set - known_ids)
    for node_id in unknown_included:
        reasons.append(f"included_node_absent_from_graph:{node_id}")
    included_not_selected = sorted(included_set - selected_set)
    if included_not_selected:
        reasons.append(
            "included_node_not_selected_by_research_plan:"
            + ",".join(included_not_selected)
        )
    if evidence_sufficiency_assessment_id is None or (
        evidence_sufficiency_assessment_hash is None
    ):
        reasons.append("evidence_sufficiency_assessment_required")

    selected_known = selected_set & known_ids
    included_known = included_set & known_ids
    excluded_set = known_ids - included_known
    included_frontier = (
        min(weights[node_id] for node_id in included_known)
        if included_known
        else None
    )
    maximum_excluded = (
        max(weights[node_id] for node_id in excluded_set)
        if excluded_set
        else None
    )
    higher = (
        {
            node_id
            for node_id in excluded_set
            if weights[node_id] > included_frontier
        }
        if included_frontier is not None
        else set()
    )
    ties = (
        {
            node_id
            for node_id in excluded_set
            if weights[node_id] == included_frontier
        }
        if included_frontier is not None
        else set()
    )
    higher_ids = _sorted_ids_by_weight(higher, weights)
    tie_ids = _sorted_ids_by_weight(ties, weights)
    if not included_known:
        reasons.append("material_node_coverage_has_no_known_included_nodes")
    if higher_ids:
        reasons.append("higher_importance_graph_node_excluded")

    warnings: list[str] = []
    if tie_ids:
        warnings.append("equal_weight_nodes_excluded_at_included_frontier")
    missing_parents: list[MissingParentRelationship] = []
    missing_dependencies: list[MissingDependencyRelationship] = []
    graph_nodes = {node.id: node for node in graph.nodes}
    for node_id in sorted(included_known):
        node = graph_nodes[node_id]
        if node.parent_node_id in excluded_set:
            missing_parents.append(
                MissingParentRelationship(
                    node_id=node_id,
                    excluded_parent_node_id=str(node.parent_node_id),
                )
            )
        for dependency_id in sorted(set(node.dependencies) & excluded_set):
            missing_dependencies.append(
                MissingDependencyRelationship(
                    node_id=node_id,
                    excluded_dependency_node_id=dependency_id,
                )
            )
    if missing_parents:
        warnings.append("included_nodes_have_excluded_parents")
    if missing_dependencies:
        warnings.append("included_nodes_have_excluded_dependencies")

    selected_ids = _sorted_ids_by_weight(selected_known, weights)
    included_ids = _sorted_ids_by_weight(included_known, weights)
    excluded_ids = _sorted_ids_by_weight(excluded_set, weights)
    total_weight = sum(weights.values(), Decimal("0"))
    included_weight = sum(
        (weights[node_id] for node_id in included_known),
        Decimal("0"),
    )
    excluded_weight = total_weight - included_weight
    policy_snapshot = policy.model_dump(mode="json")
    failure_payload = [
        failure.model_dump(mode="json")
        for failure in sorted(
            node_failures,
            key=lambda value: (
                value.node_id or "",
                value.stage,
                value.error_code,
                value.id,
            ),
        )
    ]
    hash_payload = {
        "policy": policy_snapshot,
        "graph": {
            "id": graph.id,
            "version": graph.version,
            "node_weights": {
                node_id: _decimal_text(weights[node_id])
                for node_id in sorted(weights)
            },
        },
        "research_plan": {
            "id": plan.id,
            "selected_node_ids": sorted(plan.selected_nodes),
            "skipped_node_ids": sorted(plan.skipped_nodes),
        },
        "included_node_forecast_runs": sorted(
            (
                {"id": run.id, "node_id": run.node_id}
                for run in node_runs
            ),
            key=lambda value: (value["node_id"], value["id"]),
        ),
        "excluded_node_ids": sorted(excluded_set),
        "evidence_sufficiency_assessment": {
            "id": evidence_sufficiency_assessment_id,
            "assessment_input_hash": evidence_sufficiency_assessment_hash,
        },
        "node_failures_affecting_inclusion": failure_payload,
    }
    return MaterialNodeCoverageAssessment(
        id=_assessment_id(forecast_run_id, policy.version),
        forecast_run_id=forecast_run_id,
        policy_version=policy.version,
        status="failed" if reasons else "passed",
        graph_id=graph.id,
        graph_version=graph.version,
        research_plan_id=plan.id,
        evidence_sufficiency_assessment_id=evidence_sufficiency_assessment_id,
        evidence_sufficiency_assessment_hash=evidence_sufficiency_assessment_hash,
        graph_node_count=len(weights),
        selected_node_count=len(selected_known),
        included_node_count=len(included_known),
        excluded_node_count=len(excluded_set),
        total_graph_weight=_decimal_text(total_weight),
        included_graph_weight=_decimal_text(included_weight),
        excluded_graph_weight=_decimal_text(excluded_weight),
        included_frontier_weight=(
            _decimal_text(included_frontier)
            if included_frontier is not None
            else None
        ),
        maximum_excluded_weight=(
            _decimal_text(maximum_excluded)
            if maximum_excluded is not None
            else None
        ),
        selected_node_ids=selected_ids,
        included_node_ids=included_ids,
        excluded_node_ids=excluded_ids,
        higher_importance_excluded_node_ids=higher_ids,
        frontier_tie_excluded_node_ids=tie_ids,
        missing_parent_relationships=missing_parents,
        missing_dependency_relationships=missing_dependencies,
        reasons=_ordered_unique(reasons),
        warnings=warnings,
        policy_snapshot=policy_snapshot,
        assessment_input_hash=sha256_text(canonical_json(hash_payload)),
    )

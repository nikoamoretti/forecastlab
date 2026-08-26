from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.research_planning import ResearchPlan
from forecastlab.schemas import EvidenceClaim, ForecastGraph, ForecastNodeRun
from forecastlab.timeutil import utcnow

POLICY_VERSION = "private_v1_evidence_gate_v1"


class EvidenceSufficiencyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["private_v1_evidence_gate_v1"] = POLICY_VERSION
    minimum_included_node_forecasts: int = 3
    selected_coverage_numerator: int = 2
    selected_coverage_denominator: int = 3
    minimum_graph_weight_coverage: str = "0.50"
    critical_primary_claims_required: int = 1
    critical_secondary_claims_required: int = 2
    critical_secondary_distinct_hosts_required: int = 2
    noncritical_structured_claims_required: int = 1
    minimum_primary_nodes: int = 1
    minimum_distinct_hosts: int = 2
    structured_extraction_methods: tuple[
        Literal[
            "structured_full_document",
            "structured_smaller_chunk",
            "mock_structured",
        ],
        ...,
    ] = (
        "structured_full_document",
        "structured_smaller_chunk",
        "mock_structured",
    )
    fallback_can_satisfy_included_node: bool = False
    unknown_legacy_can_satisfy_provenance: bool = False


PRIVATE_V1_EVIDENCE_GATE_V1 = EvidenceSufficiencyPolicy()


class EvidenceSufficiencyItem(BaseModel):
    """The persisted EvidenceItem facts needed by the pure evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source_url: str
    rejected: bool
    as_of_eligible: bool


EvidenceGrade = Literal[
    "strong_primary",
    "corroborated_secondary",
    "adequate_secondary",
    "weak_fallback",
    "insufficient",
]


class NodeEvidenceSufficiencyAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    selected: bool
    included: bool
    critical: bool
    grade: EvidenceGrade
    passed: bool
    reasons: list[str]
    cited_claim_ids: list[str]
    eligible_claim_ids: list[str]
    cited_item_ids: list[str]
    distinct_hosts: list[str]
    structured_claim_count: int
    primary_claim_count: int
    secondary_claim_count: int
    fallback_claim_count: int
    unknown_legacy_claim_count: int


class EvidenceSufficiencyAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    forecast_run_id: str
    policy_version: str
    policy_snapshot: dict[str, Any]
    status: Literal["passed", "failed"]
    reasons: list[str]
    warnings: list[str]
    selected_node_count: int
    included_node_count: int
    critical_node_count: int
    selected_coverage_numerator: int
    selected_coverage_denominator: int
    selected_node_coverage: float
    graph_coverage_numerator: int
    graph_coverage_denominator: int
    graph_node_coverage: float
    included_graph_weight: float
    total_graph_weight: float
    graph_weight_coverage: float
    cited_claim_count: int
    cited_item_count: int
    cited_source_count: int
    distinct_host_count: int
    distinct_hosts: list[str]
    primary_claim_count: int
    primary_node_count: int
    structured_claim_count: int
    fallback_claim_count: int
    included_node_ids: list[str]
    excluded_node_ids: list[str]
    insufficient_node_ids: list[str]
    per_node: list[NodeEvidenceSufficiencyAssessment]
    assessment_input_hash: str
    created_at: datetime = Field(default_factory=utcnow)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _canonical_source_url(value: str) -> str:
    return value.strip()


def _assessment_id(run_id: str, policy_version: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecastlab:{run_id}:{policy_version}"))


def assess_evidence_sufficiency(
    *,
    forecast_run_id: str,
    graph: ForecastGraph,
    plan: ResearchPlan,
    node_runs: list[ForecastNodeRun],
    claims: list[EvidenceClaim],
    items: list[EvidenceSufficiencyItem],
    policy: EvidenceSufficiencyPolicy = PRIVATE_V1_EVIDENCE_GATE_V1,
) -> EvidenceSufficiencyAssessment:
    """Evaluate the frozen private-V1 policy using persisted deterministic facts only."""

    graph_nodes = {node.id: node for node in graph.nodes}
    selected_ids = [node_id for node_id in plan.selected_nodes if node_id in graph_nodes]
    selected_set = set(selected_ids)
    runs_by_node = {
        run.node_id: run for run in node_runs if run.node_id in selected_set
    }
    included_ids = [node_id for node_id in selected_ids if node_id in runs_by_node]
    included_set = set(included_ids)
    critical_ids = sorted(
        selected_set
        & {
            str(node_id)
            for node_id in plan.budget_allocation.get("critical_node_ids", [])
        }
    )
    critical_set = set(critical_ids)
    claims_by_id = {claim.id: claim for claim in claims}
    items_by_id = {item.id: item for item in items}

    warnings: list[str] = []
    per_node: list[NodeEvidenceSufficiencyAssessment] = []
    all_eligible_claims: dict[str, EvidenceClaim] = {}
    cited_items: set[str] = set()
    cited_sources: set[str] = set()
    cited_hosts: set[str] = set()
    primary_nodes: set[str] = set()

    for node_id in selected_ids:
        node_run = runs_by_node.get(node_id)
        cited_ids = (
            _ordered_unique(
                [*node_run.supporting_claim_ids, *node_run.opposing_claim_ids]
            )
            if node_run is not None
            else []
        )
        eligible: list[EvidenceClaim] = []
        node_reasons: list[str] = []
        for claim_id in cited_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                warnings.append(f"cited_claim_not_found:{node_id}:{claim_id}")
                continue
            if claim.forecast_node_id != node_id:
                warnings.append(f"wrong_node_claim_ignored:{node_id}:{claim_id}")
                continue
            item = items_by_id.get(claim.evidence_item_id)
            if item is None:
                warnings.append(f"evidence_item_not_found:{node_id}:{claim_id}")
                continue
            if item.source_url != claim.source_url:
                warnings.append(f"claim_item_source_mismatch:{node_id}:{claim_id}")
                continue
            if (
                item.rejected
                or not item.as_of_eligible
                or not claim.as_of_eligible
                or not claim.cutoff_verified
            ):
                warnings.append(f"temporally_or_document_ineligible_claim_ignored:{node_id}:{claim_id}")
                continue
            eligible.append(claim)
            all_eligible_claims[claim.id] = claim
            cited_items.add(item.id)
            cited_sources.add(_canonical_source_url(claim.source_url))
            if claim.source_host:
                cited_hosts.add(claim.source_host)

        structured_methods = set(policy.structured_extraction_methods)
        structured = [
            claim
            for claim in eligible
            if claim.extraction_method in structured_methods
            and claim.source_class != "unknown_legacy"
        ]
        primaries = [claim for claim in structured if claim.source_class == "primary"]
        secondaries = [claim for claim in structured if claim.source_class == "secondary"]
        fallback = [claim for claim in eligible if claim.extraction_method == "document_fallback"]
        unknown = [
            claim
            for claim in eligible
            if claim.source_class == "unknown_legacy"
            or claim.extraction_method == "unknown_legacy"
        ]
        hosts = sorted({claim.source_host for claim in structured if claim.source_host})
        secondary_hosts = {claim.source_host for claim in secondaries if claim.source_host}

        if primaries:
            grade: EvidenceGrade = "strong_primary"
            primary_nodes.add(node_id)
        elif len(secondaries) >= 2 and len(secondary_hosts) >= 2:
            grade = "corroborated_secondary"
        elif secondaries:
            grade = "adequate_secondary"
        elif fallback:
            grade = "weak_fallback"
        else:
            grade = "insufficient"

        included = node_run is not None
        critical = node_id in critical_set
        if not included:
            node_reasons.append("node_forecast_missing")
        elif not eligible:
            node_reasons.append("no_eligible_cited_claims")
        if included and fallback and not structured:
            node_reasons.append("fallback_only_evidence")
        if included and unknown and not structured:
            node_reasons.append("unknown_legacy_provenance")
        if included and critical and not (
            len(primaries) >= policy.critical_primary_claims_required
            or (
                len(secondaries) >= policy.critical_secondary_claims_required
                and len(secondary_hosts)
                >= policy.critical_secondary_distinct_hosts_required
            )
        ):
            node_reasons.append(
                "critical_structured_primary_or_two_secondary_hosts_required"
            )
        if (
            included
            and not critical
            and len(structured) < policy.noncritical_structured_claims_required
        ):
            node_reasons.append("noncritical_structured_evidence_required")

        node_passed = included and not node_reasons
        per_node.append(
            NodeEvidenceSufficiencyAssessment(
                node_id=node_id,
                selected=True,
                included=included,
                critical=critical,
                grade=grade,
                passed=node_passed,
                reasons=_ordered_unique(node_reasons),
                cited_claim_ids=cited_ids,
                eligible_claim_ids=sorted(claim.id for claim in eligible),
                cited_item_ids=sorted({claim.evidence_item_id for claim in eligible}),
                distinct_hosts=hosts,
                structured_claim_count=len(structured),
                primary_claim_count=len(primaries),
                secondary_claim_count=len(secondaries),
                fallback_claim_count=len(fallback),
                unknown_legacy_claim_count=len(unknown),
            )
        )

    selected_count = len(selected_ids)
    included_count = len(included_ids)
    graph_count = len(graph.nodes)
    total_weight_decimal = sum(
        (Decimal(str(node.importance_weight)) for node in graph.nodes), Decimal("0")
    )
    included_weight_decimal = sum(
        (
            Decimal(str(graph_nodes[node_id].importance_weight))
            for node_id in included_ids
        ),
        Decimal("0"),
    )
    selected_coverage = included_count / selected_count if selected_count else 0.0
    graph_coverage = included_count / graph_count if graph_count else 0.0
    weight_coverage = (
        included_weight_decimal / total_weight_decimal
        if total_weight_decimal > 0
        else Decimal("0")
    )

    reasons: list[str] = []
    if included_count < policy.minimum_included_node_forecasts:
        reasons.append("minimum_included_node_forecasts_not_met")
    if (
        included_count * policy.selected_coverage_denominator
        < selected_count * policy.selected_coverage_numerator
    ):
        reasons.append("selected_node_coverage_below_two_thirds")
    if weight_coverage < Decimal(policy.minimum_graph_weight_coverage):
        reasons.append("graph_weight_coverage_below_half")
    for node_assessment in per_node:
        if node_assessment.critical and not node_assessment.passed:
            reasons.append(
                f"selected_critical_node_requirement_not_met:{node_assessment.node_id}"
            )
        elif node_assessment.included and not node_assessment.passed:
            reasons.append(f"included_node_evidence_insufficient:{node_assessment.node_id}")
    if len(primary_nodes) < policy.minimum_primary_nodes:
        reasons.append("structured_primary_evidence_required")
    if len(cited_hosts) < policy.minimum_distinct_hosts:
        reasons.append("two_distinct_source_hosts_required")

    structured_claims = [
        claim
        for claim in all_eligible_claims.values()
        if claim.extraction_method in set(policy.structured_extraction_methods)
        and claim.source_class != "unknown_legacy"
    ]
    primary_claims = [claim for claim in structured_claims if claim.source_class == "primary"]
    fallback_claims = [
        claim
        for claim in all_eligible_claims.values()
        if claim.extraction_method == "document_fallback"
    ]
    insufficient_ids = sorted(
        assessment.node_id
        for assessment in per_node
        if assessment.included and not assessment.passed
    )

    hash_payload = {
        "policy": policy.model_dump(mode="json"),
        "graph": {
            "id": graph.id,
            "version": graph.version,
            "nodes": [
                {
                    "id": node.id,
                    "importance_weight": str(Decimal(str(node.importance_weight))),
                }
                for node in graph.nodes
            ],
        },
        "plan": {
            "id": plan.id,
            "selected_nodes": plan.selected_nodes,
            "skipped_nodes": plan.skipped_nodes,
            "critical_node_ids": critical_ids,
        },
        "node_runs": [
            {
                "id": run.id,
                "node_id": run.node_id,
                "supporting_claim_ids": run.supporting_claim_ids,
                "opposing_claim_ids": run.opposing_claim_ids,
            }
            for run in sorted(node_runs, key=lambda value: (value.node_id, value.id))
            if run.node_id in selected_set
        ],
        "claims": [
            {
                "id": claim.id,
                "evidence_item_id": claim.evidence_item_id,
                "forecast_node_id": claim.forecast_node_id,
                "source_url": claim.source_url,
                "source_host": claim.source_host,
                "source_class": claim.source_class,
                "extraction_method": claim.extraction_method,
                "as_of_eligible": claim.as_of_eligible,
                "cutoff_verified": claim.cutoff_verified,
            }
            for claim in sorted(claims, key=lambda value: value.id)
            if claim.id
            in {
                claim_id
                for run in node_runs
                for claim_id in [*run.supporting_claim_ids, *run.opposing_claim_ids]
            }
        ],
        "items": [
            item.model_dump(mode="json")
            for item in sorted(items, key=lambda value: value.id)
            if item.id
            in {
                claim.evidence_item_id
                for claim in claims
                if claim.id
                in {
                    claim_id
                    for run in node_runs
                    for claim_id in [*run.supporting_claim_ids, *run.opposing_claim_ids]
                }
            }
        ],
    }
    input_hash = sha256_text(canonical_json(hash_payload))
    return EvidenceSufficiencyAssessment(
        id=_assessment_id(forecast_run_id, policy.version),
        forecast_run_id=forecast_run_id,
        policy_version=policy.version,
        policy_snapshot=policy.model_dump(mode="json"),
        status="failed" if reasons else "passed",
        reasons=_ordered_unique(reasons),
        warnings=_ordered_unique(warnings),
        selected_node_count=selected_count,
        included_node_count=included_count,
        critical_node_count=len(critical_ids),
        selected_coverage_numerator=included_count,
        selected_coverage_denominator=selected_count,
        selected_node_coverage=round(selected_coverage, 12),
        graph_coverage_numerator=included_count,
        graph_coverage_denominator=graph_count,
        graph_node_coverage=round(graph_coverage, 12),
        included_graph_weight=float(included_weight_decimal),
        total_graph_weight=float(total_weight_decimal),
        graph_weight_coverage=round(float(weight_coverage), 12),
        cited_claim_count=len(all_eligible_claims),
        cited_item_count=len(cited_items),
        cited_source_count=len(cited_sources),
        distinct_host_count=len(cited_hosts),
        distinct_hosts=sorted(cited_hosts),
        primary_claim_count=len(primary_claims),
        primary_node_count=len(primary_nodes),
        structured_claim_count=len(structured_claims),
        fallback_claim_count=len(fallback_claims),
        included_node_ids=included_ids,
        excluded_node_ids=[node_id for node_id in selected_ids if node_id not in included_set],
        insufficient_node_ids=insufficient_ids,
        per_node=per_node,
        assessment_input_hash=input_hash,
    )

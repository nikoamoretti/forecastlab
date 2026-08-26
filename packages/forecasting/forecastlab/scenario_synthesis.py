from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from forecastlab.budget import Budget, estimate_prompt_tokens
from forecastlab.budgeted_provider import BudgetedModelProvider
from forecastlab.errors import (
    BudgetExceeded,
    PermanentProviderError,
    TransientProviderError,
)
from forecastlab.hashing import canonical_json, redact_secrets, sha256_text
from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider, StructuredOutputDiagnostics
from forecastlab.schemas import (
    EvidenceClaim,
    ForecastContract,
    ForecastGraph,
    ForecastNodeRun,
)
from forecastlab.structured_outputs import (
    sanitize_validation_errors,
    scenario_synthesis_json_schema,
    validate_structured_output,
)
from forecastlab.timeutil import utcnow

SCENARIO_SYNTHESIS_POLICY_VERSION = "private_v1_scenario_synthesis_v1"
SCENARIO_SYNTHESIS_SCHEMA_NAME = "scenario_synthesis"
SCENARIO_SYNTHESIS_CALL_KIND = "scenario_synthesis"
SCENARIO_SYNTHESIS_MAX_INPUT_CHARACTERS = 30_000
SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS = 10_000
SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS = 2_048
SCENARIO_SYNTHESIS_WALL_CLOCK_SECONDS = 30.0

ScenarioKind = Literal["base_case", "yes_case", "no_case"]
ScenarioSynthesisStatus = Literal["passed", "failed"]


class ScenarioPathwayOutput(BaseModel):
    """Strict model-authored pathway before deterministic ID assignment."""

    model_config = ConfigDict(extra="forbid", strict=True)

    local_id: str = Field(min_length=1, max_length=32)
    kind: ScenarioKind
    title: str = Field(min_length=1, max_length=96)
    summary: str = Field(min_length=1, max_length=600)
    node_ids: list[str] = Field(min_length=2, max_length=8)
    claim_ids: list[str] = Field(min_length=1, max_length=16)
    mechanisms: list[str] = Field(min_length=1, max_length=4)
    triggers: list[str] = Field(min_length=1, max_length=4)
    invalidators: list[str] = Field(min_length=1, max_length=4)
    unresolved_uncertainties: list[str] = Field(min_length=1, max_length=4)

    @field_validator("local_id", "title", "summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("scenario_text_required")
        return normalized

    @field_validator("node_ids", "claim_ids")
    @classmethod
    def validate_unique_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("scenario_reference_required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate_scenario_reference")
        return normalized

    @field_validator("mechanisms")
    @classmethod
    def validate_mechanisms(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()).strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("scenario_list_item_required")
        if any(len(value) > 240 for value in normalized):
            raise ValueError("scenario_list_item_too_long")
        return normalized

    @field_validator("triggers", "invalidators", "unresolved_uncertainties")
    @classmethod
    def validate_bounded_text_lists(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()).strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("scenario_list_item_required")
        if any(len(value) > 200 for value in normalized):
            raise ValueError("scenario_list_item_too_long")
        return normalized


class ScenarioSynthesisOutput(BaseModel):
    """Strict provider-facing output containing exactly three pathways."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenarios: list[ScenarioPathwayOutput] = Field(min_length=3, max_length=3)


class ScenarioPathway(BaseModel):
    """Persisted grounded pathway with a deterministic stable identifier."""

    model_config = ConfigDict(extra="forbid")

    id: str
    local_id: str
    kind: ScenarioKind
    title: str
    summary: str
    node_ids: list[str]
    claim_ids: list[str]
    mechanisms: list[str]
    triggers: list[str]
    invalidators: list[str]
    unresolved_uncertainties: list[str]


class ScenarioCoverageAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included_node_ids: list[str]
    covered_node_ids: list[str]
    uncovered_node_ids: list[str]
    required_relationships: list[dict[str, str]]
    covered_relationships: list[dict[str, str]]
    uncovered_relationships: list[dict[str, str]]
    cited_claim_ids: list[str]
    referenced_claim_ids: list[str]
    errors: list[str] = Field(default_factory=list)


class ScenarioSynthesis(BaseModel):
    """Immutable audit artifact for one private-V1 scenario stage."""

    model_config = ConfigDict(extra="forbid")

    id: str
    forecast_run_id: str
    policy_version: str
    policy_snapshot: dict[str, Any]
    status: ScenarioSynthesisStatus
    created_at: datetime
    completed_at: datetime
    prompt_version: str
    provider: str
    model: str
    input_hash: str
    output_hash: str | None = None
    scenarios: list[ScenarioPathway] = Field(default_factory=list)
    coverage_audit: ScenarioCoverageAudit
    failure_reasons: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    evidence_sufficiency_assessment_id: str
    evidence_sufficiency_assessment_hash: str
    material_node_coverage_assessment_id: str
    material_node_coverage_assessment_hash: str


class ScenarioSynthesisPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = SCENARIO_SYNTHESIS_POLICY_VERSION
    required_kinds: tuple[ScenarioKind, ...] = (
        "base_case",
        "yes_case",
        "no_case",
    )
    minimum_nodes_per_scenario: int = 2
    minimum_claims_per_scenario: int = 1
    maximum_input_characters: int = SCENARIO_SYNTHESIS_MAX_INPUT_CHARACTERS
    reserved_input_tokens: int = SCENARIO_SYNTHESIS_RESERVED_INPUT_TOKENS
    maximum_output_tokens: int = SCENARIO_SYNTHESIS_MAX_OUTPUT_TOKENS
    maximum_logical_calls: int = 1
    semantic_retries: int = 0
    reasoning_effort: Literal["minimal"] = "minimal"
    verbosity: Literal["low"] = "low"


PRIVATE_V1_SCENARIO_SYNTHESIS_V1 = ScenarioSynthesisPolicy()


class ScenarioSynthesisInputEnvelopeError(ValueError):
    """Raised before a provider call when the frozen input reserve is unsafe."""


@dataclass(frozen=True)
class PreparedScenarioSynthesis:
    packet: dict[str, Any]
    packet_json: str
    input_hash: str
    included_node_ids: tuple[str, ...]
    cited_claim_ids: tuple[str, ...]


def _policy_snapshot(policy: ScenarioSynthesisPolicy) -> dict[str, Any]:
    return policy.model_dump(mode="json")


def _relationship_key(source: str, target: str, kind: str) -> dict[str, str]:
    return {"source_node_id": source, "target_node_id": target, "kind": kind}


def _required_included_relationships(
    graph: ForecastGraph,
    included_node_ids: set[str],
) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    for node in sorted(graph.nodes, key=lambda item: item.id):
        if node.id not in included_node_ids:
            continue
        if node.parent_node_id in included_node_ids:
            assert node.parent_node_id is not None
            relationships.append(
                _relationship_key(node.id, node.parent_node_id, "parent")
            )
        for dependency in sorted(set(node.dependencies)):
            if dependency in included_node_ids:
                relationships.append(
                    _relationship_key(node.id, dependency, "dependency")
                )
    return relationships


def prepare_scenario_synthesis(
    *,
    contract: ForecastContract,
    graph: ForecastGraph,
    node_runs: list[ForecastNodeRun],
    claims: list[EvidenceClaim],
    evidence_sufficiency_assessment_id: str,
    evidence_sufficiency_assessment_hash: str,
    material_node_coverage_assessment_id: str,
    material_node_coverage_assessment_hash: str,
    policy: ScenarioSynthesisPolicy = PRIVATE_V1_SCENARIO_SYNTHESIS_V1,
) -> PreparedScenarioSynthesis:
    node_by_id = {node.id: node for node in graph.nodes}
    if len(node_by_id) != len(graph.nodes):
        raise ValueError("duplicate_graph_node_id")
    run_by_node = {run.node_id: run for run in node_runs}
    if len(run_by_node) != len(node_runs):
        raise ValueError("duplicate_included_node_run")
    if len(run_by_node) < 3:
        raise ValueError("scenario_synthesis_requires_three_included_nodes")
    if any(node_id not in node_by_id for node_id in run_by_node):
        raise ValueError("scenario_included_node_outside_graph")

    claims_by_id = {claim.id: claim for claim in claims}
    if len(claims_by_id) != len(claims):
        raise ValueError("duplicate_scenario_claim_id")
    cited_ids = sorted(
        {
            claim_id
            for run in node_runs
            for claim_id in [*run.supporting_claim_ids, *run.opposing_claim_ids]
        }
    )
    if not cited_ids:
        raise ValueError("scenario_synthesis_cited_evidence_required")
    for run in node_runs:
        for claim_id in [*run.supporting_claim_ids, *run.opposing_claim_ids]:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                raise ValueError(f"scenario_cited_claim_missing:{claim_id}")
            if claim.forecast_node_id != run.node_id:
                raise ValueError(f"scenario_wrong_node_claim:{claim_id}")
            if not claim.as_of_eligible or not claim.cutoff_verified:
                raise ValueError(f"scenario_ineligible_claim:{claim_id}")

    included_ids = sorted(run_by_node)
    packet = {
        "policy_version": policy.version,
        "contract": {
            "normalized_question": contract.normalized_question,
            "yes_condition": contract.yes_condition,
            "no_condition": contract.no_condition,
            "resolution_date": (
                contract.resolution_date.isoformat()
                if contract.resolution_date is not None
                else None
            ),
            "resolver_risk_notes": contract.resolver_risk_notes,
        },
        "graph": {
            "id": graph.id,
            "version": graph.version,
            "included_node_ids": included_ids,
            "nodes": [
                {
                    "id": node.id,
                    "question": node.question,
                    "node_type": node.node_type,
                    "importance_weight": node.importance_weight,
                    "parent_node_id": (
                        node.parent_node_id
                        if node.parent_node_id in run_by_node
                        else None
                    ),
                    "dependency_ids": sorted(
                        dependency
                        for dependency in set(node.dependencies)
                        if dependency in run_by_node
                    ),
                }
                for node in sorted(graph.nodes, key=lambda item: item.id)
                if node.id in run_by_node
            ],
        },
        "node_forecasts": [
            {
                "id": run.id,
                "node_id": run.node_id,
                "probability": run.probability,
                "reasoning": run.reasoning,
                "uncertainty_notes": list(run.uncertainty_notes),
                "supporting_claim_ids": sorted(set(run.supporting_claim_ids)),
                "opposing_claim_ids": sorted(set(run.opposing_claim_ids)),
            }
            for run in sorted(node_runs, key=lambda item: (item.node_id, item.id))
        ],
        "cited_evidence_claims": [
            {
                "id": claim.id,
                "forecast_node_id": claim.forecast_node_id,
                "claim": claim.claim,
                "excerpt": claim.excerpt,
                "supports_or_refutes": claim.supports_or_refutes,
                "source_title": claim.source_title,
                "source_host": claim.source_host,
                "source_class": claim.source_class,
                "extraction_method": claim.extraction_method,
                "temporal_basis": claim.temporal_basis,
                "cutoff_verified": claim.cutoff_verified,
            }
            for claim in sorted(
                (claims_by_id[claim_id] for claim_id in cited_ids),
                key=lambda item: item.id,
            )
        ],
        "assessments": {
            "evidence_sufficiency": {
                "id": evidence_sufficiency_assessment_id,
                "input_hash": evidence_sufficiency_assessment_hash,
            },
            "material_node_coverage": {
                "id": material_node_coverage_assessment_id,
                "input_hash": material_node_coverage_assessment_hash,
            },
        },
    }
    packet_json = canonical_json(packet)
    if len(packet_json) > policy.maximum_input_characters:
        raise ValueError("scenario_synthesis_input_character_limit_exceeded")
    return PreparedScenarioSynthesis(
        packet=packet,
        packet_json=packet_json,
        input_hash=sha256_text(packet_json),
        included_node_ids=tuple(included_ids),
        cited_claim_ids=tuple(cited_ids),
    )


def validate_scenario_grounding(
    *,
    output: ScenarioSynthesisOutput,
    graph: ForecastGraph,
    node_runs: list[ForecastNodeRun],
    claims: list[EvidenceClaim],
) -> ScenarioCoverageAudit:
    included = {run.node_id for run in node_runs}
    claims_by_id = {claim.id: claim for claim in claims}
    cited = {
        claim_id
        for run in node_runs
        for claim_id in [*run.supporting_claim_ids, *run.opposing_claim_ids]
    }
    required_relationships = _required_included_relationships(graph, included)
    errors: list[str] = []
    by_kind = {scenario.kind: scenario for scenario in output.scenarios}
    if len(by_kind) != 3 or set(by_kind) != {
        "base_case",
        "yes_case",
        "no_case",
    }:
        errors.append("scenario_kinds_must_be_exactly_base_yes_no")
    titles = [scenario.title.casefold() for scenario in output.scenarios]
    if len(titles) != len(set(titles)):
        errors.append("scenario_titles_must_be_unique")
    local_ids = [scenario.local_id for scenario in output.scenarios]
    if len(local_ids) != len(set(local_ids)):
        errors.append("scenario_local_ids_must_be_unique")
    if "yes_case" in by_kind and "no_case" in by_kind:
        if set(by_kind["yes_case"].node_ids) == set(by_kind["no_case"].node_ids):
            errors.append("yes_no_scenario_node_sets_must_differ")

    covered_nodes: set[str] = set()
    referenced_claims: set[str] = set()
    scenario_node_sets: list[set[str]] = []
    for scenario in output.scenarios:
        node_ids = set(scenario.node_ids)
        claim_ids = set(scenario.claim_ids)
        scenario_node_sets.append(node_ids)
        covered_nodes.update(node_ids & included)
        referenced_claims.update(claim_ids)
        for node_id in sorted(node_ids - included):
            errors.append(f"scenario_unknown_or_excluded_node:{node_id}")
        for claim_id in sorted(claim_ids):
            claim = claims_by_id.get(claim_id)
            if claim is None:
                errors.append(f"scenario_unknown_claim:{claim_id}")
                continue
            if claim_id not in cited:
                errors.append(f"scenario_uncited_claim:{claim_id}")
            if not claim.as_of_eligible or not claim.cutoff_verified:
                errors.append(f"scenario_ineligible_claim:{claim_id}")
            if claim.forecast_node_id not in node_ids:
                errors.append(f"scenario_wrong_node_claim:{claim_id}")

    uncovered_nodes = sorted(included - covered_nodes)
    if uncovered_nodes:
        errors.append("scenario_included_node_coverage_incomplete")
    covered_relationships: list[dict[str, str]] = []
    uncovered_relationships: list[dict[str, str]] = []
    for relationship in required_relationships:
        endpoints = {
            relationship["source_node_id"],
            relationship["target_node_id"],
        }
        if any(endpoints.issubset(node_set) for node_set in scenario_node_sets):
            covered_relationships.append(relationship)
        else:
            uncovered_relationships.append(relationship)
    if uncovered_relationships:
        errors.append("scenario_relationship_coverage_incomplete")

    return ScenarioCoverageAudit(
        included_node_ids=sorted(included),
        covered_node_ids=sorted(covered_nodes),
        uncovered_node_ids=uncovered_nodes,
        required_relationships=required_relationships,
        covered_relationships=covered_relationships,
        uncovered_relationships=uncovered_relationships,
        cited_claim_ids=sorted(cited),
        referenced_claim_ids=sorted(referenced_claims),
        errors=list(dict.fromkeys(errors)),
    )


def _empty_coverage(prepared: PreparedScenarioSynthesis, reason: str) -> ScenarioCoverageAudit:
    return ScenarioCoverageAudit(
        included_node_ids=list(prepared.included_node_ids),
        covered_node_ids=[],
        uncovered_node_ids=list(prepared.included_node_ids),
        required_relationships=[],
        covered_relationships=[],
        uncovered_relationships=[],
        cited_claim_ids=list(prepared.cited_claim_ids),
        referenced_claim_ids=[],
        errors=[reason],
    )


def _diagnostics_payload(diagnostics: StructuredOutputDiagnostics | None) -> dict[str, Any]:
    return diagnostics.audit_payload() if diagnostics is not None else {}


class ScenarioSynthesizer:
    """One-call grounded scenario authoring with deterministic validation."""

    def __init__(
        self,
        model: ModelProvider,
        budget: Budget,
        *,
        policy: ScenarioSynthesisPolicy = PRIVATE_V1_SCENARIO_SYNTHESIS_V1,
        prompt_bundle: PromptBundle | None = None,
    ) -> None:
        self.model = model
        self.budget = budget
        self.policy = policy
        self.prompt_bundle = prompt_bundle

    def synthesize(
        self,
        *,
        forecast_run_id: str,
        prepared: PreparedScenarioSynthesis,
        graph: ForecastGraph,
        node_runs: list[ForecastNodeRun],
        claims: list[EvidenceClaim],
        evidence_sufficiency_assessment_id: str,
        evidence_sufficiency_assessment_hash: str,
        material_node_coverage_assessment_id: str,
        material_node_coverage_assessment_hash: str,
    ) -> ScenarioSynthesis:
        created_at = utcnow()
        if self.prompt_bundle is not None:
            system, prompt_version = self.prompt_bundle.get("scenario_synthesis")
        else:
            system, prompt_version = load_prompt("scenario_synthesis")
        provider_name = str(getattr(self.model, "name", "unknown"))
        model_name = str(getattr(self.model, "model", provider_name))
        diagnostics: dict[str, Any] = {}
        reason: str | None = None
        output: ScenarioSynthesisOutput | None = None
        try:
            estimated_input_tokens = estimate_prompt_tokens(
                system,
                prepared.packet_json,
            )
            diagnostics["estimated_input_tokens"] = estimated_input_tokens
            diagnostics["reserved_input_tokens"] = self.policy.reserved_input_tokens
            if estimated_input_tokens > self.policy.reserved_input_tokens:
                raise ScenarioSynthesisInputEnvelopeError(
                    "scenario_synthesis_input_token_reservation_exceeded"
                )
            result = BudgetedModelProvider(
                self.model,
                self.budget,
                stage="scenario_synthesis",
                max_output_tokens_cap=self.policy.maximum_output_tokens,
                call_kind=SCENARIO_SYNTHESIS_CALL_KIND,
            ).complete_json(
                system=system,
                user=prepared.packet_json,
                schema_name=SCENARIO_SYNTHESIS_SCHEMA_NAME,
                max_output_tokens=self.policy.maximum_output_tokens,
                max_completion_tokens=self.policy.maximum_output_tokens,
                max_visible_output_tokens=self.policy.maximum_output_tokens,
                estimated_input_tokens=self.policy.reserved_input_tokens,
                json_schema=scenario_synthesis_json_schema(),
                reasoning_effort=self.policy.reasoning_effort,
                verbosity=self.policy.verbosity,
            )
            diagnostics.update(_diagnostics_payload(result.diagnostics))
            if result.diagnostics is not None and result.diagnostics.refusal_present:
                reason = "structured_output_refused"
            elif (
                result.diagnostics is not None
                and result.diagnostics.finish_reason == "length"
            ):
                reason = "structured_output_truncated"
            elif not result.content.strip() and result.parsed is None:
                reason = "structured_output_empty"
            elif result.parsed is None:
                try:
                    decoded = json.loads(result.content)
                except (json.JSONDecodeError, TypeError):
                    reason = "structured_output_invalid_json"
                else:
                    validated, validation_errors = validate_structured_output(
                        SCENARIO_SYNTHESIS_SCHEMA_NAME,
                        decoded,
                    )
                    if validated is None:
                        diagnostics["schema_validation_errors"] = validation_errors
                        reason = "structured_output_schema_invalid"
                    else:
                        output = ScenarioSynthesisOutput.model_validate(validated)
            else:
                validated, validation_errors = validate_structured_output(
                    SCENARIO_SYNTHESIS_SCHEMA_NAME,
                    result.parsed,
                )
                if validated is None:
                    diagnostics["schema_validation_errors"] = validation_errors
                    reason = "structured_output_schema_invalid"
                else:
                    output = ScenarioSynthesisOutput.model_validate(validated)
        except ValidationError as exc:
            diagnostics["schema_validation_errors"] = sanitize_validation_errors(exc)
            reason = "structured_output_schema_invalid"
        except BudgetExceeded as exc:
            diagnostics["budget_stage"] = exc.stage
            diagnostics["budget_reason"] = exc.reason
            reason = "scenario_synthesis_budget_exceeded"
        except ScenarioSynthesisInputEnvelopeError as exc:
            reason = str(exc)
        except (PermanentProviderError, TransientProviderError) as exc:
            diagnostics["provider_error"] = redact_secrets(str(exc))[:240]
            reason = "scenario_synthesis_provider_failure"

        coverage = _empty_coverage(
            prepared,
            reason or "scenario_synthesis_domain_validation_failed",
        )
        output_hash: str | None = None
        scenarios: list[ScenarioPathway] = []
        if reason is None and output is not None:
            coverage = validate_scenario_grounding(
                output=output,
                graph=graph,
                node_runs=node_runs,
                claims=claims,
            )
            if coverage.errors:
                reason = "scenario_synthesis_domain_validation_failed"
            else:
                canonical_output = canonical_json(output.model_dump(mode="json"))
                output_hash = sha256_text(canonical_output)
                for pathway in output.scenarios:
                    stable_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                "forecastlab:scenario:"
                                f"{forecast_run_id}:{prepared.input_hash}:"
                                f"{output_hash}:{pathway.kind}"
                            ),
                        )
                    )
                    scenarios.append(
                        ScenarioPathway(
                            id=stable_id,
                            **pathway.model_dump(mode="json"),
                        )
                    )

        completed_at = utcnow()
        artifact_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"forecastlab:scenario-synthesis:{forecast_run_id}:{prepared.input_hash}",
            )
        )
        return ScenarioSynthesis(
            id=artifact_id,
            forecast_run_id=forecast_run_id,
            policy_version=self.policy.version,
            policy_snapshot=_policy_snapshot(self.policy),
            status="failed" if reason is not None else "passed",
            created_at=created_at,
            completed_at=completed_at,
            prompt_version=prompt_version,
            provider=provider_name,
            model=model_name,
            input_hash=prepared.input_hash,
            output_hash=output_hash,
            scenarios=scenarios,
            coverage_audit=coverage,
            failure_reasons=[reason] if reason is not None else [],
            diagnostics=diagnostics,
            evidence_sufficiency_assessment_id=(
                evidence_sufficiency_assessment_id
            ),
            evidence_sufficiency_assessment_hash=(
                evidence_sufficiency_assessment_hash
            ),
            material_node_coverage_assessment_id=(
                material_node_coverage_assessment_id
            ),
            material_node_coverage_assessment_hash=(
                material_node_coverage_assessment_hash
            ),
        )

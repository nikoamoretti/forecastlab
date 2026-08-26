from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from forecastlab.budget import estimate_prompt_tokens
from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider
from forecastlab.schemas import (
    ForecastContract,
    ForecastGraph,
    ForecastGraphGenerationAudit,
    ForecastGraphGenerationResult,
    ForecastNode,
)
from forecastlab.structured_outputs import (
    COMPACT_FORECAST_GRAPH_SCHEMA_NAME,
    CompactForecastGraphOutput,
    ForecastGraphOutput,
    compact_forecast_graph_json_schema,
    forecast_graph_json_schema,
    validate_structured_output,
)
from forecastlab.timeutil import utcnow


class ForecastGraphError(ValueError):
    def __init__(
        self,
        reasons: list[str],
        message: str = "Forecast Graph could not be generated",
        *,
        audit: dict[str, Any] | None = None,
    ) -> None:
        self.reasons = reasons
        self.audit = audit or {}
        super().__init__(message)


def _normalized_node_question(question: str) -> str:
    return re.sub(r"[\W_]+", " ", question.casefold()).strip()


def _cycle_exists(graph: ForecastGraph) -> bool:
    node_ids = {node.id for node in graph.nodes}
    edges: dict[str, set[str]] = {}
    for node in graph.nodes:
        related = {dependency for dependency in node.dependencies if dependency in node_ids}
        if node.parent_node_id in node_ids:
            related.add(node.parent_node_id)
        edges[node.id] = related

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for dependency in edges[node_id]:
            if visit(dependency):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in edges if node_id not in visited)


def graph_approval_errors(graph: ForecastGraph) -> list[str]:
    """Return deterministic validation errors for a graph approval decision."""

    errors: list[str] = []
    if not graph.root_question.strip():
        errors.append("root_outcome_required")
    if len(graph.nodes) < 3:
        errors.append("minimum_three_nodes_required")
    if not any(node.node_type == "adversarial" for node in graph.nodes):
        errors.append("adversarial_node_required")
    if not any(node.node_type == "resolver" for node in graph.nodes):
        errors.append("resolver_node_required")

    node_ids = [node.id for node in graph.nodes]
    known_ids = set(node_ids)
    if len(known_ids) != len(node_ids):
        errors.append("duplicate_node_id")
    if any(node.graph_id != graph.id for node in graph.nodes):
        errors.append("node_graph_id_mismatch")

    normalized_questions = [_normalized_node_question(node.question) for node in graph.nodes]
    if any(not question for question in normalized_questions):
        errors.append("node_question_required")
    if len(set(normalized_questions)) != len(normalized_questions):
        errors.append("duplicate_node_question")
    if any(not node.required_output_type.strip() for node in graph.nodes):
        errors.append("required_output_type_required")

    dangling_parent = any(
        node.parent_node_id is not None and node.parent_node_id not in known_ids for node in graph.nodes
    )
    if dangling_parent:
        errors.append("unknown_parent_node")
    dangling_dependency = any(dependency not in known_ids for node in graph.nodes for dependency in node.dependencies)
    if dangling_dependency:
        errors.append("unknown_dependency_node")
    if any(node.parent_node_id == node.id or node.id in node.dependencies for node in graph.nodes):
        errors.append("self_dependency")
    if not dangling_parent and not dangling_dependency and _cycle_exists(graph):
        errors.append("dependency_cycle_detected")
    return errors


def ensure_graph_approvable(graph: ForecastGraph) -> None:
    errors = graph_approval_errors(graph)
    if errors:
        raise ForecastGraphError(errors, "Forecast Graph failed approval validation")


def _mock_nodes(contract: ForecastContract) -> list[dict[str, Any]]:
    outcome = contract.normalized_question.rstrip("?")
    reference_class = contract.initial_reference_class or "comparable historical outcomes"
    driver = contract.suggested_drivers[0] if contract.suggested_drivers else "the most important causal driver"
    dependency = contract.known_dependencies[0] if contract.known_dependencies else "shared upstream assumptions"
    source = contract.authoritative_source or "the authoritative resolution source"
    return [
        {
            "id": "base-rate",
            "parent_node_id": None,
            "question": f"What base rate does {reference_class} imply for: {outcome}?",
            "node_type": "base_rate",
            "importance_weight": 0.9,
            "dependencies": [],
            "preferred_sources": ["official historical series", "peer-reviewed reference-class studies"],
            "required_output_type": "probability",
            "status": "pending",
        },
        {
            "id": "current-trend",
            "parent_node_id": None,
            "question": f"What is the current level and direction of the observable indicators most relevant to: {outcome}?",
            "node_type": "trend",
            "importance_weight": 0.85,
            "dependencies": [],
            "preferred_sources": ["current official statistics", "primary institutional releases"],
            "required_output_type": "directional_update",
            "status": "pending",
        },
        {
            "id": "primary-driver",
            "parent_node_id": None,
            "question": f"How could {driver} change the likelihood of: {outcome}?",
            "node_type": "driver",
            "importance_weight": 0.8,
            "dependencies": ["current-trend"],
            "preferred_sources": ["primary records", "domain-specific empirical research"],
            "required_output_type": "directional_update",
            "status": "pending",
        },
        {
            "id": "known-dependency",
            "parent_node_id": "primary-driver",
            "question": f"How does {dependency} constrain or mediate the primary driver?",
            "node_type": "dependency",
            "importance_weight": 0.65,
            "dependencies": ["current-trend"],
            "preferred_sources": ["official methodology", "primary dependency indicators"],
            "required_output_type": "structured_categorical",
            "status": "pending",
        },
        {
            "id": "alternative-scenario",
            "parent_node_id": "primary-driver",
            "question": f"Which plausible alternative scenario would most change the expected outcome of: {outcome}?",
            "node_type": "scenario",
            "importance_weight": 0.7,
            "dependencies": ["known-dependency"],
            "preferred_sources": ["scenario analyses", "leading indicators"],
            "required_output_type": "scenario_weight",
            "status": "pending",
        },
        {
            "id": "adversarial-case",
            "parent_node_id": None,
            "question": f"What strongest evidence or hidden assumption could overturn the leading view on: {outcome}?",
            "node_type": "adversarial",
            "importance_weight": 0.75,
            "dependencies": ["current-trend", "alternative-scenario"],
            "preferred_sources": ["contradictory primary evidence", "methodological critiques"],
            "required_output_type": "directional_update",
            "status": "pending",
        },
        {
            "id": "resolution-mechanics",
            "parent_node_id": None,
            "question": f"What resolver, publication, revision, or boundary risk at {source} could change the scored outcome?",
            "node_type": "resolver",
            "importance_weight": 0.6,
            "dependencies": [],
            "preferred_sources": [source, *contract.fallback_sources],
            "required_output_type": "structured_categorical",
            "status": "pending",
        },
    ]


def _mock_compact_nodes() -> list[dict[str, Any]]:
    """Compact deterministic graph used only by the non-provider mock path."""

    return [
        {"q": "What historical base rate applies?", "t": "base_rate", "w": 0.9, "p": None, "d": [], "s": ["official historical data"], "o": "probability"},
        {"q": "What is the current trend?", "t": "trend", "w": 0.85, "p": None, "d": [], "s": ["official current data"], "o": "directional_update"},
        {"q": "What primary driver changes the outcome?", "t": "driver", "w": 0.8, "p": None, "d": [1], "s": ["primary records"], "o": "directional_update"},
        {"q": "What dependency constrains the driver?", "t": "dependency", "w": 0.65, "p": 2, "d": [1], "s": ["official methodology"], "o": "structured_categorical"},
        {"q": "What alternative scenario matters most?", "t": "scenario", "w": 0.7, "p": 2, "d": [3], "s": ["scenario analysis"], "o": "scenario_weight"},
        {"q": "What could overturn the leading view?", "t": "adversarial", "w": 0.75, "p": None, "d": [1, 4], "s": ["contradictory evidence"], "o": "directional_update"},
        {"q": "What resolution mechanics could change scoring?", "t": "resolver", "w": 0.6, "p": None, "d": [], "s": ["authoritative resolver"], "o": "structured_categorical"},
    ]


def _compact_transport_prompt(system: str) -> str:
    """Replace only the wire-format section while preserving graph semantics."""

    prefix, marker, remainder = system.partition("Required JSON shape:")
    if not marker:
        prefix = system
        rules = ""
    else:
        _canonical_shape, rules_marker, rules = remainder.partition("Rules:")
        if not rules_marker:
            rules = ""
    replacements = {
        "- Use parent_node_id for hierarchy; null means the node is directly beneath the contract outcome.": (
            "- Use p for hierarchy; null means the node is directly beneath the contract outcome."
        ),
        "- Dependencies and parent_node_id must reference node ids in this response.": (
            "- Dependencies in d and the parent in p must reference zero-based indexes in n."
        ),
    }
    semantic_rules = "\n".join(
        replacements.get(line.strip(), line.strip())
        for line in rules.splitlines()
        if line.strip()
    )
    compact_shape = """Required JSON shape:
{"n":[{"q":"question","t":"node_type","w":0.0,"p":null,"d":[0],"s":["source guidance"],"o":"required_output_type"}]}

Transport rules:
- Array indexes are zero-based.
- Do not emit ids, UUIDs, status, or canonical property names.
- ForecastLab deterministically restores local ids and pending status after strict validation."""
    return f"{prefix.rstrip()}\n\n{compact_shape}\n\nRules:\n{semantic_rules}".strip()


def _transport_validation_error(
    field_path: str,
    error_type: str,
    message: str,
) -> dict[str, str]:
    return {
        "field_path": field_path,
        "error_type": error_type,
        "message": message,
    }


def _canonicalize_compact_graph(
    payload: dict[str, Any],
    *,
    max_characters: int,
    question_max_characters: int,
    local_id_max_characters: int,
    max_dependencies_per_node: int,
    max_preferred_sources_per_node: int,
    preferred_source_max_characters: int,
) -> tuple[dict[str, Any] | None, int, list[dict[str, str]]]:
    """Validate compact transport bounds and restore the canonical graph shape."""

    try:
        compact = CompactForecastGraphOutput.model_validate(payload)
    except ValueError as exc:
        # The provider boundary already sanitizes normal Pydantic failures. This
        # fallback remains content-free for direct and mock callers.
        return None, 0, [
            _transport_validation_error(
                "$",
                "compact_transport_validation_error",
                str(exc).splitlines()[0][:240],
            )
        ]
    serialized = json.dumps(
        compact.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    character_count = len(serialized)
    errors: list[dict[str, str]] = []
    if character_count > max_characters:
        errors.append(
            _transport_validation_error(
                "$",
                "compact_transport_too_large",
                "Compact Forecast Graph exceeds its character budget",
            )
        )

    node_count = len(compact.n)
    local_ids = [f"n{index}" for index in range(node_count)]
    if any(len(local_id) > local_id_max_characters for local_id in local_ids):
        errors.append(
            _transport_validation_error(
                "n",
                "compact_local_id_too_long",
                "Generated local node id exceeds the configured limit",
            )
        )

    for index, node in enumerate(compact.n):
        if len(node.q) > question_max_characters:
            errors.append(
                _transport_validation_error(
                    f"n.{index}.q",
                    "string_too_long",
                    "Compact graph question exceeds the configured limit",
                )
            )
        if len(node.d) > max_dependencies_per_node:
            errors.append(
                _transport_validation_error(
                    f"n.{index}.d",
                    "too_many_dependencies",
                    "Compact graph node has too many dependencies",
                )
            )
        if len(node.s) > max_preferred_sources_per_node:
            errors.append(
                _transport_validation_error(
                    f"n.{index}.s",
                    "too_many_preferred_sources",
                    "Compact graph node has too many preferred sources",
                )
            )
        if any(
            len(source) > preferred_source_max_characters
            for source in node.s
        ):
            errors.append(
                _transport_validation_error(
                    f"n.{index}.s",
                    "preferred_source_too_long",
                    "Compact preferred source exceeds the configured limit",
                )
            )
        references = ([node.p] if node.p is not None else []) + list(node.d)
        if any(reference < 0 or reference >= node_count for reference in references):
            errors.append(
                _transport_validation_error(
                    f"n.{index}",
                    "invalid_node_index",
                    "Compact graph relationship index is outside the node array",
                )
            )
    if errors:
        return None, character_count, errors

    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(compact.n):
        nodes.append(
            {
                "id": local_ids[index],
                "parent_node_id": (
                    local_ids[node.p] if node.p is not None else None
                ),
                "question": node.q,
                "node_type": node.t,
                "importance_weight": node.w,
                "dependencies": [local_ids[item] for item in node.d],
                "preferred_sources": list(node.s),
                "required_output_type": node.o,
                "status": "pending",
            }
        )
    return {"nodes": nodes}, character_count, []


class GraphGenerator:
    """Create and validate a question-specific research graph from an approved contract."""

    def __init__(
        self,
        model: ModelProvider,
        *,
        max_output_tokens: int,
        max_completion_tokens: int | None = None,
        max_visible_output_tokens: int | None = None,
        reasoning_effort: Literal[
            "none",
            "minimal",
            "low",
            "medium",
            "high",
        ] | None = "minimal",
        verbosity: Literal["low", "medium", "high"] | None = None,
        transport: Literal["compact_indexed_v1"] | None = None,
        transport_max_characters: int | None = None,
        node_question_max_characters: int | None = None,
        local_id_max_characters: int | None = None,
        max_dependencies_per_node: int | None = None,
        max_preferred_sources_per_node: int | None = None,
        preferred_source_max_characters: int | None = None,
        run_token_ceiling: int | None = None,
        reserved_follow_on_tokens: int = 0,
        generation_model: str | None = None,
        prompt_bundle: PromptBundle | None = None,
    ) -> None:
        if max_output_tokens <= 0:
            raise ValueError("Graph generation max_output_tokens must be positive")
        self.model = model
        self.max_output_tokens = int(max_output_tokens)
        self.max_completion_tokens = int(
            max_completion_tokens or max_output_tokens
        )
        self.max_visible_output_tokens = int(
            max_visible_output_tokens or max_output_tokens
        )
        if self.max_completion_tokens <= 0:
            raise ValueError(
                "Graph generation max_completion_tokens must be positive"
            )
        if self.max_visible_output_tokens <= 0:
            raise ValueError(
                "Graph generation max_visible_output_tokens must be positive"
            )
        if self.max_visible_output_tokens > self.max_completion_tokens:
            raise ValueError(
                "Graph generation visible output exceeds completion envelope"
            )
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.transport = transport
        self.transport_max_characters = transport_max_characters
        self.node_question_max_characters = node_question_max_characters
        self.local_id_max_characters = local_id_max_characters
        self.max_dependencies_per_node = max_dependencies_per_node
        self.max_preferred_sources_per_node = max_preferred_sources_per_node
        self.preferred_source_max_characters = preferred_source_max_characters
        if self.transport is not None and any(
            value is None
            for value in (
                self.transport_max_characters,
                self.node_question_max_characters,
                self.local_id_max_characters,
                self.max_dependencies_per_node,
                self.max_preferred_sources_per_node,
                self.preferred_source_max_characters,
            )
        ):
            raise ValueError("Compact graph transport requires explicit limits")
        self.run_token_ceiling = run_token_ceiling
        self.reserved_follow_on_tokens = max(0, int(reserved_follow_on_tokens))
        self.prompt_bundle = prompt_bundle
        model_name = str(getattr(model, "model", "unspecified"))
        self.generation_model = generation_model or f"{model.name}:{model_name}"

    def generate(
        self,
        contract: ForecastContract,
        *,
        version: int = 1,
        graph_id: str | None = None,
    ) -> ForecastGraphGenerationResult:
        if contract.status != "approved":
            raise ForecastGraphError(
                ["approved_forecast_contract_required"],
                "Approve the Forecast Contract before generating a Forecast Graph",
            )

        generated_at = utcnow()
        if self.prompt_bundle is not None:
            system, prompt_version = self.prompt_bundle.get("forecast_graph")
        else:
            system, prompt_version = load_prompt("forecast_graph")
        schema_name = (
            COMPACT_FORECAST_GRAPH_SCHEMA_NAME
            if self.transport == "compact_indexed_v1"
            else "forecast_graph"
        )
        json_schema = (
            compact_forecast_graph_json_schema(
                question_max_characters=int(self.node_question_max_characters),
                max_dependencies_per_node=int(self.max_dependencies_per_node),
                max_preferred_sources_per_node=int(
                    self.max_preferred_sources_per_node
                ),
                preferred_source_max_characters=int(
                    self.preferred_source_max_characters
                ),
            )
            if self.transport == "compact_indexed_v1"
            else forecast_graph_json_schema()
        )
        if self.transport == "compact_indexed_v1":
            system = _compact_transport_prompt(system)
        audit: dict[str, Any] = {
            "provider": self.model.name,
            "model": str(getattr(self.model, "model", "unspecified")),
            "schema_name": schema_name,
            "transport": self.transport or "canonical_v1",
            "transport_max_characters": self.transport_max_characters,
            "requested_max_output_tokens": self.max_output_tokens,
            "requested_max_completion_tokens": self.max_completion_tokens,
            "requested_max_visible_output_tokens": self.max_visible_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "verbosity": self.verbosity,
            "prompt_version": prompt_version,
            "generated_at": generated_at.isoformat(),
        }
        diagnostics_payload: dict[str, Any] = {}
        if self.model.name == "mock":
            payload: Any = (
                {"n": _mock_compact_nodes()}
                if self.transport == "compact_indexed_v1"
                else {"nodes": _mock_nodes(contract)}
            )
        else:
            user_payload = contract.model_dump_json()
            estimated_input_tokens = estimate_prompt_tokens(system, user_payload)
            if (
                self.run_token_ceiling is not None
                and estimated_input_tokens
                + self.max_completion_tokens
                + self.reserved_follow_on_tokens
                > self.run_token_ceiling
            ):
                raise ForecastGraphError(
                    ["graph_generation_workload_exceeds_run_token_budget"],
                    "Forecast Graph and minimum follow-on workload exceed the run token budget",
                    audit={
                        **audit,
                        "estimated_graph_input_tokens": estimated_input_tokens,
                        "reserved_follow_on_tokens": self.reserved_follow_on_tokens,
                        "run_token_ceiling": self.run_token_ceiling,
                    },
                )
            result = self.model.complete_json(
                system=system,
                user=user_payload,
                schema_name=schema_name,
                max_output_tokens=self.max_output_tokens,
                max_completion_tokens=self.max_completion_tokens,
                max_visible_output_tokens=self.max_visible_output_tokens,
                estimated_input_tokens=estimated_input_tokens,
                json_schema=json_schema,
                reasoning_effort=self.reasoning_effort,
                verbosity=self.verbosity,
            )
            audit["provider"] = result.usage.provider or audit["provider"]
            audit["model"] = result.usage.model or audit["model"]
            if result.diagnostics is not None:
                diagnostics_payload = result.diagnostics.audit_payload()
                audit["structured_output"] = diagnostics_payload
                if result.diagnostics.refusal_present:
                    raise ForecastGraphError(
                        ["structured_output_refused"],
                        "Forecast Graph structured output was refused",
                        audit=audit,
                    )
                visible_tokens = result.diagnostics.visible_output_tokens
                if (
                    visible_tokens is not None
                    and visible_tokens > self.max_visible_output_tokens
                ):
                    raise ForecastGraphError(
                        ["structured_output_visible_budget_exceeded"],
                        "Forecast Graph visible output exceeded its configured budget",
                        audit=audit,
                    )
                if (
                    self.transport == "compact_indexed_v1"
                    and self.transport_max_characters is not None
                    and len(result.content) > self.transport_max_characters
                ):
                    raise ForecastGraphError(
                        ["structured_output_transport_character_budget_exceeded"],
                        "Forecast Graph compact output exceeded its character budget",
                        audit=audit,
                    )
                if result.diagnostics.finish_reason == "length":
                    raise ForecastGraphError(
                        ["structured_output_truncated"],
                        "Forecast Graph structured output reached its output limit",
                        audit=audit,
                    )
            else:
                diagnostics_payload = {
                    "schema_name": schema_name,
                    "provider_request_id": result.usage.request_id,
                    "finish_reason": None,
                    "refusal_present": False,
                    "refusal_category": None,
                    "requested_max_output_tokens": self.max_output_tokens,
                    "requested_max_completion_tokens": self.max_completion_tokens,
                    "requested_max_visible_output_tokens": self.max_visible_output_tokens,
                    "reasoning_effort": self.reasoning_effort,
                    "verbosity": self.verbosity,
                    "completion_tokens": result.usage.completion_tokens,
                    "reasoning_tokens": None,
                    "visible_output_tokens": result.usage.completion_tokens,
                    "token_split_available": False,
                    "token_split_interpretation": (
                        "completion_tokens_used_as_visible_upper_bound"
                    ),
                    "content_character_count": len(result.content),
                    "json_parsing_succeeded": result.parsed is not None,
                    "strict_schema_validation_succeeded": None,
                    "schema_validation_errors": [],
                }
            if result.parsed is not None:
                payload = result.parsed
            elif not result.content.strip():
                raise ForecastGraphError(
                    ["structured_output_empty"],
                    "Forecast Graph structured output was empty",
                    audit=audit,
                )
            else:
                try:
                    payload = json.loads(result.content)
                    diagnostics_payload["json_parsing_succeeded"] = True
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ForecastGraphError(
                        ["structured_output_invalid_json"],
                        "Forecast Graph structured output was not valid JSON",
                        audit=audit,
                    ) from exc

        if self.transport == "compact_indexed_v1":
            compact_payload, compact_validation_errors = validate_structured_output(
                schema_name,
                payload,
            )
            if compact_payload is None:
                audit["schema_validation_errors"] = compact_validation_errors
                raise ForecastGraphError(
                    ["structured_output_schema_invalid"],
                    "Forecast Graph JSON did not match the compact transport schema",
                    audit=audit,
                )
            canonical_payload, transport_character_count, transport_errors = (
                _canonicalize_compact_graph(
                    compact_payload,
                    max_characters=int(self.transport_max_characters),
                    question_max_characters=int(
                        self.node_question_max_characters
                    ),
                    local_id_max_characters=int(self.local_id_max_characters),
                    max_dependencies_per_node=int(
                        self.max_dependencies_per_node
                    ),
                    max_preferred_sources_per_node=int(
                        self.max_preferred_sources_per_node
                    ),
                    preferred_source_max_characters=int(
                        self.preferred_source_max_characters
                    ),
                )
            )
            audit["transport_character_count"] = transport_character_count
            if canonical_payload is None:
                audit["schema_validation_errors"] = transport_errors
                raise ForecastGraphError(
                    ["structured_output_schema_invalid"],
                    "Forecast Graph compact transport failed validation",
                    audit=audit,
                )
            payload = canonical_payload

        validated_payload, validation_errors = validate_structured_output(
            "forecast_graph", payload
        )
        if validated_payload is None:
            audit["schema_validation_errors"] = validation_errors
            raise ForecastGraphError(
                ["structured_output_schema_invalid"],
                "Forecast Graph JSON did not match the required schema",
                audit=audit,
            )
        generated = ForecastGraphOutput.model_validate(validated_payload)

        if not 5 <= len(generated.nodes) <= 10:
            raise ForecastGraphError(
                ["graph_domain_validation_failed"],
                "Forecast Graph failed domain validation",
                audit={**audit, "domain_validation_errors": ["graph_node_count_must_be_between_5_and_10"]},
            )
        generated_types = {node.node_type for node in generated.nodes}
        missing_types = [
            reason
            for node_type, reason in (
                ("base_rate", "base_rate_node_required"),
                ("driver", "driver_node_required"),
                ("adversarial", "adversarial_node_required"),
                ("resolver", "resolver_node_required"),
            )
            if node_type not in generated_types
        ]
        if missing_types:
            raise ForecastGraphError(
                ["graph_domain_validation_failed"],
                "Forecast Graph failed domain validation",
                audit={**audit, "domain_validation_errors": missing_types},
            )

        source_ids = [node.id.strip() for node in generated.nodes]
        if any(not node_id for node_id in source_ids) or len(set(source_ids)) != len(source_ids):
            raise ForecastGraphError(
                ["graph_domain_validation_failed"],
                "Forecast Graph failed domain validation",
                audit={
                    **audit,
                    "domain_validation_errors": ["duplicate_or_missing_generated_node_id"],
                },
            )
        id_map = {node_id: str(uuid.uuid4()) for node_id in source_ids}

        unknown_references: set[str] = set()
        final_graph_id = graph_id or str(uuid.uuid4())
        nodes: list[ForecastNode] = []
        for generated_node, source_id in zip(generated.nodes, source_ids, strict=True):
            parent_id = generated_node.parent_node_id.strip() if generated_node.parent_node_id else None
            if parent_id and parent_id not in id_map:
                unknown_references.add(parent_id)
            dependency_ids: list[str] = []
            for dependency in generated_node.dependencies:
                clean_dependency = dependency.strip()
                if clean_dependency not in id_map:
                    unknown_references.add(clean_dependency)
                    continue
                dependency_ids.append(id_map[clean_dependency])
            nodes.append(
                ForecastNode(
                    id=id_map[source_id],
                    graph_id=final_graph_id,
                    parent_node_id=id_map.get(parent_id) if parent_id else None,
                    question=" ".join(generated_node.question.split()).strip(),
                    node_type=generated_node.node_type,
                    importance_weight=generated_node.importance_weight,
                    dependencies=list(dict.fromkeys(dependency_ids)),
                    preferred_sources=[source.strip() for source in generated_node.preferred_sources if source.strip()],
                    required_output_type=generated_node.required_output_type.strip(),
                    status="pending",
                )
            )
        if unknown_references:
            raise ForecastGraphError(
                ["graph_domain_validation_failed"],
                "Forecast Graph failed domain validation",
                audit={**audit, "domain_validation_errors": ["unknown_generated_node_reference"]},
            )

        graph = ForecastGraph(
            id=final_graph_id,
            contract_id=contract.id,
            version=version,
            status="approved",
            created_at=utcnow(),
            generation_model=self.generation_model,
            root_question=contract.normalized_question,
            nodes=nodes,
        )
        try:
            ensure_graph_approvable(graph)
        except ForecastGraphError as exc:
            raise ForecastGraphError(
                ["graph_domain_validation_failed"],
                "Forecast Graph failed domain validation",
                audit={**audit, "domain_validation_errors": exc.reasons},
            ) from exc
        generation_audit = ForecastGraphGenerationAudit(
            provider=str(audit["provider"]),
            model=str(audit["model"]),
            schema_name=schema_name,
            transport=self.transport or "canonical_v1",
            transport_character_count=audit.get("transport_character_count"),
            transport_max_characters=self.transport_max_characters,
            provider_request_id=diagnostics_payload.get("provider_request_id"),
            requested_max_output_tokens=self.max_output_tokens,
            requested_max_completion_tokens=self.max_completion_tokens,
            requested_max_visible_output_tokens=self.max_visible_output_tokens,
            reasoning_effort=self.reasoning_effort,
            verbosity=self.verbosity,
            finish_reason=diagnostics_payload.get("finish_reason"),
            refusal_present=bool(diagnostics_payload.get("refusal_present")),
            refusal_category=diagnostics_payload.get("refusal_category"),
            completion_tokens=(
                int(diagnostics_payload["completion_tokens"])
                if diagnostics_payload.get("completion_tokens") is not None
                else None
            ),
            reasoning_tokens=diagnostics_payload.get("reasoning_tokens"),
            visible_output_tokens=diagnostics_payload.get("visible_output_tokens"),
            token_split_available=diagnostics_payload.get(
                "token_split_available"
            ),
            token_split_interpretation=diagnostics_payload.get(
                "token_split_interpretation"
            ),
            content_character_count=int(diagnostics_payload.get("content_character_count") or 0),
            json_parsing_succeeded=(
                bool(diagnostics_payload.get("json_parsing_succeeded"))
                if diagnostics_payload
                else True
            ),
            schema_validation_succeeded=True,
            strict_schema_validation_succeeded=(
                diagnostics_payload.get("strict_schema_validation_succeeded")
                if diagnostics_payload
                else None
            ),
            schema_validation_errors=list(
                diagnostics_payload.get("schema_validation_errors") or []
            ),
            domain_validation_succeeded=True,
            domain_validation_errors=[],
            errors=[],
            prompt_version=prompt_version,
            generated_at=generated_at,
        )
        graph = graph.model_copy(update={"generation_audit": generation_audit})
        return ForecastGraphGenerationResult(
            graph=graph,
            generation_audit=generation_audit,
        )

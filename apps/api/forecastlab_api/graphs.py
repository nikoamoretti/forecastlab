from __future__ import annotations

import json

from sqlalchemy.orm import Session

from forecastlab.execution import resolve_execution_context
from forecastlab.graphs import GraphGenerator, ensure_graph_approvable
from forecastlab.profiles import load_profile
from forecastlab.providers.factory import build_model_provider
from forecastlab.research_planning import (
    MINIMUM_PLANNED_TOKENS_PER_NODE,
    MINIMUM_RESEARCH_NODES,
)
from forecastlab.schemas import ForecastGraph, ForecastGraphGenerationAudit, ForecastNode
from forecastlab.timeutil import as_utc
from forecastlab_api.models import ForecastGraphRow, ForecastNodeRow, Question
from forecastlab_api.pipeline import provider_settings_from_secrets
from forecastlab_api.secrets import load_secrets


def _json_list(raw: str) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _generation_audit(raw: str | None) -> ForecastGraphGenerationAudit | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid_forecast_graph_generation_audit_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_forecast_graph_generation_audit_type")
    try:
        return ForecastGraphGenerationAudit.model_validate(payload)
    except ValueError as exc:
        raise ValueError("invalid_forecast_graph_generation_audit_schema") from exc


def build_graph_generator(question: Question) -> GraphGenerator:
    secrets = load_secrets()
    context = resolve_execution_context(
        requested_mode=question.requested_mode,  # type: ignore[arg-type]
        profile_id=question.requested_profile_id,
        settings=provider_settings_from_secrets(secrets),
        as_of=question.requested_as_of,
    )
    model = build_model_provider(
        provider=context.model_provider,
        api_key=secrets.get("model_api_key"),
        base_url=context.model_base_url,
        model=context.model_name,
        timeout=float(context.model_timeout_seconds or 60),
        execution=context,
    )
    profile = load_profile(context.profile_id)
    return GraphGenerator(
        model,
        max_output_tokens=profile.max_output_tokens_per_call,
        max_completion_tokens=(
            profile.graph_generation_max_completion_tokens
            or profile.max_output_tokens_per_call
        ),
        max_visible_output_tokens=(
            profile.graph_generation_max_visible_output_tokens
            or profile.max_output_tokens_per_call
        ),
        reasoning_effort=(
            profile.graph_generation_reasoning_effort or "minimal"
        ),
        verbosity=profile.graph_generation_verbosity,
        transport=profile.graph_generation_transport,
        transport_max_characters=(
            profile.graph_generation_transport_max_characters
        ),
        node_question_max_characters=(
            profile.graph_generation_node_question_max_characters
        ),
        local_id_max_characters=(
            profile.graph_generation_local_id_max_characters
        ),
        max_dependencies_per_node=(
            profile.graph_generation_max_dependencies_per_node
        ),
        max_preferred_sources_per_node=(
            profile.graph_generation_max_preferred_sources_per_node
        ),
        preferred_source_max_characters=(
            profile.graph_generation_preferred_source_max_characters
        ),
        run_token_ceiling=profile.max_tokens,
        reserved_follow_on_tokens=(
            MINIMUM_RESEARCH_NODES * MINIMUM_PLANNED_TOKENS_PER_NODE
        ),
        generation_model=f"{context.model_provider}:{context.model_name}",
    )


def forecast_graph_from_row(row: ForecastGraphRow) -> ForecastGraph:
    nodes = [forecast_node_from_row(node) for node in row.nodes]
    return ForecastGraph(
        id=row.id,
        contract_id=row.contract_id,
        version=row.version,
        status=row.status,  # type: ignore[arg-type]
        created_at=as_utc(row.created_at),
        generation_model=row.generation_model,
        root_question=row.root_question,
        nodes=nodes,
        generation_audit=_generation_audit(row.generation_audit_json),
    )


def forecast_node_from_row(node: ForecastNodeRow) -> ForecastNode:
    return ForecastNode(
        id=node.id,
        graph_id=node.graph_id,
        parent_node_id=node.parent_node_id,
        question=node.question,
        node_type=node.node_type,  # type: ignore[arg-type]
        importance_weight=node.importance_weight,
        dependencies=_json_list(node.dependencies_json),
        preferred_sources=_json_list(node.preferred_sources_json),
        required_output_type=node.required_output_type,
        status=node.status,  # type: ignore[arg-type]
    )


def store_forecast_graph(session: Session, graph: ForecastGraph) -> ForecastGraphRow:
    if graph.status == "approved":
        ensure_graph_approvable(graph)
    row = ForecastGraphRow(
        id=graph.id,
        contract_id=graph.contract_id,
        version=graph.version,
        status=graph.status,
        created_at=graph.created_at,
        generation_model=graph.generation_model,
        root_question=graph.root_question,
        generation_audit_json=(
            json.dumps(graph.generation_audit.model_dump(mode="json"), sort_keys=True)
            if graph.generation_audit is not None
            else None
        ),
    )
    session.add(row)
    session.flush()

    node_rows: dict[str, ForecastNodeRow] = {}
    for node in graph.nodes:
        node_row = ForecastNodeRow(
            id=node.id,
            graph_id=graph.id,
            parent_node_id=None,
            question=node.question,
            node_type=node.node_type,
            importance_weight=node.importance_weight,
            dependencies_json=json.dumps(node.dependencies),
            preferred_sources_json=json.dumps(node.preferred_sources),
            required_output_type=node.required_output_type,
            status=node.status,
        )
        node_rows[node.id] = node_row
        session.add(node_row)
    session.flush()
    for node in graph.nodes:
        node_rows[node.id].parent_node_id = node.parent_node_id
    session.flush()
    return row

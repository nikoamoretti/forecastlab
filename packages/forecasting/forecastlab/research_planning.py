from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forecastlab.budget import Budget
from forecastlab.schemas import ForecastGraph, ForecastNode
from forecastlab.timeutil import utcnow

MAX_RESEARCH_NODES = 8
MAX_SEARCHES_PER_NODE = 5
MAX_EVIDENCE_CLAIMS = 20
MINIMUM_RESEARCH_NODES = 3
CRITICAL_IMPORTANCE_THRESHOLD = 0.8
PLANNER_VERSION = "graph_research_planner_v2"
RESEARCH_PLAN_CALLS_PER_NODE = 1
NODE_FORECAST_CALLS_PER_NODE = 1
PARALLEL_RESEARCH_WORKERS = 4

_UNCERTAINTY_BY_NODE_TYPE = {
    "base_rate": 0.35,
    "trend": 0.65,
    "driver": 0.75,
    "dependency": 0.7,
    "scenario": 1.0,
    "adversarial": 0.9,
    "resolver": 0.45,
}


@dataclass(frozen=True)
class _ResourceTier:
    name: str
    evidence_document_max_chars: int
    research_plan_output_tokens: int
    evidence_extraction_output_tokens: int
    node_forecast_output_tokens: int
    research_plan_input_tokens: int
    evidence_extraction_input_tokens: int
    node_forecast_input_tokens: int


_RESOURCE_TIERS = (
    _ResourceTier("standard", 8000, 1024, 1536, 1536, 1200, 3000, 2800),
    _ResourceTier("compact", 6000, 768, 1024, 1024, 1000, 2200, 2400),
    _ResourceTier("constrained", 4000, 512, 768, 768, 800, 1600, 2200),
)


class ResearchPlanningError(ValueError):
    def __init__(
        self,
        reasons: list[str],
        message: str,
        *,
        audit: dict[str, Any] | None = None,
    ) -> None:
        self.reasons = list(dict.fromkeys(reasons))
        self.audit = audit or {}
        super().__init__(message)


class ResearchPlan(BaseModel):
    """A frozen, budget-aware selection of graph nodes to research for one run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    forecast_run_id: str = Field(min_length=1)
    selected_nodes: list[str] = Field(min_length=1)
    skipped_nodes: list[str] = Field(default_factory=list)
    priority_scores: dict[str, float]
    budget_allocation: dict[str, Any]
    created_at: datetime

    @model_validator(mode="after")
    def validate_node_partition(self) -> ResearchPlan:
        selected = set(self.selected_nodes)
        skipped = set(self.skipped_nodes)
        if len(selected) != len(self.selected_nodes):
            raise ValueError("duplicate_selected_research_node")
        if len(skipped) != len(self.skipped_nodes):
            raise ValueError("duplicate_skipped_research_node")
        if selected & skipped:
            raise ValueError("research_node_selected_and_skipped")
        if set(self.priority_scores) != selected | skipped:
            raise ValueError("research_priority_score_coverage_mismatch")
        return self


def _normalized_question(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


def _related_node_ids(node: ForecastNode) -> set[str]:
    related = set(node.dependencies)
    if node.parent_node_id:
        related.add(node.parent_node_id)
    return related


def _topological_node_ids(graph: ForecastGraph) -> list[str]:
    graph_order = {node.id: index for index, node in enumerate(graph.nodes)}
    known_ids = set(graph_order)
    dependencies = {
        node.id: {
            dependency
            for dependency in _related_node_ids(node)
            if dependency in known_ids
        }
        for node in graph.nodes
    }
    ordered: list[str] = []
    ready = [
        node.id
        for node in graph.nodes
        if not dependencies[node.id]
    ]
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for candidate in graph.nodes:
            if node_id not in dependencies[candidate.id]:
                continue
            dependencies[candidate.id].remove(node_id)
            if not dependencies[candidate.id] and candidate.id not in ordered:
                if candidate.id not in ready:
                    ready.append(candidate.id)
    if len(ordered) != len(graph.nodes):
        raise ResearchPlanningError(
            ["forecast_graph_dependency_cycle"],
            "The Forecast Graph dependency order is not executable",
        )
    return ordered


class ResearchPlanner:
    """Rank graph nodes and freeze a workload that can complete within run limits."""

    def __init__(
        self,
        *,
        max_researched_nodes: int = MAX_RESEARCH_NODES,
        max_searches_per_node: int = MAX_SEARCHES_PER_NODE,
        max_evidence_claims: int = MAX_EVIDENCE_CLAIMS,
        minimum_research_nodes: int = MINIMUM_RESEARCH_NODES,
        extraction_retries_enabled: bool = True,
    ) -> None:
        self.max_researched_nodes = max(1, max_researched_nodes)
        self.max_searches_per_node = max(1, max_searches_per_node)
        self.max_evidence_claims = max(1, max_evidence_claims)
        self.minimum_research_nodes = max(1, minimum_research_nodes)
        self.extraction_retries_enabled = bool(extraction_retries_enabled)

    @staticmethod
    def _priority_components(graph: ForecastGraph) -> dict[str, dict[str, float]]:
        related = {node.id: _related_node_ids(node) for node in graph.nodes}
        components: dict[str, dict[str, float]] = {}
        for node in graph.nodes:
            dependent_count = sum(node.id in values for values in related.values())
            dependency_count = len(related[node.id]) + dependent_count
            uncertainty = _UNCERTAINTY_BY_NODE_TYPE[node.node_type]
            components[node.id] = {
                "importance_weight": float(node.importance_weight),
                "dependency_count": float(dependency_count),
                "uncertainty_score": uncertainty,
                "priority_score": round(
                    float(node.importance_weight) + dependency_count + uncertainty,
                    12,
                ),
            }
        return components

    @staticmethod
    def _ranked_nodes(
        graph: ForecastGraph,
        components: dict[str, dict[str, float]],
    ) -> list[ForecastNode]:
        graph_order = {node.id: index for index, node in enumerate(graph.nodes)}
        return sorted(
            graph.nodes,
            key=lambda node: (
                -components[node.id]["priority_score"],
                -float(node.importance_weight),
                graph_order[node.id],
                node.id,
            ),
        )

    @staticmethod
    def _critical_nodes(nodes: list[ForecastNode]) -> list[ForecastNode]:
        return [
            node
            for node in nodes
            if float(node.importance_weight) >= CRITICAL_IMPORTANCE_THRESHOLD
        ]

    @staticmethod
    def _deduplicate_noncritical_nodes(
        ranked: list[ForecastNode],
        critical_ids: set[str],
    ) -> tuple[list[ForecastNode], dict[str, str]]:
        retained: list[ForecastNode] = []
        canonical_by_question: dict[str, str] = {}
        skipped: dict[str, str] = {}
        for node in ranked:
            key = _normalized_question(node.question)
            duplicate_of = canonical_by_question.get(key)
            if duplicate_of is not None and node.id not in critical_ids:
                skipped[node.id] = f"duplicate_research_question:{duplicate_of}"
                continue
            retained.append(node)
            canonical_by_question.setdefault(key, node.id)
        return retained, skipped

    @staticmethod
    def _estimated_per_node(
        budget: Budget,
        *,
        tier: _ResourceTier,
        searches_per_node: int,
        fetches_per_node: int,
        extraction_retries_enabled: bool,
    ) -> dict[str, Any]:
        research_plan_calls = RESEARCH_PLAN_CALLS_PER_NODE
        primary_extraction_calls = fetches_per_node
        extraction_retry_calls = (
            fetches_per_node if extraction_retries_enabled else 0
        )
        node_forecast_calls = NODE_FORECAST_CALLS_PER_NODE
        total_model_calls = (
            research_plan_calls
            + primary_extraction_calls
            + extraction_retry_calls
            + node_forecast_calls
        )
        call_specs = {
            "research_plan": (
                research_plan_calls,
                tier.research_plan_input_tokens,
                tier.research_plan_output_tokens,
            ),
            "primary_extraction": (
                primary_extraction_calls,
                tier.evidence_extraction_input_tokens,
                tier.evidence_extraction_output_tokens,
            ),
            # Use the primary extraction estimate for each bounded retry. The
            # smaller chunk is expected to be cheaper, so this remains a
            # conservative frozen upper bound without inventing provider use.
            "extraction_retry": (
                extraction_retry_calls,
                tier.evidence_extraction_input_tokens,
                tier.evidence_extraction_output_tokens,
            ),
            "node_forecast": (
                node_forecast_calls,
                tier.node_forecast_input_tokens,
                tier.node_forecast_output_tokens,
            ),
        }
        estimated_tokens_by_phase = {
            phase: count * (input_tokens + output_tokens)
            for phase, (count, input_tokens, output_tokens) in call_specs.items()
        }
        estimated_cost_by_phase = {
            phase: round(
                count
                * budget.estimate_model_cost(input_tokens, output_tokens),
                12,
            )
            for phase, (count, input_tokens, output_tokens) in call_specs.items()
        }
        model_cost = math.fsum(estimated_cost_by_phase.values())
        search_cost, search_cost_label = budget.estimate_search_charge()
        estimated_search_cost = round(search_cost * searches_per_node, 12)
        tokens = sum(estimated_tokens_by_phase.values())
        return {
            "resource_tier": tier.name,
            # model_calls is retained for existing report compatibility.
            "model_calls": total_model_calls,
            "research_plan_calls": research_plan_calls,
            "primary_extraction_calls": primary_extraction_calls,
            "extraction_retry_calls": extraction_retry_calls,
            "node_forecast_calls": node_forecast_calls,
            "total_model_calls": total_model_calls,
            "searches": searches_per_node,
            "fetches": fetches_per_node,
            "extraction_fallbacks_enabled": extraction_retries_enabled,
            "estimated_tokens": tokens,
            "estimated_tokens_by_phase": estimated_tokens_by_phase,
            "estimated_model_cost_usd": round(model_cost, 12),
            "estimated_search_cost_usd": estimated_search_cost,
            "estimated_cost_by_phase_usd": {
                **estimated_cost_by_phase,
                "search": estimated_search_cost,
            },
            "estimated_cost_usd": round(
                model_cost + estimated_search_cost,
                12,
            ),
            "search_cost_label": search_cost_label,
            "evidence_document_max_chars": tier.evidence_document_max_chars,
            "research_plan_output_tokens": tier.research_plan_output_tokens,
            "evidence_extraction_output_tokens": (
                tier.evidence_extraction_output_tokens
            ),
            "node_forecast_output_tokens": tier.node_forecast_output_tokens,
        }

    @staticmethod
    def _remaining_budget(budget: Budget) -> dict[str, int | float]:
        elapsed = float(budget.snapshot()["elapsed_seconds"])
        return {
            "model_calls": max(
                0,
                budget.profile.max_model_calls - budget.state.model_calls,
            ),
            "search_calls": max(
                0,
                budget.profile.max_search_calls - budget.state.search_calls,
            ),
            "fetches": max(
                0,
                budget.profile.max_fetched_documents - budget.state.fetches,
            ),
            "tokens": max(0, budget.profile.max_tokens - budget.state.tokens),
            "cost_usd": max(
                0.0,
                budget.profile.max_estimated_cost_usd - budget.state.cost_usd,
            ),
            "wall_clock_seconds": max(
                0.0,
                budget.profile.max_wall_clock_seconds - elapsed,
            ),
        }

    @staticmethod
    def _fits(
        *,
        node_count: int,
        per_node: dict[str, Any],
        remaining: dict[str, int | float],
    ) -> bool:
        parallel_batches = math.ceil(node_count / PARALLEL_RESEARCH_WORKERS)
        estimated_seconds = parallel_batches * 30 + node_count * 12
        return bool(
            node_count * int(per_node["total_model_calls"])
            <= remaining["model_calls"]
            and node_count * int(per_node["searches"])
            <= remaining["search_calls"]
            and node_count * int(per_node["fetches"]) <= remaining["fetches"]
            and node_count * int(per_node["estimated_tokens"]) <= remaining["tokens"]
            and node_count * float(per_node["estimated_cost_usd"])
            <= float(remaining["cost_usd"]) + 1e-12
            and estimated_seconds <= float(remaining["wall_clock_seconds"])
        )

    def _select_allocation(
        self,
        *,
        candidate_count: int,
        required_count: int,
        budget: Budget,
        remaining: dict[str, int | float],
    ) -> tuple[int, dict[str, Any]] | None:
        max_searches = min(
            self.max_searches_per_node,
            budget.profile.max_search_calls,
        )
        max_fetches = min(
            budget.profile.fetches_per_subquestion,
            budget.profile.max_fetched_documents,
        )
        if max_searches < 1 or max_fetches < 1:
            return None
        for count in range(candidate_count, required_count - 1, -1):
            for searches in range(max_searches, 0, -1):
                for fetches in range(max_fetches, 0, -1):
                    for tier in _RESOURCE_TIERS:
                        per_node = self._estimated_per_node(
                            budget,
                            tier=tier,
                            searches_per_node=searches,
                            fetches_per_node=fetches,
                            extraction_retries_enabled=(
                                self.extraction_retries_enabled
                            ),
                        )
                        if self._fits(
                            node_count=count,
                            per_node=per_node,
                            remaining=remaining,
                        ):
                            return count, per_node
        return None

    def plan(
        self,
        graph: ForecastGraph,
        *,
        forecast_run_id: str,
        budget: Budget,
        created_at: datetime | None = None,
    ) -> ResearchPlan:
        if not graph.nodes:
            raise ResearchPlanningError(
                ["research_graph_has_no_nodes"],
                "The Forecast Graph has no nodes to research",
            )
        components = self._priority_components(graph)
        scores = {
            node_id: values["priority_score"]
            for node_id, values in components.items()
        }
        ranked = self._ranked_nodes(graph, components)
        critical = self._critical_nodes(ranked)
        critical_ids = {node.id for node in critical}
        if len(critical) > self.max_researched_nodes:
            raise ResearchPlanningError(
                ["critical_nodes_exceed_research_node_limit"],
                "Critical graph nodes exceed the maximum research-node limit",
            )
        if len(critical) > self.max_evidence_claims:
            raise ResearchPlanningError(
                ["critical_nodes_exceed_evidence_claim_limit"],
                "Critical graph nodes exceed the total Evidence Claim limit",
            )

        candidates, skipped_reasons = self._deduplicate_noncritical_nodes(
            ranked,
            critical_ids,
        )
        required_count = max(
            len(critical),
            min(self.minimum_research_nodes, len(candidates)),
        )
        candidate_count = min(
            self.max_researched_nodes,
            self.max_evidence_claims,
            len(candidates),
        )
        remaining = self._remaining_budget(budget)

        if (
            budget.provider != "mock"
            and budget.estimate_model_cost(1000, 1000) <= 0
        ):
            raise ResearchPlanningError(
                ["research_cost_estimate_unavailable"],
                "A model cost estimate is required before graph research can start",
            )
        search_cost, search_label = budget.estimate_search_charge()
        if budget.search_provider != "mock" and search_label == "unavailable":
            raise ResearchPlanningError(
                ["research_search_cost_estimate_unavailable"],
                "A search cost estimate is required before graph research can start",
            )

        allocation = self._select_allocation(
            candidate_count=candidate_count,
            required_count=required_count,
            budget=budget,
            remaining=remaining,
        )
        if allocation is None:
            minimum_per_node = self._estimated_per_node(
                budget,
                tier=_RESOURCE_TIERS[-1],
                searches_per_node=1,
                fetches_per_node=1,
                extraction_retries_enabled=self.extraction_retries_enabled,
            )
            minimum_model_calls = (
                required_count * int(minimum_per_node["total_model_calls"])
            )
            critical_model_calls = (
                len(critical) * int(minimum_per_node["total_model_calls"])
            )
            call_envelope_audit = {
                "planner_version": PLANNER_VERSION,
                "required_node_count": required_count,
                "critical_node_count": len(critical),
                "model_calls_available_before_research": int(
                    remaining["model_calls"]
                ),
                "minimum_required_model_calls": minimum_model_calls,
                "critical_required_model_calls": critical_model_calls,
                "minimum_per_node_call_envelope": {
                    "research_plan_calls": minimum_per_node[
                        "research_plan_calls"
                    ],
                    "primary_extraction_calls": minimum_per_node[
                        "primary_extraction_calls"
                    ],
                    "extraction_retry_calls": minimum_per_node[
                        "extraction_retry_calls"
                    ],
                    "node_forecast_calls": minimum_per_node[
                        "node_forecast_calls"
                    ],
                    "total_model_calls": minimum_per_node[
                        "total_model_calls"
                    ],
                },
                "available_before_research": remaining,
            }
            critical_allocation = self._select_allocation(
                candidate_count=len(critical),
                required_count=len(critical),
                budget=budget,
                remaining=remaining,
            ) if critical else (0, {})
            if critical and critical_allocation is None:
                reasons = ["critical_nodes_exceed_budget"]
                message = "Critical graph nodes cannot be completed within the remaining run budget"
            elif minimum_model_calls > int(remaining["model_calls"]):
                reasons = ["minimum_graph_execution_exceeds_model_call_budget"]
                message = (
                    "The minimum executable graph exceeds the remaining logical "
                    "model-call budget"
                )
            else:
                reasons = ["minimum_research_nodes_exceed_budget"]
                message = "The minimum research set cannot be completed within the remaining run budget"
            raise ResearchPlanningError(
                reasons,
                message,
                audit=call_envelope_audit,
            )

        selected_count, per_node = allocation
        selected_ids: list[str] = [node.id for node in critical]
        selected_ids.extend(
            node.id
            for node in candidates
            if node.id not in critical_ids
            and node.id not in selected_ids
        )
        selected_ids = selected_ids[:selected_count]
        selected_set = set(selected_ids)
        for node in graph.nodes:
            if node.id not in selected_set:
                skipped_reasons.setdefault(node.id, "lower_priority_or_budget_limited")
        skipped_ids = [node.id for node in graph.nodes if node.id not in selected_set]
        persistence_order = [
            node_id
            for node_id in _topological_node_ids(graph)
            if node_id in selected_set
        ]

        claims_remaining = self.max_evidence_claims
        per_node_allocations: dict[str, dict[str, Any]] = {}
        for index, node_id in enumerate(selected_ids):
            nodes_left = len(selected_ids) - index
            claims = max(1, claims_remaining // nodes_left)
            claims = min(claims, claims_remaining)
            claims_remaining -= claims
            per_node_allocations[node_id] = {
                **per_node,
                "max_evidence_claims": claims,
            }

        estimated_cost = round(
            selected_count * float(per_node["estimated_cost_usd"]),
            12,
        )
        planned_calls_by_kind = {
            "research_plan": selected_count
            * int(per_node["research_plan_calls"]),
            "primary_extraction": selected_count
            * int(per_node["primary_extraction_calls"]),
            "extraction_retry": selected_count
            * int(per_node["extraction_retry_calls"]),
            "node_forecast": selected_count
            * int(per_node["node_forecast_calls"]),
        }
        planned_research_phase_model_calls = sum(
            count
            for phase, count in planned_calls_by_kind.items()
            if phase != "node_forecast"
        )
        planned_total_model_calls = sum(planned_calls_by_kind.values())
        estimated_tokens_by_phase = {
            phase: selected_count * int(tokens)
            for phase, tokens in dict(
                per_node["estimated_tokens_by_phase"]
            ).items()
        }
        estimated_cost_by_phase = {
            phase: round(selected_count * float(cost), 12)
            for phase, cost in dict(
                per_node["estimated_cost_by_phase_usd"]
            ).items()
        }
        return ResearchPlan(
            id=str(uuid.uuid4()),
            forecast_run_id=forecast_run_id,
            selected_nodes=selected_ids,
            skipped_nodes=skipped_ids,
            priority_scores=scores,
            budget_allocation={
                "planner_version": PLANNER_VERSION,
                "research_plan_calls_per_node": int(
                    per_node["research_plan_calls"]
                ),
                "primary_extraction_calls_per_node": int(
                    per_node["primary_extraction_calls"]
                ),
                "extraction_retry_calls_per_node": int(
                    per_node["extraction_retry_calls"]
                ),
                "node_forecast_calls_per_node": int(
                    per_node["node_forecast_calls"]
                ),
                "total_model_calls_per_node": int(
                    per_node["total_model_calls"]
                ),
                "selected_node_count": selected_count,
                "planned_calls_by_kind": planned_calls_by_kind,
                "planned_research_phase_model_calls": (
                    planned_research_phase_model_calls
                ),
                "reserved_node_forecast_calls": planned_calls_by_kind[
                    "node_forecast"
                ],
                "planned_total_model_calls": planned_total_model_calls,
                "model_calls_available_before_research": int(
                    remaining["model_calls"]
                ),
                "model_call_headroom_after_plan": int(
                    remaining["model_calls"]
                ) - planned_total_model_calls,
                "extraction_fallbacks_enabled": bool(
                    per_node["extraction_fallbacks_enabled"]
                ),
                "allocated_fetches_per_node": int(per_node["fetches"]),
                "allocated_searches_per_node": int(per_node["searches"]),
                "estimated_tokens_by_phase": estimated_tokens_by_phase,
                "estimated_cost_by_phase": estimated_cost_by_phase,
                "limits": {
                    "maximum_researched_nodes": self.max_researched_nodes,
                    "maximum_searches_per_node": self.max_searches_per_node,
                    "maximum_evidence_claims_total": self.max_evidence_claims,
                    "minimum_research_nodes": self.minimum_research_nodes,
                    "critical_importance_threshold": CRITICAL_IMPORTANCE_THRESHOLD,
                },
                "available_before_research": remaining,
                "skipped_node_count": len(skipped_ids),
                "critical_node_ids": [node.id for node in critical],
                "priority_components": components,
                "skipped_reasons": skipped_reasons,
                "per_node": per_node_allocations,
                "estimated_total_cost_usd": estimated_cost,
                "estimated_search_cost_per_call_usd": search_cost,
                "estimated_search_cost_label": search_label,
                "parallel_research_workers": min(
                    PARALLEL_RESEARCH_WORKERS,
                    selected_count,
                ),
                "deterministic_persistence_order": [
                    *persistence_order,
                ],
            },
            created_at=created_at or utcnow(),
        )

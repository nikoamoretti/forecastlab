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
MODEL_CALLS_PER_NODE = 3
FETCHES_PER_NODE = 1
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
    def __init__(self, reasons: list[str], message: str) -> None:
        self.reasons = list(dict.fromkeys(reasons))
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
    ) -> None:
        self.max_researched_nodes = max(1, max_researched_nodes)
        self.max_searches_per_node = max(1, max_searches_per_node)
        self.max_evidence_claims = max(1, max_evidence_claims)
        self.minimum_research_nodes = max(1, minimum_research_nodes)

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
    ) -> dict[str, int | float | str]:
        calls = (
            (
                tier.research_plan_input_tokens,
                tier.research_plan_output_tokens,
            ),
            (
                tier.evidence_extraction_input_tokens,
                tier.evidence_extraction_output_tokens,
            ),
            (
                tier.node_forecast_input_tokens,
                tier.node_forecast_output_tokens,
            ),
        )
        model_cost = math.fsum(
            budget.estimate_model_cost(input_tokens, output_tokens)
            for input_tokens, output_tokens in calls
        )
        search_cost, search_cost_label = budget.estimate_search_charge()
        tokens = sum(input_tokens + output_tokens for input_tokens, output_tokens in calls)
        return {
            "resource_tier": tier.name,
            "model_calls": MODEL_CALLS_PER_NODE,
            "searches": searches_per_node,
            "fetches": FETCHES_PER_NODE,
            "estimated_tokens": tokens,
            "estimated_model_cost_usd": round(model_cost, 12),
            "estimated_search_cost_usd": round(search_cost * searches_per_node, 12),
            "estimated_cost_usd": round(
                model_cost + search_cost * searches_per_node,
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
        per_node: dict[str, int | float | str],
        remaining: dict[str, int | float],
    ) -> bool:
        parallel_batches = math.ceil(node_count / PARALLEL_RESEARCH_WORKERS)
        estimated_seconds = parallel_batches * 30 + node_count * 12
        return bool(
            node_count * int(per_node["model_calls"]) <= remaining["model_calls"]
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
    ) -> tuple[int, dict[str, int | float | str]] | None:
        max_searches = min(
            self.max_searches_per_node,
            budget.profile.max_search_calls,
        )
        for count in range(candidate_count, required_count - 1, -1):
            for searches in range(max_searches, 0, -1):
                for tier in _RESOURCE_TIERS:
                    per_node = self._estimated_per_node(
                        budget,
                        tier=tier,
                        searches_per_node=searches,
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
            critical_allocation = self._select_allocation(
                candidate_count=len(critical),
                required_count=len(critical),
                budget=budget,
                remaining=remaining,
            ) if critical else (0, {})
            if critical and critical_allocation is None:
                reasons = ["critical_nodes_exceed_budget"]
                message = "Critical graph nodes cannot be completed within the remaining run budget"
            else:
                reasons = ["minimum_research_nodes_exceed_budget"]
                message = "The minimum research set cannot be completed within the remaining run budget"
            raise ResearchPlanningError(reasons, message)

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
        per_node_allocations: dict[str, dict[str, int | float | str]] = {}
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
        return ResearchPlan(
            id=str(uuid.uuid4()),
            forecast_run_id=forecast_run_id,
            selected_nodes=selected_ids,
            skipped_nodes=skipped_ids,
            priority_scores=scores,
            budget_allocation={
                "limits": {
                    "maximum_researched_nodes": self.max_researched_nodes,
                    "maximum_searches_per_node": self.max_searches_per_node,
                    "maximum_evidence_claims_total": self.max_evidence_claims,
                    "minimum_research_nodes": self.minimum_research_nodes,
                    "critical_importance_threshold": CRITICAL_IMPORTANCE_THRESHOLD,
                },
                "available_before_research": remaining,
                "selected_node_count": selected_count,
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

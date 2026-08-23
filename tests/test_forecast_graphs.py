from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from forecastlab.graphs import ForecastGraphError, GraphGenerator, graph_approval_errors
from forecastlab.prompts import PromptBundle, PromptRecord
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import ForecastContract, ForecastGraph, ForecastNode, ModelUsage


class StubGraphModel:
    name = "stub"
    model = "stub-graph-v1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_request: dict[str, Any] | None = None

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls += 1
        self.last_request = kwargs
        return ChatResult(
            content="{}",
            parsed=self.payload,
            usage=ModelUsage(model=self.model, provider=self.name),
        )


def approved_contract() -> ForecastContract:
    return ForecastContract(
        id="contract-1",
        question_id="question-1",
        version=1,
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        created_by="test",
        original_question="Will US unemployment exceed 5% before June 2027?",
        normalized_question="Will the US BLS U-3 unemployment rate exceed 5% before 30 June 2027?",
        yes_condition="BLS reports U-3 above 5.0% before the deadline.",
        no_condition="BLS does not report U-3 above 5.0% before the deadline.",
        resolution_date=datetime(2027, 6, 30, tzinfo=UTC),
        authoritative_source="Bureau of Labor Statistics",
        resolution_method="Check each official U-3 release through the deadline.",
        initial_reference_class="Postwar US expansions beginning from U-3 below 5%.",
        suggested_drivers=["labor demand", "monetary policy"],
        known_dependencies=["claims and payrolls share business-cycle causes"],
        status="approved",
    )


def valid_graph_payload() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "base",
                "question": "What is the historical frequency of crossing 5% from comparable starting points?",
                "node_type": "base_rate",
                "importance_weight": 0.9,
                "dependencies": [],
                "preferred_sources": ["BLS historical series"],
                "required_output_type": "probability",
            },
            {
                "id": "trend",
                "question": "What direction do current labor-market indicators show?",
                "node_type": "trend",
                "importance_weight": 0.8,
                "dependencies": [],
                "preferred_sources": ["BLS", "DOL"],
                "required_output_type": "directional_update",
            },
            {
                "id": "driver",
                "question": "How would labor demand move U-3 through the threshold?",
                "node_type": "driver",
                "importance_weight": 0.85,
                "dependencies": ["trend"],
                "preferred_sources": ["JOLTS"],
                "required_output_type": "directional_update",
            },
            {
                "id": "scenario",
                "parent_node_id": "driver",
                "question": "Which recession scenario would produce a threshold breach?",
                "node_type": "scenario",
                "importance_weight": 0.7,
                "dependencies": ["trend"],
                "preferred_sources": ["NBER chronology"],
                "required_output_type": "scenario_weight",
            },
            {
                "id": "adversarial",
                "question": "Which evidence most strongly contradicts the leading labor-market view?",
                "node_type": "adversarial",
                "importance_weight": 0.75,
                "dependencies": ["scenario"],
                "preferred_sources": ["contradictory primary indicators"],
                "required_output_type": "directional_update",
            },
            {
                "id": "resolver",
                "question": "Which BLS revision or release-date rule could change resolution?",
                "node_type": "resolver",
                "importance_weight": 0.6,
                "dependencies": [],
                "preferred_sources": ["BLS methodology"],
                "required_output_type": "structured_categorical",
            },
        ]
    }


def test_graph_generation_from_approved_contract() -> None:
    model = StubGraphModel(valid_graph_payload())
    graph = GraphGenerator(model).generate(approved_contract())

    assert model.calls == 1
    assert graph.status == "approved"
    assert graph.contract_id == "contract-1"
    assert graph.generation_model == "stub:stub-graph-v1"
    assert graph.root_question == approved_contract().normalized_question
    assert len(graph.nodes) == 6
    assert {"base_rate", "driver", "adversarial", "resolver"} <= {
        node.node_type for node in graph.nodes
    }
    assert all(node.graph_id == graph.id for node in graph.nodes)
    assert all(node.status == "pending" for node in graph.nodes)
    assert graph_approval_errors(graph) == []


def test_graph_generation_uses_frozen_experiment_prompt_bundle() -> None:
    model = StubGraphModel(valid_graph_payload())
    bundle = PromptBundle(
        prompts={
            "forecast_graph": PromptRecord(
                name="forecast_graph",
                text="FROZEN FORECAST GRAPH PROMPT",
                version="test-frozen",
                sha256="a" * 64,
            )
        }
    )

    GraphGenerator(model, prompt_bundle=bundle).generate(approved_contract())

    assert model.last_request is not None
    assert model.last_request["system"] == "FROZEN FORECAST GRAPH PROMPT"


def test_graph_approval_rejects_too_few_nodes() -> None:
    graph = ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        generation_model="stub:model",
        root_question="Will the outcome occur?",
        nodes=[
            ForecastNode(
                id="adversarial",
                graph_id="graph-1",
                question="What could invalidate the leading view?",
                node_type="adversarial",
                importance_weight=0.5,
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="resolver",
                graph_id="graph-1",
                question="How will the source resolve the outcome?",
                node_type="resolver",
                importance_weight=0.5,
                required_output_type="structured_categorical",
            ),
        ],
    )

    assert "minimum_three_nodes_required" in graph_approval_errors(graph)


def test_graph_approval_detects_dependency_cycle() -> None:
    graph = ForecastGraph(
        id="graph-1",
        contract_id="contract-1",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        generation_model="stub:model",
        root_question="Will the outcome occur?",
        nodes=[
            ForecastNode(
                id="driver",
                graph_id="graph-1",
                question="What is the main driver?",
                node_type="driver",
                importance_weight=0.8,
                dependencies=["adversarial"],
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="adversarial",
                graph_id="graph-1",
                question="What could overturn the driver?",
                node_type="adversarial",
                importance_weight=0.7,
                dependencies=["driver"],
                required_output_type="directional_update",
            ),
            ForecastNode(
                id="resolver",
                graph_id="graph-1",
                question="How will the official source resolve it?",
                node_type="resolver",
                importance_weight=0.5,
                required_output_type="structured_categorical",
            ),
        ],
    )

    assert "dependency_cycle_detected" in graph_approval_errors(graph)


def test_generator_rejects_invalid_graph() -> None:
    payload = valid_graph_payload()
    payload["nodes"] = [node for node in payload["nodes"] if node["node_type"] != "adversarial"]
    model = StubGraphModel(payload)

    with pytest.raises(ForecastGraphError) as exc_info:
        GraphGenerator(model).generate(approved_contract())

    assert "adversarial_node_required" in exc_info.value.reasons


def test_contract_without_graph_cannot_start_forecasting(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    approved = client.post(f"/api/contracts/{draft['id']}/approve")
    assert approved.status_code == 200

    blocked = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    )

    assert blocked.status_code == 422
    assert "approved_forecast_graph_required" in blocked.json()["reasons"]


def test_graph_api_generate_get_and_start_existing_forecast_flow(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()

    generated = client.post(f"/api/contracts/{draft['id']}/graph")
    assert generated.status_code == 200
    graph = generated.json()
    assert graph["status"] == "approved"
    assert graph["contract_id"] == draft["id"]
    assert graph["root_question"] == draft["normalized_question"]
    assert 5 <= len(graph["nodes"]) <= 10
    assert {"base_rate", "driver", "adversarial", "resolver"} <= {
        node["node_type"] for node in graph["nodes"]
    }

    fetched = client.get(f"/api/graphs/{graph['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == graph

    repeated = client.post(f"/api/contracts/{draft['id']}/graph")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == graph["id"]

    run = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"


def test_graph_api_rejects_unapproved_contract(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()

    response = client.post(f"/api/contracts/{draft['id']}/graph")

    assert response.status_code == 422
    assert "approved_forecast_contract_required" in response.json()["reasons"]

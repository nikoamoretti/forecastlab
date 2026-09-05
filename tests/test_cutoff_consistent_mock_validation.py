from __future__ import annotations

import json
from pathlib import Path

from forecastlab_api.cutoff_consistent_mock import CORPUS_ID, CORPUS_VERSION
from forecastlab_api.cutoff_consistent_mock_validation import (
    EXACT_QUESTION_IDS,
    REQUIRED_NOTICE,
    evaluate_cutoff_consistent_gates,
    exact_question_manifests,
    render_cutoff_consistent_validation_report,
    summarize_cutoff_consistent_validation,
    write_cutoff_consistent_validation_artifacts,
)


def _row(question_id: str, *, cutoff_rejections: int = 0) -> dict:
    return {
        "evaluation_question_id": question_id,
        "question_hash": "5a67ed2ad06d47cc4512030b6cc156b5165bb73dc01b8921fc18ddcc4d646436",
        "question": "Will the synthetic fixture resolve yes?",
        "domain": "economics",
        "category": "fixture",
        "forecast_date": "2024-01-01T00:00:00+00:00",
        "evidence_cutoff": "2024-01-01T00:00:00+00:00",
        "resolution_date": "2024-04-01T00:00:00+00:00",
        "forecast_run_id": f"run-{question_id}",
        "status": "completed",
        "corpus": {
            "corpus_id": CORPUS_ID,
            "corpus_version": CORPUS_VERSION,
            "corpus_hash": "b145d971c23082be67cfe27b37f1182258739b26f540068b42206b71f8fe11b3",
            "eligible_documents": [
                {
                    "document_id": "fixture-document",
                    "fixture_url": "https://fixtures.forecastlab.local/fixture",
                    "canonical_source_url": "https://example.com/source",
                }
            ],
            "excluded_documents": [],
            "query_rankings": [
                {
                    "query": "fixture query",
                    "ranked_documents": [
                        {"document_id": "fixture-document", "score": 1.0}
                    ],
                }
            ],
        },
        "research_plan": {
            "id": "plan-id",
            "all_graph_nodes": [
                {
                    "id": "node-id",
                    "question": "What is the base rate?",
                    "node_type": "base_rate",
                    "importance_weight": 0.9,
                    "dependencies": [],
                    "critical": True,
                }
            ],
            "selected_nodes": ["node-id"],
            "skipped_nodes": [],
            "critical_nodes": ["node-id"],
            "priority_scores": {"node-id": 1.25},
            "normalized_priority_scores": {"node-id": 1.0},
            "allocated_search_count": 1,
            "allocated_search_count_by_node": {"node-id": 1},
            "estimated_pre_execution_cost_usd": 0.2,
            "selected_worker_concurrency": 1,
            "budget_allocation": {},
            "shadow_estimated_model_cost_usd": 0.18,
            "shadow_estimated_search_cost_usd": 0.008,
            "shadow_estimated_total_cost_usd": 0.188,
            "shadow_pricing_identity": {},
        },
        "research_execution": {
            "nodes": [
                {
                    "node_id": "node-id",
                    "question": "What is the base rate?",
                    "node_type": "base_rate",
                    "critical": True,
                    "research_selected": True,
                    "claims_created": 1,
                    "node_forecast_created": True,
                    "failure_code": None,
                }
            ],
            "queries_attempted": 1,
            "documents_fetched": 1,
            "cutoff_rejections": cutoff_rejections,
            "smaller_chunk_retries": 0,
            "document_level_fallbacks": 0,
            "extraction_failures": 0,
            "evidence_claims_persisted": 1,
        },
        "forecast_execution": {
            "critical_node_failures": 0,
            "node_forecast_runs_persisted": 1,
            "forecast_aggregation_persisted": True,
            "forecast_version_persisted": True,
            "final_probability": 0.5,
            "reduced_confidence": False,
        },
        "operations": {
            "actual_model_calls": 2,
            "actual_search_calls": 1,
            "provider_ledger_rows": [],
            "live_provider_call_count": 0,
            "actual_mock_model_cost_usd": 0.0,
            "actual_mock_search_cost_usd": 0.0,
            "actual_mock_total_cost_usd": 0.0,
            "whole_run_attempts": 1,
            "whole_run_retries": 0,
            "physical_provider_retries": 0,
            "retries": 0,
            "latency_ms": 100,
        },
        "failure_categories": (
            {"cutoff_rejection": cutoff_rejections}
            if cutoff_rejections
            else {}
        ),
        "committed_limits_respected": True,
    }


def _frozen() -> dict:
    questions = [
        {
            "evaluation_question_id": question_id,
            "domain": "economics",
            "evidence_cutoff": "2024-01-01T00:00:00+00:00",
        }
        for question_id in EXACT_QUESTION_IDS
    ]
    return {
        "source_validations": {
            "validation_a": {
                "summary": {
                    "completed_questions": 0,
                    "evidence_claims_created": 0,
                    "node_forecasts_created": 0,
                    "final_aggregations_created": 0,
                    "forecast_versions_created": 0,
                    "total_cost_usd": 1.03,
                    "mean_latency_ms": 228884.8,
                }
            },
            "validation_b": {
                "summary": {
                    "completed_questions": 1,
                    "evidence_claims_created": 7,
                    "node_forecast_runs_created": 7,
                    "forecast_aggregations_created": 1,
                    "forecast_versions_created": 1,
                    "cutoff_rejections": 28,
                    "critical_node_failures": 12,
                    "total_actual_cost_usd": 0.0,
                    "mean_latency_ms": 307.8,
                }
            },
        },
        "dataset": {"questions": questions},
        "profile": {"id": "graph_forecaster_v1", "version": 5},
        "corpus": {
            "corpus_id": CORPUS_ID,
            "corpus_version": CORPUS_VERSION,
            "corpus_hash": "b145d971c23082be67cfe27b37f1182258739b26f540068b42206b71f8fe11b3",
        },
        "provider": {
            "model_provider": "mock",
            "search_provider": "mock",
            "live_provider_calls_authorized": False,
        },
        "shadow_pricing": {"per_question_cost_ceiling_usd": 0.25},
        "validation_scope": {
            "production_cutoff_rules_weakened": False,
            "forecasting_behavior_tuned": False,
            "adapter_boundary_only": True,
            "protected_behavior_changes": [],
            "normal_cutoff_self_check": {"passed": True},
        },
        "code": {"git_commit": "fixture-commit"},
    }


def test_exact_question_selection_preserves_ids_and_order() -> None:
    manifests = exact_question_manifests()

    assert tuple(item["evaluation_question_id"] for item in manifests) == EXACT_QUESTION_IDS
    assert [item["domain"] for item in manifests] == [
        "economics",
        "economics",
        "business",
        "technology",
        "regulation",
    ]


def test_summary_and_all_fourteen_gates_pass_for_complete_fixture() -> None:
    rows = [_row(question_id) for question_id in EXACT_QUESTION_IDS]

    summary = summarize_cutoff_consistent_validation(rows)
    gates = evaluate_cutoff_consistent_gates(rows, _frozen())

    assert summary["completed_questions"] == 5
    assert summary["forecast_versions_created"] == 5
    assert summary["forecast_aggregations_created"] == 5
    assert summary["evidence_claims_created"] == 5
    assert summary["node_forecast_runs_created"] == 5
    assert summary["actual_mock_total_cost_usd"] == 0.0
    assert len(gates) == 14
    assert all(gate["passed"] for gate in gates)


def test_cutoff_failure_is_reported_without_softening() -> None:
    rows = [_row(question_id) for question_id in EXACT_QUESTION_IDS]
    rows[-1] = _row(EXACT_QUESTION_IDS[-1], cutoff_rejections=1)

    gates = evaluate_cutoff_consistent_gates(rows, _frozen())
    cutoff_gate = next(
        gate
        for gate in gates
        if gate["description"] == "Zero runs fail due to cutoff_rejection"
    )

    assert cutoff_gate["passed"] is False
    assert cutoff_gate["evidence"] == "1"


def test_report_contains_required_notice_cost_separation_and_fourteen_gates() -> None:
    rows = [_row(question_id) for question_id in EXACT_QUESTION_IDS]
    frozen = _frozen()
    artifact = {
        "validation_id": "validation-id",
        "passed": True,
        "freeze": frozen,
        "summary": summarize_cutoff_consistent_validation(rows),
        "rows": rows,
        "gate_results": evaluate_cutoff_consistent_gates(rows, frozen),
    }

    report = render_cutoff_consistent_validation_report(artifact)

    assert REQUIRED_NOTICE in report
    assert "Shadow estimated model / search / total cost" in report
    assert "Actual mock model / search / total cost" in report
    assert "Fourteen-condition pass/fail gate" in report
    assert report.count("| PASS |") == 14
    assert "Production historical cutoff rules changed: no." in report
    assert "Live OpenAI or Tavily calls: none." in report


def test_artifact_writer_creates_json_and_markdown(tmp_path: Path) -> None:
    rows = [_row(question_id) for question_id in EXACT_QUESTION_IDS]
    frozen = _frozen()
    artifact = {
        "validation_id": "validation-id",
        "passed": True,
        "freeze": frozen,
        "summary": summarize_cutoff_consistent_validation(rows),
        "rows": rows,
        "gate_results": evaluate_cutoff_consistent_gates(rows, frozen),
    }
    artifact_path = tmp_path / "artifact" / "validation_results.json"
    report_path = tmp_path / "report.md"

    write_cutoff_consistent_validation_artifacts(
        artifact,
        artifact_path=artifact_path,
        report_path=report_path,
    )

    assert json.loads(artifact_path.read_text(encoding="utf-8"))["passed"] is True
    assert REQUIRED_NOTICE in report_path.read_text(encoding="utf-8")

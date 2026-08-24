from __future__ import annotations

import json

import pytest

from forecastlab_api.graph_planner_validation import (
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_REPORT_PATH,
    PILOT_V1_DATASET_HASH,
    PREVIOUS_QUESTION_IDS_BY_HASH,
    PREVIOUS_VALIDATION_ID,
    VALIDATION_PROFILE_ID,
    VALIDATION_PROFILE_VERSION,
    evaluate_validation_gates,
    previous_question_manifests,
    render_graph_planner_validation_report,
    summarize_graph_planner_validation,
)


def _row(*, completed: bool = True, budget_exceeded: bool = False) -> dict:
    return {
        "evaluation_question_id": "evaluation-question",
        "question_hash": "question-hash",
        "question": "Will the fixture resolve yes?",
        "domain": "economics",
        "category": "fixture",
        "forecast_date": "2024-01-01T00:00:00+00:00",
        "evidence_cutoff": "2024-01-01T00:00:00+00:00",
        "resolution_date": "2024-04-01T00:00:00+00:00",
        "forecast_run_id": "run-id",
        "status": "completed" if completed else "failed",
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
            "estimated_pre_execution_cost_usd": 0.01,
            "selected_worker_concurrency": 1,
            "budget_allocation": {},
        },
        "research_execution": {
            "nodes": [
                {
                    "node_id": "node-id",
                    "question": "What is the base rate?",
                    "node_type": "base_rate",
                    "importance_weight": 0.9,
                    "dependencies": [],
                    "critical": True,
                    "research_selected": True,
                    "skip_reason": None,
                    "queries_attempted": ["base rate query"],
                    "urls_discovered": ["https://fixtures.forecastlab.local/source"],
                    "sources_checked": [],
                    "documents_fetched": 1,
                    "cutoff_rejections": 0,
                    "extraction_attempts": 1,
                    "smaller_chunk_retries": 0,
                    "document_level_fallbacks": 0,
                    "extraction_failures": 0,
                    "evidence_records": 1,
                    "rejected_records": 0,
                    "claims_created": 1,
                    "node_forecast_created": completed,
                    "failure_code": None if completed else "cutoff_rejection",
                    "failure_stage": None if completed else "node_research",
                    "failure_detail": None,
                }
            ],
            "queries_attempted": 1,
            "queries_attempted_by_node": {"node-id": ["base rate query"]},
            "urls_discovered": 1,
            "urls_discovered_by_node": {"node-id": ["https://fixtures.forecastlab.local/source"]},
            "documents_fetched": 1,
            "cutoff_rejections": 0,
            "extraction_attempts": 1,
            "smaller_chunk_retries": 0,
            "document_level_fallbacks": 0,
            "extraction_failures": 0,
            "evidence_claims_persisted": 1 if completed else 0,
        },
        "forecast_execution": {
            "selected_nodes_successfully_researched": 1 if completed else 0,
            "selected_nodes_failed": 0 if completed else 1,
            "selected_node_failure_ids": [] if completed else ["node-id"],
            "critical_node_failures": 0 if completed else 1,
            "critical_node_failure_ids": [] if completed else ["node-id"],
            "noncritical_node_exclusions": 0,
            "noncritical_node_exclusion_ids": [],
            "node_forecast_runs_persisted": 1 if completed else 0,
            "forecast_aggregation_persisted": completed,
            "forecast_version_persisted": completed,
            "final_probability": 0.5 if completed else None,
            "reduced_confidence": False,
            "reliability": {},
        },
        "operations": {
            "actual_model_calls": 3,
            "actual_search_calls": 1,
            "provider_ledger_row_count": 4,
            "provider_ledger_rows": [],
            "live_provider_call_count": 0,
            "actual_cost_usd": 0.01,
            "budget_remaining_usd": 0.24,
            "budget_exceeded_events": 1 if budget_exceeded else 0,
            "latency_ms": 100,
            "whole_run_attempts": 1,
            "whole_run_retries": 0,
            "physical_provider_retries": 0,
            "failed_provider_attempts": 0,
            "provider_names": ["mock"],
        },
        "failure_categories": {"budget_exceeded": 1} if budget_exceeded else {},
        "search_and_evidence_limits_respected": True,
        "execution_error": None,
    }


def _frozen() -> dict:
    return {
        "budget": {"hard_per_question_cost_ceiling_usd": 0.25},
        "provider": {"model_provider": "mock", "search_provider": "mock"},
        "validation_scope": {
            "core_behavior_modified": False,
            "unexpected_nonvalidation_changes": [],
        },
    }


def test_previous_selection_preserves_exact_ids_hashes_and_order() -> None:
    manifests = previous_question_manifests()

    assert len(manifests) == 5
    assert [item["question_hash"] for item in manifests] == list(PREVIOUS_QUESTION_IDS_BY_HASH)
    assert manifests[0]["domain"] == "economics"
    assert manifests[2]["domain"] == "business"
    assert manifests[3]["domain"] == "technology"
    assert manifests[4]["domain"] == "regulation"


def test_summary_and_gates_reconcile_a_passing_five_run_fixture() -> None:
    rows = [_row() for _ in range(5)]

    summary = summarize_graph_planner_validation(rows)
    gates = evaluate_validation_gates(rows, _frozen())

    assert summary["completed_questions"] == 5
    assert summary["forecast_versions_created"] == 5
    assert summary["forecast_aggregations_created"] == 5
    assert summary["node_forecast_runs_created"] == 5
    assert summary["evidence_claims_created"] == 5
    assert summary["live_provider_calls"] == 0
    assert len(gates) == 11
    assert all(gate["passed"] for gate in gates)


def test_gate_failure_is_preserved_without_softening() -> None:
    rows = [_row() for _ in range(4)] + [_row(completed=False, budget_exceeded=True)]

    gates = evaluate_validation_gates(rows, _frozen())

    failed = {gate["condition"] for gate in gates if not gate["passed"]}
    assert {1, 2, 3, 4, 5, 6}.issubset(failed)
    assert gates[9]["passed"] is True
    assert gates[10]["passed"] is True


def test_report_renders_previous_comparison_audit_and_all_gates() -> None:
    rows = [_row() for _ in range(5)]
    frozen = {
        **_frozen(),
        "code": {
            "planner_source_commit": "planner-sha",
            "git_commit": "harness-sha",
        },
        "profile": {
            "id": VALIDATION_PROFILE_ID,
            "version": VALIDATION_PROFILE_VERSION,
        },
        "dataset": {
            "hash": PILOT_V1_DATASET_HASH,
            "questions": [
                {
                    "evaluation_question_id": f"question-{index}",
                    "domain": "economics",
                    "evidence_cutoff": "2024-01-01T00:00:00+00:00",
                    "question": f"Question {index}",
                }
                for index in range(5)
            ],
        },
    }
    gates = evaluate_validation_gates(rows, frozen)
    artifact = {
        "passed": True,
        "validation_id": "validation-id",
        "freeze": frozen,
        "previous_state": {
            "forecasts_completed": 0,
            "assigned_questions": 5,
            "successful_nodes": 0,
            "failed_nodes": 36,
            "budget_exceeded_runs": 5,
            "extraction_failures": 6,
            "nodes_skipped_after_budget_stop": 25,
            "evidence_claims": 0,
            "node_forecasts": 0,
            "aggregations": 0,
            "forecast_versions": 0,
            "total_cost_usd": 1.02979,
            "mean_latency_ms": 228884.8,
        },
        "summary": summarize_graph_planner_validation(rows),
        "rows": rows,
        "gate_results": gates,
        "claim_boundary": "Execution reliability only.",
    }

    report = render_graph_planner_validation_report(artifact)

    assert report.startswith("# Graph Research Planner Validation Report")
    assert "Previous versus current" in report
    assert "Detailed execution audit" in report
    assert "| 11 |" in report
    assert "Live provider ledger rows: 0" in report
    assert "do not support accuracy conclusions" in report


@pytest.mark.skipif(
    not DEFAULT_ARTIFACT_PATH.exists(),
    reason="one-shot planner validation artifact has not been generated",
)
def test_first_run_artifact_and_report_reconcile() -> None:
    artifact = json.loads(DEFAULT_ARTIFACT_PATH.read_text(encoding="utf-8"))
    report = DEFAULT_REPORT_PATH.read_text(encoding="utf-8")

    assert artifact["validation_id"] != PREVIOUS_VALIDATION_ID
    assert artifact["freeze"]["dataset"]["hash"] == PILOT_V1_DATASET_HASH
    assert artifact["freeze"]["profile"]["version"] == VALIDATION_PROFILE_VERSION
    assert artifact["freeze"]["provider"]["model_provider"] == "mock"
    assert artifact["freeze"]["provider"]["search_provider"] == "mock"
    assert artifact["summary"] == summarize_graph_planner_validation(artifact["rows"])
    assert artifact["gate_results"] == evaluate_validation_gates(artifact["rows"], artifact["freeze"])
    assert report == render_graph_planner_validation_report(artifact)
    assert artifact["summary"]["live_provider_calls"] == 0
    assert all(
        row["operations"]["whole_run_attempts"] == 1 and row["operations"]["whole_run_retries"] == 0
        for row in artifact["rows"]
    )

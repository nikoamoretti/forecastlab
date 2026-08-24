from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from forecastlab_api.graph_validation import (
    PILOT_V1_DATASET_HASH,
    VALIDATION_DOMAIN_COUNTS,
    VALIDATION_PROFILE_ID,
    VALIDATION_PROFILE_VERSION,
    freeze_graph_validation,
    render_graph_validation_report,
    selected_pilot_questions,
    summarize_graph_validation,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = (
    ROOT / "artifacts" / "graph_execution_validation" / "validation_results.json"
)
REPORT_PATH = ROOT / "docs" / "GRAPH_EXECUTION_VALIDATION_REPORT.md"
VALIDATION_ID = "4a5c23e0-0cf6-463b-a608-a1ce65c88e63"
EXECUTION_COMMIT = "99a3a9beebb8c285f079adb5964da30f84941b89"


def _artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def _provider_settings() -> dict:
    return {
        "model_provider": "openai",
        "model_name": "gpt-5-mini-2025-08-07",
        "model_base_url": "https://api.openai.com/v1",
        "model_api_key": "test-model-key",
        "search_provider": "tavily",
        "search_api_key": "test-search-key",
        "max_cost_usd": 0.25,
        "model_timeout_seconds": 60.0,
    }


def _rows() -> list[dict]:
    return [
        {
            "question_hash": f"question-{index}",
            "question": f"Question {index}",
            "domain": domain,
            "category": "fixture",
            "forecast_date": "2024-01-01T00:00:00+00:00",
            "resolution_date": "2024-04-01T00:00:00+00:00",
            "forecast_run_id": f"run-{index}",
            "status": "failed" if index == 4 else "completed",
            "total_nodes": 7,
            "successful_nodes": 6 if index == 4 else 7,
            "failed_nodes": 1 if index == 4 else 0,
            "failed_node_ids": ["node-failed"] if index == 4 else [],
            "evidence_claims_created": 6 if index == 4 else 7,
            "nodes_with_evidence_claims": 6 if index == 4 else 7,
            "evidence_success_rate": 6 / 7 if index == 4 else 1.0,
            "node_forecasts_created": 6 if index == 4 else 7,
            "final_aggregation_created": index != 4,
            "forecast_version_created": index != 4,
            "latency_ms": 1000 + index,
            "cost_usd": 0.01,
            "cost_source": "estimated",
            "whole_run_attempts": 1,
            "whole_run_retries": 0,
            "provider_requests": 10,
            "physical_provider_retries": 1 if index == 3 else 0,
            "failed_provider_attempts": 1 if index == 3 else 0,
            "failure_categories": {"no_matching_source": 1} if index == 4 else {},
            "execution_error": None,
        }
        for index, domain in enumerate(
            ["economics", "economics", "business", "technology", "regulation"]
        )
    ]


def test_validation_selection_uses_five_manifest_order_questions() -> None:
    selected = selected_pilot_questions()

    assert len(selected) == 5
    counts = {
        domain: sum(item["domain"] == domain for item in selected)
        for domain in VALIDATION_DOMAIN_COUNTS
    }
    assert counts == VALIDATION_DOMAIN_COUNTS
    assert selected[0]["question"].startswith(
        "Will the U.S. seasonally adjusted U-3 unemployment rate for June 2024"
    )
    assert selected[1]["question"].startswith(
        "Will the 12-month U.S. CPI-U all-items inflation rate for September 2024"
    )


def test_validation_freeze_is_profile_v5_reproducible_and_secret_free() -> None:
    frozen = freeze_graph_validation(
        settings_data=_provider_settings(),
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    assert frozen["dataset"]["hash"] == PILOT_V1_DATASET_HASH
    assert frozen["dataset"]["selected_question_count"] == 5
    assert frozen["profile"]["id"] == VALIDATION_PROFILE_ID
    assert frozen["profile"]["version"] == VALIDATION_PROFILE_VERSION
    assert frozen["provider"]["model_api_key_set"] is True
    assert frozen["provider"]["search_api_key_set"] is True
    assert "test-model-key" not in str(frozen)
    assert "test-search-key" not in str(frozen)
    assert frozen["budget"]["hard_validation_cost_ceiling_usd"] == 1.25
    assert frozen["execution_policy"]["automatic_whole_run_retry"] is False
    assert frozen["execution_policy"]["accuracy_metrics"] == "not_computed"
    assert frozen["configuration_hash"]


def test_validation_summary_reconciles_execution_evidence_cost_and_retries() -> None:
    summary = summarize_graph_validation(_rows())

    assert summary["assigned_questions"] == 5
    assert summary["completed_questions"] == 4
    assert summary["completion_rate"] == 0.8
    assert summary["total_nodes"] == 35
    assert summary["successful_nodes"] == 34
    assert summary["failed_nodes"] == 1
    assert summary["evidence_claims_created"] == 34
    assert summary["node_forecasts_created"] == 34
    assert summary["final_aggregations_created"] == 4
    assert summary["total_cost_usd"] == 0.05
    assert summary["whole_run_retries"] == 0
    assert summary["physical_provider_retries"] == 1
    assert summary["failure_categories"] == {"no_matching_source": 1}


def test_validation_report_compares_execution_reliability_without_accuracy_claims() -> None:
    frozen = freeze_graph_validation(
        settings_data=_provider_settings(),
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    artifact = {
        "validation_id": frozen["validation_id"],
        "freeze": frozen,
        "previous_state": {
            "assigned_questions": 20,
            "completed_questions": 10,
            "failed_questions": 10,
            "completion_rate": 0.5,
        },
        "summary": summarize_graph_validation(_rows()),
        "rows": _rows(),
        "claim_boundary": "Execution reliability only. No forecast-accuracy metric is computed.",
    }

    report = render_graph_validation_report(artifact)

    assert report.startswith("# Graph Execution Validation Report")
    assert "Previous graph benchmark" in report
    assert "Current validation" in report
    assert "Completion rate | 50.0% | 80.0%" in report
    assert "Brier" not in report
    assert "log loss" not in report.casefold()
    assert "full 20-question pilot benchmark was not executed" in report


def test_execution_artifact_has_frozen_identity_and_requested_selection() -> None:
    artifact = _artifact()
    frozen = artifact["freeze"]

    assert artifact["validation_id"] == VALIDATION_ID
    assert artifact["artifact_schema_version"] == 1
    assert frozen["dataset"]["hash"] == PILOT_V1_DATASET_HASH
    assert frozen["dataset"]["selected_question_count"] == 5
    assert frozen["profile"]["id"] == VALIDATION_PROFILE_ID
    assert frozen["profile"]["version"] == VALIDATION_PROFILE_VERSION
    assert frozen["code"]["git_commit"] == EXECUTION_COMMIT
    assert frozen["code"]["working_tree_dirty"] is False
    assert Counter(row["domain"] for row in artifact["rows"]) == Counter(
        VALIDATION_DOMAIN_COUNTS
    )
    assert len(artifact["rows"]) == 5


def test_execution_artifact_reconciles_fail_closed_result_and_cost() -> None:
    artifact = _artifact()
    rows = artifact["rows"]
    summary = artifact["summary"]

    assert summary == summarize_graph_validation(rows)
    assert summary["completed_questions"] == 0
    assert summary["failed_questions"] == 5
    assert summary["completion_rate"] == 0.0
    assert summary["successful_nodes"] == 0
    assert summary["failed_nodes"] == summary["total_nodes"] == 36
    assert summary["evidence_claims_created"] == 0
    assert summary["node_forecasts_created"] == 0
    assert summary["final_aggregations_created"] == 0
    assert summary["forecast_versions_created"] == 0
    assert summary["failure_categories"] == {
        "budget_exceeded": 5,
        "extraction_failure": 6,
        "not_run_after_budget_stop": 25,
    }
    assert summary["total_cost_usd"] == 1.02979
    assert summary["total_cost_usd"] <= artifact["freeze"]["budget"][
        "hard_validation_cost_ceiling_usd"
    ]
    assert all(row["whole_run_attempts"] == 1 for row in rows)
    assert all(row["whole_run_retries"] == 0 for row in rows)
    assert summary["physical_provider_retries"] == 0
    assert summary["failed_provider_attempts"] == 0


def test_execution_artifact_is_provider_safe_and_contains_no_accuracy_results() -> None:
    artifact = _artifact()
    provider = artifact["freeze"]["provider"]

    assert provider == {
        "evidence_policy": "strict_historical_snapshot",
        "model": "gpt-5-mini-2025-08-07",
        "model_api_key_set": True,
        "model_base_url": "https://api.openai.com/v1",
        "model_provider": "openai",
        "model_timeout_seconds": 60.0,
        "search_api_key_set": True,
        "search_provider": "tavily",
    }
    forbidden_result_fields = {
        "outcome",
        "probability",
        "brier_score",
        "log_loss",
    }
    assert all(not forbidden_result_fields.intersection(row) for row in artifact["rows"])
    assert artifact["freeze"]["execution_policy"]["accuracy_metrics"] == (
        "not_computed"
    )


def test_execution_report_matches_artifact_and_records_no_full_rerun() -> None:
    artifact = _artifact()
    report = REPORT_PATH.read_text(encoding="utf-8")

    assert report == render_graph_validation_report(artifact)
    assert VALIDATION_ID in report
    assert PILOT_V1_DATASET_HASH in report
    assert "Completion rate | 50.0% | 0.0%" in report
    assert "Evidence Claims created: 0" in report
    assert "Final aggregations created: 0" in report
    assert "Total measured cost: $1.029790" in report
    assert "Whole-run retries: 0" in report
    assert "full 20-question pilot benchmark was not executed" in report
    assert "does not compare forecast accuracy" in report
    assert not re.search(r"\b(?:winner|best|superior)\b", report, re.IGNORECASE)

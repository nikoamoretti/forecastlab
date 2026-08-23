from __future__ import annotations

from forecastlab_api.reports import build_v1_report, v1_report_markdown


def _claim(claim_id: str, node_id: str, stance: str) -> dict:
    return {
        "id": claim_id,
        "evidence_item_id": f"evidence-{claim_id}",
        "forecast_node_id": node_id,
        "claim": f"Claim {claim_id}",
        "excerpt": f"Excerpt {claim_id}",
        "source_url": f"https://example.test/{claim_id}",
        "source_title": f"Source {claim_id}",
        "publisher": "Example publisher",
        "publication_date": "2024-01-01T00:00:00Z",
        "retrieval_date": "2024-01-02T00:00:00Z",
        "supports_or_refutes": stance,
        "confidence": 0.8,
        "source_quality": 0.9,
        "primary_source": True,
        "as_of_eligible": True,
        "cutoff_verified": True,
    }


def test_v1_report_groups_node_forecasts_evidence_and_calculation_trace() -> None:
    run = {
        "profile_id": "graph_forecaster_v1",
        "forecast_contract": {"id": "contract-1", "status": "approved"},
        "forecast_graph": {
            "id": "graph-1",
            "version": 1,
            "status": "approved",
            "root_question": "Will the event occur?",
            "nodes": [
                {
                    "id": "node-a",
                    "question": "What is the base rate?",
                    "node_type": "base_rate",
                    "importance_weight": 0.8,
                    "dependencies": [],
                    "preferred_sources": ["official data"],
                    "required_output_type": "probability",
                    "status": "completed",
                },
                {
                    "id": "node-b",
                    "question": "What could overturn the forecast?",
                    "node_type": "adversarial",
                    "importance_weight": 0.6,
                    "dependencies": ["node-a"],
                    "preferred_sources": ["contradictory evidence"],
                    "required_output_type": "probability",
                    "status": "completed",
                },
            ],
        },
        "node_runs": [
            {
                "node_id": "node-a",
                "probability": 0.6,
                "confidence": 0.7,
                "reasoning": "Base-rate reasoning",
                "supporting_claim_ids": ["support-a"],
                "opposing_claim_ids": ["oppose-a"],
                "uncertainty": 0.3,
                "uncertainty_notes": ["The base-rate source covers a narrow reference class."],
                "model_used": "stub:node-v1",
                "raw_importance_weight": 0.8,
                "dependency_factor": 1.0,
                "normalized_weight": 0.7,
                "probability_contribution": 0.42,
            },
            {
                "node_id": "node-b",
                "probability": 0.4,
                "confidence": 0.4,
                "reasoning": "Adversarial reasoning",
                "supporting_claim_ids": [],
                "opposing_claim_ids": [],
                "uncertainty": 0.6,
                "uncertainty_notes": ["No claim was selected for this fixture node."],
                "model_used": "stub:node-v1",
                "raw_importance_weight": 0.6,
                "dependency_factor": 0.5,
                "normalized_weight": 0.3,
                "probability_contribution": 0.12,
            },
        ],
        "evidence_claims": [
            _claim("support-a", "node-a", "supports"),
            _claim("oppose-a", "node-a", "refutes"),
            _claim("uncited-b", "node-b", "supports"),
        ],
        "aggregation": {
            "method": "dependency_discounted_weighted_mean_v1",
            "formula": "weighted sum",
            "normalization_denominator": 1.0,
            "unbounded_probability": 0.54,
            "final_probability": 0.54,
            "calculation_trace": [
                {"step": "node_contribution", "node_id": "node-a", "contribution": 0.42},
                {"step": "final", "final_probability": 0.54},
            ],
        },
    }

    report = build_v1_report(run)

    assert report is not None
    assert report["final_probability"] == 0.54
    assert report["graph"]["node_count"] == 2
    assert report["nodes"][0]["question"] == "What is the base rate?"
    assert report["nodes"][0]["uncertainty_notes"] == [
        "The base-rate source covers a narrow reference class."
    ]
    assert report["nodes"][0]["model_used"] == "stub:node-v1"
    assert [item["id"] for item in report["nodes"][0]["supporting_evidence"]] == ["support-a"]
    assert [item["id"] for item in report["nodes"][0]["opposing_evidence"]] == ["oppose-a"]
    assert [item["id"] for item in report["nodes"][1]["uncited_evidence"]] == ["uncited-b"]
    assert report["evidence_coverage"]["covered_units"] == 1
    assert report["evidence_coverage"]["total_units"] == 2
    assert report["evidence_coverage"]["rate"] == 0.5
    assert report["calculation"]["trace"][-1]["step"] == "final"

    markdown = "\n".join(v1_report_markdown(report))
    assert "Supporting evidence:" in markdown
    assert "Opposing evidence:" in markdown
    assert "Claim support-a" in markdown
    assert "Excerpt: Excerpt support-a" in markdown
    assert "Uncertainty:" in markdown
    assert "The base-rate source covers a narrow reference class." in markdown
    assert "Model used: stub:node-v1" in markdown
    assert "Final probability:** 0.54" in markdown


def test_v1_report_is_absent_for_legacy_track_run() -> None:
    assert build_v1_report({"tracks": [{"track_type": "base_rate"}]}) is None


def test_v1_report_renders_first_class_log_odds_aggregation() -> None:
    contribution = {
        "node_id": "node-a",
        "node_question": "What is the historical base rate?",
        "input_probability": 0.35,
        "raw_importance_weight": 0.3,
        "normalized_weight": 1.0,
        "log_odds": -0.619039208406,
        "weighted_log_odds_contribution": -0.619039208406,
    }
    run = {
        "profile_id": "graph_forecaster_v1",
        "forecast_graph": {
            "id": "graph-1",
            "nodes": [
                {
                    "id": "node-a",
                    "question": "What is the historical base rate?",
                    "node_type": "base_rate",
                    "importance_weight": 0.3,
                }
            ],
        },
        "node_runs": [
            {
                "node_id": "node-a",
                "probability": 0.35,
                "reasoning": "Historical outcomes favor no.",
            }
        ],
        "forecast_aggregation": {
            "id": "aggregation-1",
            "forecast_run_id": "run-1",
            "method": "importance_weighted_log_odds_v1",
            "final_probability": 0.35,
            "node_contributions": [contribution],
            "calculation_trace": [
                {"step": "node_contribution", **contribution, "contribution": contribution["weighted_log_odds_contribution"]},
                {"step": "final", "combined_log_odds": -0.619039208406, "final_probability": 0.35},
            ],
        },
    }

    report = build_v1_report(run)

    assert report is not None
    assert report["final_probability"] == 0.35
    assert report["nodes"][0]["normalized_weight"] == 1.0
    assert report["nodes"][0]["log_odds"] == -0.619039208406
    assert report["nodes"][0]["weighted_log_odds_contribution"] == -0.619039208406
    assert report["calculation"]["node_contributions"] == [contribution]
    assert "convert each node probability to log odds" in report["calculation"]["formula"]

    markdown = "\n".join(v1_report_markdown(report))
    assert "### Node contributions" in markdown
    assert "Weighted log-odds contribution: -0.619039208406" in markdown
    assert "What is the historical base rate?: p=0.35, weight=1.0" in markdown


def test_v1_report_exposes_incomplete_graph_execution_without_probability() -> None:
    run = {
        "profile_id": "graph_forecaster_v1",
        "status": "failed",
        "forecast_contract": {"id": "contract-1", "status": "approved"},
        "forecast_graph": {
            "id": "graph-1",
            "status": "approved",
            "nodes": [
                {
                    "id": "node-a",
                    "question": "What is the base rate?",
                    "node_type": "base_rate",
                    "importance_weight": 1.0,
                }
            ],
        },
        "node_runs": [],
        "evidence_claims": [],
        "aggregation": {},
        "graph_execution_failures": [
            {
                "node_id": "node-a",
                "stage": "node_forecast",
                "error_code": "no_eligible_evidence",
                "error_message": "node_forecast_evidence_required",
            }
        ],
    }

    report = build_v1_report(run)

    assert report is not None
    assert report["final_probability"] is None
    assert report["nodes"][0]["forecast_status"] == "failed"
    assert report["nodes"][0]["failures"][0]["error_code"] == "no_eligible_evidence"
    assert report["final_answer"] == {
        "status": "failed",
        "probability": None,
        "statement": "No final probability was produced because graph execution was incomplete.",
    }
    markdown = "\n".join(v1_report_markdown(report))
    assert "### Execution failures" in markdown
    assert "no_eligible_evidence" in markdown
    assert "### Final answer" in markdown

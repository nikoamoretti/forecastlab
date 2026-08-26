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
        "publication_date_source": "html_meta:article:published_time",
        "publication_date_verified": True,
        "retrieval_date": "2024-01-02T00:00:00Z",
        "source_available_at": "2024-01-01T00:00:00Z",
        "temporal_basis": "publication_date",
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
    assert report["evidence_claims"][0]["temporal_quality_label"] == "Published date verified"

    markdown = "\n".join(v1_report_markdown(report))
    assert "Supporting evidence:" in markdown
    assert "Opposing evidence:" in markdown
    assert "Claim support-a" in markdown
    assert "Excerpt: Excerpt support-a" in markdown
    assert "Temporal provenance: Published date verified" in markdown
    assert "Uncertainty:" in markdown
    assert "The base-rate source covers a narrow reference class." in markdown
    assert "Model used: stub:node-v1" in markdown
    assert "Final probability:** 0.54" in markdown


def test_v1_report_labels_live_retrieval_basis_evidence_without_a_publication_date() -> None:
    claim = _claim("live-undated", "node-a", "supports")
    claim.update(
        {
            "publication_date": None,
            "publication_date_source": None,
            "publication_date_verified": False,
            "source_available_at": "2026-08-24T12:00:00Z",
            "retrieval_date": "2026-08-24T12:00:00Z",
            "temporal_basis": "retrieval_date",
        }
    )
    run = {
        "profile_id": "graph_live_smoke_v1",
        "forecast_graph": {
            "id": "graph-1",
            "nodes": [
                {
                    "id": "node-a",
                    "question": "What does the current source show?",
                    "node_type": "driver",
                    "importance_weight": 1.0,
                }
            ],
        },
        "node_runs": [
            {
                "node_id": "node-a",
                "probability": 0.5,
                "reasoning": "Current evidence is mixed.",
                "supporting_claim_ids": ["live-undated"],
            }
        ],
        "evidence_claims": [claim],
        "forecast_aggregation": {
            "method": "importance_weighted_log_odds_v1",
            "final_probability": 0.5,
        },
    }

    report = build_v1_report(run)

    assert report is not None
    summary = report["evidence_claims"][0]
    assert summary["publication_date"] is None
    assert summary["publication_date_verified"] is False
    assert summary["source_available_at"] == "2026-08-24T12:00:00Z"
    assert summary["temporal_basis"] == "retrieval_date"
    assert summary["temporal_quality_label"] == (
        "Publication date unavailable; page observed during live run"
    )


def test_fl_r005_shaped_report_preserves_profile_audit_and_three_of_eight_coverage() -> None:
    node_ids = [f"node-{index}" for index in range(8)]
    claims = [_claim(f"claim-{index}", node_id, "supports") for index, node_id in enumerate(node_ids[:3])]
    audit = {
        "provider": "openai",
        "model": "gpt-5-mini-2025-08-07",
        "schema_name": "forecast_graph",
        "provider_request_id": "rid-fl-r005",
        "requested_max_output_tokens": 1536,
        "finish_reason": "stop",
        "refusal_present": False,
        "refusal_category": None,
        "completion_tokens": 700,
        "reasoning_tokens": 120,
        "visible_output_tokens": 580,
        "content_character_count": 2048,
        "json_parsing_succeeded": True,
        "schema_validation_succeeded": True,
        "strict_schema_validation_succeeded": True,
        "schema_validation_errors": [],
        "domain_validation_succeeded": True,
        "domain_validation_errors": [],
        "errors": [],
        "prompt_version": "v1",
        "generated_at": "2026-08-25T12:00:00Z",
    }
    run = {
        "profile_id": "graph_live_smoke_v1",
        "status": "completed",
        "execution_context": {
            "forecast_graph_resolution": {
                "graph_id": "graph-fl-r005",
                "graph_version": 1,
                "status": "generated",
                "model_request_issued": True,
                "audit_source": "persisted_generation_audit",
                "generation_audit": audit,
            }
        },
        "forecast_graph": {
            "id": "graph-fl-r005",
            "version": 1,
            "status": "approved",
            "generation_model": "openai:gpt-5-mini-2025-08-07",
            "generation_audit": audit,
            "nodes": [
                {
                    "id": node_id,
                    "question": f"Research node {index}",
                    "node_type": "driver",
                    "importance_weight": 0.5,
                }
                for index, node_id in enumerate(node_ids)
            ],
        },
        "node_runs": [
            {
                "node_id": node_id,
                "probability": 0.4,
                "reasoning": "Auditable node forecast.",
                "supporting_claim_ids": [f"claim-{index}"],
                "opposing_claim_ids": [],
            }
            for index, node_id in enumerate(node_ids[:3])
        ],
        "evidence_claims": claims,
        "forecast_aggregation": {
            "method": "importance_weighted_log_odds_v1",
            "final_probability": 0.4,
        },
    }

    report = build_v1_report(run)

    assert report is not None
    assert report["profile_id"] == "graph_live_smoke_v1"
    assert report["graph"]["generation_audit"] == audit
    assert report["graph_resolution"]["status"] == "generated"
    assert report["evidence_coverage"]["covered_units"] == 3
    assert report["evidence_coverage"]["total_units"] == 8
    assert report["evidence_coverage"]["rate"] == 0.375
    assert report["final_answer"]["statement"] == "The graph_live_smoke_v1 probability is 0.4."
    markdown = "\n".join(v1_report_markdown(report))
    assert "**Profile:** graph_live_smoke_v1" in markdown
    assert "### Graph generation audit" in markdown
    assert "Evidence coverage:** 3/8 graph nodes (0.375)" in markdown


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
    assert (
        "What is the historical base rate?: p=0.35, raw=0.3, "
        "effective=None, normalized=1.0"
    ) in markdown


def test_v1_report_renders_relationship_mass_and_neutral_residual() -> None:
    contribution = {
        "node_id": "node-a",
        "node_question": "What is the historical base rate?",
        "input_probability": 0.35,
        "raw_importance_weight": 0.6,
        "normalized_weight": 0.3,
        "log_odds": -0.619039208406,
        "weighted_log_odds_contribution": -0.185711762522,
        "effective_importance_weight": 0.3,
        "self_allocated_weight": 0.3,
        "relationship_received_weight": 0.0,
        "relationship_source_node_ids": [],
        "direct_parent_id": None,
        "direct_dependency_ids": ["node-x"],
    }
    policy = {
        "step": "relationship_aggregation_policy",
        "method": "relationship_mass_conserving_log_odds_v1",
        "policy_version": "relationship_mass_conserving_log_odds_v1",
        "direct_relationships_only": True,
        "neutral_probability": 0.5,
        "no_imputation": True,
        "allocation_hash": "a" * 64,
    }
    mass = {
        "step": "graph_mass",
        "total_graph_raw_weight": "1",
        "effective_included_weight": "0.3",
        "neutral_residual_weight": "0.7",
        "normalized_effective_weight_sum": "0.3",
        "neutral_residual_fraction": "0.7",
        "conservation_check": True,
        "allocation_hash": "a" * 64,
    }
    source = {
        "step": "source_node_allocation",
        "source_node_id": "node-a",
        "source_raw_importance_weight": "0.6",
        "recipient_ids": ["node-a", "node-x"],
        "equal_share": "0.3",
        "mass_allocated_to_included_recipients": "0.3",
        "mass_sent_to_neutral_residual": "0.3",
    }
    excluded = {
        "step": "excluded_node_mass",
        "node_id": "node-x",
        "raw_importance_weight": "0.4",
        "exclusion_origin": "research_plan",
        "mass_sent_to_neutral_residual": "0.4",
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
                    "importance_weight": 0.6,
                    "dependencies": ["node-x"],
                },
                {
                    "id": "node-x",
                    "question": "What excluded driver remains?",
                    "node_type": "driver",
                    "importance_weight": 0.4,
                    "dependencies": [],
                },
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
            "method": "relationship_mass_conserving_log_odds_v1",
            "final_probability": 0.453649,
            "node_contributions": [contribution],
            "calculation_trace": [
                policy,
                mass,
                source,
                excluded,
                {"step": "node_contribution", **contribution},
                {
                    "step": "final",
                    "combined_log_odds": -0.185711762522,
                    "neutral_residual_contribution": 0.0,
                    "final_probability": 0.453649,
                },
            ],
        },
    }

    report = build_v1_report(run)

    assert report is not None
    relationship = report["calculation"]["relationship_aggregation"]
    assert relationship["graph_mass"]["neutral_residual_fraction"] == "0.7"
    assert relationship["graph_mass"]["conservation_check"] is True
    assert relationship["source_allocations"] == [source]
    assert relationship["excluded_nodes"] == [excluded]
    assert report["nodes"][0]["effective_importance_weight"] == 0.3
    assert report["nodes"][0]["aggregation_direct_dependency_ids"] == [
        "node-x"
    ]
    assert "not a Bayesian network" in relationship["heuristic_notice"]

    markdown = "\n".join(v1_report_markdown(report))
    assert "### Relationship-aware aggregation" in markdown
    assert "Neutral residual weight / fraction: 0.7 / 0.7" in markdown
    assert "Mass conserved: True" in markdown
    assert "node-x: raw=0.4; origin=research_plan; neutral=0.4" in markdown


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

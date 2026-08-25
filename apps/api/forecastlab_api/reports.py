from __future__ import annotations

from typing import Any

from forecastlab.graph_aggregation import LOG_ODDS_FORMULA, LOG_ODDS_METHOD


def _temporal_quality_label(claim: dict[str, Any]) -> str:
    basis = claim.get("temporal_basis")
    if basis == "snapshot_date" and claim.get("cutoff_verified"):
        return "Historical snapshot verified"
    if claim.get("publication_date") and claim.get("publication_date_verified"):
        return "Published date verified"
    if basis == "retrieval_date" and not claim.get("publication_date"):
        return "Publication date unavailable; page observed during live run"
    if claim.get("publication_date"):
        return "Publication date available but not independently verified"
    return "Temporal provenance unavailable"


def _claim_summary(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": claim.get("id"),
        "claim": claim.get("claim"),
        "excerpt": claim.get("excerpt"),
        "supports_or_refutes": claim.get("supports_or_refutes"),
        "confidence": claim.get("confidence"),
        "source_quality": claim.get("source_quality"),
        "primary_source": bool(claim.get("primary_source")),
        "source_url": claim.get("source_url"),
        "source_title": claim.get("source_title"),
        "publisher": claim.get("publisher"),
        "publication_date": claim.get("publication_date"),
        "publication_date_source": claim.get("publication_date_source"),
        "publication_date_verified": bool(claim.get("publication_date_verified")),
        "retrieval_date": claim.get("retrieval_date"),
        "source_available_at": claim.get("source_available_at"),
        "temporal_basis": claim.get("temporal_basis"),
        "temporal_quality_label": _temporal_quality_label(claim),
        "as_of_eligible": bool(claim.get("as_of_eligible")),
        "cutoff_verified": bool(claim.get("cutoff_verified")),
    }


def build_v1_report(run: dict[str, Any]) -> dict[str, Any] | None:
    """Build the auditable Contract -> Graph -> Claims -> Probability report payload."""

    graph = run.get("forecast_graph") or {}
    profile_id = str(run.get("profile_id") or "unknown_profile")
    execution_context = run.get("execution_context") or {}
    graph_resolution = execution_context.get("forecast_graph_resolution")
    graph_nodes = graph.get("nodes") or []
    node_runs = run.get("node_runs") or []
    research_plan = run.get("research_plan") or {}
    planned_selected = set(research_plan.get("selected_nodes") or [])
    planned_skipped = set(research_plan.get("skipped_nodes") or [])
    skipped_reasons = (
        (research_plan.get("budget_allocation") or {}).get("skipped_reasons")
        or {}
    )
    if not graph_nodes and not node_runs:
        return None

    aggregation = run.get("forecast_aggregation") or run.get("aggregation") or {}
    contributions_by_node = {
        str(item.get("node_id")): item
        for item in aggregation.get("node_contributions") or []
        if isinstance(item, dict) and item.get("node_id")
    }
    claims = run.get("evidence_claims") or []
    claims_by_id = {str(claim.get("id")): claim for claim in claims if claim.get("id")}
    claims_by_node: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        node_id = str(claim.get("forecast_node_id") or "")
        if node_id:
            claims_by_node.setdefault(node_id, []).append(claim)
    node_runs_by_id = {str(item.get("node_id")): item for item in node_runs if item.get("node_id")}
    failures = run.get("graph_execution_failures") or []
    failures_by_node: dict[str, list[dict[str, Any]]] = {}
    for failure in failures:
        node_id = str(failure.get("node_id") or "")
        if node_id:
            failures_by_node.setdefault(node_id, []).append(failure)

    report_nodes: list[dict[str, Any]] = []
    covered_node_ids: list[str] = []
    cited_claim_ids: set[str] = set()
    for node in graph_nodes:
        node_id = str(node.get("id") or "")
        node_run = node_runs_by_id.get(node_id) or {}
        node_contribution = contributions_by_node.get(node_id) or {}
        supporting_ids = [str(item) for item in node_run.get("supporting_claim_ids") or []]
        opposing_ids = [str(item) for item in node_run.get("opposing_claim_ids") or []]
        selected_ids = set(supporting_ids) | set(opposing_ids)
        supporting = [
            _claim_summary(claims_by_id[claim_id])
            for claim_id in supporting_ids
            if claim_id in claims_by_id and claims_by_id[claim_id].get("forecast_node_id") == node_id
        ]
        opposing = [
            _claim_summary(claims_by_id[claim_id])
            for claim_id in opposing_ids
            if claim_id in claims_by_id and claims_by_id[claim_id].get("forecast_node_id") == node_id
        ]
        uncited = [
            _claim_summary(claim)
            for claim in claims_by_node.get(node_id, [])
            if str(claim.get("id")) not in selected_ids
        ]
        valid_selected_ids = {str(claim["id"]) for claim in [*supporting, *opposing] if claim.get("id")}
        if valid_selected_ids:
            covered_node_ids.append(node_id)
            cited_claim_ids.update(valid_selected_ids)
        report_nodes.append(
            {
                "id": node_id,
                "question": node.get("question"),
                "node_type": node.get("node_type"),
                "parent_node_id": node.get("parent_node_id"),
                "dependencies": node.get("dependencies") or [],
                "preferred_sources": node.get("preferred_sources") or [],
                "required_output_type": node.get("required_output_type"),
                "importance_weight": node.get("importance_weight"),
                "status": node.get("status"),
                "forecast_status": (
                    "skipped"
                    if node_id in planned_skipped
                    else "failed"
                    if failures_by_node.get(node_id)
                    else "completed"
                    if node_run
                    else "missing"
                ),
                "research_selected": (
                    node_id in planned_selected
                    if research_plan
                    else None
                ),
                "research_skip_reason": skipped_reasons.get(node_id),
                "failures": failures_by_node.get(node_id) or [],
                "research_plan": (
                    (failures_by_node.get(node_id) or [{}])[0].get("research_plan")
                    or None
                ),
                "queries_attempted": list(
                    dict.fromkeys(
                        query
                        for failure in failures_by_node.get(node_id) or []
                        for query in failure.get("queries_attempted") or []
                    )
                ),
                "sources_checked": [
                    source
                    for failure in failures_by_node.get(node_id) or []
                    for source in failure.get("sources_checked") or []
                ],
                "failure_impact": (
                    (failures_by_node.get(node_id) or [{}])[0].get("impact")
                ),
                "critical_node": any(
                    bool(failure.get("critical_node"))
                    for failure in failures_by_node.get(node_id) or []
                ),
                "probability": node_contribution.get("input_probability", node_run.get("probability")),
                "confidence": node_run.get("confidence"),
                "uncertainty": node_run.get("uncertainty"),
                "uncertainty_notes": node_run.get("uncertainty_notes") or [],
                "model_used": node_run.get("model_used"),
                "reasoning": node_run.get("reasoning"),
                "raw_importance_weight": node_contribution.get(
                    "raw_importance_weight",
                    node_run.get("raw_importance_weight"),
                ),
                "dependency_factor": node_run.get("dependency_factor"),
                "normalized_weight": node_contribution.get(
                    "normalized_weight",
                    node_run.get("normalized_weight"),
                ),
                "probability_contribution": node_run.get("probability_contribution"),
                "log_odds": node_contribution.get("log_odds"),
                "weighted_log_odds_contribution": node_contribution.get(
                    "weighted_log_odds_contribution"
                ),
                "supporting_evidence": supporting,
                "opposing_evidence": opposing,
                "uncited_evidence": uncited,
                "evidence_claim_count": len(claims_by_node.get(node_id, [])),
                "cited_claim_count": len(valid_selected_ids),
            }
        )

    total_nodes = len(graph_nodes)
    covered_nodes = len(covered_node_ids)
    final_probability = aggregation.get("final_probability", aggregation.get("ensemble_probability"))
    reduced_failures = [
        failure
        for failure in failures
        if failure.get("impact") == "excluded_reduced_confidence"
    ]
    critical_failures = [
        failure for failure in failures if bool(failure.get("critical_node"))
    ]
    return {
        "profile_id": profile_id,
        "execution_status": run.get("status"),
        "final_probability": final_probability,
        "forecast_contract": run.get("forecast_contract"),
        "graph": {
            "id": graph.get("id"),
            "version": graph.get("version"),
            "status": graph.get("status"),
            "created_at": graph.get("created_at"),
            "generation_model": graph.get("generation_model"),
            "root_question": graph.get("root_question"),
            "node_count": total_nodes,
            "generation_audit": graph.get("generation_audit"),
        },
        "graph_resolution": graph_resolution,
        "research_plan": research_plan or None,
        "nodes": report_nodes,
        "evidence_claims": [_claim_summary(claim) for claim in claims],
        "evidence_coverage": {
            "definition": "Fraction of graph nodes whose node forecast cites at least one persisted Evidence Claim.",
            "covered_units": covered_nodes,
            "total_units": total_nodes,
            "rate": round(covered_nodes / total_nodes, 12) if total_nodes else None,
            "covered_node_ids": covered_node_ids,
            "cited_claim_count": len(cited_claim_ids),
            "persisted_claim_count": len(claims),
        },
        "calculation": {
            "method": aggregation.get("method"),
            "formula": aggregation.get("formula")
            or (LOG_ODDS_FORMULA if aggregation.get("method") == LOG_ODDS_METHOD else None),
            "normalization_denominator": aggregation.get("normalization_denominator"),
            "unbounded_probability": aggregation.get("unbounded_probability"),
            "final_probability": aggregation.get("final_probability", aggregation.get("ensemble_probability")),
            "node_contributions": aggregation.get("node_contributions") or [],
            "trace": aggregation.get("calculation_trace") or [],
        },
        "failures": failures,
        "research_reliability": {
            "status": (
                "failed"
                if final_probability is None and failures
                else "reduced_confidence"
                if reduced_failures
                else "complete"
            ),
            "failed_node_count": len(
                {failure.get("node_id") for failure in failures if failure.get("node_id")}
            ),
            "excluded_node_count": len(
                {
                    failure.get("node_id")
                    for failure in reduced_failures
                    if failure.get("node_id")
                }
            ),
            "critical_failure_count": len(critical_failures),
        },
        "final_answer": {
            "status": "completed" if final_probability is not None else "failed",
            "probability": final_probability,
            "statement": (
                (
                    f"The {profile_id} probability is {final_probability} with "
                    "reduced research confidence."
                    if reduced_failures
                    else f"The {profile_id} probability is {final_probability}."
                )
                if final_probability is not None
                else "No final probability was produced because graph execution was incomplete."
            ),
        },
    }


def v1_report_markdown(report: dict[str, Any]) -> list[str]:
    """Render the structured V1 report without losing its provenance groupings."""

    coverage = report.get("evidence_coverage") or {}
    calculation = report.get("calculation") or {}
    lines = [
        "## V1 Forecast Graph report",
        "",
        f"**Profile:** {report.get('profile_id')}",
        f"**Final probability:** {report.get('final_probability')}",
        (
            "**Evidence coverage:** "
            f"{coverage.get('covered_units', 0)}/{coverage.get('total_units', 0)} graph nodes "
            f"({coverage.get('rate')})"
        ),
        "",
    ]
    graph = report.get("graph") or {}
    graph_resolution = report.get("graph_resolution") or {}
    generation_audit = graph.get("generation_audit") or {}
    if graph_resolution or generation_audit:
        lines.extend([
            "### Graph generation audit",
            f"Graph: {graph.get('id')} version {graph.get('version')}",
            f"Resolution: {graph_resolution.get('status') or 'unavailable'}",
            f"Model request issued for this run: {graph_resolution.get('model_request_issued')}",
            f"Provider/model: {generation_audit.get('provider')} / {generation_audit.get('model')}",
            f"Schema: {generation_audit.get('schema_name')}",
            f"Request ID: {generation_audit.get('provider_request_id') or 'unavailable'}",
            f"Output cap: {generation_audit.get('requested_max_output_tokens')}",
            f"Finish reason: {generation_audit.get('finish_reason') or 'unavailable'}",
            f"Refusal: {generation_audit.get('refusal_present')} "
            f"({generation_audit.get('refusal_category') or 'none'})",
            f"Completion/reasoning/visible tokens: {generation_audit.get('completion_tokens')} / "
            f"{generation_audit.get('reasoning_tokens')} / "
            f"{generation_audit.get('visible_output_tokens')}",
            f"Content characters: {generation_audit.get('content_character_count')}",
            f"JSON/schema/strict-schema/domain valid: {generation_audit.get('json_parsing_succeeded')} / "
            f"{generation_audit.get('schema_validation_succeeded')} / "
            f"{generation_audit.get('strict_schema_validation_succeeded')} / "
            f"{generation_audit.get('domain_validation_succeeded')}",
            f"Errors: {generation_audit.get('errors') or []}",
            f"Schema errors: {generation_audit.get('schema_validation_errors') or []}",
            f"Domain errors: {generation_audit.get('domain_validation_errors') or []}",
            f"Prompt version: {generation_audit.get('prompt_version')}",
            f"Generated at: {generation_audit.get('generated_at')}",
            "",
        ])
    lines.append("### Graph nodes and node forecasts")
    for node in report.get("nodes") or []:
        lines.extend(
            [
                "",
                f"#### {node.get('node_type')}: {node.get('question')}",
                f"Probability: {node.get('probability')}",
                f"Confidence: {node.get('confidence')}",
                f"Model used: {node.get('model_used')}",
                f"Normalized weight: {node.get('normalized_weight')}",
            ]
        )
        if node.get("weighted_log_odds_contribution") is not None:
            lines.extend(
                [
                    f"Log odds: {node.get('log_odds')}",
                    f"Weighted log-odds contribution: {node.get('weighted_log_odds_contribution')}",
                ]
            )
        else:
            lines.append(f"Probability contribution: {node.get('probability_contribution')}")
        if node.get("failures"):
            lines.append(f"Failure impact: {node.get('failure_impact')}")
            lines.append(f"Critical node: {node.get('critical_node')}")
            queries = node.get("queries_attempted") or []
            if queries:
                lines.append("Queries attempted:")
                lines.extend(f"- {query}" for query in queries)
        lines.extend(
            [
                node.get("reasoning") or "No node reasoning was produced.",
                "Uncertainty:",
            ]
        )
        uncertainty_notes = node.get("uncertainty_notes") or []
        if uncertainty_notes:
            lines.extend(f"- {note}" for note in uncertainty_notes)
        else:
            lines.append("- No uncertainty notes were produced.")
        lines.extend(["", "Supporting evidence:"])
        supporting = node.get("supporting_evidence") or []
        if supporting:
            for claim in supporting:
                lines.append(f"- {claim.get('claim')}")
                lines.append(f"  - Excerpt: {claim.get('excerpt')}")
                lines.append(f"  - Source: {claim.get('source_url')}")
                lines.append(f"  - Temporal provenance: {claim.get('temporal_quality_label')}")
                lines.append(
                    f"  - Publication date: {claim.get('publication_date') or 'unavailable'} "
                    f"(verified={bool(claim.get('publication_date_verified'))})"
                )
                lines.append(f"  - Source available at: {claim.get('source_available_at')}")
                lines.append(f"  - Retrieved at: {claim.get('retrieval_date')}")
                lines.append(
                    f"  - Temporal basis: {claim.get('temporal_basis')} · "
                    f"cutoff verified={bool(claim.get('cutoff_verified'))}"
                )
        else:
            lines.append("- None cited.")
        lines.append("Opposing evidence:")
        opposing = node.get("opposing_evidence") or []
        if opposing:
            for claim in opposing:
                lines.append(f"- {claim.get('claim')}")
                lines.append(f"  - Excerpt: {claim.get('excerpt')}")
                lines.append(f"  - Source: {claim.get('source_url')}")
                lines.append(f"  - Temporal provenance: {claim.get('temporal_quality_label')}")
                lines.append(
                    f"  - Publication date: {claim.get('publication_date') or 'unavailable'} "
                    f"(verified={bool(claim.get('publication_date_verified'))})"
                )
                lines.append(f"  - Source available at: {claim.get('source_available_at')}")
                lines.append(f"  - Retrieved at: {claim.get('retrieval_date')}")
                lines.append(
                    f"  - Temporal basis: {claim.get('temporal_basis')} · "
                    f"cutoff verified={bool(claim.get('cutoff_verified'))}"
                )
        else:
            lines.append("- None cited.")
    node_contributions = calculation.get("node_contributions") or []
    if node_contributions:
        lines.extend(["", "### Node contributions"])
        for contribution in node_contributions:
            lines.append(
                "- "
                f"{contribution.get('node_question') or contribution.get('node_id')}: "
                f"p={contribution.get('input_probability')}, "
                f"weight={contribution.get('normalized_weight')}, "
                f"log_odds={contribution.get('log_odds')}, "
                f"contribution={contribution.get('weighted_log_odds_contribution')}"
            )
    lines.extend(
        [
            "",
            "### Calculation trace",
            f"Method: {calculation.get('method')}",
            calculation.get("formula") or "",
            "",
        ]
    )
    for step in calculation.get("trace") or []:
        lines.append(f"- {step}")
    failures = report.get("failures") or []
    if failures:
        lines.extend(["", "### Execution failures"])
        for failure in failures:
            node_suffix = f" for node {failure.get('node_id')}" if failure.get("node_id") else ""
            lines.append(
                f"- {failure.get('stage')}{node_suffix}: "
                f"{failure.get('error_code')}: {failure.get('error_message')}"
            )
    final_answer = report.get("final_answer") or {}
    lines.extend(
        [
            "",
            "### Final answer",
            final_answer.get("statement") or "No final answer was produced.",
        ]
    )
    return lines

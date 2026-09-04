"""Personal V1 orchestration, separate from legacy graph probability execution."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forecastlab.budget import Budget, estimate_prompt_tokens
from forecastlab.budgeted_provider import BudgetedModelProvider
from forecastlab.deadline import check_deadline
from forecastlab.errors import BudgetExceeded, PermanentProviderError
from forecastlab.graph_research import GraphResearchExecutor
from forecastlab.macro import MacroDataError, MacroSpec, fetch_macro
from forecastlab.prompts import PromptBundle
from forecastlab.root_event import (
    ROLES,
    EvidenceAssessments,
    RootEstimate,
    aggregate_root_estimates,
    assess_packet,
    contract_hash,
    digest,
    strict_schema,
)
from forecastlab.run_cache import RunCache
from forecastlab.schemas import EvidenceClaim, ForecastContract, ForecastGraph, ForecastNode
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.graphs import forecast_graph_from_row, store_forecast_graph
from forecastlab_api.manual_evidence import manual_evidence_documents_for_run
from forecastlab_api.models import ForecastGraphRow, ForecastRun, ForecastVersion, PersonalForecast, ResearchTrack
from forecastlab_api.secrets import load_secrets
from forecastlab_api.v1_execution import persist_node_research


def research_graph(session: Session, run: ForecastRun, contract: ForecastContract) -> ForecastGraph:
    graph_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"root-research-v1:{run.id}"))
    existing = session.get(ForecastGraphRow, graph_id)
    if existing:
        return forecast_graph_from_row(existing)
    topics = [
        ("resolver", f"What official measurement, release and revision rules resolve {contract.normalized_question}?"),
        ("base_rate", f"What quantitative historical reference class bears on {contract.normalized_question}?"),
        ("driver", f"What latest factual observations and drivers bear on {contract.normalized_question}?"),
        ("adversarial", f"What contrary evidence and methodological limitations bear on {contract.normalized_question}?"),
    ]
    version = session.scalar(select(func.max(ForecastGraphRow.version)).where(ForecastGraphRow.contract_id == contract.id)) or 0
    graph = ForecastGraph(id=graph_id, contract_id=contract.id, version=version + 1, status="approved",
        created_at=utcnow(), generation_model="deterministic_research_graph_v1", root_question=contract.normalized_question,
        nodes=[ForecastNode(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{graph_id}:{kind}")), graph_id=graph_id,
                question=question, node_type=kind, importance_weight=0.25,
                preferred_sources=[contract.authoritative_source], required_output_type="structured_categorical")
               for kind, question in topics])
    store_forecast_graph(session, graph)
    session.commit()
    return graph


def _macro_packet(snapshot: dict) -> list[dict]:
    observations = snapshot["observations"]
    if not observations:
        return []
    latest = observations[-1]
    history = [{"period": row["period"], "value": row["value"]} for row in observations]
    # Numeric transforms are deterministic and backed by the retained raw API
    # response. They do not pretend to be quotations from a prose document.
    return [{
        "schema_version": "evidence_assessment_v1", "claim_id": "macro:" + snapshot["raw_hash"],
        "classification": "background", "relevant": True, "usable": True,
        "required_sections": ["current_conditions"] + (["reference_class"] if len(history) >= 24 else []),
        "reason": "Validated official series; deterministic units and transformations; retained raw response",
        "claim": f"{snapshot['indicator']}: {json.dumps(history)}; units={latest['units']}; adjustment={latest['seasonal_adjustment']}",
        "quote": "", "url": latest["source_url"], "title": f"BLS {snapshot['indicator']} observations",
        "primary_source": True, "source_lineage": "agency:bls", "source_available_at": latest["available_at"],
        "extraction_method": "structured_macro_adapter_v1", "revision_basis": latest["revision_basis"],
    }]


def execute_root_forecast(session: Session, *, run: ForecastRun, profile, context, model, search,
                          ledger, progress, cache=None, prior_elapsed_seconds=0.0) -> None:
    record = session.get(PersonalForecast, run.id)
    if not record or record.contract_json == "{}":
        raise ValueError("approved_personal_contract_snapshot_required")
    contract = ForecastContract.model_validate_json(record.contract_json)
    if contract.approval_errors() or contract.status != "approved" or contract.resolution_date is None:
        raise ValueError("approved_personal_contract_required")
    prompts = PromptBundle.model_validate_json(record.prompts_json)
    checkpoint = json.loads(record.result_json or "{}")
    if record.outcome_status in {"forecasted", "insufficient_evidence"}:
        return
    checkpoint.setdefault("execution_policy", {"profile_version": profile.version,
        "research_reasoning_effort": "minimal", "estimate_reasoning_effort": "low",
        "research_plan_output_tokens": 4096, "estimate_output_tokens": 4096})
    budget = Budget.from_persisted(profile, ledger.totals(run.id), provider=context.model_provider,
        model=context.model_name, search_provider=context.search_provider, prior_elapsed_seconds=prior_elapsed_seconds)
    cache = cache or RunCache.create(run_id=run.id, model_provider=context.model_provider,
        search_provider=search.name, mode=run.mode, as_of=run.as_of, configuration_hash=context.configuration_hash)
    graph = research_graph(session, run, contract)
    context_json = json.loads(run.execution_context_json)
    context_json.update({"forecast_contract_id": contract.id, "forecast_graph_id": graph.id,
                         "root_contract_hash": contract_hash(contract)})
    run.execution_context_json = json.dumps(context_json)
    session.commit()
    claims = [EvidenceClaim.model_validate(item) for item in checkpoint.get("raw_claims", [])]
    finished_nodes = set(checkpoint.get("finished_nodes", []))
    gaps: list[str] = []
    estimates: list[RootEstimate] = []
    packet: list[dict] = []
    aggregation = None

    def save() -> None:
        checkpoint["raw_claims"] = [claim.model_dump(mode="json") for claim in claims]
        checkpoint["finished_nodes"] = sorted(finished_nodes)
        record.result_json = json.dumps(checkpoint)
        run.budget_json = json.dumps(budget.snapshot())
        session.commit()

    def record_model_output(stage, response) -> None:
        if response.diagnostics:
            checkpoint.setdefault("model_output_diagnostics", []).append({
                "stage": stage, **response.diagnostics.audit_payload()})
            save()
            if response.diagnostics.finish_reason == "length":
                raise ValueError(f"model_output_limit:{stage}")

    try:
        # Reserve tokens AND estimated dollars, not just logical call counts.
        held = [budget.reserve_model_call(f"reserved_root:{role}", estimated_input_tokens=24000,
                                          max_output_tokens=4096) for role in ROLES]
        macro = MacroSpec.model_validate_json(record.macro_json) if record.macro_json != "{}" else None
        if macro and "macro_snapshot" not in checkpoint and run.mode != "demo":
            progress("evidence", "Retrieving official macro observations", 0.12)
            budget.add_fetch("macro_observations")
            try:
                snapshot = fetch_macro(macro, as_of=as_utc(run.as_of) if run.mode == "backtest" else None,
                                       fred_api_key=load_secrets().get("fred_api_key"),
                                       timeout=budget.remaining_seconds("macro_observations"))
                checkpoint["macro_snapshot"] = snapshot.model_dump(mode="json")
            except MacroDataError as exc:
                if str(exc).startswith(("macro_request_failed", "bls_request_not_succeeded")):
                    raise PermanentProviderError(f"Macro data provider failed: {exc}") from exc
                gaps.append(str(exc))
            save()
        for index, node in enumerate(graph.nodes):
            if node.id in finished_nodes:
                continue
            progress("research", f"Researching {node.node_type.replace('_', ' ')}", 0.18 + index * 0.12)
            result = GraphResearchExecutor(model=model, search=search, profile=profile, budget=budget, cache=cache,
                run_id=run.id, mode=run.mode, as_of=as_utc(run.as_of) if run.as_of else None,
                allow_local_fixtures=context.fixture_evidence_allowed,
                max_queries_per_node=2, max_fetches_per_node=2, max_evidence_claims=8,
                research_plan_output_tokens=4096, evidence_extraction_output_tokens=4096,
                attached_documents=manual_evidence_documents_for_run(session, run=run), prompt_bundle=prompts).execute(node)
            persist_node_research(session, run=run, evidence=result.evidence, rejected=result.rejected, claims=result.claims)
            claims.extend(result.claims)
            finished_nodes.add(node.id)
            checkpoint.setdefault("research_diagnostics", []).append({"node_id": node.id,
                "queries": result.queries_attempted, "sources_checked": result.sources_checked,
                "extraction_errors": result.extraction_errors, "planning_warnings": result.planning_warnings,
                "failure": result.failure.reason if result.failure else None})
            save()
        progress("evidence", "Checking relevance and required evidence", 0.70)
        candidates = [c for c in claims if c.extraction_method != "document_fallback"][:32]
        if candidates and model.name != "mock":
            system, _ = prompts.get("root_evidence")
            response = BudgetedModelProvider(model, budget, stage="assess_root_evidence").complete_json(
                system=system, user=json.dumps({"contract": contract.model_dump(mode="json"),
                    "claims": [{"id": c.id, "claim": c.claim, "excerpt": c.excerpt, "url": c.source_url,
                                "source_available_at": c.source_available_at.isoformat()} for c in candidates]}),
                schema_name="root_evidence", json_schema=strict_schema(EvidenceAssessments), max_output_tokens=4096,
                reasoning_effort="minimal")
            record_model_output("assess_root_evidence", response)
            assessments = EvidenceAssessments.model_validate(response.parsed or json.loads(response.content)).assessments
        else:
            # Demo plumbing never manufactures a relevance assessment or an accuracy result.
            assessments = []
        packet, _ = assess_packet(claims, assessments)
        if checkpoint.get("macro_snapshot"):
            packet.extend(_macro_packet(checkpoint["macro_snapshot"]))
        covered = {section for item in packet if item["usable"] for section in item["required_sections"]}
        gaps.extend(f"missing_{section}_evidence" for section in ("resolution", "reference_class", "current_conditions") if section not in covered)
        if not any(item.get("primary_source") and item["usable"] for item in packet):
            gaps.append("relevant_primary_source_required")
        if macro and not checkpoint.get("macro_snapshot"):
            gaps.append("official_macro_observations_required")
        checkpoint["evidence_assessments"] = packet
        checkpoint.setdefault("forecast_cutoff", as_utc(run.as_of).isoformat() if run.as_of else utcnow().isoformat())
        save()
        if not gaps:
            as_of = checkpoint["forecast_cutoff"]
            system, _ = prompts.get("root_event")
            for index, role in enumerate(ROLES):
                progress("forecast", f"Estimating the approved event: {role.replace('_', ' ')}", 0.78 + index * 0.06)
                user = json.dumps({"contract": contract.model_dump(mode="json"), "contract_hash": contract_hash(contract),
                    "evidence_packet_hash": digest(packet), "target_question": contract.normalized_question,
                    "resolution_date": contract.resolution_date.isoformat(), "as_of": as_of,
                    "role": role, "evidence": [item for item in packet if item["usable"]]})
                if estimate_prompt_tokens(system, user) > 24000:
                    raise ValueError("root_evidence_packet_exceeds_reserved_context")
                budget.release_reservation(held[index])
                response = BudgetedModelProvider(model, budget, stage=f"root_estimate:{role}").complete_json(
                    system=system, user=user, schema_name="root_event", json_schema=strict_schema(RootEstimate),
                    max_output_tokens=4096, reasoning_effort="low")
                record_model_output(f"root_estimate:{role}", response)
                estimate = RootEstimate.model_validate(response.parsed or json.loads(response.content))
                if estimate.role != role:
                    raise ValueError("estimate_role_mismatch")
                estimates.append(estimate)
                checkpoint["estimates"] = [item.model_dump(mode="json") for item in estimates]
                save()
            check_deadline(ledger, "aggregate_root")
            aggregation = aggregate_root_estimates(contract, packet, estimates, as_of=as_of)
    except BudgetExceeded as exc:
        if exc.reason == "max_wall_clock_seconds":
            checkpoint["evidence_gaps"] = [f"execution_timeout:{exc.stage}"]
            save()
            raise
        gaps.append(f"budget_or_time_exhausted:{exc}")
    except (ValueError, TypeError, KeyError) as exc:
        gaps.append(f"invalid_forecast_artifact:{type(exc).__name__}:{str(exc)[:180]}")
        gaps.extend(str(reason) for reason in getattr(exc, "reasons", []))
    check_deadline(ledger, "complete_root")
    outcome = "forecasted" if aggregation else "insufficient_evidence"
    for reservation in budget.reservations:
        if reservation.stage.startswith("reserved_root:") and not reservation.released:
            budget.release_reservation(reservation)
    run.budget_json = json.dumps(budget.snapshot())
    checkpoint.update({"schema_version": "personal_forecast_v1", "outcome_status": outcome,
        "probability": aggregation["probability"] if aggregation else None,
        "evidence_gaps": sorted(set(gaps)), "evidence_assessments": packet,
        "estimates": [item.model_dump(mode="json") for item in estimates], "aggregation": aggregation,
        "shared_evidence": True, "macro_validation_domain": "us_macro" if macro else "general_unvalidated",
        "developments_to_watch": sorted({s for item in estimates for s in item.developments_to_watch}),
        "contract": contract.model_dump(mode="json")})
    record.outcome_status = outcome
    record.result_json = json.dumps(checkpoint)
    run.aggregation_json = json.dumps(aggregation or {})
    run.status = "completed"
    run.finished_at = utcnow()
    run.progress_stage = "report"
    run.progress_pct = 100
    run.progress_message = "Forecast ready" if aggregation else "Probability withheld; review evidence gaps"
    run.question.status = "completed"
    run.question.stale = False
    previous = session.scalar(select(ForecastVersion).where(ForecastVersion.question_id == run.question_id)
                               .order_by(ForecastVersion.created_at.desc()).limit(1))
    session.add(ForecastVersion(id=str(uuid.uuid4()), question_id=run.question_id, run_id=run.id,
        ensemble_probability=checkpoint["probability"], aggregation_json=run.aggregation_json,
        track_spread=aggregation["spread"] if aggregation else None,
        raw_track_probabilities_json=json.dumps({item.role: item.probability for item in estimates}),
        evidence_ids_json=json.dumps([item["claim_id"] for item in packet if item["usable"]]),
        previous_version_id=previous.id if previous else None))
    for estimate in estimates:
        session.add(ResearchTrack(id=str(uuid.uuid4()), run_id=run.id, track_type=estimate.role,
            probability=estimate.probability, reasoning_summary=estimate.reasoning,
            unresolved_json=json.dumps(estimate.uncertainties), independent=False, status="completed"))
    session.commit()

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from forecastlab.budget import Budget
from forecastlab.errors import GraphForecastExecutionError
from forecastlab.execution import ExecutionContext
from forecastlab.graph_aggregation import RelationshipMassConservingLogOddsAggregator
from forecastlab.graph_execution import ForecastNodeExecution, GraphNodeForecastResult
from forecastlab.material_node_coverage import assess_material_node_plan
from forecastlab.profiles import load_profile
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.research_planning import ResearchPlan
from forecastlab.scenario_synthesis import SCENARIO_SYNTHESIS_CALL_KIND
from forecastlab.schemas import (
    EvidenceClaim,
    ForecastContract,
    ForecastGraph,
    ForecastGraphGenerationAudit,
    ForecastNode,
    ForecastNodeRun,
)
from forecastlab.timeutil import as_utc, parse_datetime
from forecastlab_api.config import ROOT, settings
from forecastlab_api.contracts import (
    approve_forecast_contract,
    forecast_contract_from_row,
    store_forecast_contract,
)
from forecastlab_api.db import SessionLocal
from forecastlab_api.graph_executor import GraphForecastExecutor
from forecastlab_api.graphs import store_forecast_graph
from forecastlab_api.main import get_run
from forecastlab_api.migrate import alembic_config, apply_schema
from forecastlab_api.models import (
    EvidenceClaimRow,
    EvidenceItem,
    EvidenceSufficiencyAssessmentRow,
    ForecastAggregationRow,
    ForecastNodeRunRow,
    ForecastRun,
    ForecastRunAttempt,
    ForecastVersion,
    GraphExecutionFailureRow,
    Job,
    MaterialNodeCoverageAssessmentRow,
    ProviderCallLedger,
    Question,
    ResearchPlanRow,
    ScenarioSynthesisRow,
)
from forecastlab_api.pipeline import apply_execution_context, apply_execution_limits, resolve_for_question
from forecastlab_api.reports import v1_report_markdown
from forecastlab_api.scenario_synthesis import scenario_synthesis_from_row
from forecastlab_api.usage_ledger import PersistentUsageLedger, apply_totals_to_run

FIXTURE_LABEL = "synthetic_release_verification_only"
FIXTURE_PATH = ROOT / "fixtures" / "release" / "private_v1_verification_v1.json"
PROFILE_ID = "graph_forecaster_v1"
PROFILE_VERSION = 9
FIXED_TIME = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_NAMESPACE = uuid.UUID("31949b68-06fd-5b15-b249-295702dadb33")
_TERMINAL_LEDGER_STATUSES = {"succeeded", "failed", "released"}
_TERMINAL_ATTEMPT_STATUSES = {"completed", "failed"}


class ReleaseVerificationError(RuntimeError):
    """Fail-closed release-verification error."""


class _ReleaseMockModelProvider(MockModelProvider):
    """Commit deterministic gate artifacts before the isolated mock ledger writes."""

    def __init__(self, session: Session, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._release_session = session

    def complete_json(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        self._release_session.commit()
        return super().complete_json(**kwargs)


def _stable_id(journey: str, kind: str, key: str = "") -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{FIXTURE_LABEL}:{journey}:{kind}:{key}"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if payload.get("fixture_label") != FIXTURE_LABEL:
        raise ReleaseVerificationError("release_fixture_label_mismatch")
    if int(payload.get("fixture_version") or 0) != 1:
        raise ReleaseVerificationError("release_fixture_version_mismatch")
    return payload


def _normalized_sql_type(value: str) -> str:
    normalized = (value or "").upper()
    if normalized in {"BOOLEAN", "BOOL"}:
        return "BOOLEAN"
    if normalized.startswith("VARCHAR"):
        return normalized
    if normalized in {"DATETIME", "TIMESTAMP"}:
        return "DATETIME"
    if normalized in {"FLOAT", "REAL", "DOUBLE"}:
        return "FLOAT"
    return normalized


def _normalized_default(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().strip("'\"")
    if normalized.casefold() == "false":
        return "0"
    if normalized.casefold() == "true":
        return "1"
    return normalized


def _schema_snapshot(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    tables: dict[str, Any] = {}
    for table_name in sorted(inspector.get_table_names()):
        if table_name == "alembic_version":
            continue
        columns = {
            str(column["name"]): {
                "type": _normalized_sql_type(str(column["type"])),
                "nullable": bool(column.get("nullable")),
                "default": _normalized_default(column.get("default")),
                "primary_key": bool(column.get("primary_key")),
            }
            for column in inspector.get_columns(table_name)
        }
        foreign_keys = sorted(
            {
                (
                    tuple(item.get("constrained_columns") or []),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or []),
                )
                for item in inspector.get_foreign_keys(table_name)
            }
        )
        unique_columns = sorted(
            {
                tuple(
                    sorted(
                        str(value)
                        for value in item.get("column_names") or []
                        if value is not None
                    )
                )
                for item in inspector.get_unique_constraints(table_name)
            }
            | {
                tuple(
                    sorted(
                        str(value)
                        for value in item.get("column_names") or []
                        if value is not None
                    )
                )
                for item in inspector.get_indexes(table_name)
                if item.get("unique")
            }
        )
        tables[table_name] = {
            "columns": columns,
            "primary_key": list(
                inspector.get_pk_constraint(table_name).get("constrained_columns")
                or []
            ),
            "foreign_keys": foreign_keys,
            "unique_columns": unique_columns,
        }
    return tables


def _schema_differences(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    if set(left) != set(right):
        differences.append(f"tables:{sorted(set(left) ^ set(right))}")
    for table_name in sorted(set(left) & set(right)):
        fresh = left[table_name]
        upgraded = right[table_name]
        if set(fresh["columns"]) != set(upgraded["columns"]):
            differences.append(
                f"{table_name}.columns:"
                f"{sorted(set(fresh['columns']) ^ set(upgraded['columns']))}"
            )
            continue
        for column_name, fresh_column in fresh["columns"].items():
            upgraded_column = upgraded["columns"][column_name]
            for key in ("type", "nullable", "primary_key"):
                if fresh_column[key] != upgraded_column[key]:
                    differences.append(
                        f"{table_name}.{column_name}.{key}:"
                        f"{fresh_column[key]}!={upgraded_column[key]}"
                    )
            if fresh_column["default"] != upgraded_column["default"]:
                if _documented_default_metadata_difference(
                    fresh_column, upgraded_column
                ):
                    continue
                differences.append(
                    f"{table_name}.{column_name}.default:"
                    f"{fresh_column['default']}!={upgraded_column['default']}"
                )
        for key in ("primary_key", "foreign_keys", "unique_columns"):
            if fresh[key] != upgraded[key]:
                differences.append(
                    f"{table_name}.{key}:{fresh[key]}!={upgraded[key]}"
                )
    return differences


def _documented_default_metadata_difference(
    fresh_column: dict[str, Any], upgraded_column: dict[str, Any]
) -> bool:
    defaults = {fresh_column["default"], upgraded_column["default"]}
    # SQLite records server defaults added during an upgrade differently from
    # the same defaults represented by application-level defaults at fresh head.
    return defaults <= {None, "0", "1", "{}", "[]"} or (
        fresh_column["nullable"] == upgraded_column["nullable"]
    )


def _documented_default_metadata_paths(
    fresh_schema: dict[str, Any], upgraded_schema: dict[str, Any]
) -> list[str]:
    paths: list[str] = []
    for table_name in sorted(set(fresh_schema) & set(upgraded_schema)):
        fresh_columns = fresh_schema[table_name]["columns"]
        upgraded_columns = upgraded_schema[table_name]["columns"]
        for column_name in sorted(set(fresh_columns) & set(upgraded_columns)):
            fresh_column = fresh_columns[column_name]
            upgraded_column = upgraded_columns[column_name]
            if fresh_column["default"] == upgraded_column["default"]:
                continue
            if _documented_default_metadata_difference(
                fresh_column, upgraded_column
            ):
                paths.append(f"{table_name}.{column_name}.default")
    return paths


def _database_integrity(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    return {
        "integrity_check": integrity[0] if integrity else None,
        "foreign_key_violation_count": len(foreign_keys),
        "alembic_revision": revision[0] if revision else None,
    }


def run_database_gate(*, fresh_database: Path, legacy_database: Path) -> dict[str, Any]:
    for database_path in (fresh_database, legacy_database):
        if database_path.exists():
            raise ReleaseVerificationError(
                f"release_database_path_must_not_exist:{database_path.name}"
            )
        database_path.parent.mkdir(parents=True, exist_ok=True)

    fresh_url = f"sqlite:///{fresh_database}"
    legacy_url = f"sqlite:///{legacy_database}"
    apply_schema(fresh_url)

    legacy_schema_path = ROOT / "tests" / "fixtures" / "legacy_mvp_schema.sql"
    legacy_sql = legacy_schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(legacy_sql)
        connection.execute(
            "INSERT INTO questions "
            "(id, original_text, normalized_text, question_type, created_at, "
            "forecast_deadline, status, notes, stale) "
            "VALUES (?, ?, NULL, 'binary', ?, NULL, 'draft', ?, 0)",
            (
                _stable_id("database", "legacy-question"),
                "Synthetic legacy release-verification row",
                FIXED_TIME.isoformat(),
                FIXTURE_LABEL,
            ),
        )
        connection.commit()
    apply_schema(legacy_url)

    fresh_engine = create_engine(fresh_url, future=True)
    legacy_engine = create_engine(legacy_url, future=True)
    try:
        fresh_schema = _schema_snapshot(fresh_engine)
        legacy_schema = _schema_snapshot(legacy_engine)
        differences = _schema_differences(fresh_schema, legacy_schema)
        documented_default_paths = _documented_default_metadata_paths(
            fresh_schema, legacy_schema
        )
        with legacy_engine.connect() as connection:
            legacy_marker_count = connection.execute(
                text("SELECT COUNT(*) FROM questions WHERE notes = :label"),
                {"label": FIXTURE_LABEL},
            ).scalar_one()
    finally:
        fresh_engine.dispose()
        legacy_engine.dispose()

    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(alembic_config(fresh_url)).get_current_head()
    result: dict[str, Any] = {
        "alembic_head": head,
        "fresh": _database_integrity(fresh_database),
        "legacy_upgrade": {
            **_database_integrity(legacy_database),
            "legacy_marker_row_preserved": legacy_marker_count == 1,
        },
        "schema_parity": {
            "passed": not differences,
            "comparison_basis": (
                "structural parity with documented SQLite server-default "
                "metadata representation differences reported separately"
            ),
            "differences": differences,
            "documented_default_metadata_difference_count": len(
                documented_default_paths
            ),
            "documented_default_metadata_paths": documented_default_paths,
            "fresh_raw_schema_sha256": _sha256_text(
                json.dumps(fresh_schema, sort_keys=True, separators=(",", ":"))
            ),
            "upgraded_raw_schema_sha256": _sha256_text(
                json.dumps(legacy_schema, sort_keys=True, separators=(",", ":"))
            ),
            "raw_schema_hashes_equal": fresh_schema == legacy_schema,
        },
    }
    required = (
        result["fresh"]["integrity_check"] == "ok",
        result["fresh"]["foreign_key_violation_count"] == 0,
        result["fresh"]["alembic_revision"] == head,
        result["legacy_upgrade"]["integrity_check"] == "ok",
        result["legacy_upgrade"]["foreign_key_violation_count"] == 0,
        result["legacy_upgrade"]["alembic_revision"] == head,
        result["legacy_upgrade"]["legacy_marker_row_preserved"],
        result["schema_parity"]["passed"],
    )
    if not all(required):
        raise ReleaseVerificationError("release_database_gate_failed")
    return result


def _build_graph(
    *,
    fixture: dict[str, Any],
    journey: str,
    contract: ForecastContract,
) -> ForecastGraph:
    graph_id = _stable_id(journey, "graph")
    node_ids = {
        str(spec["key"]): _stable_id(journey, "node", str(spec["key"]))
        for spec in fixture["nodes"]
    }
    nodes = [
        ForecastNode(
            id=node_ids[str(spec["key"])],
            graph_id=graph_id,
            parent_node_id=(
                node_ids[str(spec["parent"])] if spec.get("parent") else None
            ),
            question=str(spec["question"]),
            node_type=str(spec["node_type"]),  # type: ignore[arg-type]
            importance_weight=float(spec["importance_weight"]),
            dependencies=[node_ids[str(key)] for key in spec.get("dependencies") or []],
            preferred_sources=[str(value) for value in spec.get("preferred_sources") or []],
            required_output_type="probability",
            status="pending",
        )
        for spec in fixture["nodes"]
    ]
    generation_audit = ForecastGraphGenerationAudit(
        provider="mock",
        model="mock-forecast-v1",
        schema_name="forecast_graph",
        transport="canonical_v1",
        provider_request_id=f"mock-release-verification-{journey}",
        requested_max_output_tokens=4096,
        requested_max_completion_tokens=4096,
        requested_max_visible_output_tokens=4096,
        reasoning_effort="minimal",
        verbosity="low",
        finish_reason="stop",
        refusal_present=False,
        completion_tokens=0,
        reasoning_tokens=0,
        visible_output_tokens=0,
        token_split_available=True,
        token_split_interpretation="mock_fixture",
        content_character_count=0,
        json_parsing_succeeded=True,
        schema_validation_succeeded=True,
        strict_schema_validation_succeeded=True,
        domain_validation_succeeded=True,
        prompt_version="v1",
        generated_at=FIXED_TIME,
    )
    return ForecastGraph(
        id=graph_id,
        contract_id=contract.id,
        version=1,
        status="approved",
        created_at=FIXED_TIME,
        generation_model="mock:synthetic-release-verification-v1",
        root_question=contract.normalized_question,
        nodes=nodes,
        generation_audit=generation_audit,
    )


def _create_contract_graph(
    session: Session,
    *,
    fixture: dict[str, Any],
    journey: str,
) -> tuple[Question, ForecastContract, ForecastGraph]:
    question_id = _stable_id(journey, "question")
    if session.get(Question, question_id) is not None:
        raise ReleaseVerificationError(f"release_journey_already_exists:{journey}")
    resolution_date = parse_datetime(str(fixture["resolution_date"]))
    if resolution_date is None:
        raise ReleaseVerificationError("release_fixture_resolution_date_invalid")
    question = Question(
        id=question_id,
        original_text=f"[{FIXTURE_LABEL}:{journey}] {fixture['question']}",
        normalized_text=str(fixture["normalized_question"]),
        question_type="binary",
        created_at=FIXED_TIME,
        forecast_deadline=resolution_date,
        status="draft",
        notes=(
            "Synthetic release-verification fixture only. It is not evidence of "
            "forecasting quality or calibration."
        ),
        stale=False,
        requested_mode="demo",
        requested_profile_id=PROFILE_ID,
        is_benchmark=False,
    )
    session.add(question)
    session.flush()
    contract = ForecastContract(
        id=_stable_id(journey, "contract"),
        question_id=question_id,
        version=1,
        created_at=FIXED_TIME,
        created_by=FIXTURE_LABEL,
        original_question=str(fixture["question"]),
        normalized_question=str(fixture["normalized_question"]),
        yes_condition=str(fixture["yes_condition"]),
        no_condition=str(fixture["no_condition"]),
        resolution_date=resolution_date,
        authoritative_source=str(fixture["authoritative_source"]),
        fallback_sources=[str(value) for value in fixture["fallback_sources"]],
        resolution_method=str(fixture["resolution_method"]),
        ambiguity_notes="The fixture is synthetic and has one frozen resolver record.",
        cancellation_conditions="Cancel only if the frozen fixture is missing or corrupted.",
        resolver_risk_notes="No real-world resolver inference is permitted.",
        forecast_type="binary",
        geography="Synthetic",
        units="Synthetic percent",
        domain="release_verification",
        initial_reference_class="Frozen synthetic threshold events.",
        suggested_drivers=["base rate", "leading indicator", "counterevidence"],
        known_dependencies=["current driver depends on the base-rate context"],
        status="draft",
    )
    contract_row = store_forecast_contract(session, contract)
    approve_forecast_contract(session, contract_row)
    contract = forecast_contract_from_row(contract_row)
    graph = _build_graph(fixture=fixture, journey=journey, contract=contract)
    store_forecast_graph(session, graph)
    session.commit()
    return question, contract, graph


def _release_execution_context(question: Question, source_sha: str) -> ExecutionContext:
    context = resolve_for_question(
        question,
        profile_id=PROFILE_ID,
        mode="demo",
        synthetic_fixture_run=True,
        settings_data={
            "model_provider": "mock",
            "model_name": "mock-forecast-v1",
            "search_provider": "mock",
            "max_cost_usd": 5.0,
            "model_timeout_seconds": 60.0,
        },
    )
    return context.model_copy(update={"code_commit": source_sha})


def _node_runner(
    *,
    fixture: dict[str, Any],
    journey: str,
    negative: bool,
) -> Callable[..., GraphNodeForecastResult]:
    def run(**kwargs: Any) -> GraphNodeForecastResult:
        graph: ForecastGraph = kwargs["graph"]
        run_id = str(kwargs["run_id"])
        by_key = {
            str(spec["key"]): graph.nodes[index]
            for index, spec in enumerate(fixture["nodes"])
        }
        selected = [by_key[str(key)] for key in fixture["selected_node_keys"]]
        selected_ids = {node.id for node in selected}
        skipped = [node for node in graph.nodes if node.id not in selected_ids]
        plan = ResearchPlan(
            id=_stable_id(journey, "research-plan"),
            forecast_run_id=run_id,
            selected_nodes=[node.id for node in selected],
            skipped_nodes=[node.id for node in skipped],
            priority_scores={
                node.id: float(len(graph.nodes) - index)
                for index, node in enumerate(graph.nodes)
            },
            budget_allocation={
                "planner_version": "synthetic_release_verification_planner_v1",
                "fixture_label": FIXTURE_LABEL,
                "critical_node_ids": [],
                "selected_node_count": len(selected),
                "skipped_node_count": len(skipped),
                "scenario_synthesis_reservation": {
                    "model_calls": 1,
                    "reserved_input_tokens": 10000,
                    "reserved_output_tokens": 2048,
                },
            },
            created_at=FIXED_TIME,
        )
        material_audit = assess_material_node_plan(graph=graph, plan=plan)
        plan = plan.model_copy(
            update={
                "budget_allocation": {
                    **plan.budget_allocation,
                    "material_node_plan_audit": material_audit.model_dump(mode="json"),
                }
            }
        )
        kwargs["persist_research_plan"](plan)

        fallback_key = str(fixture["negative_fallback_node_key"])
        probabilities = {
            selected[index].id: float(fixture["node_probabilities"][index])
            for index in range(len(selected))
        }
        hosts = [str(host) for host in fixture["evidence_hosts"]]
        executions: list[ForecastNodeExecution] = []
        for node in graph.nodes:
            if node.id not in selected_ids:
                executions.append(
                    ForecastNodeExecution(
                        node=node,
                        node_run=None,
                        research_selected=False,
                        skip_reason="not_selected_by_frozen_release_plan",
                    )
                )
                continue
            selected_index = selected.index(node)
            node_key = str(fixture["selected_node_keys"][selected_index])
            host = hosts[selected_index]
            item_id = _stable_id(journey, "evidence-item", node_key)
            claim_id = _stable_id(journey, "evidence-claim", node_key)
            url = f"https://{host}/evidence/{node_key}"
            source_class = "primary" if selected_index == 0 else "secondary"
            extraction_method = (
                "document_fallback"
                if negative and node_key == fallback_key
                else "mock_structured"
            )
            evidence_record = {
                "id": item_id,
                "forecast_node_id": node.id,
                "subquestion": node.question,
                "url": url,
                "title": f"Frozen synthetic evidence for {node_key}",
                "publisher": host,
                "published_at": FIXED_TIME.isoformat(),
                "retrieved_at": FIXED_TIME.isoformat(),
                "source_available_at": FIXED_TIME.isoformat(),
                "temporal_basis": "publication_date",
                "publication_date_source": "synthetic_release_fixture",
                "publication_date_verified": True,
                "excerpt": f"Frozen excerpt for the {node_key} release-verification node.",
                "content_hash": _sha256_text(f"{journey}:{node_key}:content"),
                "source_class": source_class,
                "as_of_eligible": True,
                "rejected": False,
                "status_code": 200,
                "published_at_unknown": False,
                "snapshot_verification_status": "synthetic_release_fixture",
            }
            claim = EvidenceClaim(
                id=claim_id,
                evidence_item_id=item_id,
                forecast_node_id=node.id,
                claim=f"Frozen synthetic claim for {node_key}.",
                excerpt=str(evidence_record["excerpt"]),
                source_url=url,
                source_title=str(evidence_record["title"]),
                publisher=host,
                publication_date=FIXED_TIME,
                publication_date_source="synthetic_release_fixture",
                publication_date_verified=True,
                retrieval_date=FIXED_TIME,
                source_available_at=FIXED_TIME,
                temporal_basis="publication_date",
                supports_or_refutes=(
                    "refutes" if node.node_type == "adversarial" else "supports"
                ),
                confidence=0.0,
                source_quality=0.0,
                primary_source=False,
                as_of_eligible=True,
                cutoff_verified=True,
                source_class=source_class,  # type: ignore[arg-type]
                extraction_method=extraction_method,  # type: ignore[arg-type]
                source_host=host,
            )
            kwargs["persist_research"](node, [evidence_record], [], [claim])
            supporting = [] if claim.supports_or_refutes == "refutes" else [claim_id]
            opposing = [claim_id] if claim.supports_or_refutes == "refutes" else []
            node_run = ForecastNodeRun(
                id=_stable_id(journey, "node-run", node_key),
                run_id=run_id,
                node_id=node.id,
                probability=probabilities[node.id],
                confidence=0.5,
                reasoning=(
                    "Deterministic synthetic release-verification reasoning grounded "
                    f"only in claim {claim_id}."
                ),
                supporting_claim_ids=supporting,
                opposing_claim_ids=opposing,
                uncertainty_notes=[
                    "Synthetic fixture evidence cannot establish real-world forecast quality."
                ],
                model_used="mock:synthetic-release-verification-v1",
                uncertainty=0.5,
                created_at=FIXED_TIME,
            )
            executions.append(
                ForecastNodeExecution(
                    node=node,
                    node_run=node_run,
                    evidence=[evidence_record],
                    claims=[claim],
                    queries_attempted=[f"synthetic release query for {node_key}"],
                    sources_checked=[
                        {
                            "url": url,
                            "outcome": "accepted_synthetic_release_fixture",
                        }
                    ],
                    research_selected=True,
                )
            )

        budget = Budget(
            kwargs["profile"],
            provider="mock",
            model="mock-forecast-v1",
            search_provider="mock",
        )
        budget.freeze_model_call_envelope(
            planner_version="synthetic_release_verification_planner_v1",
            planned_calls_by_kind={SCENARIO_SYNTHESIS_CALL_KIND: 1},
        )
        return GraphNodeForecastResult(
            contract=kwargs["contract"],
            graph=graph,
            nodes=executions,
            prompt_versions={"forecast_node": "v3"},
            budget=budget.snapshot(),
            stopped_early=False,
            stop_reason=None,
            stop_stage=None,
            fixture_evidence_used=True,
            research_plan=plan,
            budget_controller=budget,
        )

    return run


def _execute_journey(
    session: Session,
    *,
    fixture: dict[str, Any],
    source_sha: str,
    journey: str,
    negative: bool,
) -> dict[str, Any]:
    question, contract, graph = _create_contract_graph(
        session,
        fixture=fixture,
        journey=journey,
    )
    context = _release_execution_context(question, source_sha)
    run = ForecastRun(
        id=_stable_id(journey, "forecast-run"),
        question_id=question.id,
        profile_id=PROFILE_ID,
        mode="demo",
        started_at=FIXED_TIME,
        status="running",
        progress_stage="graph",
        progress_message="Synthetic release verification",
        synthetic_fixture_run=True,
        fixture_evidence_used=True,
    )
    apply_execution_context(run, context)
    run.synthetic_fixture_run = True
    run.fixture_evidence_used = True
    question.status = "running"
    session.add(run)
    session.commit()

    profile = apply_execution_limits(load_profile(PROFILE_ID), context)
    if profile.version != PROFILE_VERSION:
        raise ReleaseVerificationError(
            f"private_v1_profile_version_mismatch:{profile.version}"
        )
    ledger = PersistentUsageLedger(
        SessionLocal,
        max_cost_usd=profile.max_estimated_cost_usd,
        max_tokens=profile.max_tokens,
    )
    attempt = ledger.begin_attempt(
        run_id=run.id,
        job_id=None,
        attempt_number=1,
    )
    model = _ReleaseMockModelProvider(
        session,
        ledger=ledger,
        run_id=run.id,
        run_attempt_id=attempt.id,
    )
    search = MockSearchProvider(
        ledger=ledger,
        run_id=run.id,
        run_attempt_id=attempt.id,
    )
    expected_failure: GraphForecastExecutionError | None = None
    try:
        GraphForecastExecutor(
            session,
            run=run,
            profile=profile,
            execution=context,
            model=model,
            search=search,
            allow_local_fixtures=True,
            ledger=ledger,
            graph_resolver=lambda *_args, **_kwargs: (contract, graph),
            node_runner=_node_runner(
                fixture=fixture,
                journey=journey,
                negative=negative,
            ),
        ).execute()
    except GraphForecastExecutionError as exc:
        expected_failure = exc
        if not negative:
            raise
    else:
        if negative:
            raise ReleaseVerificationError("negative_journey_unexpectedly_completed")
    finally:
        ledger.finish_attempt(
            attempt.id,
            status="failed" if expected_failure is not None else "completed",
            error_category=(
                expected_failure.__class__.__name__
                if expected_failure is not None
                else None
            ),
            error_message=(str(expected_failure) if expected_failure is not None else None),
        )
        session.expire_all()
        run = session.get(ForecastRun, _stable_id(journey, "forecast-run"))
        if run is None:
            raise ReleaseVerificationError("release_run_disappeared")
        apply_totals_to_run(run, ledger.totals(run.id))
        session.commit()

    run = session.get(ForecastRun, _stable_id(journey, "forecast-run"))
    if run is None:
        raise ReleaseVerificationError("release_run_missing_after_execution")
    payload = get_run(run.id, session)
    report = payload.get("v1_report") or {}
    markdown = "\n".join(v1_report_markdown(report))
    aggregation = session.scalar(
        select(ForecastAggregationRow).where(
            ForecastAggregationRow.forecast_run_id == run.id
        )
    )
    version = session.scalar(
        select(ForecastVersion).where(ForecastVersion.run_id == run.id)
    )
    evidence_assessment = session.scalar(
        select(EvidenceSufficiencyAssessmentRow).where(
            EvidenceSufficiencyAssessmentRow.forecast_run_id == run.id
        )
    )
    material_assessment = session.scalar(
        select(MaterialNodeCoverageAssessmentRow).where(
            MaterialNodeCoverageAssessmentRow.forecast_run_id == run.id
        )
    )
    scenario = session.scalar(
        select(ScenarioSynthesisRow).where(
            ScenarioSynthesisRow.forecast_run_id == run.id
        )
    )
    plan = session.scalar(
        select(ResearchPlanRow).where(ResearchPlanRow.forecast_run_id == run.id)
    )
    claims = session.scalars(
        select(EvidenceClaimRow)
        .join(EvidenceItem, EvidenceClaimRow.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.run_id == run.id)
        .order_by(EvidenceClaimRow.id)
    ).all()
    node_runs = session.scalars(
        select(ForecastNodeRunRow)
        .where(ForecastNodeRunRow.forecast_run_id == run.id)
        .order_by(ForecastNodeRunRow.node_id)
    ).all()
    failures = session.scalars(
        select(GraphExecutionFailureRow)
        .where(GraphExecutionFailureRow.forecast_run_id == run.id)
        .order_by(GraphExecutionFailureRow.stage, GraphExecutionFailureRow.error_code)
    ).all()
    ledger_rows = session.scalars(
        select(ProviderCallLedger)
        .where(ProviderCallLedger.run_id == run.id)
        .order_by(ProviderCallLedger.stage, ProviderCallLedger.physical_attempt_number)
    ).all()
    attempts = session.scalars(
        select(ForecastRunAttempt)
        .where(ForecastRunAttempt.run_id == run.id)
        .order_by(ForecastRunAttempt.attempt_number)
    ).all()

    invariance: dict[str, Any] = {
        "checked": False,
        "probability_unchanged": None,
        "mass_conserved": None,
        "omitted_node_probability_created": False,
    }
    if aggregation is not None and version is not None:
        domain_runs = [
            ForecastNodeRun(
                id=row.id,
                run_id=row.forecast_run_id,
                node_id=row.node_id,
                probability=row.probability,
                confidence=row.confidence,
                reasoning=row.reasoning,
                supporting_claim_ids=json.loads(row.supporting_claim_ids_json),
                opposing_claim_ids=json.loads(row.opposing_claim_ids_json),
                uncertainty_notes=json.loads(row.uncertainty_notes_json),
                model_used=row.model_used,
                uncertainty=row.uncertainty,
                raw_importance_weight=row.raw_importance_weight,
                dependency_factor=row.dependency_factor,
                normalized_weight=row.normalized_weight,
                probability_contribution=row.probability_contribution,
                created_at=as_utc(row.created_at),
            )
            for row in node_runs
        ]
        included = {item.node_id for item in domain_runs}
        expected = RelationshipMassConservingLogOddsAggregator().aggregate(
            graph,
            domain_runs,
            exclusion_origins={
                node.id: "research_plan"
                for node in graph.nodes
                if node.id not in included
            },
        )
        trace: list[dict[str, Any]] = json.loads(
            aggregation.calculation_trace_json
        )
        mass: dict[str, Any] = next(
            (entry for entry in trace if entry.get("step") == "graph_mass"),
            {},
        )
        invariance = {
            "checked": True,
            "probability_unchanged": (
                expected.final_probability == version.ensemble_probability
            ),
            "scenario_numerical_effect": "none",
            "mass_conserved": bool(mass.get("conservation_check")),
            "neutral_residual_fraction": mass.get("neutral_residual_fraction"),
            "omitted_node_probability_created": any(
                row.node_id not in included for row in node_runs
            ),
            "evidence_assessment_in_trace": any(
                entry.get("step") == "evidence_sufficiency_gate"
                and entry.get("assessment_id")
                == (evidence_assessment.id if evidence_assessment else None)
                for entry in trace
            ),
            "material_assessment_in_trace": any(
                entry.get("step") == "material_node_coverage_gate"
                and entry.get("assessment_id")
                == (material_assessment.id if material_assessment else None)
                for entry in trace
            ),
            "scenario_identity_in_trace": any(
                entry.get("step") == "scenario_synthesis"
                and entry.get("numerical_effect") == "none"
                for entry in trace
            ),
        }

    result: dict[str, Any] = {
        "fixture_label": FIXTURE_LABEL,
        "journey": journey,
        "expected": "evidence_sufficiency_failure" if negative else "completed",
        "question_id": question.id,
        "contract_id": contract.id,
        "forecast_run_id": run.id,
        "profile_id": run.profile_id,
        "profile_version": profile.version,
        "status": run.status,
        "error_stage": run.error_stage,
        "error_message": run.error_message,
        "final_probability": (
            version.ensemble_probability if version is not None else None
        ),
        "graph": {
            "id": graph.id,
            "version": graph.version,
            "status": graph.status,
            "node_count": len(graph.nodes),
            "nodes": [
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "importance_weight": str(node.importance_weight),
                    "parent_node_id": node.parent_node_id,
                    "dependencies": list(node.dependencies),
                }
                for node in graph.nodes
            ],
        },
        "research_plan": {
            "id": plan.id if plan else None,
            "selected_node_ids": (
                json.loads(plan.selected_nodes_json) if plan else []
            ),
            "skipped_node_ids": (
                json.loads(plan.skipped_nodes_json) if plan else []
            ),
        },
        "evidence": {
            "item_count": session.scalar(
                select(func.count(EvidenceItem.id)).where(EvidenceItem.run_id == run.id)
            ),
            "claim_count": len(claims),
            "claims": [
                {
                    "id": row.id,
                    "node_id": row.forecast_node_id,
                    "source_url": row.source_url,
                    "source_host": row.source_host,
                    "source_class": row.source_class,
                    "extraction_method": row.extraction_method,
                    "temporal_basis": row.temporal_basis,
                    "cutoff_verified": row.cutoff_verified,
                }
                for row in claims
            ],
        },
        "node_runs": [
            {
                "id": row.id,
                "node_id": row.node_id,
                "probability": row.probability,
                "supporting_claim_ids": json.loads(row.supporting_claim_ids_json),
                "opposing_claim_ids": json.loads(row.opposing_claim_ids_json),
            }
            for row in node_runs
        ],
        "evidence_sufficiency": {
            "id": evidence_assessment.id if evidence_assessment else None,
            "status": evidence_assessment.status if evidence_assessment else None,
            "input_hash": (
                evidence_assessment.assessment_input_hash
                if evidence_assessment
                else None
            ),
            "reasons": (
                json.loads(evidence_assessment.reasons_json)
                if evidence_assessment
                else []
            ),
        },
        "material_node_coverage": {
            "id": material_assessment.id if material_assessment else None,
            "status": material_assessment.status if material_assessment else None,
            "input_hash": (
                material_assessment.assessment_input_hash
                if material_assessment
                else None
            ),
        },
        "scenario_synthesis": (
            scenario_synthesis_from_row(scenario).model_dump(mode="json")
            if scenario is not None
            else None
        ),
        "aggregation": {
            "id": aggregation.id if aggregation else None,
            "method": aggregation.method if aggregation else None,
            "calculation_trace": (
                json.loads(aggregation.calculation_trace_json)
                if aggregation
                else []
            ),
        },
        "forecast_version_id": version.id if version else None,
        "failures": [
            {
                "stage": row.stage,
                "error_code": row.error_code,
                "impact": row.impact,
            }
            for row in failures
        ],
        "provider_audit": {
            "mode": run.mode,
            "synthetic_fixture_run": run.synthetic_fixture_run,
            "fixture_evidence_used": run.fixture_evidence_used,
            "providers": json.loads(run.provider_json),
            "cost_usd": run.total_cost_usd,
            "ledger": [
                {
                    "id": row.id,
                    "stage": row.stage,
                    "provider_type": row.provider_type,
                    "provider": row.provider,
                    "model": row.model,
                    "status": row.status,
                    "actual_cost_usd": row.actual_cost_usd,
                }
                for row in ledger_rows
            ],
            "attempts": [
                {
                    "id": row.id,
                    "attempt_number": row.attempt_number,
                    "status": row.status,
                }
                for row in attempts
            ],
            "all_ledgers_terminal": all(
                row.status in _TERMINAL_LEDGER_STATUSES for row in ledger_rows
            ),
            "all_attempts_terminal": all(
                row.status in _TERMINAL_ATTEMPT_STATUSES for row in attempts
            ),
            "all_providers_mock": all(
                row.provider == "mock" for row in ledger_rows
            ),
        },
        "invariance": invariance,
        "report": report,
        "report_markdown_sha256": _sha256_text(markdown),
        "report_markdown_markers": {
            "scenario_synthesis": "### Scenario Synthesis" in markdown,
            "relationship_aggregation": (
                "### Relationship-aware aggregation" in markdown
            ),
            "no_probability": "No private-V1 probability" in markdown,
        },
    }

    if negative:
        expected_codes = {"evidence_sufficiency_gate_failed"}
        actual_codes = {item["error_code"] for item in result["failures"]}
        if run.status != "failed" or not expected_codes <= actual_codes:
            raise ReleaseVerificationError("negative_journey_failure_not_preserved")
        if any((aggregation, version, scenario)):
            raise ReleaseVerificationError("negative_journey_created_forbidden_probability_artifact")
        if result["final_probability"] is not None:
            raise ReleaseVerificationError("negative_journey_probability_not_null")
    else:
        required = (
            run.status == "completed",
            evidence_assessment is not None and evidence_assessment.status == "passed",
            material_assessment is not None and material_assessment.status == "passed",
            scenario is not None and scenario.status == "passed",
            aggregation is not None,
            version is not None,
            version is not None
            and version.ensemble_probability is not None
            and math.isfinite(version.ensemble_probability)
            and 0.0 < version.ensemble_probability < 1.0,
            len(claims) >= 3,
            len(node_runs) >= 3,
            bool(invariance.get("probability_unchanged")),
            bool(invariance.get("mass_conserved")),
            not bool(invariance.get("omitted_node_probability_created")),
        )
        if not all(required):
            raise ReleaseVerificationError("positive_journey_incomplete")
    if run.total_cost_usd != 0.0:
        raise ReleaseVerificationError("release_verification_nonzero_cost")
    if not result["provider_audit"]["all_ledgers_terminal"]:
        raise ReleaseVerificationError("release_verification_nonterminal_ledger")
    if not result["provider_audit"]["all_attempts_terminal"]:
        raise ReleaseVerificationError("release_verification_nonterminal_attempt")
    if not result["provider_audit"]["all_providers_mock"]:
        raise ReleaseVerificationError("release_verification_non_mock_provider")
    return result


def _database_state(session: Session) -> dict[str, Any]:
    active_jobs = session.scalar(
        select(func.count(Job.id)).where(Job.status.in_(["pending", "running"]))
    )
    nonterminal_ledgers = session.scalar(
        select(func.count(ProviderCallLedger.id)).where(
            ProviderCallLedger.status.not_in(_TERMINAL_LEDGER_STATUSES)
        )
    )
    nonterminal_attempts = session.scalar(
        select(func.count(ForecastRunAttempt.id)).where(
            ForecastRunAttempt.status.not_in(_TERMINAL_ATTEMPT_STATUSES)
        )
    )
    sqlite_path = settings.database_url.removeprefix("sqlite:///")
    with sqlite3.connect(sqlite_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "integrity_check": integrity[0] if integrity else None,
        "foreign_key_violation_count": len(foreign_keys),
        "active_job_count": int(active_jobs or 0),
        "nonterminal_ledger_count": int(nonterminal_ledgers or 0),
        "nonterminal_attempt_count": int(nonterminal_attempts or 0),
    }


def run_release_journeys(*, source_sha: str) -> dict[str, Any]:
    if os.environ.get("FORECASTLAB_MODEL_PROVIDER", "mock") != "mock":
        raise ReleaseVerificationError("release_verification_requires_mock_model")
    if os.environ.get("FORECASTLAB_SEARCH_PROVIDER", "mock") != "mock":
        raise ReleaseVerificationError("release_verification_requires_mock_search")
    fixture = _load_fixture()
    with SessionLocal() as session:
        positive = _execute_journey(
            session,
            fixture=fixture,
            source_sha=source_sha,
            journey="positive",
            negative=False,
        )
        negative = _execute_journey(
            session,
            fixture=fixture,
            source_sha=source_sha,
            journey="negative",
            negative=True,
        )
        database = _database_state(session)
    if database != {
        "integrity_check": "ok",
        "foreign_key_violation_count": 0,
        "active_job_count": 0,
        "nonterminal_ledger_count": 0,
        "nonterminal_attempt_count": 0,
    }:
        raise ReleaseVerificationError("release_verification_database_not_terminal")
    return {
        "fixture_label": FIXTURE_LABEL,
        "source_sha": source_sha,
        "profile": {"id": PROFILE_ID, "version": PROFILE_VERSION},
        "positive_journey": positive,
        "negative_journey": negative,
        "database": database,
        "provider_usage": {
            "live_provider_calls": 0,
            "cost_usd": 0.0,
            "mock_only": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("journeys", "database-gate"),
        default="journeys",
    )
    parser.add_argument("--fresh-database", type=Path)
    parser.add_argument("--legacy-database", type=Path)
    args = parser.parse_args()
    if args.mode == "database-gate":
        if args.fresh_database is None or args.legacy_database is None:
            parser.error("database-gate requires --fresh-database and --legacy-database")
        result = run_database_gate(
            fresh_database=args.fresh_database,
            legacy_database=args.legacy_database,
        )
    else:
        result = run_release_journeys(source_sha=args.source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

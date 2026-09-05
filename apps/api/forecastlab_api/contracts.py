from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.contracts import ForecastContractError, QuestionCompiler
from forecastlab.execution import resolve_execution_context
from forecastlab.providers.factory import build_model_provider
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc
from forecastlab_api.models import ForecastContractRow
from forecastlab_api.persist import save_contract
from forecastlab_api.pipeline import provider_settings_from_secrets
from forecastlab_api.secrets import load_secrets


def _json_list(raw: str) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def build_question_compiler(
    *,
    mode: str,
    profile_id: str,
    as_of: datetime | None,
) -> QuestionCompiler:
    secrets = load_secrets()
    context = resolve_execution_context(
        requested_mode=mode,  # type: ignore[arg-type]
        profile_id=profile_id,
        settings=provider_settings_from_secrets(secrets),
        as_of=as_of,
    )
    model = build_model_provider(
        provider=context.model_provider,
        api_key=secrets.get("model_api_key"),
        base_url=context.model_base_url,
        model=context.model_name,
        timeout=float(context.model_timeout_seconds or 60),
        execution=context,
    )
    return QuestionCompiler(model)


def forecast_contract_from_row(row: ForecastContractRow) -> ForecastContract:
    return ForecastContract(
        id=row.id,
        question_id=row.question_id,
        version=row.version,
        created_at=as_utc(row.created_at),
        created_by=row.created_by,
        original_question=row.original_question,
        normalized_question=row.normalized_question,
        yes_condition=row.yes_condition,
        no_condition=row.no_condition,
        resolution_date=as_utc(row.resolution_date) if row.resolution_date else None,
        authoritative_source=row.authoritative_source,
        fallback_sources=_json_list(row.fallback_sources_json),
        resolution_method=row.resolution_method,
        ambiguity_notes=row.ambiguity_notes,
        cancellation_conditions=row.cancellation_conditions,
        resolver_risk_notes=row.resolver_risk_notes,
        forecast_type=row.forecast_type,
        geography=row.geography,
        units=row.units,
        domain=row.domain,
        initial_reference_class=row.initial_reference_class,
        suggested_drivers=_json_list(row.suggested_drivers_json),
        known_dependencies=_json_list(row.known_dependencies_json),
        status=row.status,  # type: ignore[arg-type]
    )


def store_forecast_contract(session: Session, contract: ForecastContract) -> ForecastContractRow:
    row = ForecastContractRow(
        id=contract.id,
        question_id=contract.question_id,
        version=contract.version,
        created_at=contract.created_at,
        created_by=contract.created_by,
        original_question=contract.original_question,
        normalized_question=contract.normalized_question,
        yes_condition=contract.yes_condition,
        no_condition=contract.no_condition,
        resolution_date=contract.resolution_date,
        authoritative_source=contract.authoritative_source,
        fallback_sources_json=json.dumps(contract.fallback_sources),
        resolution_method=contract.resolution_method,
        ambiguity_notes=contract.ambiguity_notes,
        cancellation_conditions=contract.cancellation_conditions,
        resolver_risk_notes=contract.resolver_risk_notes,
        forecast_type=contract.forecast_type,
        geography=contract.geography,
        units=contract.units,
        domain=contract.domain,
        initial_reference_class=contract.initial_reference_class,
        suggested_drivers_json=json.dumps(contract.suggested_drivers),
        known_dependencies_json=json.dumps(contract.known_dependencies),
        status=contract.status,
    )
    session.add(row)
    session.flush()
    return row


def apply_forecast_contract_review(row: ForecastContractRow, updates: dict[str, object]) -> ForecastContractRow:
    if row.status != "draft":
        raise ForecastContractError(["contract_not_editable"], "Only draft Forecast Contracts can be edited")
    json_fields = {
        "fallback_sources": "fallback_sources_json",
        "suggested_drivers": "suggested_drivers_json",
        "known_dependencies": "known_dependencies_json",
    }
    allowed_fields = {
        "normalized_question",
        "yes_condition",
        "no_condition",
        "resolution_date",
        "authoritative_source",
        "resolution_method",
        "ambiguity_notes",
        "cancellation_conditions",
        "resolver_risk_notes",
        "forecast_type",
        "geography",
        "units",
        "domain",
        "initial_reference_class",
    }
    for key, value in updates.items():
        if key in json_fields:
            items = value if isinstance(value, list) else []
            setattr(row, json_fields[key], json.dumps([str(item).strip() for item in items if str(item).strip()]))
        elif key in allowed_fields:
            setattr(row, key, value.strip() if isinstance(value, str) else value)
    return row


def approve_forecast_contract(session: Session, row: ForecastContractRow) -> ForecastContractRow:
    if row.status == "approved":
        return row
    if row.status != "draft":
        raise ForecastContractError(["contract_not_approvable"], "Forecast Contract is not a draft")

    contract = forecast_contract_from_row(row)
    errors = contract.approval_errors()
    if errors:
        raise ForecastContractError(errors, "Forecast Contract is missing required resolution fields")

    prior_approved = session.scalars(
        select(ForecastContractRow).where(
            ForecastContractRow.question_id == row.question_id,
            ForecastContractRow.id != row.id,
            ForecastContractRow.status == "approved",
        )
    ).all()
    for prior in prior_approved:
        prior.status = "superseded"

    row.status = "approved"
    question = row.question
    save_contract(session, question, contract.to_resolution_contract())
    question.normalized_text = contract.normalized_question
    question.forecast_deadline = contract.resolution_date
    session.flush()
    return row

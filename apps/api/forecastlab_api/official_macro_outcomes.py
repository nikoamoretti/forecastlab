"""Deterministic, append-only adjudication from an official dated macro release.

This boundary is intentionally narrower than the historical human confirmation
flows.  It accepts only an exact BLS archive identity, validates it against the
already frozen macro contract, and records the fact that the outcome became
known after the forecast cutoff.  It never modifies a frozen forecast.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecastlab.http_client import safe_get
from forecastlab.macro import MacroDataError, MacroSpec
from forecastlab.macro_evidence import FIRST_RELEASE_PARSER_VERSION, archived_first_release_url, parse_release_document
from forecastlab.official_releases import PUBLIC_DATA_USER_AGENT
from forecastlab.root_event import contract_hash, digest
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.artifact_store import put_bytes
from forecastlab_api.autopilot_models import (
    AutopilotRun,
    ManagedQuestion,
    OfficialMacroOutcomeAmendment,
    QuestionAdjudication,
)
from forecastlab_api.autopilot_store import notify
from forecastlab_api.models import PersonalForecast, ProspectiveCohort, ProspectiveEntry, ProspectiveOutcome

POLICY_VERSION = "official_macro_first_release_v1"
SYSTEM_ATTRIBUTION = "system:official_macro_first_release_v1"
RETRYABLE_EXCEPTION_CODES = frozenset({
    "official_dated_release_unavailable_or_redirected",
    "official_dated_release_content_type_invalid",
    "official_dated_release_unavailable",
    "official_release_timeout",
    "official_release_connecterror",
})
RETRY_INTERVAL = timedelta(minutes=30)


class OfficialMacroOutcomeError(ValueError):
    """A stable, auditable reason that no system outcome was recorded."""


@dataclass(frozen=True)
class FrozenMacroTarget:
    question_id: str
    contract: ForecastContract
    expected_contract_hash: str
    macro: MacroSpec
    release_event: str
    prospective_entry_id: str | None = None


def _begin_immediate(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite" and not session.in_transaction():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _comparison(spec: MacroSpec, value: object) -> int:
    try:
        measured, threshold = Decimal(str(value)), Decimal(str(spec.threshold))
    except (InvalidOperation, ValueError) as exc:
        raise OfficialMacroOutcomeError("decimal_measurement_or_threshold_invalid") from exc
    result = {"gt": measured > threshold, "ge": measured >= threshold,
              "lt": measured < threshold, "le": measured <= threshold}[spec.comparison]
    return int(result)


def _validate_frozen_contract(target: FrozenMacroTarget) -> None:
    contract = target.contract
    if contract_hash(contract) != target.expected_contract_hash:
        raise OfficialMacroOutcomeError("frozen_contract_hash_mismatch")
    expected = target.macro.template(contract.question_id, contract.id)
    fields = (
        "original_question", "normalized_question", "yes_condition", "no_condition", "resolution_date",
        "authoritative_source", "fallback_sources", "resolution_method", "cancellation_conditions",
        "domain", "geography", "units", "initial_reference_class", "suggested_drivers",
    )
    if any(getattr(contract, field) != getattr(expected, field) for field in fields):
        raise OfficialMacroOutcomeError("frozen_contract_macro_spec_mismatch")
    if contract.status != "approved" or target.macro.revision_policy != "first_release":
        raise OfficialMacroOutcomeError("frozen_contract_not_first_release_approved")


def _source_identities(target: FrozenMacroTarget) -> list[dict[str, str]]:
    family = "cpi" if target.macro.indicator == "cpi" else "empsit"
    suffix = as_utc(target.macro.release_at).astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
    common = {
        "publisher": "U.S. Bureau of Labor Statistics",
        "indicator": target.macro.indicator,
        "observation_period": target.macro.observation_period,
        "release_at": as_utc(target.macro.release_at).isoformat(),
        "revision_policy": target.macro.revision_policy,
    }
    return [
        {**common, "source_kind": "dated_first_release_archive", "source_url": archived_first_release_url(target.macro)},
        {**common, "source_kind": "dated_original_pdf_republication", "source_url": f"https://www.dol.gov/newsroom/economicdata/{family}_{suffix}.pdf"},
    ]


def _source_identity(target: FrozenMacroTarget) -> dict[str, str]:
    """Compatibility helper: the first identity is the preferred BLS archive."""
    return _source_identities(target)[0]


def _valid_response(identity: dict[str, str], response) -> bool:
    if response.status_code != 200 or response.final_url != identity["source_url"] or not response.content:
        return False
    kind = response.content_type.lower().split(";", 1)[0]
    return ((identity["source_kind"] == "dated_first_release_archive" and kind.startswith("text/html")) or
            (identity["source_kind"] == "dated_original_pdf_republication" and kind == "application/pdf"))


def _exception_code(exc: Exception) -> str:
    if isinstance(exc, OfficialMacroOutcomeError):
        return str(exc)
    if isinstance(exc, MacroDataError):
        return str(exc)
    return f"official_release_{type(exc).__name__.lower()}"


def _append_amendment(
    session: Session, target: FrozenMacroTarget, *, status: str, source_url: str | None = None,
    source_identity: dict | None = None, artifact: dict | None = None, source_sha256: str | None = None,
    measurement: dict | None = None, outcome: int | None = None, outcome_known_at: datetime | None = None,
    retrieved_at: datetime | None = None, exception_code: str | None = None,
) -> OfficialMacroOutcomeAmendment:
    """Idempotently retain either a ready measurement or a terminal exception."""
    identity = {"question_id": target.question_id, "contract_hash": target.expected_contract_hash,
                "source_sha256": source_sha256, "status": status, "exception_code": exception_code}
    if status == "exception" and exception_code in RETRYABLE_EXCEPTION_CODES:
        # A new bounded retry is an immutable new attempt. Reusing the first
        # exception ID forever would bypass the 30-minute cooldown after the
        # first retry because its created_at could never advance.
        identity["retry_window"] = str(int(utcnow().timestamp() // int(RETRY_INTERVAL.total_seconds())))
    amendment_id = digest({"policy": POLICY_VERSION, **identity})
    existing = session.get(OfficialMacroOutcomeAmendment, amendment_id)
    if existing:
        return existing
    prior = list(session.scalars(select(OfficialMacroOutcomeAmendment).where(
        OfficialMacroOutcomeAmendment.question_id == target.question_id).order_by(OfficialMacroOutcomeAmendment.revision)).all())
    ready = next((row for row in prior if row.status == "ready"), None)
    if status == "ready" and ready and ready.source_sha256 != source_sha256:
        return _append_amendment(session, target, status="exception", source_url=source_url,
            source_identity=source_identity, source_sha256=source_sha256,
            exception_code="official_release_conflicting_or_tampered")
    row = OfficialMacroOutcomeAmendment(
        id=amendment_id, question_id=target.question_id, prospective_entry_id=target.prospective_entry_id,
        revision=(prior[-1].revision if prior else 0) + 1, policy_version=POLICY_VERSION,
        contract_hash=target.expected_contract_hash, release_event=target.release_event, status=status,
        source_url=source_url, source_identity_json=json.dumps(source_identity or {}, sort_keys=True),
        artifact_json=json.dumps(artifact or {}, sort_keys=True), source_sha256=source_sha256,
        parser_version=FIRST_RELEASE_PARSER_VERSION if measurement else None,
        measurement_json=json.dumps(measurement or {}, sort_keys=True), outcome=outcome,
        outcome_known_at=outcome_known_at, retrieved_at=retrieved_at,
        exception_code=exception_code, exception_detail=exception_code, created_at=utcnow(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.get(OfficialMacroOutcomeAmendment, amendment_id)
        if existing:
            return existing
        raise
    return row


def _latest_amendment(session: Session, *, question_id: str, prospective_entry_id: str | None = None) -> OfficialMacroOutcomeAmendment | None:
    query = select(OfficialMacroOutcomeAmendment).where(OfficialMacroOutcomeAmendment.question_id == question_id)
    if prospective_entry_id is not None:
        query = query.where(OfficialMacroOutcomeAmendment.prospective_entry_id == prospective_entry_id)
    return session.scalar(query.order_by(OfficialMacroOutcomeAmendment.revision.desc()).limit(1))


def _may_attempt(session: Session, *, question_id: str, prospective_entry_id: str | None = None) -> OfficialMacroOutcomeAmendment | None:
    previous = _latest_amendment(session, question_id=question_id, prospective_entry_id=prospective_entry_id)
    if previous and (previous.status == "ready" or previous.exception_code not in RETRYABLE_EXCEPTION_CODES
                     or utcnow() - as_utc(previous.created_at) < RETRY_INTERVAL):
        return previous
    return None


def retain_official_macro_amendment(session: Session, target: FrozenMacroTarget) -> OfficialMacroOutcomeAmendment:
    """Fetch one immutable source identity and store an append-only amendment.

    No provider, forecast, or model call is involved.  Any missing, revised,
    malformed, conflicting, or tampered source becomes an exception row.
    """
    _begin_immediate(session)
    try:
        _validate_frozen_contract(target)
        response = None
        identity = None
        for candidate in _source_identities(target):
            try:
                current = safe_get(candidate["source_url"], timeout=8, headers={"User-Agent": PUBLIC_DATA_USER_AGENT})
            except Exception:
                # Failure to reach the preferred archive must not suppress the
                # agency's exact dated original-PDF fallback. If both fail, the
                # attempt remains an explicit retriable exception below.
                continue
            if _valid_response(candidate, current):
                identity, response = candidate, current
                break
        if identity is None or response is None:
            raise OfficialMacroOutcomeError("official_dated_release_unavailable")
        retrieved = utcnow()
        measurement = parse_release_document(response.content, target.macro, source_url=identity["source_url"], retrieved_at=retrieved)
        outcome = _comparison(target.macro, measurement["value"])
        if outcome != measurement["outcome"]:
            raise OfficialMacroOutcomeError("decimal_outcome_disagrees_with_parser")
        artifact = put_bytes(response.content, content_type=response.content_type, prefix="official-macro-outcomes")
        canonical_measurement = {**measurement, "value_decimal": str(Decimal(str(measurement["value"]))),
                                 "outcome": outcome, "parser_version": FIRST_RELEASE_PARSER_VERSION}
        return _append_amendment(session, target, status="ready", source_url=identity["source_url"],
            source_identity=identity, artifact=artifact, source_sha256=artifact["sha256"], measurement=canonical_measurement,
            outcome=outcome, outcome_known_at=datetime.fromisoformat(measurement["publication_time"]), retrieved_at=retrieved)
    except Exception as exc:
        return _append_amendment(session, target, status="exception", source_url=archived_first_release_url(target.macro),
            source_identity=_source_identity(target), exception_code=_exception_code(exc))


def _existing_system_outcome(session: Session, *, question_id: str, amendment_id: str, prospective: bool):
    if prospective:
        return session.scalar(select(ProspectiveOutcome).where(ProspectiveOutcome.entry_id == question_id).order_by(ProspectiveOutcome.revision.desc()).limit(1))
    return session.scalar(select(QuestionAdjudication).where(QuestionAdjudication.question_id == question_id).order_by(QuestionAdjudication.revision.desc()).limit(1))


def apply_prospective_amendment(session: Session, entry: ProspectiveEntry, amendment: OfficialMacroOutcomeAmendment) -> ProspectiveOutcome | None:
    if amendment.status != "ready" or amendment.outcome is None:
        return None
    existing = _existing_system_outcome(session, question_id=entry.id, amendment_id=amendment.id, prospective=True)
    if existing:
        payload = json.loads(existing.evidence)
        if payload.get("amendment_id") == amendment.id:
            return existing
        raise OfficialMacroOutcomeError("prospective_outcome_already_adjudicated")
    revision = session.scalar(select(func.max(ProspectiveOutcome.revision)).where(ProspectiveOutcome.entry_id == entry.id)) or 0
    row = ProspectiveOutcome(id=str(uuid.uuid4()), entry_id=entry.id, revision=revision + 1, outcome=amendment.outcome,
        source_url=amendment.source_url or "", evidence=json.dumps({"amendment_id": amendment.id, "policy_version": POLICY_VERSION,
        "outcome_known_at": amendment.outcome_known_at.isoformat() if amendment.outcome_known_at else None}),
        confirmed_by=SYSTEM_ATTRIBUTION, created_at=utcnow())
    session.add(row)
    return row


def apply_autopilot_amendment(session: Session, managed: ManagedQuestion, amendment: OfficialMacroOutcomeAmendment) -> QuestionAdjudication | None:
    if amendment.status != "ready" or amendment.outcome is None:
        return None
    existing = _existing_system_outcome(session, question_id=managed.question_id, amendment_id=amendment.id, prospective=False)
    if existing:
        payload = json.loads(existing.evidence_json)
        if payload.get("amendment_id") == amendment.id:
            return existing
        raise OfficialMacroOutcomeError("autopilot_outcome_already_adjudicated")
    revision = session.scalar(select(func.max(QuestionAdjudication.revision)).where(QuestionAdjudication.question_id == managed.question_id)) or 0
    row = QuestionAdjudication(id=str(uuid.uuid4()), question_id=managed.question_id, revision=revision + 1,
        proposal_id=None, contract_hash=amendment.contract_hash, outcome=amendment.outcome,
        evidence_json=json.dumps({"amendment_id": amendment.id, "policy_version": POLICY_VERSION,
        "outcome_known_at": amendment.outcome_known_at.isoformat() if amendment.outcome_known_at else None}),
        confirmed_by=SYSTEM_ATTRIBUTION)
    session.add(row)
    managed.status = "resolved"
    notify(session, "automatic-adjudication:" + row.id, "outcome", "Official macro outcome recorded",
           "Dated BLS first-release evidence satisfied the frozen contract; no owner confirmation was impersonated.", managed.question_id)
    return row


def process_prospective_entry(session: Session, entry_id: str) -> OfficialMacroOutcomeAmendment:
    _begin_immediate(session)
    entry = session.scalar(select(ProspectiveEntry).where(ProspectiveEntry.id == entry_id).with_for_update())
    if entry is None:
        raise HTTPException(404, "Prospective question not found")
    previous = _may_attempt(session, question_id=entry.question_id, prospective_entry_id=entry.id)
    if previous:
        return previous
    cohort = session.get(ProspectiveCohort, entry.cohort_id)
    if cohort is None or cohort.status == "draft":
        raise OfficialMacroOutcomeError("prospective_cohort_not_frozen")
    contract = ForecastContract.model_validate_json(entry.contract_json)
    manifest = json.loads(cohort.manifest_json or "{}")
    frozen = next((item for item in manifest.get("entries", []) if item.get("entry_id") == entry.id), None)
    if not frozen:
        raise OfficialMacroOutcomeError("prospective_frozen_manifest_contract_missing")
    if utcnow() < as_utc(contract.resolution_date):
        raise OfficialMacroOutcomeError("official_release_not_due")
    frozen_contract = ForecastContract.model_validate(frozen["contract"])
    target = FrozenMacroTarget(question_id=entry.question_id, prospective_entry_id=entry.id, contract=contract,
        expected_contract_hash=contract_hash(frozen_contract),
        macro=MacroSpec.model_validate_json(entry.macro_json), release_event=entry.release_event)
    amendment = retain_official_macro_amendment(session, target)
    apply_prospective_amendment(session, entry, amendment)
    session.commit()
    return amendment


def process_managed_question(session: Session, managed: ManagedQuestion) -> OfficialMacroOutcomeAmendment:
    _begin_immediate(session)
    managed = session.scalar(select(ManagedQuestion).where(ManagedQuestion.question_id == managed.question_id).with_for_update())
    if managed is None:
        raise OfficialMacroOutcomeError("managed_question_not_found")
    previous = _may_attempt(session, question_id=managed.question_id)
    if previous:
        return previous
    personal = session.get(PersonalForecast, managed.initial_run_id)
    if personal is None:
        raise OfficialMacroOutcomeError("managed_initial_forecast_missing")
    contract = ForecastContract.model_validate_json(personal.contract_json)
    macro = MacroSpec.model_validate_json(managed.macro_json)
    if utcnow() < as_utc(macro.release_at):
        raise OfficialMacroOutcomeError("official_release_not_due")
    initial = session.get(AutopilotRun, managed.initial_run_id)
    approval = json.loads(initial.approval_json) if initial else {}
    expected_hash = approval.get("contract_hash")
    if not isinstance(expected_hash, str):
        raise OfficialMacroOutcomeError("managed_frozen_contract_hash_missing")
    amendment = retain_official_macro_amendment(session, FrozenMacroTarget(question_id=managed.question_id, contract=contract,
        expected_contract_hash=expected_hash, macro=macro, release_event=managed.release_event))
    apply_autopilot_amendment(session, managed, amendment)
    session.commit()
    return amendment


def process_due_prospective_entries(
    session: Session, *, cohort_id: str | None = None,
) -> list[OfficialMacroOutcomeAmendment]:
    """Process due frozen entries, optionally scoped to one selected cohort."""
    query = select(ProspectiveEntry).join(ProspectiveCohort).where(
        ProspectiveCohort.status.in_(("running", "awaiting_resolution", "scored")),
    )
    if cohort_id is not None:
        query = query.where(ProspectiveEntry.cohort_id == cohort_id)
    entries = list(session.scalars(query.order_by(ProspectiveEntry.id)).all())
    results = []
    for entry in entries:
        if _may_attempt(session, question_id=entry.question_id, prospective_entry_id=entry.id):
            continue
        if as_utc(ForecastContract.model_validate_json(entry.contract_json).resolution_date) <= utcnow():
            results.append(process_prospective_entry(session, entry.id))
    return results

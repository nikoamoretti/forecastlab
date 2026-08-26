from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evidence_claims import EvidenceClaimError, normalize_source_host
from forecastlab.schemas import EvidenceClaim
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.models import EvidenceClaimRow, EvidenceItem, ForecastNodeRow


def evidence_claim_from_row(row: EvidenceClaimRow) -> EvidenceClaim:
    return EvidenceClaim(
        id=row.id,
        evidence_item_id=row.evidence_item_id,
        forecast_node_id=row.forecast_node_id,
        claim=row.claim,
        excerpt=row.excerpt,
        source_url=row.source_url,
        source_title=row.source_title,
        publisher=row.publisher,
        publication_date=(
            as_utc(row.publication_date) if row.publication_date is not None else None
        ),
        publication_date_source=row.publication_date_source,
        publication_date_verified=row.publication_date_verified,
        retrieval_date=as_utc(row.retrieval_date),
        source_available_at=as_utc(row.source_available_at),
        temporal_basis=row.temporal_basis,  # type: ignore[arg-type]
        supports_or_refutes=row.supports_or_refutes,  # type: ignore[arg-type]
        confidence=row.confidence,
        source_quality=row.source_quality,
        primary_source=row.primary_source,
        as_of_eligible=row.as_of_eligible,
        cutoff_verified=row.cutoff_verified,
        source_class=row.source_class,  # type: ignore[arg-type]
        extraction_method=row.extraction_method,  # type: ignore[arg-type]
        source_host=row.source_host,
    )


def _provenance_errors(claim: EvidenceClaim, item: EvidenceItem) -> list[str]:
    errors: list[str] = []
    if item.rejected:
        errors.append("evidence_item_rejected")
    if not item.as_of_eligible:
        errors.append("evidence_item_not_as_of_eligible")
    if claim.source_url != item.url:
        errors.append("source_url_mismatch")
    if claim.source_title != item.title:
        errors.append("source_title_mismatch")
    if claim.publisher != (item.publisher or ""):
        errors.append("publisher_mismatch")
    if (claim.publication_date is None) != (item.published_at is None):
        errors.append("publication_date_mismatch")
    elif claim.publication_date is not None and item.published_at is not None:
        if as_utc(claim.publication_date) != as_utc(item.published_at):
            errors.append("publication_date_mismatch")
    if claim.publication_date_source != item.publication_date_source:
        errors.append("publication_date_source_mismatch")
    if claim.publication_date_verified != item.publication_date_verified:
        errors.append("publication_date_verification_mismatch")
    if as_utc(claim.retrieval_date) != as_utc(item.retrieved_at):
        errors.append("retrieval_date_mismatch")
    if as_utc(claim.source_available_at) != as_utc(item.source_available_at):
        errors.append("source_available_at_mismatch")
    if claim.temporal_basis != item.temporal_basis:
        errors.append("temporal_basis_mismatch")
    if claim.source_class != "unknown_legacy" and claim.source_class != item.source_class:
        errors.append("source_class_mismatch")
    if claim.source_host and claim.source_host != normalize_source_host(item.url):
        errors.append("source_host_mismatch")
    if item.run.mode == "backtest":
        if item.temporal_basis == "retrieval_date":
            errors.append("historical_retrieval_basis_forbidden")
        if item.temporal_basis == "snapshot_date":
            if item.snapshot_verification_status not in {
                "verified",
                "fixture",
                "cutoff_consistent_mock_manifest_verified",
            }:
                errors.append("historical_snapshot_not_verified")
            snapshot_at = item.final_snapshot_at or item.snapshot_at
            if snapshot_at is None:
                errors.append("historical_snapshot_timestamp_required")
            elif as_utc(snapshot_at) != as_utc(item.source_available_at):
                errors.append("historical_snapshot_availability_mismatch")
        if item.temporal_basis == "publication_date" and (
            not item.publication_date_verified
            or item.snapshot_verification_status
            not in {"fixture", "immutable_historical_timestamp_verified"}
        ):
            errors.append("historical_immutable_timestamp_adapter_required")
    return errors


def store_evidence_claims(
    session: Session,
    claims: list[EvidenceClaim],
    *,
    cutoff: datetime | None = None,
) -> list[EvidenceClaimRow]:
    """Persist a complete valid batch or reject it without adding partial claims."""

    errors: list[str] = []
    seen_ids: set[str] = set()
    resolved: list[tuple[EvidenceClaim, EvidenceItem, ForecastNodeRow]] = []
    for claim in claims:
        if claim.id in seen_ids or session.get(EvidenceClaimRow, claim.id) is not None:
            errors.append("evidence_claim_id_not_unique")
        seen_ids.add(claim.id)

        item = session.get(EvidenceItem, claim.evidence_item_id)
        node = session.get(ForecastNodeRow, claim.forecast_node_id)
        if item is None:
            errors.append("evidence_item_not_found")
        if node is None:
            errors.append("forecast_node_not_found")
        if item is None or node is None:
            continue
        effective_cutoff = cutoff or item.run.as_of
        errors.extend(
            claim.forecasting_errors(
                mode=item.run.mode,  # type: ignore[arg-type]
                cutoff=effective_cutoff,
                run_completion_time=item.run.finished_at or utcnow(),
            )
        )
        errors.extend(_provenance_errors(claim, item))
        if item.run.question_id != node.graph.contract.question_id:
            errors.append("evidence_and_node_question_mismatch")
        resolved.append((claim, item, node))

    if errors:
        raise EvidenceClaimError(errors, "Evidence Claims failed persistence validation")

    rows: list[EvidenceClaimRow] = []
    for claim, _item, _node in resolved:
        row = EvidenceClaimRow(**claim.model_dump())
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def evidence_for_node(session: Session, node_id: str) -> list[EvidenceClaim]:
    rows = session.scalars(
        select(EvidenceClaimRow)
        .where(EvidenceClaimRow.forecast_node_id == node_id)
        .order_by(EvidenceClaimRow.source_available_at.desc(), EvidenceClaimRow.id)
    ).all()
    return [evidence_claim_from_row(row) for row in rows]

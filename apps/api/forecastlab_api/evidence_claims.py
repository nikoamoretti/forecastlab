from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.evidence_claims import EvidenceClaimError
from forecastlab.schemas import EvidenceClaim
from forecastlab.timeutil import as_utc
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
        publication_date=as_utc(row.publication_date),
        retrieval_date=as_utc(row.retrieval_date),
        supports_or_refutes=row.supports_or_refutes,  # type: ignore[arg-type]
        confidence=row.confidence,
        source_quality=row.source_quality,
        primary_source=row.primary_source,
        as_of_eligible=row.as_of_eligible,
        cutoff_verified=row.cutoff_verified,
    )


def _provenance_errors(claim: EvidenceClaim, item: EvidenceItem) -> list[str]:
    errors: list[str] = []
    if item.rejected:
        errors.append("evidence_item_rejected")
    if not item.as_of_eligible:
        errors.append("evidence_item_not_as_of_eligible")
    if item.published_at_unknown or item.published_at is None:
        errors.append("evidence_item_publication_date_unverified")
    if claim.source_url != item.url:
        errors.append("source_url_mismatch")
    if claim.source_title != item.title:
        errors.append("source_title_mismatch")
    if claim.publisher != (item.publisher or ""):
        errors.append("publisher_mismatch")
    if item.published_at is not None and as_utc(claim.publication_date) != as_utc(item.published_at):
        errors.append("publication_date_mismatch")
    if as_utc(claim.retrieval_date) != as_utc(item.retrieved_at):
        errors.append("retrieval_date_mismatch")
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
        errors.extend(claim.forecasting_errors(cutoff=cutoff))
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
        .order_by(EvidenceClaimRow.publication_date.desc(), EvidenceClaimRow.id)
    ).all()
    return [evidence_claim_from_row(row) for row in rows]

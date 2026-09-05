from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.fetch import fetch_document
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.ranking import classify_source
from forecastlab.schemas import AttachedEvidenceDocument, FetchedDocument
from forecastlab.timeutil import as_utc, utcnow
from forecastlab.wayback import (
    WaybackSnapshot,
    canonicalize_url,
    discover_snapshots,
    nearest_eligible_snapshot,
)
from forecastlab_api.models import (
    EvidenceItem,
    ForecastNodeRow,
    ForecastRun,
    ForecastVersion,
    ManualEvidenceAttachment,
    Question,
)

ManualEvidenceMode = Literal["live", "backtest"]
ManualEvidenceUse = Literal["general_question_evidence", "forecast_node"]
FetchDocumentFn = Callable[..., FetchedDocument]
DiscoverSnapshotsFn = Callable[..., list[WaybackSnapshot]]


class ManualEvidenceError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _same_timestamp(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return as_utc(left) == as_utc(right)


def _target_node_for_question(
    session: Session,
    *,
    question_id: str,
    target_node_id: str | None,
    intended_use: ManualEvidenceUse,
) -> ForecastNodeRow | None:
    if intended_use == "forecast_node" and not target_node_id:
        raise ManualEvidenceError("forecast_node_id_required")
    if intended_use == "general_question_evidence" and target_node_id is not None:
        raise ManualEvidenceError("forecast_node_id_not_allowed_for_general_evidence")
    if target_node_id is None:
        return None
    node = session.get(ForecastNodeRow, target_node_id)
    if node is None:
        raise ManualEvidenceError("forecast_node_not_found")
    if node.graph.contract.question_id != question_id:
        raise ManualEvidenceError("forecast_node_question_mismatch")
    return node


def _intake_key(
    *,
    question_id: str,
    canonical_url: str,
    target_node_id: str | None,
    mode: ManualEvidenceMode,
    as_of: datetime | None,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "question_id": question_id,
                "canonical_url": canonical_url,
                "target_node_id": target_node_id,
                "mode": mode,
                "as_of": as_utc(as_of).isoformat() if as_of is not None else None,
            }
        )
    )


def _fetch_for_intake(
    *,
    url: str,
    mode: ManualEvidenceMode,
    as_of: datetime | None,
    fetcher: FetchDocumentFn,
    snapshot_discoverer: DiscoverSnapshotsFn,
) -> FetchedDocument:
    if mode == "live":
        return fetcher(
            url,
            mode="live",
            as_of=None,
            allow_local_fixtures=False,
        )
    assert as_of is not None
    try:
        snapshots = snapshot_discoverer(url, as_of=as_of)
    except Exception:
        snapshots = []
    snapshot = nearest_eligible_snapshot(snapshots, as_of)
    if snapshot is None:
        return fetcher(
            url,
            mode="backtest",
            as_of=as_of,
            allow_local_fixtures=False,
        )
    return fetcher(
        url,
        mode="backtest",
        as_of=as_of,
        allow_local_fixtures=False,
        snapshot_url=snapshot.snapshot_url,
        snapshot_at=snapshot.timestamp,
    )


def _duplicate_content_attachment(
    session: Session,
    *,
    question_id: str,
    target_node_id: str | None,
    mode: ManualEvidenceMode,
    as_of: datetime | None,
    document: FetchedDocument,
) -> ManualEvidenceAttachment | None:
    if document.rejected or not document.as_of_eligible:
        return None
    raw_hash = document.raw_content_hash or ""
    text_hash = document.extracted_text_hash or document.content_hash
    candidates = session.scalars(
        select(ManualEvidenceAttachment).where(
            ManualEvidenceAttachment.question_id == question_id,
            ManualEvidenceAttachment.target_node_id == target_node_id,
            ManualEvidenceAttachment.mode == mode,
            ManualEvidenceAttachment.status == "accepted",
            ManualEvidenceAttachment.raw_content_hash == raw_hash,
            ManualEvidenceAttachment.extracted_text_hash == text_hash,
        )
    ).all()
    return next(
        (candidate for candidate in candidates if _same_timestamp(candidate.as_of, as_of)),
        None,
    )


def intake_manual_evidence(
    session: Session,
    *,
    question: Question,
    url: str,
    note: str | None,
    intended_use: ManualEvidenceUse,
    target_node_id: str | None,
    mode: ManualEvidenceMode,
    as_of: datetime | None,
    fetcher: FetchDocumentFn | None = None,
    snapshot_discoverer: DiscoverSnapshotsFn | None = None,
) -> tuple[ManualEvidenceAttachment, bool]:
    submitted_url = url.strip()
    if not submitted_url:
        raise ManualEvidenceError("url_required")
    if mode == "backtest" and as_of is None:
        raise ManualEvidenceError("as_of_required_for_backtest")
    if mode == "live" and as_of is not None:
        raise ManualEvidenceError("as_of_not_allowed_for_live")
    if as_of is not None:
        as_of = as_utc(as_of)
    _target_node_for_question(
        session,
        question_id=question.id,
        target_node_id=target_node_id,
        intended_use=intended_use,
    )
    canonical_url = canonicalize_url(submitted_url)
    key = _intake_key(
        question_id=question.id,
        canonical_url=canonical_url,
        target_node_id=target_node_id,
        mode=mode,
        as_of=as_of,
    )
    existing = session.scalar(
        select(ManualEvidenceAttachment).where(
            ManualEvidenceAttachment.intake_key == key
        )
    )
    if existing is not None:
        return existing, False

    document = _fetch_for_intake(
        url=submitted_url,
        mode=mode,
        as_of=as_of,
        fetcher=fetcher or fetch_document,
        snapshot_discoverer=snapshot_discoverer or discover_snapshots,
    )
    duplicate = _duplicate_content_attachment(
        session,
        question_id=question.id,
        target_node_id=target_node_id,
        mode=mode,
        as_of=as_of,
        document=document,
    )
    if duplicate is not None:
        return duplicate, False

    accepted = not document.rejected and document.as_of_eligible
    attachment = ManualEvidenceAttachment(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecastlab:manual-evidence:{key}")),
        intake_key=key,
        question_id=question.id,
        target_node_id=target_node_id,
        intended_use=intended_use,
        note=(note or "").strip() or None,
        mode=mode,
        as_of=as_of,
        submitted_url=submitted_url,
        canonical_url=canonical_url,
        final_url=document.final_url,
        status="accepted" if accepted else "rejected",
        rejection_reason=None if accepted else document.rejection_reason or "document_rejected",
        title=document.title,
        publisher=document.publisher,
        published_at=document.published_at,
        retrieved_at=document.retrieved_at,
        source_available_at=document.source_available_at,
        temporal_basis=document.temporal_basis,
        publication_date_source=document.publication_date_source,
        publication_date_verified=document.publication_date_verified,
        publication_date_hint=document.publication_date_hint,
        publication_date_hint_source=document.publication_date_hint_source,
        modified_at=document.modified_at,
        modified_date_source=document.modified_date_source,
        published_at_unknown=document.published_at_unknown,
        source_class=classify_source(document.url),
        raw_content_hash=document.raw_content_hash or "",
        extracted_text_hash=document.extracted_text_hash or document.content_hash,
        extracted_text=document.text,
        content_type=document.content_type,
        byte_length=document.byte_length,
        status_code=document.status_code,
        as_of_eligible=document.as_of_eligible,
        requested_snapshot_url=document.requested_snapshot_url,
        requested_snapshot_at=document.requested_snapshot_at,
        final_snapshot_url=document.final_snapshot_url,
        final_snapshot_at=document.final_snapshot_at,
        archived_original_url=document.archived_original_url,
        snapshot_verification_status=document.snapshot_verification_status,
        created_at=utcnow(),
    )
    session.add(attachment)
    if accepted:
        has_version = session.scalar(
            select(ForecastVersion.id)
            .where(ForecastVersion.question_id == question.id)
            .limit(1)
        )
        if has_version is not None:
            question.stale = True
            question.status = "stale"
    session.flush()
    return attachment, True


def _evidence_item_id(run_id: str, attachment_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"forecastlab:manual-evidence-item:{run_id}:{attachment_id}",
        )
    )


def attach_manual_evidence_to_run(
    session: Session,
    *,
    run: ForecastRun,
) -> list[EvidenceItem]:
    attachments = session.scalars(
        select(ManualEvidenceAttachment)
        .where(
            ManualEvidenceAttachment.question_id == run.question_id,
            ManualEvidenceAttachment.status == "accepted",
            ManualEvidenceAttachment.as_of_eligible.is_(True),
            ManualEvidenceAttachment.mode == run.mode,
        )
        .order_by(ManualEvidenceAttachment.created_at, ManualEvidenceAttachment.id)
    ).all()
    created: list[EvidenceItem] = []
    for attachment in attachments:
        if not _same_timestamp(attachment.as_of, run.as_of):
            continue
        item_id = _evidence_item_id(run.id, attachment.id)
        existing = session.get(EvidenceItem, item_id)
        if existing is not None:
            created.append(existing)
            continue
        item = EvidenceItem(
            id=item_id,
            run_id=run.id,
            manual_evidence_attachment_id=attachment.id,
            track_id=None,
            subquestion="User-supplied evidence; pending node-specific claim extraction",
            url=attachment.canonical_url,
            title=attachment.title,
            publisher=attachment.publisher,
            published_at=attachment.published_at,
            retrieved_at=attachment.retrieved_at,
            source_available_at=attachment.source_available_at,
            temporal_basis=attachment.temporal_basis,
            publication_date_source=attachment.publication_date_source,
            publication_date_verified=attachment.publication_date_verified,
            publication_date_hint=attachment.publication_date_hint,
            publication_date_hint_source=attachment.publication_date_hint_source,
            modified_at=attachment.modified_at,
            modified_date_source=attachment.modified_date_source,
            excerpt=attachment.extracted_text[:800],
            content_hash=attachment.extracted_text_hash,
            source_class=attachment.source_class,
            as_of_eligible=attachment.as_of_eligible,
            rejected=False,
            rejection_reason=None,
            snapshot_url=attachment.final_snapshot_url,
            snapshot_at=attachment.final_snapshot_at,
            requested_snapshot_url=attachment.requested_snapshot_url,
            requested_snapshot_at=attachment.requested_snapshot_at,
            final_snapshot_url=attachment.final_snapshot_url,
            final_snapshot_at=attachment.final_snapshot_at,
            archived_original_url=attachment.archived_original_url,
            snapshot_verification_status=attachment.snapshot_verification_status,
            status_code=attachment.status_code,
            published_at_unknown=attachment.published_at_unknown,
        )
        session.add(item)
        created.append(item)
    session.flush()
    return created


def manual_evidence_documents_for_run(
    session: Session,
    *,
    run: ForecastRun,
) -> list[AttachedEvidenceDocument]:
    rows = session.scalars(
        select(EvidenceItem)
        .where(
            EvidenceItem.run_id == run.id,
            EvidenceItem.manual_evidence_attachment_id.is_not(None),
            EvidenceItem.rejected.is_(False),
            EvidenceItem.as_of_eligible.is_(True),
        )
        .order_by(EvidenceItem.id)
    ).all()
    output: list[AttachedEvidenceDocument] = []
    for item in rows:
        attachment = item.manual_evidence_attachment
        if attachment is None:
            raise ManualEvidenceError("manual_evidence_attachment_missing")
        if attachment.question_id != run.question_id:
            raise ManualEvidenceError("manual_evidence_cross_question_attachment")
        document = FetchedDocument(
            url=attachment.canonical_url,
            final_url=attachment.final_url,
            title=attachment.title,
            publisher=attachment.publisher,
            published_at=attachment.published_at,
            retrieved_at=attachment.retrieved_at,
            source_available_at=attachment.source_available_at,
            temporal_basis=attachment.temporal_basis,  # type: ignore[arg-type]
            publication_date_source=attachment.publication_date_source,
            publication_date_verified=attachment.publication_date_verified,
            publication_date_hint=attachment.publication_date_hint,
            publication_date_hint_source=attachment.publication_date_hint_source,
            modified_at=attachment.modified_at,
            modified_date_source=attachment.modified_date_source,
            text=attachment.extracted_text,
            content_hash=attachment.extracted_text_hash,
            raw_content_hash=attachment.raw_content_hash,
            extracted_text_hash=attachment.extracted_text_hash,
            content_type=attachment.content_type,
            byte_length=attachment.byte_length,
            snapshot_url=attachment.final_snapshot_url,
            snapshot_at=attachment.final_snapshot_at,
            requested_snapshot_url=attachment.requested_snapshot_url,
            requested_snapshot_at=attachment.requested_snapshot_at,
            final_snapshot_url=attachment.final_snapshot_url,
            final_snapshot_at=attachment.final_snapshot_at,
            archived_original_url=attachment.archived_original_url,
            snapshot_verification_status=attachment.snapshot_verification_status,
            status_code=attachment.status_code,
            rejected=False,
            rejection_reason=None,
            as_of_eligible=True,
            published_at_unknown=attachment.published_at_unknown,
        )
        output.append(
            AttachedEvidenceDocument(
                attachment_id=attachment.id,
                evidence_item_id=item.id,
                target_node_id=attachment.target_node_id,
                document=document,
            )
        )
    return output


def manual_evidence_out(
    session: Session,
    attachment: ManualEvidenceAttachment,
) -> dict[str, object]:
    run_ids = session.scalars(
        select(EvidenceItem.run_id)
        .where(EvidenceItem.manual_evidence_attachment_id == attachment.id)
        .order_by(EvidenceItem.run_id)
    ).all()
    has_version = session.scalar(
        select(ForecastVersion.id)
        .where(ForecastVersion.question_id == attachment.question_id)
        .limit(1)
    )
    return {
        "id": attachment.id,
        "question_id": attachment.question_id,
        "forecast_node_id": attachment.target_node_id,
        "intended_use": attachment.intended_use,
        "note": attachment.note,
        "mode": attachment.mode,
        "as_of": attachment.as_of.isoformat() if attachment.as_of else None,
        "submitted_url": attachment.submitted_url,
        "canonical_url": attachment.canonical_url,
        "final_url": attachment.final_url,
        "status": attachment.status,
        "accepted": attachment.status == "accepted",
        "rejection_reason": attachment.rejection_reason,
        "title": attachment.title,
        "publisher": attachment.publisher,
        "publication_date": (
            attachment.published_at.isoformat() if attachment.published_at else None
        ),
        "publication_date_source": attachment.publication_date_source,
        "publication_date_verified": attachment.publication_date_verified,
        "retrieval_date": attachment.retrieved_at.isoformat(),
        "source_available_at": attachment.source_available_at.isoformat(),
        "temporal_basis": attachment.temporal_basis,
        "published_at_unknown": attachment.published_at_unknown,
        "source_class": attachment.source_class,
        "content_hash": attachment.raw_content_hash,
        "extracted_text_hash": attachment.extracted_text_hash,
        "content_type": attachment.content_type,
        "byte_length": attachment.byte_length,
        "status_code": attachment.status_code,
        "as_of_eligible": attachment.as_of_eligible,
        "requested_snapshot_url": attachment.requested_snapshot_url,
        "requested_snapshot_at": (
            attachment.requested_snapshot_at.isoformat()
            if attachment.requested_snapshot_at
            else None
        ),
        "final_snapshot_url": attachment.final_snapshot_url,
        "final_snapshot_at": (
            attachment.final_snapshot_at.isoformat()
            if attachment.final_snapshot_at
            else None
        ),
        "archived_original_url": attachment.archived_original_url,
        "snapshot_verification_status": attachment.snapshot_verification_status,
        "created_at": attachment.created_at.isoformat(),
        "attached_run_ids": list(run_ids),
        "fresh_explicit_rerun_required": (
            attachment.status == "accepted"
            and has_version is not None
            and not run_ids
        ),
        "claim_created_at_intake": False,
    }

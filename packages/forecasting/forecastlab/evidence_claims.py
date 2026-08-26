from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from forecastlab.prompts import PromptBundle, load_prompt
from forecastlab.providers.base import ModelProvider
from forecastlab.ranking import classify_source
from forecastlab.schemas import EvidenceClaim, EvidenceStance, FetchedDocument, RunMode
from forecastlab.timeutil import as_utc, utcnow


class EvidenceClaimError(ValueError):
    def __init__(self, reasons: list[str], message: str = "Evidence claims could not be extracted") -> None:
        self.reasons = list(dict.fromkeys(reasons))
        super().__init__(message)


class _ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim: str
    excerpt: str
    supports_or_refutes: EvidenceStance
    confidence: float = Field(ge=0.0, le=1.0)
    source_quality: float = Field(ge=0.0, le=1.0)
    primary_source: bool

    @field_validator("claim", mode="before")
    @classmethod
    def normalize_claim(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return " ".join(value.split()).strip()

    @field_validator("excerpt", mode="before")
    @classmethod
    def trim_excerpt(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()


class _ExtractedClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claims: list[_ExtractedClaim] = Field(default_factory=list)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _fallback_excerpt(text: str, *, max_chars: int = 500) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    candidate = next(
        (sentence.strip() for sentence in sentences if len(sentence.strip()) >= 40),
        cleaned,
    )
    return candidate[:max_chars].strip()


_VERIFIED_HISTORICAL_SNAPSHOT_STATUSES = {
    "verified",
    "fixture",
    "cutoff_consistent_mock_manifest_verified",
}
_VERIFIED_IMMUTABLE_TIMESTAMP_STATUSES = {
    "fixture",
    "immutable_historical_timestamp_verified",
}


def _effective_mode(mode: RunMode | None, as_of: datetime | None) -> RunMode:
    return mode or ("backtest" if as_of is not None else "live")


def _document_errors(
    document: FetchedDocument,
    *,
    mode: RunMode,
    cutoff: datetime | None,
    run_completion_time: datetime | None,
) -> list[str]:
    errors: list[str] = []
    if document.rejected:
        errors.append("document_rejected")
    if not document.as_of_eligible:
        errors.append("document_not_as_of_eligible")
    if not document.url.strip():
        errors.append("source_url_required")
    if not document.title.strip():
        errors.append("source_title_required")
    if not (document.publisher or "").strip():
        errors.append("publisher_required")
    if not document.text.strip():
        errors.append("document_text_required")
    if document.published_at is None and document.publication_date_verified:
        errors.append("verified_publication_date_required")
    if document.published_at is not None:
        if as_utc(document.published_at) > as_utc(document.retrieved_at):
            errors.append("publication_after_retrieval")
        if cutoff is not None and as_utc(document.published_at) > as_utc(cutoff):
            errors.append("claim_after_cutoff")
    if document.temporal_basis == "publication_date":
        if document.published_at is None:
            errors.append("publication_basis_date_required")
        elif as_utc(document.source_available_at) != as_utc(document.published_at):
            errors.append("publication_basis_timestamp_mismatch")
    if document.temporal_basis == "retrieval_date" and as_utc(document.source_available_at) != as_utc(
        document.retrieved_at
    ):
        errors.append("retrieval_basis_timestamp_mismatch")

    if mode == "backtest":
        if cutoff is None:
            errors.append("historical_cutoff_required")
        elif as_utc(document.source_available_at) > as_utc(cutoff):
            errors.append("claim_after_cutoff")
        if document.temporal_basis == "retrieval_date":
            errors.append("historical_retrieval_basis_forbidden")
        elif document.temporal_basis == "snapshot_date":
            if document.snapshot_verification_status not in _VERIFIED_HISTORICAL_SNAPSHOT_STATUSES:
                errors.append("historical_snapshot_not_verified")
            if document.final_snapshot_at is None and document.snapshot_at is None:
                errors.append("historical_snapshot_timestamp_required")
        elif document.temporal_basis == "publication_date":
            if not document.publication_date_verified:
                errors.append("historical_publication_date_not_verified")
            if document.snapshot_verification_status not in _VERIFIED_IMMUTABLE_TIMESTAMP_STATUSES:
                errors.append("historical_immutable_timestamp_adapter_required")
    else:
        completion = as_utc(run_completion_time or utcnow())
        if as_utc(document.source_available_at) > completion:
            errors.append("source_available_after_run_completion")
    return errors


def _mock_claims(document: FetchedDocument) -> dict[str, Any]:
    excerpt = document.text.strip()[:500]
    publisher = (document.publisher or "").casefold()
    primary = any(token in publisher for token in ("bureau", "department", "agency", "official", "fixture"))
    return {
        "claims": [
            {
                "claim": excerpt,
                "excerpt": excerpt,
                "supports_or_refutes": "supports",
                "confidence": 0.8,
                "source_quality": 0.8 if primary else 0.6,
                "primary_source": primary,
            }
        ]
    }


def eligible_claims_for_forecasting(
    claims: list[EvidenceClaim],
    *,
    mode: RunMode | None = None,
    cutoff: datetime | None = None,
    run_completion_time: datetime | None = None,
) -> list[EvidenceClaim]:
    """Return only fully validated claims for a future forecasting context."""

    return [
        claim
        for claim in claims
        if not claim.forecasting_errors(
            mode=mode,
            cutoff=cutoff,
            run_completion_time=run_completion_time,
        )
    ]


class EvidenceExtractor:
    """Extract node-linked, provenance-preserving claims from one fetched document."""

    def __init__(
        self,
        model: ModelProvider,
        *,
        prompt_bundle: PromptBundle | None = None,
        max_output_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.prompt_bundle = prompt_bundle
        self.max_output_tokens = max(1, max_output_tokens)

    def extract(
        self,
        document: FetchedDocument,
        *,
        evidence_item_id: str,
        forecast_node_id: str,
        forecast_node_question: str | None = None,
        as_of: datetime | None = None,
        mode: RunMode | None = None,
        run_completion_time: datetime | None = None,
    ) -> list[EvidenceClaim]:
        identity_errors: list[str] = []
        if not evidence_item_id.strip():
            identity_errors.append("evidence_item_id_required")
        if not forecast_node_id.strip():
            identity_errors.append("forecast_node_id_required")
        effective_mode = _effective_mode(mode, as_of)
        cutoff = as_utc(as_of) if as_of is not None else None
        completion = as_utc(run_completion_time or utcnow())
        preflight_errors = identity_errors + _document_errors(
            document,
            mode=effective_mode,
            cutoff=cutoff,
            run_completion_time=completion,
        )
        if preflight_errors:
            raise EvidenceClaimError(preflight_errors, "Fetched document is not eligible for claim extraction")

        try:
            if self.model.name == "mock":
                payload: dict[str, Any] = _mock_claims(document)
            else:
                system, _prompt_version = (
                    self.prompt_bundle.get("evidence_claims")
                    if self.prompt_bundle is not None
                    else load_prompt("evidence_claims")
                )
                result = self.model.complete_json(
                    system=system,
                    user=json.dumps(
                        {
                            "forecast_node_id": forecast_node_id,
                            "forecast_node_question": forecast_node_question or "",
                            "document": {
                                "source_url": document.url,
                                "source_title": document.title,
                                "publisher": document.publisher,
                                "publication_date": (
                                    document.published_at.isoformat()
                                    if document.published_at is not None
                                    else None
                                ),
                                "publication_date_verified": document.publication_date_verified,
                                "retrieval_date": document.retrieved_at.isoformat(),
                                "source_available_at": document.source_available_at.isoformat(),
                                "temporal_basis": document.temporal_basis,
                                "text": document.text,
                            },
                        }
                    ),
                    schema_name="evidence_claims",
                    max_output_tokens=self.max_output_tokens,
                )
                payload = result.parsed if result.parsed is not None else json.loads(result.content)
            extracted = _ExtractedClaims.model_validate(payload)
        except (json.JSONDecodeError, TypeError, ValidationError, KeyError) as exc:
            raise EvidenceClaimError(["invalid_structured_output"]) from exc

        claims: list[EvidenceClaim] = []
        seen: set[tuple[str, str]] = set()
        output_errors: list[str] = []
        for item in extracted.claims:
            if not item.claim:
                output_errors.append("claim_required")
            if not item.excerpt:
                output_errors.append("excerpt_required")
            elif item.excerpt not in document.text:
                output_errors.append("excerpt_not_in_document")
            fingerprint = (_normalized_text(item.claim), _normalized_text(item.excerpt))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            claims.append(
                EvidenceClaim(
                    id=str(uuid.uuid4()),
                    evidence_item_id=evidence_item_id,
                    forecast_node_id=forecast_node_id,
                    claim=item.claim,
                    excerpt=item.excerpt,
                    source_url=document.url,
                    source_title=document.title,
                    publisher=document.publisher or "",
                    publication_date=document.published_at,
                    publication_date_source=document.publication_date_source,
                    publication_date_verified=document.publication_date_verified,
                    retrieval_date=document.retrieved_at,
                    source_available_at=document.source_available_at,
                    temporal_basis=document.temporal_basis,
                    supports_or_refutes=item.supports_or_refutes,
                    confidence=item.confidence,
                    source_quality=item.source_quality,
                    primary_source=item.primary_source,
                    as_of_eligible=document.as_of_eligible,
                    cutoff_verified=True,
                )
            )
        if output_errors:
            raise EvidenceClaimError(output_errors, "Structured claims failed provenance validation")
        context_errors = [
            error
            for claim in claims
            for error in claim.forecasting_errors(
                mode=effective_mode,
                cutoff=cutoff,
                run_completion_time=completion,
            )
        ]
        if context_errors:
            raise EvidenceClaimError(context_errors, "Extracted claims are not eligible for forecasting context")
        return claims

    def document_fallback_claim(
        self,
        document: FetchedDocument,
        *,
        evidence_item_id: str,
        forecast_node_id: str,
        as_of: datetime | None = None,
        mode: RunMode | None = None,
        run_completion_time: datetime | None = None,
    ) -> EvidenceClaim:
        """Create one low-confidence, verbatim claim when structured extraction fails."""

        effective_mode = _effective_mode(mode, as_of)
        cutoff = as_utc(as_of) if as_of is not None else None
        completion = as_utc(run_completion_time or utcnow())
        errors = _document_errors(
            document,
            mode=effective_mode,
            cutoff=cutoff,
            run_completion_time=completion,
        )
        if not evidence_item_id.strip():
            errors.append("evidence_item_id_required")
        if not forecast_node_id.strip():
            errors.append("forecast_node_id_required")
        excerpt = _fallback_excerpt(document.text)
        if not excerpt:
            errors.append("document_fallback_excerpt_required")
        if errors:
            raise EvidenceClaimError(
                errors,
                "Document-level fallback claim could not preserve provenance",
            )

        publisher = document.publisher or ""
        primary = classify_source(document.url) == "primary" or any(
            token in publisher.casefold()
            for token in (
                "bureau",
                "department",
                "agency",
                "commission",
                "official",
                "fixture",
            )
        )
        claim = EvidenceClaim(
            id=str(uuid.uuid4()),
            evidence_item_id=evidence_item_id,
            forecast_node_id=forecast_node_id,
            # The claim is the exact source passage, not a generated summary.
            claim=excerpt,
            excerpt=excerpt,
            source_url=document.url,
            source_title=document.title,
            publisher=publisher,
            publication_date=document.published_at,
            publication_date_source=document.publication_date_source,
            publication_date_verified=document.publication_date_verified,
            retrieval_date=document.retrieved_at,
            source_available_at=document.source_available_at,
            temporal_basis=document.temporal_basis,
            supports_or_refutes="supports",
            confidence=0.35,
            source_quality=0.7 if primary else 0.5,
            primary_source=primary,
            as_of_eligible=document.as_of_eligible,
            cutoff_verified=True,
        )
        claim_errors = claim.forecasting_errors(
            mode=effective_mode,
            cutoff=cutoff,
            run_completion_time=completion,
        )
        if claim_errors:
            raise EvidenceClaimError(
                claim_errors,
                "Document-level fallback claim is not eligible for forecasting",
            )
        return claim

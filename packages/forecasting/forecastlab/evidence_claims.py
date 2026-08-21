from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from forecastlab.prompts import load_prompt
from forecastlab.providers.base import ModelProvider
from forecastlab.schemas import EvidenceClaim, EvidenceStance, FetchedDocument
from forecastlab.timeutil import as_utc


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


def _document_errors(document: FetchedDocument, *, cutoff: datetime) -> list[str]:
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
    if document.published_at_unknown:
        errors.append("publication_date_unverified")
    if document.published_at is None:
        errors.append("publication_date_required")
    elif as_utc(document.published_at) > as_utc(cutoff):
        errors.append("claim_after_cutoff")
    if not document.text.strip():
        errors.append("document_text_required")
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
    cutoff: datetime | None = None,
) -> list[EvidenceClaim]:
    """Return only fully validated claims for a future forecasting context."""

    return [claim for claim in claims if not claim.forecasting_errors(cutoff=cutoff)]


class EvidenceExtractor:
    """Extract node-linked, provenance-preserving claims from one fetched document."""

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def extract(
        self,
        document: FetchedDocument,
        *,
        evidence_item_id: str,
        forecast_node_id: str,
        forecast_node_question: str | None = None,
        as_of: datetime | None = None,
    ) -> list[EvidenceClaim]:
        identity_errors: list[str] = []
        if not evidence_item_id.strip():
            identity_errors.append("evidence_item_id_required")
        if not forecast_node_id.strip():
            identity_errors.append("forecast_node_id_required")
        cutoff = as_utc(as_of) if as_of is not None else as_utc(document.retrieved_at)
        preflight_errors = identity_errors + _document_errors(document, cutoff=cutoff)
        if preflight_errors:
            raise EvidenceClaimError(preflight_errors, "Fetched document is not eligible for claim extraction")

        publication_date = document.published_at
        assert publication_date is not None

        try:
            if self.model.name == "mock":
                payload: dict[str, Any] = _mock_claims(document)
            else:
                system, _prompt_version = load_prompt("evidence_claims")
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
                                "publication_date": publication_date.isoformat(),
                                "retrieval_date": document.retrieved_at.isoformat(),
                                "text": document.text,
                            },
                        }
                    ),
                    schema_name="evidence_claims",
                    max_output_tokens=4096,
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
                    publication_date=publication_date,
                    retrieval_date=document.retrieved_at,
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
        context_errors = [error for claim in claims for error in claim.forecasting_errors(cutoff=cutoff)]
        if context_errors:
            raise EvidenceClaimError(context_errors, "Extracted claims are not eligible for forecasting context")
        return claims

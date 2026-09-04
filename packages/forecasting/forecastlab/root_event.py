"""Versioned, same-contract estimates and evidence assessments for personal V1.

The graph is a research index. Only estimates of the approved root event may
enter this module's aggregator. Legacy node probabilities are never accepted.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.aggregation import aggregate_track_probabilities
from forecastlab.schemas import EvidenceClaim, ForecastContract

ROLES = ("base_rate", "current_evidence", "skeptic")
REQUIRED_EVIDENCE = ("resolution", "reference_class", "current_conditions")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def contract_hash(contract: ForecastContract) -> str:
    return digest(contract.model_dump(mode="json", exclude={"created_at", "created_by", "status"}))


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Provider strict JSON schemas require every property, including nullable ones."""
    schema = model.model_json_schema()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    classification: Literal["supporting", "opposing", "background", "unusable"]
    relevant: bool
    required_sections: list[Literal["resolution", "reference_class", "current_conditions"]]
    reason: str = Field(min_length=1, max_length=1000)


class EvidenceAssessments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[EvidenceAssessment]


class RootEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    contract_hash: str
    evidence_packet_hash: str
    target_question: str
    resolution_date: str
    as_of: str | None
    role: Literal["base_rate", "current_evidence", "skeptic"]
    probability: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    uncertainties: list[str]
    developments_to_watch: list[str]


def source_lineage(url: str, *, publisher: str = "", text: str = "") -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    # Known mirrors share the originating statistical agency's lineage.
    if (host == "bls.gov" or host.endswith(".bls.gov")) or ((host == "stlouisfed.org" or host.endswith(".stlouisfed.org")) and
            any(s in (publisher + " " + text).lower() for s in ("bureau of labor", "bls", "unrate", "payems", "cpi"))):
        return "agency:bls"
    return "publisher:" + host


def assess_packet(claims: list[EvidenceClaim], assessments: list[EvidenceAssessment]) -> tuple[list[dict], list[str]]:
    by_id = {claim.id: claim for claim in claims}
    seen: set[str] = set()
    packet: list[dict] = []
    quoted_origins: set[str] = set()
    for assessment in assessments:
        if assessment.claim_id not in by_id or assessment.claim_id in seen:
            raise ValueError("unknown_or_duplicate_assessment_claim")
        seen.add(assessment.claim_id)
        claim = by_id[assessment.claim_id]
        # A quoted fallback is still not a validated factual finding.
        usable = (assessment.relevant and assessment.classification != "unusable"
                  and claim.extraction_method in {"structured_full_document", "structured_smaller_chunk", "mock_structured"}
                  and claim.as_of_eligible and claim.cutoff_verified)
        quote_hash = digest(" ".join(claim.excerpt.lower().split()))
        duplicate = usable and quote_hash in quoted_origins
        if usable:
            quoted_origins.add(quote_hash)
        usable = usable and not duplicate
        packet.append({
            "schema_version": "evidence_assessment_v1", **assessment.model_dump(),
            "usable": usable, "claim": claim.claim, "quote": claim.excerpt,
            "url": claim.source_url, "title": claim.source_title,
            "primary_source": claim.source_class == "primary",
            "source_lineage": source_lineage(claim.source_url, publisher=claim.publisher, text=claim.claim),
            "source_available_at": claim.source_available_at.isoformat(),
            "extraction_method": claim.extraction_method,
            "corroboration_group": quote_hash, "duplicate_quote": duplicate,
        })
    for claim in claims:
        if claim.id not in seen:
            packet.append({"claim_id": claim.id, "classification": "unusable", "usable": False,
                           "reason": "assessment_missing", "url": claim.source_url, "quote": claim.excerpt,
                           "required_sections": [], "schema_version": "evidence_assessment_v1"})
    covered = {section for item in packet if item["usable"] for section in item["required_sections"]}
    gaps = [f"missing_{section}_evidence" for section in REQUIRED_EVIDENCE if section not in covered]
    if not any(item["usable"] and item.get("primary_source") for item in packet):
        gaps.append("relevant_primary_source_required")
    return packet, gaps


def aggregate_root_estimates(contract: ForecastContract, packet: list[dict], estimates: list[RootEstimate],
                             *, as_of: str | None) -> dict:
    if contract.resolution_date is None:
        raise ValueError("resolution_date_required")
    if len(estimates) != 3 or {item.role for item in estimates} != set(ROLES):
        raise ValueError("three_valid_root_estimates_required")
    fingerprint = digest(packet)
    ids = {item["claim_id"] for item in packet if item["usable"]}
    for estimate in estimates:
        if not math.isfinite(estimate.probability) or not 0 <= estimate.probability <= 1:
            raise ValueError("invalid_root_probability")
        if (estimate.contract_hash != contract_hash(contract) or estimate.evidence_packet_hash != fingerprint
                or estimate.target_question != contract.normalized_question
                or estimate.resolution_date != contract.resolution_date.isoformat()
                or estimate.as_of != as_of):
            raise ValueError("estimate_target_mismatch")
        if not set(estimate.evidence_ids).issubset(ids):
            raise ValueError("estimate_uses_unusable_evidence")
    by_role: dict[str, float] = {item.role: item.probability for item in estimates}
    result = aggregate_track_probabilities({role: by_role[role] for role in ROLES}, shrinkage=0.10)
    return {"method": "root_event_ensemble_v1", "probability": result.ensemble_probability,
            "spread": result.track_spread, "shrinkage": 0.10, "shared_evidence": True,
            "contract_hash": contract_hash(contract), "evidence_packet_hash": fingerprint}

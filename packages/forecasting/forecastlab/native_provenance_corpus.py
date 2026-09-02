"""Deterministic native-record corpus acquisition helpers for private V1.

This module does not create an EvaluationRelease, forecast, score, or review
artifact.  It turns public, historically archived exchange records into an
outcome-blind candidate projection, a separately sealed outcome projection, and
one native-provenance packet.  Native timestamps are explicit evidence; local
retrieval time is retained solely as an audit observation.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC_V2,
    QUESTION_REVIEW_RUBRIC_V2,
    OutcomeAdjudicationManifest,
    OutcomeAdjudicationSource,
    QuestionReviewManifest,
    QuestionReviewSource,
    rubric_hash,
)

NATIVE_CORPUS_POLICY = "private_v1_native_provenance_corpus_v3"
NATIVE_RECORD_TEMPORAL_BASIS = "native_record_timestamp"
NATIVE_RECORD_MANIFEST_BASIS = "immutable_version"
NATIVE_SOURCE_ID = "kalshi_historical_market_api_v2"
NATIVE_SOURCE_PUBLISHER = "Kalshi"
SPLIT_SEED = "forecastlab-native-provenance-v3-split-v1"
SPLIT_TARGETS = {"development": 60, "validation": 40, "test": 100}
RESERVE_TARGET = 60
MIN_HORIZON = timedelta(days=7)
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset({"a", "an", "and", "at", "be", "by", "for", "in", "is", "of", "on", "or", "the", "to", "will"})


class NativeCorpusError(RuntimeError):
    """A deterministic native-provenance validation failure."""


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise NativeCorpusError("native_record_url_invalid")
    return f"https://{parsed.hostname.casefold().removeprefix('www.')}{parsed.path}"


def parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise NativeCorpusError(f"native_timestamp_missing:{field}")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NativeCorpusError(f"native_timestamp_invalid:{field}") from exc
    if result.tzinfo is None:
        raise NativeCorpusError(f"native_timestamp_timezone_missing:{field}")
    return result.astimezone(UTC)


def normalize_question(value: str) -> str:
    return " ".join(value.strip().split()).casefold().rstrip(" ?.!:")


def _tokens(value: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(value.casefold()) if token not in STOPWORDS]


def _hash(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _native_record_url(ticker: str) -> str:
    return (
        "https://api.elections.kalshi.com/trade-api/v2/historical/markets/"
        f"{ticker}"
    )


def _source_host(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().removeprefix("www.")


def _validate_market(market: Mapping[str, Any]) -> tuple[str, datetime, datetime, int]:
    ticker = str(market.get("ticker") or "").strip()
    if not ticker:
        raise NativeCorpusError("native_market_ticker_missing")
    if str(market.get("market_type") or "").casefold() != "binary":
        raise NativeCorpusError("native_market_not_binary")
    if str(market.get("result") or "").casefold() not in {"yes", "no"}:
        raise NativeCorpusError("native_market_outcome_not_binary")
    if market.get("mve_collection_ticker") or ticker.startswith("KXMVE"):
        raise NativeCorpusError("native_market_multivariate_excluded")
    question = " ".join(str(market.get("title") or "").split())
    if not 20 <= len(question) <= 400:
        raise NativeCorpusError("native_market_question_shape_invalid")
    rules = " ".join(str(market.get("rules_primary") or "").split())
    if len(rules) < 40:
        raise NativeCorpusError("native_market_resolution_rules_missing")
    created_at = parse_timestamp(market.get("created_time"), field="created_time")
    settled_at = parse_timestamp(market.get("settlement_ts"), field="settlement_ts")
    if created_at >= settled_at:
        raise NativeCorpusError("native_market_temporal_order_invalid")
    if settled_at - created_at < MIN_HORIZON:
        raise NativeCorpusError("native_market_forecast_horizon_under_minimum")
    return ticker, created_at, settled_at, 1 if str(market["result"]).casefold() == "yes" else 0


def native_candidate_from_market(
    *,
    market: Mapping[str, Any],
    category: str,
    retrieved_at: datetime,
    raw_record_hash: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build blinded, sealed, and source packet projections from one record.

    The market's source record is treated as an official immutable historical
    record only after the caller has captured the public historical endpoint.
    The native `created_time` and `settlement_ts` fields establish availability
    and outcome timing; `retrieved_at` never replaces either.
    """

    ticker, created_at, settled_at, outcome = _validate_market(market)
    event_ticker = str(market.get("event_ticker") or "").strip()
    if not event_ticker:
        raise NativeCorpusError("native_market_event_ticker_missing")
    question = " ".join(str(market["title"]).split())
    normalized = normalize_question(question)
    rules = " ".join(str(market["rules_primary"]).split())
    origin_url = _native_record_url(ticker)
    source_text = canonical_json(
        {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": question,
            "rules_primary": rules,
            "created_time": created_at.isoformat(),
            "settlement_ts": settled_at.isoformat(),
            "category": category,
        }
    )
    content_hash = sha256_text(source_text)
    text_hash = sha256_text(f"{question}\n{rules}\n")
    contract_hash = _hash(
        {
            "question": normalized,
            "yes_condition": rules,
            "no_condition": f"The Kalshi market {ticker} settles No under its published rules.",
            "resolver": NATIVE_SOURCE_PUBLISHER,
            "ticker": ticker,
        }
    )
    candidate_id = "flnv3-" + sha256_text(f"{NATIVE_SOURCE_ID}:{ticker}")[:20]
    family = "ef-kalshi-event-" + sha256_text(event_ticker)[:16]
    leakage = "lg-kalshi-event-" + sha256_text(event_ticker)[:16]
    source_common = {
        "source_id": f"kalshi-market-{ticker}",
        "url": origin_url,
        "title": f"Kalshi historical market {ticker}",
        "publisher": NATIVE_SOURCE_PUBLISHER,
        "source_available_at": created_at.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "published_at": None,
        "published_at_unknown": True,
        "temporal_basis": NATIVE_RECORD_TEMPORAL_BASIS,
        "manifest_temporal_basis": NATIVE_RECORD_MANIFEST_BASIS,
        "content_sha256": content_hash,
        "extracted_text_sha256": text_hash,
        "raw_record_sha256": raw_record_hash,
        "source_host": _source_host(origin_url),
        "source_license_status": "unknown",
        "source_use_basis": "Public unauthenticated Kalshi historical market API record.",
        "redistribution_allowed": False,
    }
    blinded = {
        "candidate_id": candidate_id,
        "source_id": NATIVE_SOURCE_ID,
        "source_record_id": ticker,
        "original_question": question,
        "normalized_question": normalized,
        "normalized_question_hash": sha256_text(normalized),
        "yes_condition": rules,
        "no_condition": f"The Kalshi market {ticker} settles No under its published rules.",
        "contract_hash": contract_hash,
        "forecast_date": created_at.isoformat(),
        "resolution_date": settled_at.isoformat(),
        "authoritative_resolver": "Kalshi final historical market settlement record",
        "origin_url": origin_url,
        "origin_timestamp": created_at.isoformat(),
        "origin_proof": {
            "type": "native_timestamped_historical_market_record",
            "record_id": ticker,
            "native_timestamp_field": "created_time",
            "native_timestamp": created_at.isoformat(),
            "record_sha256": raw_record_hash,
            "record_url": origin_url,
            "retrieval_time_not_used_as_proof": True,
        },
        "domain": category.casefold().replace(" and ", "_"),
        "category": category,
        "event_family_id": family,
        "leakage_group_id": leakage,
        "grouping_features": {
            "source": "kalshi",
            "event_ticker": event_ticker,
            "series_ticker": str(market.get("series_ticker") or ""),
            "market_ticker": ticker,
        },
        "source_use": {
            "status": "unknown_pending_human_review",
            "basis": "Public native exchange record; licensing remains V2 audit-only.",
            "redistribution_allowed": False,
            "bytes_must_stay_private": True,
        },
        "machine_screening_state": "native_provenance_ready_pending_human_review",
        "review_status": "awaiting_human_review",
        "reviewer_id": None,
        "adjudication_status": "awaiting_independent_adjudication",
        "outcome_adjudicator_id": None,
        "provisional_split_status": "unassigned",
        "native_provenance_status": "ready",
        "question_source": {**source_common, "source_role": "pre_outcome_origin"},
    }
    sealed = {
        "candidate_id": candidate_id,
        "provisional_observed_outcome": outcome,
        "outcome_known_at": settled_at.isoformat(),
        "resolution_url": origin_url,
        "resolution_record_sha256": raw_record_hash,
        "resolution_native_timestamp_field": "settlement_ts",
        "resolution_native_timestamp": settled_at.isoformat(),
        "adjudication_status": "awaiting_independent_adjudication",
        "resolution_source": {**source_common, "source_role": "authoritative_resolution"},
    }
    packet = {
        "packet_id": "native-packet-" + candidate_id,
        "candidate_id": candidate_id,
        "cutoff": created_at.isoformat(),
        "status": "ready",
        "accepted_document_ids": ["native-doc-" + candidate_id],
        "attempt_ids": ["native-attempt-" + candidate_id],
        "no_evidence_reason": None,
        "native_provenance_verified": True,
        "source": {**source_common, "source_role": "pre_outcome_origin"},
    }
    return blinded, sealed, packet


def native_document_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    source = packet["source"]
    if not isinstance(source, Mapping):
        raise NativeCorpusError("native_packet_source_missing")
    return {
        "document_id": packet["accepted_document_ids"][0],
        "candidate_id": packet["candidate_id"],
        "canonical_url": source["url"],
        "title": source["title"],
        "publisher": source["publisher"],
        "source_host": source["source_host"],
        "source_class": "primary",
        "source_role": source["source_role"],
        "retrieved_at": source["retrieved_at"],
        "published_at": None,
        "published_at_unknown": True,
        "source_available_at": source["source_available_at"],
        "temporal_basis": source["temporal_basis"],
        "cutoff_verified": True,
        "native_timestamp_field": "created_time",
        "native_timestamp_verified": True,
        "content_sha256": source["content_sha256"],
        "extracted_text_sha256": source["extracted_text_sha256"],
        "raw_record_sha256": source["raw_record_sha256"],
        "source_license_status": source["source_license_status"],
        "source_use_basis": source["source_use_basis"],
        "redistribution_allowed": source["redistribution_allowed"],
        "evidence_note": (
            "Native Kalshi historical-market record; `created_time` establishes "
            "the pre-outcome origin/cutoff timestamp. Retrieval time is audit-only."
        ),
    }


def native_failure_packet(
    *,
    source_record_id: str,
    reason: str,
    retrieved_at: datetime,
) -> dict[str, Any]:
    return {
        "source_record_id": source_record_id,
        "status": "no_eligible_evidence",
        "reason": reason,
        "retrieved_at": retrieved_at.isoformat(),
        "temporal_basis": None,
        "native_provenance_verified": False,
    }


def provisional_split(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Assign only by leakage identity and a frozen seed; never by outcome."""

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["leakage_group_id"])].append(candidate)
    remaining = dict(SPLIT_TARGETS)
    assignments: list[dict[str, str]] = []
    reserve: list[str] = []
    ordered = sorted(
        grouped.items(),
        key=lambda item: (sha256_text(f"{SPLIT_SEED}:{item[0]}"), item[0]),
    )
    for group_id, members in ordered:
        members = sorted(members, key=lambda row: str(row["candidate_id"]))
        viable = [split for split, cap in remaining.items() if cap >= len(members)]
        if viable:
            split = max(
                viable,
                key=lambda name: (remaining[name] / SPLIT_TARGETS[name], remaining[name], name),
            )
            remaining[split] -= len(members)
            assignments.extend(
                {
                    "candidate_id": str(member["candidate_id"]),
                    "split": split,
                    "leakage_group_id": group_id,
                    "event_family_id": str(member["event_family_id"]),
                    "status": "provisional_pending_human_review",
                }
                for member in members
            )
        else:
            reserve.extend(str(member["candidate_id"]) for member in members)
    counts = Counter(row["split"] for row in assignments)
    return {
        "seed": SPLIT_SEED,
        "assignments": sorted(assignments, key=lambda row: (row["split"], row["candidate_id"])),
        "reserve_candidate_ids": sorted(reserve),
        "counts": {split: counts[split] for split in SPLIT_TARGETS},
        "target_counts": SPLIT_TARGETS,
        "complete": all(counts[key] == value for key, value in SPLIT_TARGETS.items())
        and len(reserve) >= RESERVE_TARGET,
        "shortfall": {split: SPLIT_TARGETS[split] - counts[split] for split in SPLIT_TARGETS},
        "reserve_shortfall": max(0, RESERVE_TARGET - len(reserve)),
        "outcome_used": False,
    }


def attach_split(
    candidates: Sequence[dict[str, Any]], split: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return candidates with deterministic split labels and no cross-split group."""

    by_id = {str(row["candidate_id"]): row for row in candidates}
    split_by_id = {str(row["candidate_id"]): str(row["split"]) for row in split["assignments"]}
    for candidate_id in split["reserve_candidate_ids"]:
        split_by_id[str(candidate_id)] = "reserve"
    if set(by_id) != set(split_by_id):
        raise NativeCorpusError("native_split_candidate_identity_mismatch")
    result: list[dict[str, Any]] = []
    for candidate_id in sorted(by_id):
        row = dict(by_id[candidate_id])
        row["provisional_split"] = split_by_id[candidate_id]
        row["provisional_split_status"] = "provisional_pending_human_review"
        result.append(row)
    return result


def validate_native_v2_readiness(
    *,
    candidates: Sequence[Mapping[str, Any]],
    sealed_outcomes: Sequence[Mapping[str, Any]],
    packets: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate native receipts and typed V2 inputs without persisting manifests."""

    sealed_by_id = {str(row["candidate_id"]): row for row in sealed_outcomes}
    packet_by_id = {str(row["candidate_id"]): row for row in packets}
    document_by_id = {str(row["candidate_id"]): row for row in documents}
    errors: list[str] = []
    question_manifest_count = 0
    outcome_manifest_count = 0
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        sealed = sealed_by_id.get(candidate_id)
        packet = packet_by_id.get(candidate_id)
        document = document_by_id.get(candidate_id)
        if sealed is None or packet is None or document is None:
            errors.append(f"native_receipt_missing:{candidate_id}")
            continue
        if packet.get("status") != "ready" or not packet.get("native_provenance_verified"):
            errors.append(f"native_packet_not_ready:{candidate_id}")
            continue
        if document.get("temporal_basis") != NATIVE_RECORD_TEMPORAL_BASIS:
            errors.append(f"native_temporal_basis_invalid:{candidate_id}")
            continue
        if document.get("source_available_at") != candidate.get("origin_timestamp"):
            errors.append(f"native_cutoff_timestamp_mismatch:{candidate_id}")
            continue
        question_source = candidate.get("question_source")
        resolution_source = sealed.get("resolution_source")
        if not isinstance(question_source, Mapping) or not isinstance(resolution_source, Mapping):
            errors.append(f"native_source_projection_missing:{candidate_id}")
            continue
        try:
            QuestionReviewManifest(
                evaluation_release_id="native-v3-not-a-release",
                evaluation_release_question_id=candidate_id,
                evaluation_question_id=candidate_id,
                normalized_question_hash=candidate["normalized_question_hash"],
                contract_hash=candidate["contract_hash"],
                question=candidate["original_question"],
                yes_condition=candidate["yes_condition"],
                no_condition=candidate["no_condition"],
                forecast_date=parse_timestamp(candidate["forecast_date"], field="forecast_date"),
                resolution_date=parse_timestamp(candidate["resolution_date"], field="resolution_date"),
                authoritative_resolver=candidate["authoritative_resolver"],
                event_family_id=candidate["event_family_id"],
                leakage_group_id=candidate["leakage_group_id"],
                sources=[
                    QuestionReviewSource(
                        **{
                            key: question_source[key]
                            for key in (
                                "source_id", "url", "title", "publisher", "source_role",
                                "source_available_at", "retrieved_at", "published_at",
                                "published_at_unknown", "content_sha256", "extracted_text_sha256",
                                "source_license_status", "source_use_basis", "redistribution_allowed",
                            )
                        },
                        temporal_basis=NATIVE_RECORD_MANIFEST_BASIS,
                        evidence_note="Native record timestamped before outcome; retrieval time is audit-only.",
                    )
                ],
                rubric_version=QUESTION_REVIEW_RUBRIC_V2.version,
                rubric_hash=rubric_hash(QUESTION_REVIEW_RUBRIC_V2),
            )
            question_manifest_count += 1
            OutcomeAdjudicationManifest(
                evaluation_release_id="native-v3-not-a-release",
                evaluation_release_question_id=candidate_id,
                evaluation_question_id=candidate_id,
                contract_hash=candidate["contract_hash"],
                yes_condition=candidate["yes_condition"],
                no_condition=candidate["no_condition"],
                resolution_date=parse_timestamp(candidate["resolution_date"], field="resolution_date"),
                candidate_outcome=sealed["provisional_observed_outcome"],
                outcome_known_at=parse_timestamp(sealed["outcome_known_at"], field="outcome_known_at"),
                sources=[
                    OutcomeAdjudicationSource(
                        **{
                            key: resolution_source[key]
                            for key in (
                                "source_id", "url", "title", "publisher", "source_role",
                                "source_available_at", "retrieved_at", "published_at",
                                "published_at_unknown", "content_sha256", "extracted_text_sha256",
                            )
                        },
                        temporal_basis=NATIVE_RECORD_MANIFEST_BASIS,
                        evidence_note="Native final settlement timestamp; retrieval time is audit-only.",
                    )
                ],
                rubric_version=OUTCOME_ADJUDICATION_RUBRIC_V2.version,
                rubric_hash=rubric_hash(OUTCOME_ADJUDICATION_RUBRIC_V2),
            )
            outcome_manifest_count += 1
        except Exception as exc:  # pydantic details stay out of persisted public summaries
            errors.append(f"sealed_manifest_invalid:{candidate_id}:{type(exc).__name__}")
    result = {
        "policy_version": NATIVE_CORPUS_POLICY,
        "candidate_count": len(candidates),
        "question_manifest_inputs_validated": question_manifest_count,
        "outcome_manifest_inputs_validated": outcome_manifest_count,
        "errors": sorted(errors),
        "review_artifacts_created": False,
        "release_frozen": False,
    }
    result["ready"] = not errors and question_manifest_count == len(candidates)
    result["preflight_hash"] = _hash(result)
    return result

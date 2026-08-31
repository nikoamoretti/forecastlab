from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from forecastlab.evaluation_releases import (
    PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
    EvaluationProviderIdentity,
    EvaluationReleaseQuestionInput,
)
from forecastlab.evidence_claims import EvidenceExtractor
from forecastlab.frozen_evidence import (
    FrozenEvidenceIntegrityError,
)
from forecastlab.hashing import canonical_json, sha256_bytes, sha256_text
from forecastlab.historical_evidence_releases import (
    BlindedHistoricalEvidenceDocument,
    BlindedHistoricalEvidencePacket,
    HistoricalEvidenceBundleManifest,
    HistoricalEvidenceCandidateInput,
    HistoricalEvidenceDocumentInput,
    HistoricalEvidencePacketInput,
    verify_historical_evidence_bundle,
)
from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC,
    OUTCOME_ADJUDICATION_RUBRIC_HASH,
    OUTCOME_ADJUDICATION_RUBRIC_VERSION,
    PROCEDURAL_AI_REVIEW_POLICY_VERSION,
    QUESTION_REVIEW_RUBRIC,
    QUESTION_REVIEW_RUBRIC_HASH,
    QUESTION_REVIEW_RUBRIC_VERSION,
    OutcomeAdjudicationManifest,
    OutcomeAdjudicationOutput,
    QuestionReviewManifest,
    QuestionReviewOutput,
)
from forecastlab.providers.mock import MockModelProvider
from forecastlab_api.evaluation_releases import (
    EvaluationReleaseValidationError,
    create_evaluation_release,
    freeze_evaluation_release,
    record_procedural_ai_review_artifact,
    review_evaluation_release,
)
from forecastlab_api.historical_evidence_releases import (
    HistoricalEvidenceReleaseValidationError,
    _refresh_artifacts,
    build_frozen_evidence_runtime,
    create_historical_evidence_release,
    freeze_historical_evidence_release,
    get_historical_evidence_execution_manifest,
    review_historical_evidence_release,
    validate_historical_evidence_release,
    verify_release_bundle,
)
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationRelease,
    ForecastExperiment,
    HistoricalEvidenceRelease,
    Job,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)
CUTOFF = datetime(2019, 1, 1, tzinfo=UTC)
CAPTURE = datetime(2018, 12, 1, tzinfo=UTC)
SMALL_RELEASE_POLICY = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1.model_copy(
    update={
        "required_included_counts": {
            "development": 1,
            "validation": 1,
            "test": 1,
        }
    }
)
PROVIDER = EvaluationProviderIdentity(
    model_provider="mock",
    model="mock-forecast-v1",
    search_provider="mock",
)


def _procedural_review_payload(
    release: EvaluationRelease,
    release_question,
    question: EvaluationQuestion,
    *,
    artifact_type: str,
) -> dict[str, object]:
    contract_payload = json.loads(question.resolution_contract)
    contract = canonical_json(contract_payload)
    contract_hash = sha256_text(contract)
    source_id = f"fixture:{artifact_type}:{question.id}"
    if artifact_type == "question_review":
        manifest: dict[str, object] = {
            "schema_version": 1,
            "manifest_type": "question_review_manifest",
            "evaluation_release_id": release.id,
            "evaluation_release_question_id": release_question.id,
            "evaluation_question_id": question.id,
            "normalized_question_hash": sha256_text(
                " ".join(question.question.split()).strip().casefold()
            ),
            "contract_hash": contract_hash,
            "question": question.question,
            "yes_condition": contract_payload["yes_condition"],
            "no_condition": contract_payload["no_condition"],
            "forecast_date": question.forecast_date.isoformat(),
            "resolution_date": question.resolution_date.isoformat(),
            "authoritative_resolver": contract_payload["authoritative_resolver"],
            "event_family_id": release_question.event_family_id,
            "leakage_group_id": release_question.leakage_group_id,
            "inclusion_status": "included",
            "declared_source_license_status": release_question.source_license_status,
            "declared_source_use_basis": release_question.source_use_basis,
            "declared_redistribution_allowed": release_question.redistribution_allowed,
            "sources": [
                {
                    "source_id": source_id,
                    "url": f"https://origin.example/{question.id}",
                    "title": "Synthetic test-only pre-outcome source",
                    "source_role": "pre_outcome_origin",
                    "source_available_at": "2018-12-01T00:00:00Z",
                    "temporal_basis": "snapshot_date",
                    "content_sha256": sha256_text(f"origin:{question.id}"),
                    "source_license_status": release_question.source_license_status,
                    "source_use_basis": release_question.source_use_basis,
                    "redistribution_allowed": release_question.redistribution_allowed,
                }
            ],
            "rubric_version": QUESTION_REVIEW_RUBRIC_VERSION,
            "rubric_hash": QUESTION_REVIEW_RUBRIC_HASH,
        }
        output: dict[str, object] = {
            "schema_version": 1,
            "output_type": "question_review_output",
            "decision": "accepted",
            "findings": [
                {
                    "code": code,
                    "status": "pass",
                    "conclusion": f"Synthetic fixture check for {code}.",
                    "source_ids": [source_id],
                }
                for code in QUESTION_REVIEW_RUBRIC.required_finding_codes
            ],
            "uncertainties": [],
            "conflicts": [],
        }
        role = "question_review"
        rubric_version = QUESTION_REVIEW_RUBRIC_VERSION
        rubric_hash = QUESTION_REVIEW_RUBRIC_HASH
    else:
        manifest = {
            "schema_version": 1,
            "manifest_type": "outcome_adjudication_manifest",
            "evaluation_release_id": release.id,
            "evaluation_release_question_id": release_question.id,
            "evaluation_question_id": question.id,
            "contract_hash": contract_hash,
            "yes_condition": contract_payload["yes_condition"],
            "no_condition": contract_payload["no_condition"],
            "resolution_date": question.resolution_date.isoformat(),
            "candidate_outcome": question.outcome,
            "outcome_known_at": release_question.outcome_known_at.isoformat(),
            "sources": [
                {
                    "source_id": source_id,
                    "url": question.resolution_source,
                    "title": "Synthetic test-only resolution source",
                    "source_role": "authoritative_resolution",
                    "source_available_at": release_question.outcome_known_at.isoformat(),
                    "temporal_basis": "publication_date",
                    "content_sha256": sha256_text(f"resolution:{question.id}"),
                }
            ],
            "rubric_version": OUTCOME_ADJUDICATION_RUBRIC_VERSION,
            "rubric_hash": OUTCOME_ADJUDICATION_RUBRIC_HASH,
        }
        output = {
            "schema_version": 1,
            "output_type": "outcome_adjudication_output",
            "decision": "confirmed",
            "adjudicated_outcome": question.outcome,
            "findings": [
                {
                    "code": code,
                    "status": "pass",
                    "conclusion": f"Synthetic fixture check for {code}.",
                    "source_ids": [source_id],
                }
                for code in OUTCOME_ADJUDICATION_RUBRIC.required_finding_codes
            ],
            "uncertainties": [],
            "conflicts": [],
        }
        role = "outcome_adjudication"
        rubric_version = OUTCOME_ADJUDICATION_RUBRIC_VERSION
        rubric_hash = OUTCOME_ADJUDICATION_RUBRIC_HASH
    if artifact_type == "question_review":
        normalized_manifest = QuestionReviewManifest.model_validate(manifest).model_dump(
            mode="json"
        )
        normalized_output = QuestionReviewOutput.model_validate(output).model_dump(
            mode="json"
        )
    else:
        normalized_manifest = OutcomeAdjudicationManifest.model_validate(
            manifest
        ).model_dump(mode="json")
        normalized_output = OutcomeAdjudicationOutput.model_validate(output).model_dump(
            mode="json"
        )
    return {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "policy_version": PROCEDURAL_AI_REVIEW_POLICY_VERSION,
        "rubric_version": rubric_version,
        "rubric_hash": rubric_hash,
        "run_identity": {
            "role": role,
            "model_provider": "fixture",
            "model_id": "synthetic-review-fixture",
            "model_version": "v1",
            "tool_name": "codex",
            "tool_version": "fixture-v1",
            "run_id": f"fixture:{artifact_type}:{question.id}",
        },
        "started_at": (NOW + timedelta(seconds=1)).isoformat(),
        "completed_at": (NOW + timedelta(seconds=2)).isoformat(),
        "input_manifest": manifest,
        "input_manifest_hash": sha256_text(canonical_json(normalized_manifest)),
        "output": output,
        "output_hash": sha256_text(canonical_json(normalized_output)),
    }


def _record_procedural_review_fixtures(
    session,
    release: EvaluationRelease,
    *,
    policy,
) -> None:
    questions = {
        row.id: row
        for row in session.scalars(
            select(EvaluationQuestion).where(
                EvaluationQuestion.id.in_(
                    [item.evaluation_question_id for item in release.questions]
                )
            )
        ).all()
    }
    for release_question in release.questions:
        if release_question.inclusion_status != "included":
            continue
        question = questions[release_question.evaluation_question_id]
        for artifact_type in ("question_review", "outcome_adjudication"):
            try:
                record_procedural_ai_review_artifact(
                    session,
                    release,
                    artifact_type=artifact_type,  # type: ignore[arg-type]
                    payload=_procedural_review_payload(
                        release,
                        release_question,
                        question,
                        artifact_type=artifact_type,
                    ),
                    now=NOW + timedelta(seconds=3),
                    policy=policy,
                )
            except EvaluationReleaseValidationError as exc:
                raise AssertionError(
                    "synthetic procedural-review fixture invalid: "
                    + ",".join(exc.reasons)
                ) from exc


def _evaluation_release(
    session,
    *,
    counts: tuple[int, int, int] = (1, 1, 1),
    version: str = "1",
) -> EvaluationRelease:
    datasets: dict[str, EvaluationDataset] = {}
    inputs: list[EvaluationReleaseQuestionInput] = []
    for split, count in zip(
        ("development", "validation", "test"), counts, strict=True
    ):
        dataset = EvaluationDataset(
            id=str(uuid.uuid4()),
            name=f"Generated historical {split} {version}",
            version=version,
            hash=sha256_text(f"dataset:{split}:{version}:{count}"),
            description="Generated test-only resolved questions.",
            provenance="Independently curated public authoritative records.",
            status="draft",
            created_at=NOW,
            frozen_at=NOW,
            question_count=count,
        )
        session.add(dataset)
        session.flush()
        datasets[split] = dataset
        for index in range(count):
            value = index + {"development": 100, "validation": 200, "test": 300}[split]
            question = EvaluationQuestion(
                id=str(uuid.uuid4()),
                dataset_id=dataset.id,
                question=f"Did official {split} indicator {value} exceed {value}?",
                resolution_contract=canonical_json(
                    {
                        "schema_version": 1,
                        "forecast_type": "binary",
                        "yes_condition": f"Official indicator {value} exceeded {value}.",
                        "no_condition": f"Official indicator {value} did not exceed {value}.",
                        "authoritative_resolver": f"Official {split} registry",
                        "fallback_resolver_identities": [],
                    }
                ),
                forecast_date=CUTOFF,
                resolution_date=datetime(2020, 6, 1, tzinfo=UTC),
                outcome=value % 2,
                resolution_source=f"https://resolution.example/{split}/{value}",
                domain="economics",
                category=f"{split}_indicator",
                question_hash=sha256_text(f"question:{split}:{version}:{value}"),
            )
            session.add(question)
            session.flush()
            inputs.append(
                EvaluationReleaseQuestionInput(
                    evaluation_question_id=question.id,
                    split=split,  # type: ignore[arg-type]
                    event_family_id=f"event:{split}:{index}",
                    leakage_group_id=f"leakage:{split}:{index}",
                    question_author_id=f"author:{split}:{index}",
                    question_reviewer_id=f"reviewer:{split}:{index}",
                    outcome_adjudicator_id=f"adjudicator:{split}:{index}",
                    review_completed_at=datetime(2020, 6, 2, tzinfo=UTC),
                    outcome_known_at=datetime(2020, 6, 2, tzinfo=UTC),
                    source_license_status="public_domain",
                    source_use_basis="Official public metadata.",
                    redistribution_allowed=False,
                    adjudication_record_hash=sha256_text(
                        f"adjudication:{split}:{index}"
                    ),
                )
            )
        dataset.status = "frozen"
        dataset.frozen_at = NOW
    policy = (
        PRIVATE_V1_REAL_EVALUATION_RELEASE_V1
        if counts == (60, 40, 100)
        else SMALL_RELEASE_POLICY
    )
    release = create_evaluation_release(
        session,
        name=f"Generated evaluation release {version}",
        version=version,
        development_dataset_id=datasets["development"].id,
        validation_dataset_id=datasets["validation"].id,
        test_dataset_id=datasets["test"].id,
        questions=inputs,
        provider_identity=PROVIDER,
        now=NOW,
        policy=policy,
    )
    _record_procedural_review_fixtures(session, release, policy=policy)
    review_time = NOW + timedelta(seconds=4)
    review_evaluation_release(session, release, now=review_time, policy=policy)
    freeze_evaluation_release(session, release, now=review_time, policy=policy)
    session.flush()
    return release


def _bundle_bytes(root: Path, *, content: bytes = b"frozen historical source") -> tuple[str, str]:
    text = content.decode("utf-8")
    content_digest = sha256_bytes(content)
    text_digest = sha256_bytes(text.encode("utf-8"))
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    (root / "text").mkdir(parents=True, exist_ok=True)
    (root / "blobs" / content_digest).write_bytes(content)
    (root / "text" / text_digest).write_text(text, encoding="utf-8")
    return content_digest, text_digest


def _document(
    root: Path,
    *,
    local_id: str = "doc-a",
    canonical_url: str = "https://records.example/indicator",
    source_kind: str = "wayback_final_capture",
    source_license_status: str = "public_domain",
    published_at: datetime | None = None,
    source_available_at: datetime = CAPTURE,
    **overrides,
) -> HistoricalEvidenceDocumentInput:
    content_digest, text_digest = _bundle_bytes(root)
    capture_url = (
        "https://web.archive.org/web/20181201000000id_/"
        + canonical_url
    )
    payload = {
        "local_id": local_id,
        "canonical_url": canonical_url,
        "source_url": capture_url if source_kind == "wayback_final_capture" else canonical_url,
        "title": "Official frozen indicator record",
        "publisher": "Official Registry",
        "source_class": "primary",
        "source_kind": source_kind,
        "temporal_basis": (
            "snapshot_date" if source_kind == "wayback_final_capture" else "immutable_version"
        ),
        "published_at": published_at,
        "source_available_at": source_available_at,
        "final_capture_url": capture_url if source_kind == "wayback_final_capture" else None,
        "final_capture_at": CAPTURE if source_kind == "wayback_final_capture" else None,
        "archived_original_url": canonical_url if source_kind == "wayback_final_capture" else None,
        "final_capture_verified": source_kind == "wayback_final_capture",
        "immutable_adapter_id": (
            "official_versioned_dataset_v1"
            if source_kind == "immutable_version"
            else None
        ),
        "immutable_version_id": "vintage-2018-12" if source_kind == "immutable_version" else None,
        "immutable_availability_verified": source_kind == "immutable_version",
        "mime_type": "text/plain",
        "content_sha256": content_digest,
        "extracted_text_sha256": text_digest,
        "byte_length": len(b"frozen historical source"),
        "text_length": len("frozen historical source"),
        "blob_locator": f"blobs/{content_digest}",
        "text_locator": f"text/{text_digest}",
        "source_license_status": source_license_status,
        "source_use_basis": "Official public record metadata and text.",
        "redistribution_allowed": False,
        "metadata": {"synthetic_test_only": True},
    }
    payload.update(overrides)
    return HistoricalEvidenceDocumentInput.model_validate(payload)


def _packets(
    evaluation_release: EvaluationRelease,
    *,
    ready_ids: set[str],
    documents_by_question: dict[str, list[str]],
) -> list[HistoricalEvidencePacketInput]:
    result: list[HistoricalEvidencePacketInput] = []
    for release_question in evaluation_release.questions:
        question_id = release_question.evaluation_question_id
        ready = question_id in ready_ids
        candidates = (
            [
                HistoricalEvidenceCandidateInput(
                    canonical_url="https://records.example/indicator",
                    source_url=(
                        "https://web.archive.org/web/20181201000000id_/"
                        "https://records.example/indicator"
                    ),
                    rank=1,
                    search_query="official historical indicator",
                    search_provider="offline_collector",
                    status="accepted",
                    archive_check_status="verified_final_capture",
                    document_local_id=documents_by_question[question_id][0],
                )
            ]
            if ready
            else [
                HistoricalEvidenceCandidateInput(
                    canonical_url=f"https://missing.example/{question_id}",
                    source_url=f"https://missing.example/{question_id}",
                    rank=1,
                    search_query="official historical indicator",
                    search_provider="offline_collector",
                    status="rejected",
                    rejection_reason="no_verified_pre_cutoff_capture",
                    archive_check_status="no_eligible_capture",
                )
            ]
        )
        result.append(
            HistoricalEvidencePacketInput(
                evaluation_question_id=question_id,
                split=release_question.split,  # type: ignore[arg-type]
                evidence_cutoff=CUTOFF,
                status="ready" if ready else "no_eligible_evidence",
                collector_id=f"collector:{question_id}",
                reviewer_id=f"reviewer:{question_id}",
                reviewed_at=NOW,
                searches=[{"query": "official historical indicator", "result_count": 1}],
                archive_checks=[{"status": "verified" if ready else "not_found"}],
                rejection_reasons=[] if ready else ["no_verified_pre_cutoff_capture"],
                candidates=candidates,
                document_local_ids=documents_by_question.get(question_id, []),
            )
        )
    return result


def _historical_release(
    session,
    root: Path,
    *,
    evaluation_release: EvaluationRelease | None = None,
    version: str = "1",
    documents: list[HistoricalEvidenceDocumentInput] | None = None,
    packet_mutator=None,
    correction_of_release_id: str | None = None,
) -> HistoricalEvidenceRelease:
    evaluation_release = evaluation_release or _evaluation_release(session)
    documents = documents or [_document(root)]
    first = evaluation_release.questions[0].evaluation_question_id
    third = evaluation_release.questions[-1].evaluation_question_id
    docs_by_question = {first: [documents[0].local_id], third: [documents[0].local_id]}
    packets = _packets(
        evaluation_release,
        ready_ids={first, third},
        documents_by_question=docs_by_question,
    )
    if packet_mutator:
        packet_mutator(packets)
    release = create_historical_evidence_release(
        session,
        evaluation_release_id=evaluation_release.id,
        name="Generated historical evidence release",
        version=version,
        documents=documents,
        packets=packets,
        correction_of_release_id=correction_of_release_id,
        correction_summary=(
            "Corrected frozen evidence metadata."
            if correction_of_release_id
            else None
        ),
        now=NOW,
    )
    return release


def test_valid_generated_60_40_100_release_reviews_and_freezes(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(
            session, counts=(60, 40, 100), version="full"
        )
        document = _document(tmp_path)
        first = evaluation_release.questions[0].evaluation_question_id
        second = evaluation_release.questions[1].evaluation_question_id
        packets = _packets(
            evaluation_release,
            ready_ids={first, second},
            documents_by_question={first: [document.local_id], second: [document.local_id]},
        )
        release = create_historical_evidence_release(
            session,
            evaluation_release_id=evaluation_release.id,
            name="Generated 60 40 100 evidence release",
            version="1",
            documents=[document],
            packets=packets,
            now=NOW,
        )
        review_historical_evidence_release(
            session, release, bundle_root=tmp_path, now=NOW
        )
        freeze_historical_evidence_release(
            session, release, bundle_root=tmp_path, now=NOW
        )
        assert release.status == "frozen"
        assert len(release.packets) == 200
        assert sum(item.status == "no_eligible_evidence" for item in release.packets) == 198
        assert verify_release_bundle(release, tmp_path).passed


def test_bundle_deduplicates_bytes_and_documents_can_link_multiple_packets(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        verification = verify_release_bundle(release, tmp_path)
        assert verification.passed
        assert verification.document_count == 1
        assert verification.unique_blob_count == 1
        assert sum(len(item.document_links) for item in release.packets) == 2


def test_ready_packet_supports_multiple_reviewed_documents_with_deduplicated_bytes(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        first_question_id = evaluation_release.questions[0].evaluation_question_id
        first = _document(tmp_path, local_id="doc-a")
        second = _document(
            tmp_path,
            local_id="doc-b",
            canonical_url="https://records.example/second-indicator",
        )
        packets = _packets(
            evaluation_release,
            ready_ids={first_question_id},
            documents_by_question={first_question_id: [first.local_id]},
        )
        ready = next(
            item for item in packets if item.evaluation_question_id == first_question_id
        )
        ready.document_local_ids.append(second.local_id)
        ready.candidates.append(
            HistoricalEvidenceCandidateInput(
                canonical_url=second.canonical_url,
                source_url=second.source_url,
                rank=2,
                search_query="official historical second indicator",
                search_provider="offline_collector",
                status="accepted",
                archive_check_status="verified_final_capture",
                document_local_id=second.local_id,
            )
        )
        release = create_historical_evidence_release(
            session,
            evaluation_release_id=evaluation_release.id,
            name="Generated multi-document evidence release",
            version="1",
            documents=[first, second],
            packets=packets,
            now=NOW,
        )
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        manifest = get_historical_evidence_execution_manifest(release)
        packet = next(
            item
            for item in manifest.packets
            if item.evaluation_question_id == first_question_id
        )
        verification = verify_release_bundle(release, tmp_path)
        assert len(packet.documents) == 2
        assert verification.document_count == 2
        assert verification.unique_blob_count == 1


def test_ready_packet_rejects_unreviewed_document_link(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        first_question_id = evaluation_release.questions[0].evaluation_question_id
        first = _document(tmp_path, local_id="doc-a")
        second = _document(
            tmp_path,
            local_id="doc-b",
            canonical_url="https://records.example/unreviewed",
        )
        packets = _packets(
            evaluation_release,
            ready_ids={first_question_id},
            documents_by_question={first_question_id: [first.local_id]},
        )
        ready = next(
            item for item in packets if item.evaluation_question_id == first_question_id
        )
        ready.document_local_ids.append(second.local_id)
        release = create_historical_evidence_release(
            session,
            evaluation_release_id=evaluation_release.id,
            name="Generated unreviewed-link evidence release",
            version="1",
            documents=[first, second],
            packets=packets,
            now=NOW,
        )
        reasons = validate_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert any(
            reason.startswith("packet_document_accepted_candidate_required:")
            for reason in reasons
        )


def test_missing_extra_and_excluded_packets_fail_closed(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        release = _historical_release(
            session,
            tmp_path,
            evaluation_release=evaluation_release,
            packet_mutator=lambda items: items.pop(),
        )
        reasons = validate_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert any(reason.startswith("historical_evidence_packet_count:") for reason in reasons)

        packets = _packets(
            evaluation_release,
            ready_ids=set(),
            documents_by_question={},
        )
        packets.append(
            packets[0].model_copy(
                update={"evaluation_question_id": "not-in-the-evaluation-release"}
            )
        )
        with pytest.raises(HistoricalEvidenceReleaseValidationError) as extra:
            create_historical_evidence_release(
                session,
                evaluation_release_id=evaluation_release.id,
                name="Generated extra-packet evidence release",
                version="1",
                documents=[],
                packets=packets,
                now=NOW,
            )
        assert any(
            reason.startswith("evaluation_release_question_not_found:")
            for reason in extra.value.reasons
        )


def test_packet_review_independence_and_missingness_audit_are_required(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        missing = next(
            item for item in release.packets if item.status == "no_eligible_evidence"
        )
        missing.reviewer_id = missing.collector_id
        missing.searches_json = "[]"
        reasons = validate_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert f"historical_evidence_reviewer_conflict:{missing.id}" in reasons
        assert f"no_evidence_packet_search_audit_required:{missing.id}" in reasons


def test_retrieval_date_cannot_be_a_historical_document_basis(tmp_path) -> None:
    document = _document(tmp_path)
    with pytest.raises(ValidationError):
        HistoricalEvidenceDocumentInput.model_validate(
            {
                **document.model_dump(mode="json"),
                "temporal_basis": "retrieval_date",
            }
        )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"source_available_at": datetime(2019, 2, 1, tzinfo=UTC)}, "historical_evidence_after_cutoff:"),
        ({"published_at": datetime(2019, 2, 1, tzinfo=UTC)}, "historical_evidence_publication_after_cutoff:"),
        ({"archived_original_url": "https://other.example/wrong"}, "wayback_archived_original_mismatch:"),
        ({"final_capture_url": "https://records.example/live"}, "wayback_final_capture_url_invalid:"),
        ({"source_url": "https://records.example/indicator"}, "wayback_source_not_final_capture:"),
        (
            {
                "final_capture_url": (
                    "https://web.archive.org/web/20181201000000id_/"
                    "https://other.example/wrong"
                )
            },
            "wayback_capture_original_mismatch:",
        ),
        (
            {
                "final_capture_url": (
                    "https://web.archive.org/web/20181101000000id_/"
                    "https://records.example/indicator"
                )
            },
            "wayback_capture_timestamp_mismatch:",
        ),
        ({"final_capture_verified": False}, "wayback_final_capture_unverified:"),
        ({"source_license_status": "unknown"}, "historical_evidence_license_unknown:"),
    ],
)
def test_document_temporal_archive_and_license_defects_are_rejected(
    client, tmp_path, overrides, expected
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(
            session, tmp_path, documents=[_document(tmp_path, **overrides)]
        )
        reasons = validate_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert any(reason.startswith(expected) for reason in reasons), reasons


def test_registered_immutable_version_passes_and_unregistered_fails(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        valid = _historical_release(
            session,
            tmp_path,
            documents=[_document(tmp_path, source_kind="immutable_version")],
        )
        assert validate_historical_evidence_release(
            session, valid, bundle_root=tmp_path
        ) == []
        session.rollback()
    with main_mod.SessionLocal() as session:
        invalid = _historical_release(
            session,
            tmp_path,
            documents=[
                _document(
                    tmp_path,
                    source_kind="immutable_version",
                    immutable_adapter_id="unregistered_adapter",
                )
            ],
        )
        reasons = validate_historical_evidence_release(
            session, invalid, bundle_root=tmp_path
        )
        assert any(reason.startswith("immutable_adapter_unregistered:") for reason in reasons)


def test_registered_immutable_version_enters_backtest_claims_without_date_fabrication(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(
            session,
            tmp_path,
            documents=[_document(tmp_path, source_kind="immutable_version")],
        )
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        ready = next(item for item in release.packets if item.status == "ready")
        search, store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=ready.split,
            evaluation_question_id=ready.evaluation_question_id,
            evidence_cutoff=ready.evidence_cutoff,
        )
        document = store.fetch(search.search("official indicator")[0].url)
        assert document.temporal_basis == "immutable_version"
        assert document.published_at is None
        assert document.source_available_at == CAPTURE
        assert document.snapshot_verification_status == (
            "immutable_historical_timestamp_verified"
        )
        claims = EvidenceExtractor(MockModelProvider()).extract(
            document,
            evidence_item_id="frozen-item",
            forecast_node_id="frozen-node",
            as_of=CUTOFF,
            mode="backtest",
            source_class="primary",
        )
        assert len(claims) == 1
        assert claims[0].publication_date is None
        assert claims[0].temporal_basis == "immutable_version"
        assert claims[0].forecasting_errors(mode="backtest", cutoff=CUTOFF) == []


def test_frozen_wayback_capture_enters_backtest_claims_with_verified_snapshot_basis(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        ready = next(item for item in release.packets if item.status == "ready")
        search, store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=ready.split,
            evaluation_question_id=ready.evaluation_question_id,
            evidence_cutoff=ready.evidence_cutoff,
        )
        document = store.fetch(search.search("official indicator")[0].url)
        assert document.temporal_basis == "snapshot_date"
        assert document.snapshot_verification_status == "verified_frozen_final_capture"
        claims = EvidenceExtractor(MockModelProvider()).extract(
            document,
            evidence_item_id="frozen-wayback-item",
            forecast_node_id="frozen-wayback-node",
            as_of=CUTOFF,
            mode="backtest",
            source_class="primary",
        )
        assert len(claims) == 1
        assert claims[0].publication_date is None
        assert claims[0].temporal_basis == "snapshot_date"
        assert claims[0].forecasting_errors(mode="backtest", cutoff=CUTOFF) == []


def test_bundle_verifier_rejects_missing_changed_extra_and_unsafe_content(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        manifest = HistoricalEvidenceBundleManifest.model_validate_json(
            release.bundle_manifest_json
        )
        assert verify_historical_evidence_bundle(
            manifest, tmp_path, expected_release_hash=release.release_hash
        ).passed
        blob = tmp_path / manifest.documents[0].blob_locator
        original = blob.read_bytes()
        blob.write_bytes(b"changed")
        assert any(
            "bundle_content_hash_mismatch" in reason
            for reason in verify_historical_evidence_bundle(
                manifest, tmp_path, expected_release_hash=release.release_hash
            ).reasons
        )
        blob.write_bytes(original)
        text_path = tmp_path / manifest.documents[0].text_locator
        original_text = text_path.read_bytes()
        text_path.write_bytes(b"changed text")
        assert any(
            "bundle_content_hash_mismatch" in reason
            for reason in verify_historical_evidence_bundle(
                manifest, tmp_path, expected_release_hash=release.release_hash
            ).reasons
        )
        text_path.write_bytes(original_text)
        (tmp_path / "blobs" / "extra").write_bytes(b"extra")
        assert any(
            reason.startswith("extra_executable_bundle_content:")
            for reason in verify_historical_evidence_bundle(
                manifest, tmp_path, expected_release_hash=release.release_hash
            ).reasons
        )
        (tmp_path / "blobs" / "extra").unlink()
        blob.unlink()
        assert any(
            "bundle_content_missing" in reason
            for reason in verify_historical_evidence_bundle(
                manifest, tmp_path, expected_release_hash=release.release_hash
            ).reasons
        )
        with pytest.raises(ValidationError):
            BlindedHistoricalEvidenceDocument.model_validate(
                {
                    **manifest.documents[0].model_dump(mode="json"),
                    "blob_locator": "../escape",
                }
            )


def test_execution_manifest_structurally_excludes_scoring_and_outcomes(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        manifest = get_historical_evidence_execution_manifest(
            release, require_frozen=False
        )
        serialized = canonical_json(manifest.model_dump(mode="json"))
        for forbidden in (
            '"outcome"',
            '"resolution_source"',
            '"adjudication"',
            '"outcome_known_at"',
            '"scoring_manifest"',
        ):
            assert forbidden not in serialized
        with pytest.raises(ValidationError):
            BlindedHistoricalEvidencePacket.model_validate(
                {
                    **manifest.packets[0].model_dump(mode="json"),
                    "outcome": 1,
                }
            )


def test_audit_metadata_with_scoring_or_outcome_fields_fails_freeze(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(
            session,
            tmp_path,
            documents=[
                _document(
                    tmp_path,
                    metadata={"outcome": 1, "synthetic_test_only": True},
                )
            ],
        )
        reasons = validate_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert any(
            reason.startswith(
                "historical_evidence_audit_manifest_forbidden_field:"
            )
            for reason in reasons
        )


def test_outcome_only_changes_do_not_change_historical_evidence_hash(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        before = _refresh_artifacts(release)
        evaluation_release = session.get(EvaluationRelease, release.evaluation_release_id)
        assert evaluation_release is not None
        old_scoring = evaluation_release.scoring_manifest_json
        evaluation_release.scoring_manifest_json = canonical_json(
            {"sealed_outcome_changed_for_test": True}
        )
        after = _refresh_artifacts(release)
        evaluation_release.scoring_manifest_json = old_scoring
        assert before["execution_manifest_hash"] == after["execution_manifest_hash"]
        assert before["release_hash"] == after["release_hash"]


def test_offline_provider_is_deterministic_question_scoped_and_zero_hit_for_missingness(
    client, tmp_path, monkeypatch
) -> None:
    import forecastlab.http_client as http_client
    import forecastlab.wayback as wayback
    from forecastlab_api import main as main_mod

    monkeypatch.setattr(
        http_client,
        "safe_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    monkeypatch.setattr(
        wayback,
        "discover_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        ready = next(item for item in release.packets if item.status == "ready")
        search, store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=ready.split,
            evaluation_question_id=ready.evaluation_question_id,
            evidence_cutoff=ready.evidence_cutoff,
        )
        first = search.search("official indicator", max_results=5)
        second = search.search("official indicator", max_results=5)
        assert [item.url for item in first] == [item.url for item in second]
        assert store.fetch(first[0].url).as_of_eligible
        with pytest.raises(FrozenEvidenceIntegrityError):
            store.fetch("https://cross-question.example/not-linked")
        with pytest.raises(
            FrozenEvidenceIntegrityError,
            match="frozen_evidence_packet_not_found",
        ):
            build_frozen_evidence_runtime(
                release,
                bundle_root=tmp_path,
                split="validation" if ready.split != "validation" else "test",
                evaluation_question_id=ready.evaluation_question_id,
                evidence_cutoff=ready.evidence_cutoff,
            )
        missing = next(
            item for item in release.packets if item.status == "no_eligible_evidence"
        )
        no_hits, _no_store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=missing.split,
            evaluation_question_id=missing.evaluation_question_id,
            evidence_cutoff=missing.evidence_cutoff,
        )
        assert no_hits.search("anything") == []


def test_single_and_legacy_paths_use_frozen_snapshot_discovery_without_network(
    client, tmp_path, monkeypatch
) -> None:
    from forecastlab import engine as engine_module
    from forecastlab import single_model as single_model_module
    from forecastlab.budget import Budget
    from forecastlab.profiles import load_profile
    from forecastlab.run_cache import RunCache
    from forecastlab.schemas import ForecastContract, ResolutionContract
    from forecastlab_api import main as main_mod

    monkeypatch.setattr(
        single_model_module,
        "discover_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-model network archive discovery")
        ),
    )
    monkeypatch.setattr(
        engine_module,
        "discover_snapshots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy network archive discovery")
        ),
    )
    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        ready = next(item for item in release.packets if item.status == "ready")
        search, store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=ready.split,
            evaluation_question_id=ready.evaluation_question_id,
            evidence_cutoff=ready.evidence_cutoff,
        )

        single_profile = load_profile("single_model_forecaster_v1")
        single_cache = RunCache.create(
            run_id="frozen-single",
            model_provider="mock",
            search_provider=search.name,
            mode="backtest",
            as_of=CUTOFF,
            configuration_hash="frozen-single",
            document_store=store,
        )
        contract = ForecastContract(
            id="frozen-contract",
            question_id="frozen-question",
            created_at=NOW,
            created_by="test",
            original_question="Did the official indicator exceed its threshold?",
            normalized_question="Did the official indicator exceed its threshold?",
            yes_condition="The official indicator exceeded its threshold.",
            no_condition="The official indicator did not exceed its threshold.",
            resolution_date=datetime(2020, 6, 1, tzinfo=UTC),
            authoritative_source="Official Registry",
            resolution_method="Use the frozen official record.",
            status="approved",
        )
        single_budget = Budget(
            single_profile,
            search_provider=search.name,
        )
        evidence, rejected = single_model_module._collect_evidence(
            contract=contract,
            run_id="frozen-single",
            profile=single_profile,
            search=search,
            budget=single_budget,
            cache=single_cache,
            mode="backtest",
            as_of=CUTOFF,
            allow_local_fixtures=False,
        )
        assert evidence
        assert rejected == []
        assert single_budget.state.search_calls == 1
        assert single_budget.state.search_cost_usd == 0.0
        assert single_budget.state.provider_request_count == 0

        legacy_profile = load_profile("three_track_ensemble")
        legacy_cache = RunCache.create(
            run_id="frozen-legacy",
            model_provider="mock",
            search_provider=search.name,
            mode="backtest",
            as_of=CUTOFF,
            configuration_hash="frozen-legacy",
            document_store=store,
        )
        track = engine_module._run_track(
            track="base_rate",
            contract=ResolutionContract(
                exact_yes="The official indicator exceeded its threshold.",
                exact_no="The official indicator did not exceed its threshold.",
                resolution_deadline=datetime(2020, 6, 1, tzinfo=UTC),
                authoritative_source="Official Registry",
            ),
            profile=legacy_profile,
            model=MockModelProvider(),
            search=search,
            budget=Budget(legacy_profile),
            as_of=CUTOFF,
            mode="backtest",
            allow_local_fixtures=False,
            prompt_versions=dict(legacy_profile.prompt_versions),
            cache=legacy_cache,
        )
        assert track.error is None
        assert track.evidence


def test_frozen_provider_identity_overrides_live_search_without_network_cost(
    client, tmp_path
) -> None:
    from forecastlab.budget import Budget
    from forecastlab.profiles import load_profile
    from forecastlab.providers.base import effective_search_provider_identity
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        ready = next(item for item in release.packets if item.status == "ready")
        search, _store = build_frozen_evidence_runtime(
            release,
            bundle_root=tmp_path,
            split=ready.split,
            evaluation_question_id=ready.evaluation_question_id,
            evidence_cutoff=ready.evidence_cutoff,
        )

        identity = effective_search_provider_identity(
            search,
            configured_provider="tavily",
        )
        assert identity == "frozen_evidence"
        budget = Budget(
            load_profile("graph_forecaster_v1"),
            search_provider=identity,
        )
        reservation = budget.add_search("offline_frozen_search")
        assert reservation.estimated_cost_usd == 0.0
        assert reservation.cost_source == "provider_reported"
        assert budget.state.search_calls == 1
        assert budget.state.search_cost_usd == 0.0
        assert budget.state.provider_request_count == 0


def test_offline_store_rejects_cross_release_manifest_identity(client, tmp_path) -> None:
    from forecastlab.frozen_evidence import FrozenEvidenceDocumentStore
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        execution = get_historical_evidence_execution_manifest(release)
        bundle = HistoricalEvidenceBundleManifest.model_validate_json(
            release.bundle_manifest_json
        )
        ready = next(item for item in execution.packets if item.status == "ready")
        mismatched = execution.model_copy(
            update={"evaluation_release_id": "different-release"}
        )
        with pytest.raises(
            FrozenEvidenceIntegrityError,
            match="frozen_evidence_execution_manifest_identity_mismatch",
        ):
            FrozenEvidenceDocumentStore(
                manifest=bundle,
                execution_manifest=mismatched,
                bundle_root=tmp_path,
                expected_release_hash=release.release_hash,
                split=ready.split,
                evaluation_question_id=ready.evaluation_question_id,
                evidence_cutoff=ready.evidence_cutoff,
            )


def test_historical_evidence_release_api_exposes_blinded_manifest_only(
    client, tmp_path
) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        release = _historical_release(session, tmp_path)
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        release_id = release.id
        session.commit()
    listed = client.get("/api/evaluation/evidence-releases")
    assert listed.status_code == 200
    assert any(item["id"] == release_id for item in listed.json()["releases"])
    response = client.get(
        f"/api/evaluation/evidence-releases/{release_id}/execution-manifest"
    )
    assert response.status_code == 200
    serialized = canonical_json(response.json())
    assert '"outcome"' not in serialized
    assert '"scoring_manifest"' not in serialized


def test_idempotency_conflict_immutability_and_correction_lineage(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod
    from forecastlab_api.models import FrozenHistoricalEvidenceReleaseError

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        release = _historical_release(
            session, tmp_path, evaluation_release=evaluation_release
        )
        same = _historical_release(
            session, tmp_path, evaluation_release=evaluation_release
        )
        assert same.id == release.id
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        same_frozen = freeze_historical_evidence_release(
            session, release, bundle_root=tmp_path
        )
        assert same_frozen.id == release.id
        session.commit()
        release.name = "mutated"
        with pytest.raises(FrozenHistoricalEvidenceReleaseError):
            session.flush()
        session.rollback()
        release = session.get(HistoricalEvidenceRelease, release.id)
        assert release is not None
        with pytest.raises(HistoricalEvidenceReleaseValidationError) as conflict:
            _historical_release(
                session,
                tmp_path,
                evaluation_release=evaluation_release,
                documents=[
                    _document(
                        tmp_path,
                        canonical_url="https://records.example/changed",
                    )
                ],
            )
        assert "historical_evidence_release_version_conflict" in conflict.value.reasons
        correction = _historical_release(
            session,
            tmp_path,
            evaluation_release=evaluation_release,
            version="2",
            correction_of_release_id=release.id,
        )
        assert correction.correction_of_release_id == release.id
        assert correction.release_hash != release.release_hash


def test_prequeue_requirement_and_hash_failure_create_zero_tasks(
    client, tmp_path, monkeypatch
) -> None:
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings
    from forecastlab_api.forecast_experiments import create_forecast_experiment

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        with pytest.raises(ValueError, match="historical_evidence_release_required"):
            create_forecast_experiment(
                session,
                dataset_id=None,
                profile_ids=[
                    "single_model_forecaster_v1",
                    "three_track_forecaster",
                    "graph_forecaster_v1",
                ],
                evaluation_release_id=evaluation_release.id,
                evaluation_split="development",
            )
        assert session.scalar(select(func.count(Job.id))) == 0
        release = _historical_release(
            session, tmp_path, evaluation_release=evaluation_release
        )
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        monkeypatch.setattr(settings, "historical_evidence_bundle_root", tmp_path)
        other_evaluation_release = _evaluation_release(session, version="other")
        with pytest.raises(
            ValueError,
            match="historical_evidence_evaluation_release_mismatch",
        ):
            create_forecast_experiment(
                session,
                dataset_id=None,
                profile_ids=[
                    "single_model_forecaster_v1",
                    "three_track_forecaster",
                    "graph_forecaster_v1",
                ],
                evaluation_release_id=other_evaluation_release.id,
                evaluation_split="development",
                historical_evidence_release_id=release.id,
            )
        assert session.scalar(select(func.count(Job.id))) == 0
        manifest = HistoricalEvidenceBundleManifest.model_validate_json(
            release.bundle_manifest_json
        )
        (tmp_path / manifest.documents[0].blob_locator).write_bytes(b"tampered")
        with pytest.raises(ValueError, match="historical_evidence_bundle_invalid"):
            create_forecast_experiment(
                session,
                dataset_id=None,
                profile_ids=[
                    "single_model_forecaster_v1",
                    "three_track_forecaster",
                    "graph_forecaster_v1",
                ],
                evaluation_release_id=evaluation_release.id,
                evaluation_split="development",
                historical_evidence_release_id=release.id,
            )
        assert session.scalar(select(func.count(Job.id))) == 0


def test_matching_frozen_release_assigns_blinded_questions_and_legacy_synthetic_survives(
    client, tmp_path, monkeypatch
) -> None:
    from forecastlab_api import forecast_experiments as module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "historical_evidence_bundle_root", tmp_path)
    monkeypatch.setattr(module, "working_tree_dirty", lambda: False)
    monkeypatch.setattr(
        module,
        "_freeze_configuration",
        lambda **kwargs: {
            "schema_version": 1,
            "dataset": {"hash": kwargs["dataset"].hash},
            "questions": [item.model_dump(mode="json") for item in kwargs["blinded_questions"] or []],
            "profiles": {},
            "provider": {
                "model_provider": PROVIDER.model_provider,
                "model": PROVIDER.model,
                "search_provider": PROVIDER.search_provider,
            },
            "code": {},
            "synthetic_test": kwargs["synthetic_test"],
            "evaluation_release": kwargs["evaluation_release"],
            "historical_evidence_release": kwargs["historical_evidence_release"],
        },
    )
    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        release = _historical_release(
            session, tmp_path, evaluation_release=evaluation_release
        )
        review_historical_evidence_release(session, release, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, release, bundle_root=tmp_path)
        experiment = module.create_forecast_experiment(
            session,
            dataset_id=None,
            profile_ids=[
                "single_model_forecaster_v1",
                "three_track_forecaster",
                "graph_forecaster_v1",
            ],
            evaluation_release_id=evaluation_release.id,
            evaluation_split="development",
            historical_evidence_release_id=release.id,
        )
        session.flush()
        assert experiment.historical_evidence_release_id == release.id
        assert experiment.historical_evidence_release_hash == release.release_hash
        assert len(experiment.runs) == 3
        assert session.scalar(select(func.count(Job.id))) == 3
        synthetic = module.create_forecast_experiment(
            session,
            dataset_id=evaluation_release.development_dataset_id,
            profile_ids=[
                "single_model_forecaster_v1",
                "three_track_forecaster",
                "graph_forecaster_v1",
            ],
            synthetic_test=True,
        )
        session.flush()
        assert synthetic.evaluation_release_id is None
        assert synthetic.historical_evidence_release_id is None
        assert len(synthetic.runs) == 3


def test_evidence_content_change_changes_release_hash(client, tmp_path) -> None:
    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        evaluation_release = _evaluation_release(session)
        first = _historical_release(
            session, tmp_path, evaluation_release=evaluation_release
        )
        review_historical_evidence_release(session, first, bundle_root=tmp_path)
        freeze_historical_evidence_release(session, first, bundle_root=tmp_path)
        second_root = tmp_path / "second"
        changed = _document(
            second_root,
            canonical_url="https://records.example/changed-evidence",
        )
        second = _historical_release(
            session,
            second_root,
            evaluation_release=evaluation_release,
            version="2",
            documents=[changed],
            correction_of_release_id=first.id,
        )
        assert first.release_hash != second.release_hash


def test_no_real_provider_or_experiment_result_is_created(client) -> None:
    from forecastlab_api import main as main_mod
    from forecastlab_api.models import ForecastExperimentResult, ProviderCallLedger

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count(ProviderCallLedger.id))) == 0
        assert session.scalar(select(func.count(ForecastExperimentResult.id))) == 0
        assert session.scalar(select(func.count(ForecastExperiment.id))) == 0

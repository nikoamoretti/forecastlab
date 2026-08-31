from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.environment import build_environment_identity
from forecastlab.evaluation_releases import (
    PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
    BlindedEvaluationQuestion,
    BlindedExecutionManifest,
    BlindedResolutionContract,
    EvaluationPreregistration,
    EvaluationProviderIdentity,
    EvaluationReleaseIdentity,
    EvaluationReleasePolicy,
    EvaluationReleaseQuestionInput,
    EvaluationSplit,
    PreregisteredProfile,
    SealedScoringManifest,
    SealedScoringQuestion,
    manifest_hash,
)
from forecastlab.gitinfo import current_git_commit
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC_HASH,
    OUTCOME_ADJUDICATION_RUBRIC_VERSION,
    PROCEDURAL_AI_RELEASE_LABEL,
    PROCEDURAL_AI_REVIEW_POLICY_VERSION,
    QUESTION_REVIEW_RUBRIC_HASH,
    QUESTION_REVIEW_RUBRIC_VERSION,
    RESERVE_ORDER_POLICY_VERSION,
    RESERVE_ORDER_SEED,
    OutcomeAdjudicationArtifact,
    ProceduralAIArtifactIdentity,
    ProceduralAIReviewArtifact,
    ProceduralAIReviewGateManifest,
    QuestionReviewArtifact,
    artifact_integrity_reasons,
    parse_review_artifact,
)
from forecastlab.profiles import load_profile, profile_hash
from forecastlab.prompts import load_prompt_bundle
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.models import (
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationRelease,
    EvaluationReleaseQuestion,
    ProceduralAIReviewArtifactRecord,
)

REAL_EVALUATION_CONTROLLED_PROFILES = (
    "single_model_forecaster_v1",
    "three_track_forecaster",
    "graph_forecaster_v1",
)
RELEASE_STATUSES = ("draft", "reviewed", "frozen")
SPLITS: tuple[EvaluationSplit, ...] = ("development", "validation", "test")
BUDGET_FIELDS = (
    "max_model_calls",
    "max_search_calls",
    "max_fetched_documents",
    "max_tokens",
    "max_estimated_cost_usd",
    "max_wall_clock_seconds",
)
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NON_REAL_MARKERS = ("synthetic", "fixture", "demo", "mock-provider", "mock provider")
_EXECUTION_FORBIDDEN_KEYS = {
    "outcome",
    "resolution_source",
    "post_resolution_source",
    "adjudication_notes",
    "adjudication_record_hash",
    "outcome_known_at",
    "brier_score",
    "log_loss",
    "score",
}


class EvaluationReleaseValidationError(ValueError):
    """A release transition or immutable identity check failed closed."""

    def __init__(self, reasons: list[str], message: str = "Evaluation release is invalid") -> None:
        self.reasons = list(dict.fromkeys(reasons))
        super().__init__(message)


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def normalized_question_hash(question: str) -> str:
    return sha256_text(_normalized_text(question).casefold())


def resolution_contract_hash(question: EvaluationQuestion) -> str:
    try:
        payload = json.loads(question.resolution_contract)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EvaluationReleaseValidationError(["resolution_contract_invalid"]) from exc
    if not isinstance(payload, dict):
        raise EvaluationReleaseValidationError(["resolution_contract_invalid"])
    return sha256_text(canonical_json(payload))


def _dataset_map(release: EvaluationRelease) -> dict[EvaluationSplit, str]:
    return {
        "development": release.development_dataset_id,
        "validation": release.validation_dataset_id,
        "test": release.test_dataset_id,
    }


def _release_identity(release: EvaluationRelease) -> EvaluationReleaseIdentity:
    return EvaluationReleaseIdentity(
        id=release.id,
        name=release.name,
        version=release.version,
        policy_version=release.policy_version,
    )


def _blind_contract(question: EvaluationQuestion) -> BlindedResolutionContract:
    try:
        payload = json.loads(question.resolution_contract)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EvaluationReleaseValidationError(["resolution_contract_invalid"]) from exc
    if not isinstance(payload, dict):
        raise EvaluationReleaseValidationError(["resolution_contract_invalid"])
    resolver = _normalized_text(str(payload.get("authoritative_resolver") or ""))
    if not resolver:
        raise EvaluationReleaseValidationError(["authoritative_resolver_required"])
    return BlindedResolutionContract(
        schema_version=int(payload.get("schema_version") or 1),
        forecast_type="binary",
        yes_condition=_normalized_text(str(payload.get("yes_condition") or "")),
        no_condition=_normalized_text(str(payload.get("no_condition") or "")),
        resolution_date=as_utc(question.resolution_date),
        authoritative_resolver=resolver,
        fallback_resolver_identities=list(payload.get("fallback_resolver_identities") or []),
        ambiguity_notes=_normalized_text(str(payload.get("ambiguity_notes") or "")),
        cancellation_conditions=_normalized_text(
            str(payload.get("cancellation_conditions") or "")
        ),
        resolver_risk_notes=_normalized_text(
            str(payload.get("resolver_risk_notes") or "")
        ),
    )


def _question_by_id(
    session: Session,
    release: EvaluationRelease,
) -> dict[str, EvaluationQuestion]:
    ids = [item.evaluation_question_id for item in release.questions]
    rows = session.scalars(
        select(EvaluationQuestion).where(EvaluationQuestion.id.in_(ids or [""]))
    ).all()
    return {item.id: item for item in rows}


def _dataset_rows(
    session: Session,
    release: EvaluationRelease,
) -> dict[EvaluationSplit, EvaluationDataset]:
    ids = _dataset_map(release)
    rows = {
        item.id: item
        for item in session.scalars(
            select(EvaluationDataset).where(
                EvaluationDataset.id.in_(list(ids.values()))
            )
        ).all()
    }
    return {
        split: rows[dataset_id]
        for split, dataset_id in ids.items()
        if dataset_id in rows
    }


def _release_question_metadata(item: EvaluationReleaseQuestion) -> dict[str, Any]:
    return {
        "evaluation_question_id": item.evaluation_question_id,
        "split": item.split,
        "event_family_id": item.event_family_id,
        "leakage_group_id": item.leakage_group_id,
        "inclusion_status": item.inclusion_status,
        "exclusion_reason": item.exclusion_reason,
        "question_author_id": item.question_author_id,
        "question_reviewer_id": item.question_reviewer_id,
        "outcome_adjudicator_id": item.outcome_adjudicator_id,
        "review_completed_at": (
            as_utc(item.review_completed_at).isoformat()
            if item.review_completed_at
            else None
        ),
        "outcome_known_at": (
            as_utc(item.outcome_known_at).isoformat() if item.outcome_known_at else None
        ),
        "source_license_status": item.source_license_status,
        "source_use_basis": item.source_use_basis,
        "redistribution_allowed": item.redistribution_allowed,
        "adjudication_notes": item.adjudication_notes,
        "adjudication_record_hash": item.adjudication_record_hash,
    }


def _question_input_payload(item: EvaluationReleaseQuestionInput) -> dict[str, Any]:
    return {
        **item.model_dump(mode="python"),
        "event_family_id": _normalized_text(item.event_family_id),
        "leakage_group_id": _normalized_text(item.leakage_group_id),
        "exclusion_reason": _normalized_text(item.exclusion_reason) or None,
        "question_author_id": _normalized_text(item.question_author_id) or None,
        "question_reviewer_id": _normalized_text(item.question_reviewer_id) or None,
        "outcome_adjudicator_id": _normalized_text(item.outcome_adjudicator_id) or None,
        "source_use_basis": _normalized_text(item.source_use_basis) or None,
        "adjudication_notes": _normalized_text(item.adjudication_notes) or None,
        "adjudication_record_hash": (
            _normalized_text(item.adjudication_record_hash).lower() or None
        ),
    }


def _question_input_metadata(item: EvaluationReleaseQuestionInput) -> dict[str, Any]:
    payload = _question_input_payload(item)
    for field in ("review_completed_at", "outcome_known_at"):
        value = payload.get(field)
        payload[field] = as_utc(value).isoformat() if value is not None else None
    return payload


def _reserve_order_payload(
    session: Session,
    release: EvaluationRelease,
) -> dict[str, Any]:
    """Derive reserve order from non-outcome metadata only."""

    questions = _question_by_id(session, release)
    entries: list[dict[str, Any]] = []
    for item in release.questions:
        if item.inclusion_status != "excluded":
            continue
        question = questions.get(item.evaluation_question_id)
        if question is None:
            raise EvaluationReleaseValidationError(
                [f"evaluation_question_not_found:{item.evaluation_question_id}"]
            )
        question_digest = normalized_question_hash(question.question)
        contract_digest = resolution_contract_hash(question)
        sort_key = sha256_text(
            canonical_json(
                {
                    "seed": RESERVE_ORDER_SEED,
                    "evaluation_question_id": question.id,
                    "normalized_question_hash": question_digest,
                    "contract_hash": contract_digest,
                    "event_family_id": item.event_family_id,
                    "leakage_group_id": item.leakage_group_id,
                    "split": item.split,
                }
            )
        )
        entries.append(
            {
                "evaluation_question_id": question.id,
                "split": item.split,
                "normalized_question_hash": question_digest,
                "contract_hash": contract_digest,
                "sort_key_hash": sort_key,
            }
        )
    entries.sort(key=lambda value: (value["sort_key_hash"], value["evaluation_question_id"]))
    for rank, entry in enumerate(entries, start=1):
        entry["reserve_rank"] = rank
    return {
        "schema_version": 1,
        "policy_version": RESERVE_ORDER_POLICY_VERSION,
        "seed": RESERVE_ORDER_SEED,
        "outcome_fields_used": False,
        "entries": entries,
    }


def _artifact_identity(
    row: ProceduralAIReviewArtifactRecord,
) -> ProceduralAIArtifactIdentity:
    return ProceduralAIArtifactIdentity(
        artifact_id=row.id,
        evaluation_release_question_id=row.evaluation_release_question_id,
        evaluation_question_id=row.evaluation_question_id,
        artifact_type=cast(Any, row.artifact_type),
        run_id=row.run_id,
        rubric_version=row.rubric_version,
        rubric_hash=row.rubric_hash,
        input_manifest_hash=row.input_manifest_hash,
        output_hash=row.output_hash,
        gate_status=cast(Any, row.gate_status),
        completed_at=as_utc(row.completed_at),
    )


def _build_review_gate_manifest(
    release: EvaluationRelease,
) -> ProceduralAIReviewGateManifest:
    if not release.reserve_order_hash:
        raise EvaluationReleaseValidationError(["procedural_review_reserve_order_missing"])
    artifacts = sorted(
        [_artifact_identity(item) for item in release.procedural_review_artifacts],
        key=lambda item: (
            item.evaluation_release_question_id,
            item.artifact_type,
            item.artifact_id,
        ),
    )
    return ProceduralAIReviewGateManifest(
        reserve_order_hash=release.reserve_order_hash,
        artifacts=artifacts,
    )


def _artifact_rows_by_release_question(
    release: EvaluationRelease,
) -> dict[str, dict[str, ProceduralAIReviewArtifactRecord]]:
    result: dict[str, dict[str, ProceduralAIReviewArtifactRecord]] = defaultdict(dict)
    for row in release.procedural_review_artifacts:
        result[row.evaluation_release_question_id][row.artifact_type] = row
    return result


def _artifact_payload_from_row(
    row: ProceduralAIReviewArtifactRecord,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": row.artifact_type,
        "policy_version": row.policy_version,
        "rubric_version": row.rubric_version,
        "rubric_hash": row.rubric_hash,
        "run_identity": {
            "role": row.role,
            "model_provider": row.model_provider,
            "model_id": row.model_id,
            "model_version": row.model_version,
            "tool_name": row.tool_name,
            "tool_version": row.tool_version,
            "run_id": row.run_id,
        },
        "started_at": as_utc(row.started_at).isoformat(),
        "completed_at": as_utc(row.completed_at).isoformat(),
        "input_manifest": json.loads(row.input_manifest_json),
        "input_manifest_hash": row.input_manifest_hash,
        "output": json.loads(row.output_json),
        "output_hash": row.output_hash,
    }


def build_evaluation_preregistration(
    *,
    release_policy: EvaluationReleasePolicy,
    datasets: dict[EvaluationSplit, EvaluationDataset],
    provider_identity: EvaluationProviderIdentity,
    reserve_order_hash: str,
    registered_at: datetime,
) -> EvaluationPreregistration:
    """Snapshot profiles, prompts, source, and locks without loading credentials."""

    profiles = [load_profile(profile_id) for profile_id in REAL_EVALUATION_CONTROLLED_PROFILES]
    prompt_bundle = load_prompt_bundle()
    profile_hashes = {item.id: profile_hash(item) for item in profiles}
    prompt_hashes = prompt_bundle.hashes()
    prompt_bundle_hash = sha256_text(canonical_json(prompt_hashes))
    identity = build_environment_identity(
        prompt_bundle_hash=prompt_bundle_hash,
        profile_hashes=profile_hashes,
    )
    required_identity = {
        "git_commit": identity.get("git_commit"),
        "tracked_source_hash": identity.get("tracked_source_hash"),
        "pyproject_hash": identity.get("pyproject_hash"),
        "dependency_hash": identity.get("dependency_hash"),
        "package_lock_hash": identity.get("package_lock_hash"),
    }
    missing = sorted(key for key, value in required_identity.items() if not value)
    if missing:
        raise EvaluationReleaseValidationError(
            [f"preregistration_identity_missing:{key}" for key in missing]
        )
    return EvaluationPreregistration(
        release_policy_version=release_policy.version,
        registered_at=as_utc(registered_at),
        dataset_ids={split: datasets[split].id for split in SPLITS},
        dataset_hashes={split: datasets[split].hash for split in SPLITS},
        split_sizes=dict(release_policy.required_included_counts),
        profiles=[
            PreregisteredProfile(
                profile_id=item.id,
                version=item.version,
                profile_hash=profile_hashes[item.id],
                source_profile=item.model_dump(mode="json"),
                budget_ceiling={field: getattr(item, field) for field in BUDGET_FIELDS},
            )
            for item in profiles
        ],
        prompt_versions=prompt_bundle.versions(),
        prompt_hashes=prompt_hashes,
        source_code_sha=str(required_identity["git_commit"]),
        tracked_source_hash=str(required_identity["tracked_source_hash"]),
        pyproject_hash=str(required_identity["pyproject_hash"]),
        dependency_lock_hash=str(required_identity["dependency_hash"]),
        package_lock_hash=str(required_identity["package_lock_hash"]),
        provider_identity=provider_identity,
        reserve_order_hash=reserve_order_hash,
    )


def preregistration_environment_reasons(
    preregistration: EvaluationPreregistration,
) -> list[str]:
    """Compare current tracked identities to the immutable preregistration."""

    reasons: list[str] = []
    current_profiles = [
        load_profile(profile.profile_id) for profile in preregistration.profiles
    ]
    current_profile_ids = [item.id for item in current_profiles]
    expected_profile_ids = [item.profile_id for item in preregistration.profiles]
    if current_profile_ids != expected_profile_ids:
        reasons.append("preregistered_profile_set_mismatch")
    for expected, current in zip(preregistration.profiles, current_profiles, strict=False):
        if current.version != expected.version:
            reasons.append(f"profile_version_mismatch:{expected.profile_id}")
        if profile_hash(current) != expected.profile_hash:
            reasons.append(f"profile_hash_mismatch:{expected.profile_id}")
        if canonical_json(current.model_dump(mode="json")) != canonical_json(
            expected.source_profile
        ):
            reasons.append(f"profile_source_mismatch:{expected.profile_id}")
    bundle = load_prompt_bundle()
    if bundle.versions() != preregistration.prompt_versions:
        reasons.append("prompt_versions_mismatch")
    if bundle.hashes() != preregistration.prompt_hashes:
        reasons.append("prompt_hashes_mismatch")
    current_hashes = {item.id: profile_hash(item) for item in current_profiles}
    identity = build_environment_identity(
        prompt_bundle_hash=sha256_text(canonical_json(bundle.hashes())),
        profile_hashes=current_hashes,
    )
    comparisons = {
        "source_code_sha": (current_git_commit(), preregistration.source_code_sha),
        "tracked_source_hash": (
            identity.get("tracked_source_hash"),
            preregistration.tracked_source_hash,
        ),
        "pyproject_hash": (identity.get("pyproject_hash"), preregistration.pyproject_hash),
        "dependency_lock_hash": (
            identity.get("dependency_hash"),
            preregistration.dependency_lock_hash,
        ),
        "package_lock_hash": (
            identity.get("package_lock_hash"),
            preregistration.package_lock_hash,
        ),
    }
    for field, (current, frozen) in comparisons.items():
        if current != frozen:
            reasons.append(f"preregistration_{field}_mismatch")
    return reasons


def _build_manifests(
    *,
    session: Session,
    release: EvaluationRelease,
    preregistration: EvaluationPreregistration,
) -> tuple[BlindedExecutionManifest, SealedScoringManifest]:
    datasets = _dataset_rows(session, release)
    questions = _question_by_id(session, release)
    included = sorted(
        [item for item in release.questions if item.inclusion_status == "included"],
        key=lambda item: (SPLITS.index(item.split), item.evaluation_question_id),
    )
    prereg_hash = manifest_hash(preregistration)
    blinded: list[BlindedEvaluationQuestion] = []
    scoring: list[SealedScoringQuestion] = []
    artifacts_by_question = _artifact_rows_by_release_question(release)
    for item in included:
        question = questions.get(item.evaluation_question_id)
        if question is None:
            raise EvaluationReleaseValidationError(
                [f"evaluation_question_not_found:{item.evaluation_question_id}"]
            )
        contract = _blind_contract(question)
        contract_hash = resolution_contract_hash(question)
        blinded.append(
            BlindedEvaluationQuestion(
                evaluation_question_id=question.id,
                split=item.split,  # type: ignore[arg-type]
                question=question.question,
                normalized_question_hash=normalized_question_hash(question.question),
                contract_hash=contract_hash,
                resolution_contract=contract,
                forecast_date=as_utc(question.forecast_date),
                resolution_date=as_utc(question.resolution_date),
                domain=question.domain,
                category=question.category,
                authoritative_resolver=contract.authoritative_resolver,
                evidence_cutoff=as_utc(question.forecast_date),
                preregistration_hash=prereg_hash,
            )
        )
        outcome_row = artifacts_by_question.get(item.id, {}).get(
            "outcome_adjudication"
        )
        if outcome_row is None or outcome_row.gate_status != "passed":
            continue
        try:
            outcome_artifact = parse_review_artifact(
                _artifact_payload_from_row(outcome_row),
                artifact_type="outcome_adjudication",
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise EvaluationReleaseValidationError(
                [f"outcome_adjudication_artifact_invalid:{question.id}"]
            ) from exc
        if not isinstance(outcome_artifact, OutcomeAdjudicationArtifact):
            raise EvaluationReleaseValidationError(
                [f"outcome_adjudication_artifact_role_mismatch:{question.id}"]
            )
        if outcome_artifact.output.adjudicated_outcome is None:
            continue
        scoring.append(
            SealedScoringQuestion(
                evaluation_question_id=question.id,
                split=item.split,  # type: ignore[arg-type]
                outcome=outcome_artifact.output.adjudicated_outcome,
                post_resolution_source=question.resolution_source,
                outcome_known_at=as_utc(
                    outcome_artifact.input_manifest.outcome_known_at
                ),
                adjudication_record_hash=outcome_row.output_hash,
                scoring_contract_hash=sha256_text(
                    canonical_json(
                        {
                            "evaluation_question_id": question.id,
                            "normalized_question_hash": normalized_question_hash(
                                question.question
                            ),
                            "contract_hash": contract_hash,
                            "resolution_date": as_utc(question.resolution_date).isoformat(),
                        }
                    )
                ),
            )
        )
    identity = _release_identity(release)
    return (
        BlindedExecutionManifest(
            release=identity,
            dataset_hashes={split: datasets[split].hash for split in SPLITS},
            preregistration_hash=prereg_hash,
            questions=blinded,
        ),
        SealedScoringManifest(release=identity, questions=scoring),
    )


def _release_hash_payload(
    *,
    release: EvaluationRelease,
    datasets: dict[EvaluationSplit, EvaluationDataset],
    execution_manifest_hash: str,
    scoring_manifest_hash: str,
    preregistration_hash: str,
    review_manifest_hash: str,
    reserve_order_hash: str,
    policy: EvaluationReleasePolicy,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release": {
            "id": release.id,
            "name": release.name,
            "version": release.version,
            "policy_version": release.policy_version,
            "correction_of_release_id": release.correction_of_release_id,
            "correction_summary": release.correction_summary,
        },
        "policy_snapshot": policy.model_dump(mode="json"),
        "datasets": {
            split: {
                "id": datasets[split].id,
                "name": datasets[split].name,
                "version": datasets[split].version,
                "hash": datasets[split].hash,
            }
            for split in SPLITS
        },
        "release_questions": sorted(
            [_release_question_metadata(item) for item in release.questions],
            key=lambda item: (
                SPLITS.index(item["split"]),
                item["evaluation_question_id"],
            ),
        ),
        "execution_manifest_hash": execution_manifest_hash,
        "scoring_manifest_hash": scoring_manifest_hash,
        "preregistration_hash": preregistration_hash,
        "procedural_review": {
            "label": PROCEDURAL_AI_RELEASE_LABEL,
            "policy_version": release.procedural_review_policy_version,
            "review_manifest_hash": review_manifest_hash,
            "reserve_order_hash": reserve_order_hash,
        },
    }


def _refresh_artifacts(
    session: Session,
    release: EvaluationRelease,
    *,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> dict[str, str]:
    try:
        preregistration = EvaluationPreregistration.model_validate_json(
            release.preregistration_json
        )
    except ValidationError as exc:
        raise EvaluationReleaseValidationError(["preregistration_invalid"]) from exc
    execution, scoring = _build_manifests(
        session=session,
        release=release,
        preregistration=preregistration,
    )
    execution_hash = manifest_hash(execution)
    scoring_hash = manifest_hash(scoring)
    prereg_hash = manifest_hash(preregistration)
    review_manifest = _build_review_gate_manifest(release)
    review_manifest_json = canonical_json(review_manifest.model_dump(mode="json"))
    review_manifest_hash = manifest_hash(review_manifest)
    if not release.reserve_order_hash:
        raise EvaluationReleaseValidationError(["procedural_review_reserve_order_missing"])
    datasets = _dataset_rows(session, release)
    if set(datasets) != set(SPLITS):
        raise EvaluationReleaseValidationError(["evaluation_release_dataset_missing"])
    release_hash = sha256_text(
        canonical_json(
            _release_hash_payload(
                release=release,
                datasets=datasets,
                execution_manifest_hash=execution_hash,
                scoring_manifest_hash=scoring_hash,
                preregistration_hash=prereg_hash,
                review_manifest_hash=review_manifest_hash,
                reserve_order_hash=release.reserve_order_hash,
                policy=policy,
            )
        )
    )
    return {
        "execution_manifest_json": canonical_json(execution.model_dump(mode="json")),
        "execution_manifest_hash": execution_hash,
        "scoring_manifest_json": canonical_json(scoring.model_dump(mode="json")),
        "scoring_manifest_hash": scoring_hash,
        "preregistration_hash": prereg_hash,
        "review_manifest_json": review_manifest_json,
        "review_manifest_hash": review_manifest_hash,
        "release_hash": release_hash,
    }


def evaluation_release_artifact_hashes(
    session: Session,
    release: EvaluationRelease,
    *,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> dict[str, str]:
    """Recompute immutable artifact identities without mutating the release."""

    return _refresh_artifacts(session, release, policy=policy)


def _artifact_release_binding_reasons(
    session: Session,
    release: EvaluationRelease,
    release_question: EvaluationReleaseQuestion,
    artifact: ProceduralAIReviewArtifact,
    *,
    recorded_at: datetime,
) -> list[str]:
    reasons: list[str] = []
    common_manifest = artifact.input_manifest
    question = session.get(EvaluationQuestion, release_question.evaluation_question_id)
    if question is None:
        return [f"evaluation_question_not_found:{release_question.evaluation_question_id}"]
    if common_manifest.evaluation_release_id != release.id:
        reasons.append("artifact_evaluation_release_id_mismatch")
    if common_manifest.evaluation_release_question_id != release_question.id:
        reasons.append("artifact_release_question_id_mismatch")
    if common_manifest.evaluation_question_id != question.id:
        reasons.append("artifact_evaluation_question_id_mismatch")
    if artifact.started_at < as_utc(release.created_at):
        reasons.append("artifact_not_fresh_for_release")
    if artifact.completed_at > as_utc(recorded_at):
        reasons.append("artifact_completion_in_future")
    contract = _blind_contract(question)
    contract_digest = resolution_contract_hash(question)

    if isinstance(artifact, QuestionReviewArtifact):
        manifest = artifact.input_manifest
        if manifest.normalized_question_hash != normalized_question_hash(question.question):
            reasons.append("question_review_normalized_question_hash_mismatch")
        if manifest.contract_hash != contract_digest:
            reasons.append("question_review_contract_hash_mismatch")
        if manifest.question != question.question:
            reasons.append("question_review_question_mismatch")
        if manifest.yes_condition != contract.yes_condition:
            reasons.append("question_review_yes_condition_mismatch")
        if manifest.no_condition != contract.no_condition:
            reasons.append("question_review_no_condition_mismatch")
        if as_utc(manifest.forecast_date) != as_utc(question.forecast_date):
            reasons.append("question_review_forecast_date_mismatch")
        if as_utc(manifest.resolution_date) != as_utc(question.resolution_date):
            reasons.append("question_review_resolution_date_mismatch")
        if manifest.authoritative_resolver != contract.authoritative_resolver:
            reasons.append("question_review_resolver_mismatch")
        if manifest.event_family_id != release_question.event_family_id:
            reasons.append("question_review_event_family_mismatch")
        if manifest.leakage_group_id != release_question.leakage_group_id:
            reasons.append("question_review_leakage_group_mismatch")
        if (
            manifest.declared_source_license_status
            != release_question.source_license_status
        ):
            reasons.append("question_review_license_mismatch")
        if _normalized_text(manifest.declared_source_use_basis) != _normalized_text(
            release_question.source_use_basis
        ):
            reasons.append("question_review_source_use_basis_mismatch")
        if (
            manifest.declared_redistribution_allowed
            != release_question.redistribution_allowed
        ):
            reasons.append("question_review_redistribution_mismatch")
        if not any(source.source_role == "pre_outcome_origin" for source in manifest.sources):
            reasons.append("question_review_pre_outcome_origin_source_required")
        for source in manifest.sources:
            if as_utc(source.source_available_at) > as_utc(question.forecast_date):
                reasons.append(
                    f"question_review_source_after_forecast_cutoff:{source.source_id}"
                )
            if source.source_license_status == "unknown":
                reasons.append(f"question_review_source_license_unknown:{source.source_id}")
    else:
        manifest = artifact.input_manifest
        if manifest.contract_hash != contract_digest:
            reasons.append("outcome_adjudication_contract_hash_mismatch")
        if manifest.yes_condition != contract.yes_condition:
            reasons.append("outcome_adjudication_yes_condition_mismatch")
        if manifest.no_condition != contract.no_condition:
            reasons.append("outcome_adjudication_no_condition_mismatch")
        if as_utc(manifest.resolution_date) != as_utc(question.resolution_date):
            reasons.append("outcome_adjudication_resolution_date_mismatch")
        if manifest.candidate_outcome != question.outcome:
            reasons.append("outcome_adjudication_candidate_outcome_mismatch")
        if release_question.outcome_known_at is None:
            reasons.append("outcome_known_at_required")
        elif as_utc(manifest.outcome_known_at) != as_utc(
            release_question.outcome_known_at
        ):
            reasons.append("outcome_adjudication_known_at_mismatch")
        if release.reserve_order_frozen_at is None:
            reasons.append("reserve_order_not_frozen")
        elif as_utc(artifact.started_at) < as_utc(release.reserve_order_frozen_at):
            reasons.append("outcome_adjudication_precedes_reserve_order")
        expected_resolution_url = question.resolution_source.rstrip("/")
        authoritative_urls = {
            str(source.url).rstrip("/")
            for source in manifest.sources
            if source.source_role == "authoritative_resolution"
        }
        if expected_resolution_url not in authoritative_urls:
            reasons.append("authoritative_resolution_source_missing")
        for source in manifest.sources:
            available_at = as_utc(source.source_available_at)
            if available_at < as_utc(question.resolution_date):
                reasons.append(
                    f"outcome_source_before_resolution:{source.source_id}"
                )
            if available_at > as_utc(manifest.outcome_known_at):
                reasons.append(
                    f"outcome_source_after_outcome_known:{source.source_id}"
                )
    return list(dict.fromkeys(reasons))


def _semantic_artifact_reasons(
    artifact: ProceduralAIReviewArtifact,
) -> tuple[list[str], list[str]]:
    all_reasons = artifact_integrity_reasons(artifact)
    hard_prefixes = (
        "rubric_",
        "input_manifest_rubric_",
        "input_manifest_hash_",
        "output_hash_",
        "artifact_completion_",
        "citation_not_in_input_manifest:",
    )
    hard = [
        reason for reason in all_reasons if reason.startswith(hard_prefixes)
    ]
    semantic = [reason for reason in all_reasons if reason not in hard]
    return hard, semantic


def record_procedural_ai_review_artifact(
    session: Session,
    release: EvaluationRelease,
    *,
    artifact_type: Literal["question_review", "outcome_adjudication"],
    payload: dict[str, Any],
    now: datetime | None = None,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> ProceduralAIReviewArtifactRecord:
    """Validate and retain one externally produced Codex receipt without a model call."""

    if release.status != "draft":
        raise EvaluationReleaseValidationError(
            ["procedural_ai_review_requires_draft_release"]
        )
    if release.procedural_review_policy_version != PROCEDURAL_AI_REVIEW_POLICY_VERSION:
        raise EvaluationReleaseValidationError(["procedural_review_policy_mismatch"])
    try:
        artifact = parse_review_artifact(payload, artifact_type=artifact_type)
    except ValueError as exc:
        raise EvaluationReleaseValidationError(str(exc).split(";")) from exc
    release_question = session.scalar(
        select(EvaluationReleaseQuestion).where(
            EvaluationReleaseQuestion.id
            == artifact.input_manifest.evaluation_release_question_id,
            EvaluationReleaseQuestion.release_id == release.id,
        )
    )
    if release_question is None:
        raise EvaluationReleaseValidationError(["artifact_release_question_not_found"])
    if release_question.inclusion_status != "included":
        raise EvaluationReleaseValidationError(["artifact_for_excluded_question"])
    existing = session.scalar(
        select(ProceduralAIReviewArtifactRecord).where(
            ProceduralAIReviewArtifactRecord.evaluation_release_question_id
            == release_question.id,
            ProceduralAIReviewArtifactRecord.artifact_type == artifact_type,
        )
    )
    if existing is not None:
        if (
            existing.run_id == artifact.run_identity.run_id
            and existing.input_manifest_hash == artifact.input_manifest_hash
            and existing.output_hash == artifact.output_hash
        ):
            return existing
        raise EvaluationReleaseValidationError(
            ["procedural_ai_review_artifact_conflict_requires_new_release"]
        )
    reused_run = session.scalar(
        select(ProceduralAIReviewArtifactRecord.id).where(
            ProceduralAIReviewArtifactRecord.run_id == artifact.run_identity.run_id
        )
    )
    if reused_run is not None:
        raise EvaluationReleaseValidationError(["procedural_ai_review_run_id_reused"])
    hard_reasons, semantic_reasons = _semantic_artifact_reasons(artifact)
    binding_reasons = _artifact_release_binding_reasons(
        session,
        release,
        release_question,
        artifact,
        recorded_at=as_utc(now or utcnow()),
    )
    if hard_reasons or binding_reasons:
        raise EvaluationReleaseValidationError(hard_reasons + binding_reasons)
    citation_ids = sorted(
        {
            source_id
            for finding in artifact.output.findings
            for source_id in finding.source_ids
        }
    )
    row = ProceduralAIReviewArtifactRecord(
        id=str(uuid.uuid4()),
        evaluation_release_id=release.id,
        evaluation_release_question_id=release_question.id,
        evaluation_question_id=release_question.evaluation_question_id,
        artifact_type=artifact_type,
        policy_version=artifact.policy_version,
        rubric_version=artifact.rubric_version,
        rubric_hash=artifact.rubric_hash,
        role=artifact.run_identity.role,
        model_provider=artifact.run_identity.model_provider,
        model_id=artifact.run_identity.model_id,
        model_version=artifact.run_identity.model_version,
        tool_name=artifact.run_identity.tool_name,
        tool_version=artifact.run_identity.tool_version,
        run_id=artifact.run_identity.run_id,
        started_at=as_utc(artifact.started_at),
        completed_at=as_utc(artifact.completed_at),
        created_at=as_utc(now or utcnow()),
        input_manifest_json=canonical_json(
            artifact.input_manifest.model_dump(mode="json")
        ),
        input_manifest_hash=artifact.input_manifest_hash,
        output_json=canonical_json(artifact.output.model_dump(mode="json")),
        output_hash=artifact.output_hash,
        decision=artifact.output.decision,
        gate_status="passed" if not semantic_reasons else "failed",
        gate_reasons_json=canonical_json(semantic_reasons),
        source_citation_ids_json=canonical_json(citation_ids),
    )
    release.procedural_review_artifacts.append(row)
    session.flush()
    artifacts = _refresh_artifacts(session, release, policy=policy)
    for field, value in artifacts.items():
        setattr(release, field, value)
    session.flush()
    return row


def serialize_procedural_ai_review_artifact(
    row: ProceduralAIReviewArtifactRecord,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "evaluation_release_id": row.evaluation_release_id,
        "evaluation_release_question_id": row.evaluation_release_question_id,
        "evaluation_question_id": row.evaluation_question_id,
        "artifact_type": row.artifact_type,
        "policy_version": row.policy_version,
        "rubric_version": row.rubric_version,
        "rubric_hash": row.rubric_hash,
        "role": row.role,
        "model_identity": {
            "provider": row.model_provider,
            "model_id": row.model_id,
            "model_version": row.model_version,
        },
        "tool_identity": {
            "tool_name": row.tool_name,
            "tool_version": row.tool_version,
        },
        "run_id": row.run_id,
        "started_at": as_utc(row.started_at).isoformat(),
        "completed_at": as_utc(row.completed_at).isoformat(),
        "created_at": as_utc(row.created_at).isoformat(),
        "input_manifest_hash": row.input_manifest_hash,
        "output_hash": row.output_hash,
        "decision": row.decision,
        "gate_status": row.gate_status,
        "gate_reasons": json.loads(row.gate_reasons_json),
        "source_citation_ids": json.loads(row.source_citation_ids_json),
        "input_manifest": json.loads(row.input_manifest_json),
        "output": json.loads(row.output_json),
    }


def procedural_ai_review_gate_reasons(
    session: Session,
    release: EvaluationRelease,
) -> list[str]:
    reasons: list[str] = []
    if release.procedural_review_policy_version != PROCEDURAL_AI_REVIEW_POLICY_VERSION:
        reasons.append("procedural_review_policy_mismatch")
    if (
        not release.reserve_order_json
        or not release.reserve_order_hash
        or release.reserve_order_frozen_at is None
    ):
        reasons.append("procedural_review_reserve_order_missing")
    else:
        try:
            stored_reserve = json.loads(release.reserve_order_json)
        except json.JSONDecodeError:
            reasons.append("reserve_order_invalid_json")
        else:
            expected_reserve = _reserve_order_payload(session, release)
            if stored_reserve != expected_reserve:
                reasons.append("reserve_order_content_mismatch")
            if sha256_text(canonical_json(stored_reserve)) != release.reserve_order_hash:
                reasons.append("reserve_order_hash_mismatch")

    rows_by_question = _artifact_rows_by_release_question(release)
    included = [item for item in release.questions if item.inclusion_status == "included"]
    all_run_ids: list[str] = []
    for item in included:
        rows = rows_by_question.get(item.id, {})
        for artifact_type in ("question_review", "outcome_adjudication"):
            row = rows.get(artifact_type)
            if row is None:
                reasons.append(
                    f"procedural_review_artifact_missing:{item.evaluation_question_id}:{artifact_type}"
                )
                continue
            all_run_ids.append(row.run_id)
            if row.gate_status != "passed":
                reasons.append(
                    f"procedural_review_artifact_failed:{item.evaluation_question_id}:{artifact_type}"
                )
            try:
                artifact = parse_review_artifact(
                    _artifact_payload_from_row(row),
                    artifact_type=cast(Any, artifact_type),
                )
            except (ValueError, json.JSONDecodeError):
                reasons.append(
                    f"procedural_review_artifact_invalid:{item.evaluation_question_id}:{artifact_type}"
                )
                continue
            reasons.extend(
                f"{reason}:{item.evaluation_question_id}:{artifact_type}"
                for reason in artifact_integrity_reasons(artifact)
            )
            reasons.extend(
                f"{reason}:{item.evaluation_question_id}:{artifact_type}"
                for reason in _artifact_release_binding_reasons(
                    session,
                    release,
                    item,
                    artifact,
                    recorded_at=as_utc(row.created_at),
                )
            )
    if len(all_run_ids) != len(set(all_run_ids)):
        reasons.append("procedural_review_run_ids_not_role_separated")
    for row in release.procedural_review_artifacts:
        release_question = next(
            (item for item in release.questions if item.id == row.evaluation_release_question_id),
            None,
        )
        if release_question is None or release_question.inclusion_status != "included":
            reasons.append(f"procedural_review_artifact_not_for_included_question:{row.id}")
    try:
        expected_manifest = _build_review_gate_manifest(release)
    except EvaluationReleaseValidationError as exc:
        reasons.extend(exc.reasons)
    else:
        expected_json = canonical_json(expected_manifest.model_dump(mode="json"))
        expected_hash = manifest_hash(expected_manifest)
        if release.review_manifest_json != expected_json:
            reasons.append("procedural_review_manifest_content_mismatch")
        if release.review_manifest_hash != expected_hash:
            reasons.append("procedural_review_manifest_hash_mismatch")
    return list(dict.fromkeys(reasons))


def _forbidden_manifest_paths(value: Any, path: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _EXECUTION_FORBIDDEN_KEYS:
                paths.append(child_path)
            paths.extend(_forbidden_manifest_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_forbidden_manifest_paths(child, f"{path}[{index}]"))
    return paths


def _is_non_real_dataset(dataset: EvaluationDataset) -> bool:
    text = " ".join(
        (dataset.name, dataset.description, dataset.provenance)
    ).casefold()
    return any(marker in text for marker in _NON_REAL_MARKERS)


def validate_evaluation_release(
    session: Session,
    release: EvaluationRelease,
    *,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
    validate_environment: bool = True,
) -> list[str]:
    """Return stable, complete fail-closed review reasons without mutation."""

    reasons: list[str] = []
    if release.policy_version != policy.version:
        reasons.append("evaluation_release_policy_version_mismatch")
    dataset_ids = list(_dataset_map(release).values())
    if len(set(dataset_ids)) != len(dataset_ids):
        reasons.append("evaluation_release_datasets_must_be_distinct")
    datasets = _dataset_rows(session, release)
    if set(datasets) != set(SPLITS):
        reasons.append("evaluation_release_dataset_missing")
    for split in SPLITS:
        dataset = datasets.get(split)
        if dataset is None:
            continue
        if dataset.status != policy.dataset_status_required or dataset.frozen_at is None:
            reasons.append(f"source_dataset_not_frozen:{split}")
        if _is_non_real_dataset(dataset):
            reasons.append(f"synthetic_or_fixture_dataset_forbidden:{split}")

    questions = _question_by_id(session, release)
    row_ids = [item.evaluation_question_id for item in release.questions]
    if len(row_ids) != len(set(row_ids)):
        reasons.append("duplicate_evaluation_question_id")
    expected_ids = {
        question.id
        for dataset in datasets.values()
        for question in dataset.questions
    }
    if set(row_ids) != expected_ids:
        reasons.append("release_dataset_question_membership_incomplete")

    included = [item for item in release.questions if item.inclusion_status == "included"]
    counts = Counter(item.split for item in included)
    for split, required in policy.required_included_counts.items():
        if counts.get(split, 0) != required:
            reasons.append(
                f"included_split_count_mismatch:{split}:{counts.get(split, 0)}:{required}"
            )

    normalized_hashes: list[str] = []
    contract_hashes: list[str] = []
    event_splits: dict[str, set[str]] = defaultdict(set)
    leakage_splits: dict[str, set[str]] = defaultdict(set)
    dataset_map = _dataset_map(release)
    for item in release.questions:
        question = questions.get(item.evaluation_question_id)
        if question is None:
            reasons.append(f"evaluation_question_not_found:{item.evaluation_question_id}")
            continue
        split = cast(EvaluationSplit, item.split)
        if question.dataset_id != dataset_map.get(split):
            reasons.append(f"evaluation_question_split_dataset_mismatch:{question.id}")
        if question.outcome not in (0, 1):
            reasons.append(f"non_binary_outcome:{question.id}")
        if as_utc(question.forecast_date) >= as_utc(question.resolution_date):
            reasons.append(f"forecast_date_not_before_resolution:{question.id}")
        try:
            contract = _blind_contract(question)
            contract_hash = resolution_contract_hash(question)
        except EvaluationReleaseValidationError as exc:
            reasons.extend(f"{reason}:{question.id}" for reason in exc.reasons)
            contract = None
            contract_hash = ""
        if contract and (not contract.yes_condition or not contract.no_condition):
            reasons.append(f"resolution_contract_incomplete:{question.id}")
        if item.inclusion_status == "excluded":
            if not _normalized_text(item.exclusion_reason):
                reasons.append(f"excluded_question_reason_required:{question.id}")
            continue
        normalized_hashes.append(normalized_question_hash(question.question))
        contract_hashes.append(contract_hash)
        if not _normalized_text(item.event_family_id):
            reasons.append(f"event_family_id_required:{question.id}")
        else:
            event_splits[item.event_family_id].add(item.split)
        if not _normalized_text(item.leakage_group_id):
            reasons.append(f"leakage_group_id_required:{question.id}")
        else:
            leakage_splits[item.leakage_group_id].add(item.split)
        if item.outcome_known_at is None:
            reasons.append(f"outcome_known_at_required:{question.id}")
        else:
            outcome_known = as_utc(item.outcome_known_at)
            if as_utc(question.forecast_date) >= outcome_known:
                reasons.append(f"forecast_date_not_before_outcome_known:{question.id}")
            if as_utc(question.resolution_date) > outcome_known:
                reasons.append(f"resolution_date_after_outcome_known:{question.id}")
        if item.source_license_status == "unknown":
            reasons.append(f"known_source_license_required:{question.id}")
        if not _normalized_text(item.source_use_basis):
            reasons.append(f"source_use_basis_required:{question.id}")
        if item.redistribution_allowed is None:
            reasons.append(f"redistribution_allowed_required:{question.id}")

    if len(normalized_hashes) != len(set(normalized_hashes)):
        reasons.append("duplicate_normalized_question_hash")
    if len(contract_hashes) != len(set(contract_hashes)):
        reasons.append("duplicate_resolution_contract_hash")
    for family, splits in sorted(event_splits.items()):
        if len(splits) > 1:
            reasons.append(f"cross_split_event_family:{family}")
    for group, splits in sorted(leakage_splits.items()):
        if len(splits) > 1:
            reasons.append(f"cross_split_leakage_group:{group}")

    reasons.extend(procedural_ai_review_gate_reasons(session, release))

    try:
        preregistration = EvaluationPreregistration.model_validate_json(
            release.preregistration_json
        )
    except ValidationError:
        reasons.append("preregistration_invalid")
        preregistration = None
    if preregistration is not None:
        if preregistration.release_policy_version != policy.version:
            reasons.append("preregistration_policy_mismatch")
        expected_dataset_ids = _dataset_map(release)
        if preregistration.dataset_ids != expected_dataset_ids:
            reasons.append("preregistration_dataset_ids_mismatch")
        if preregistration.split_sizes != policy.required_included_counts:
            reasons.append("preregistration_split_sizes_mismatch")
        if (
            preregistration.procedural_review_policy_version
            != PROCEDURAL_AI_REVIEW_POLICY_VERSION
        ):
            reasons.append("preregistration_procedural_review_policy_mismatch")
        if preregistration.procedural_review_release_label != PROCEDURAL_AI_RELEASE_LABEL:
            reasons.append("preregistration_procedural_review_label_mismatch")
        if (
            preregistration.question_review_rubric_version
            != QUESTION_REVIEW_RUBRIC_VERSION
            or preregistration.question_review_rubric_hash
            != QUESTION_REVIEW_RUBRIC_HASH
        ):
            reasons.append("preregistration_question_review_rubric_mismatch")
        if (
            preregistration.outcome_adjudication_rubric_version
            != OUTCOME_ADJUDICATION_RUBRIC_VERSION
            or preregistration.outcome_adjudication_rubric_hash
            != OUTCOME_ADJUDICATION_RUBRIC_HASH
        ):
            reasons.append("preregistration_outcome_adjudication_rubric_mismatch")
        if (
            preregistration.reserve_order_policy_version
            != RESERVE_ORDER_POLICY_VERSION
            or preregistration.reserve_order_hash != release.reserve_order_hash
        ):
            reasons.append("preregistration_reserve_order_mismatch")
        if validate_environment:
            reasons.extend(preregistration_environment_reasons(preregistration))

    try:
        artifacts = _refresh_artifacts(session, release, policy=policy)
    except EvaluationReleaseValidationError as exc:
        reasons.extend(exc.reasons)
        artifacts = None
    if artifacts is not None:
        for field, expected in artifacts.items():
            if getattr(release, field) != expected:
                reasons.append(f"{field}_mismatch")
        try:
            execution_payload = json.loads(release.execution_manifest_json)
        except json.JSONDecodeError:
            reasons.append("execution_manifest_invalid_json")
        else:
            for path in _forbidden_manifest_paths(execution_payload):
                reasons.append(f"execution_manifest_forbidden_field:{path}")
    return list(dict.fromkeys(reasons))


def _assert_correction(
    session: Session,
    *,
    name: str,
    version: str,
    correction_of_release_id: str | None,
    correction_summary: str | None,
) -> None:
    if correction_of_release_id is None:
        if correction_summary:
            raise EvaluationReleaseValidationError(["correction_release_id_required"])
        return
    previous = session.get(EvaluationRelease, correction_of_release_id)
    reasons: list[str] = []
    if previous is None:
        reasons.append("correction_release_not_found")
    else:
        if previous.status != "frozen":
            reasons.append("correction_source_release_must_be_frozen")
        if previous.name != name:
            reasons.append("correction_release_name_mismatch")
        if previous.version == version:
            reasons.append("correction_requires_new_version")
    if not _normalized_text(correction_summary):
        reasons.append("correction_summary_required")
    if reasons:
        raise EvaluationReleaseValidationError(reasons)


def _same_creation_request(
    existing: EvaluationRelease,
    *,
    development_dataset_id: str,
    validation_dataset_id: str,
    test_dataset_id: str,
    correction_of_release_id: str | None,
    correction_summary: str | None,
    questions: list[EvaluationReleaseQuestionInput],
    provider_identity: EvaluationProviderIdentity,
) -> bool:
    basic_matches = (
        existing.development_dataset_id == development_dataset_id
        and existing.validation_dataset_id == validation_dataset_id
        and existing.test_dataset_id == test_dataset_id
        and existing.correction_of_release_id == correction_of_release_id
        and (existing.correction_summary or None)
        == (_normalized_text(correction_summary) or None)
    )
    if not basic_matches:
        return False
    stored = sorted(
        [_release_question_metadata(item) for item in existing.questions],
        key=lambda item: (item["split"], item["evaluation_question_id"]),
    )
    requested = sorted(
        [_question_input_metadata(item) for item in questions],
        key=lambda item: (item["split"], item["evaluation_question_id"]),
    )
    if canonical_json(stored) != canonical_json(requested):
        return False
    try:
        prereg = EvaluationPreregistration.model_validate_json(
            existing.preregistration_json
        )
    except ValidationError:
        return False
    return prereg.provider_identity == provider_identity


def create_evaluation_release(
    session: Session,
    *,
    name: str,
    version: str,
    development_dataset_id: str,
    validation_dataset_id: str,
    test_dataset_id: str,
    questions: list[EvaluationReleaseQuestionInput],
    provider_identity: EvaluationProviderIdentity,
    correction_of_release_id: str | None = None,
    correction_summary: str | None = None,
    now: datetime | None = None,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> EvaluationRelease:
    """Create a draft aggregate while preserving invalid policy states for review."""

    normalized_name = _normalized_text(name)
    normalized_version = version.strip()
    reasons: list[str] = []
    if not normalized_name:
        reasons.append("evaluation_release_name_required")
    if len(normalized_name) > 255:
        reasons.append("evaluation_release_name_too_long")
    if not _VERSION_PATTERN.fullmatch(normalized_version):
        reasons.append("invalid_evaluation_release_version")
    dataset_ids = [
        development_dataset_id,
        validation_dataset_id,
        test_dataset_id,
    ]
    if len(set(dataset_ids)) != 3:
        reasons.append("evaluation_release_datasets_must_be_distinct")
    datasets_by_id = {
        item.id: item
        for item in session.scalars(
            select(EvaluationDataset).where(EvaluationDataset.id.in_(dataset_ids))
        ).all()
    }
    if set(datasets_by_id) != set(dataset_ids):
        reasons.append("evaluation_release_dataset_missing")
    for dataset_id in dataset_ids:
        dataset = datasets_by_id.get(dataset_id)
        if dataset is not None and (
            dataset.status != "frozen" or dataset.frozen_at is None
        ):
            reasons.append(f"source_dataset_not_frozen:{dataset_id}")
    if reasons:
        raise EvaluationReleaseValidationError(reasons)
    _assert_correction(
        session,
        name=normalized_name,
        version=normalized_version,
        correction_of_release_id=correction_of_release_id,
        correction_summary=correction_summary,
    )
    existing = session.scalar(
        select(EvaluationRelease).where(
            EvaluationRelease.name == normalized_name,
            EvaluationRelease.version == normalized_version,
        )
    )
    if existing is not None:
        if _same_creation_request(
            existing,
            development_dataset_id=development_dataset_id,
            validation_dataset_id=validation_dataset_id,
            test_dataset_id=test_dataset_id,
            correction_of_release_id=correction_of_release_id,
            correction_summary=correction_summary,
            questions=questions,
            provider_identity=provider_identity,
        ):
            return existing
        raise EvaluationReleaseValidationError(["evaluation_release_version_conflict"])

    timestamp = as_utc(now or utcnow())
    release = EvaluationRelease(
        id=str(uuid.uuid4()),
        name=normalized_name,
        version=normalized_version,
        policy_version=policy.version,
        status="draft",
        development_dataset_id=development_dataset_id,
        validation_dataset_id=validation_dataset_id,
        test_dataset_id=test_dataset_id,
        created_at=timestamp,
        reviewed_at=None,
        frozen_at=None,
        correction_of_release_id=correction_of_release_id,
        correction_summary=_normalized_text(correction_summary) or None,
        execution_manifest_json="{}",
        execution_manifest_hash="",
        scoring_manifest_json="{}",
        scoring_manifest_hash="",
        preregistration_json="{}",
        preregistration_hash="",
        release_hash=str(uuid.uuid4()),
    )
    session.add(release)
    session.flush()
    for item in questions:
        payload = _question_input_payload(item)
        session.add(
            EvaluationReleaseQuestion(
                id=str(uuid.uuid4()),
                release_id=release.id,
                **payload,
            )
        )
    session.flush()
    reserve_order = _reserve_order_payload(session, release)
    release.procedural_review_policy_version = PROCEDURAL_AI_REVIEW_POLICY_VERSION
    release.reserve_order_json = canonical_json(reserve_order)
    release.reserve_order_hash = sha256_text(release.reserve_order_json)
    release.reserve_order_frozen_at = timestamp
    session.flush()
    datasets = {
        "development": datasets_by_id[development_dataset_id],
        "validation": datasets_by_id[validation_dataset_id],
        "test": datasets_by_id[test_dataset_id],
    }
    preregistration = build_evaluation_preregistration(
        release_policy=policy,
        datasets=datasets,
        provider_identity=provider_identity,
        reserve_order_hash=release.reserve_order_hash,
        registered_at=timestamp,
    )
    release.preregistration_json = canonical_json(
        preregistration.model_dump(mode="json")
    )
    session.flush()
    artifacts = _refresh_artifacts(session, release, policy=policy)
    for field, value in artifacts.items():
        setattr(release, field, value)
    session.flush()
    return release


def review_evaluation_release(
    session: Session,
    release: EvaluationRelease,
    *,
    now: datetime | None = None,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> EvaluationRelease:
    if release.status not in RELEASE_STATUSES:
        raise EvaluationReleaseValidationError(["evaluation_release_status_invalid"])
    reasons = validate_evaluation_release(session, release, policy=policy)
    if reasons:
        raise EvaluationReleaseValidationError(reasons)
    if release.status in {"reviewed", "frozen"}:
        return release
    release.status = "reviewed"
    release.reviewed_at = as_utc(now or utcnow())
    session.flush()
    return release


def freeze_evaluation_release(
    session: Session,
    release: EvaluationRelease,
    *,
    now: datetime | None = None,
    policy: EvaluationReleasePolicy = PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
) -> EvaluationRelease:
    if release.status == "draft":
        raise EvaluationReleaseValidationError(["evaluation_release_review_required"])
    reasons = validate_evaluation_release(session, release, policy=policy)
    if reasons:
        raise EvaluationReleaseValidationError(reasons)
    if release.status == "frozen":
        return release
    if release.status != "reviewed":
        raise EvaluationReleaseValidationError(["evaluation_release_status_invalid"])
    release.status = "frozen"
    release.frozen_at = as_utc(now or utcnow())
    session.flush()
    return release


def get_blinded_execution_manifest(
    release: EvaluationRelease,
    *,
    require_frozen: bool = True,
) -> BlindedExecutionManifest:
    if require_frozen and release.status != "frozen":
        raise EvaluationReleaseValidationError(["evaluation_release_must_be_frozen"])
    try:
        manifest = BlindedExecutionManifest.model_validate_json(
            release.execution_manifest_json
        )
    except ValidationError as exc:
        raise EvaluationReleaseValidationError(["execution_manifest_invalid"]) from exc
    if manifest_hash(manifest) != release.execution_manifest_hash:
        raise EvaluationReleaseValidationError(["execution_manifest_hash_mismatch"])
    forbidden = _forbidden_manifest_paths(manifest.model_dump(mode="json"))
    if forbidden:
        raise EvaluationReleaseValidationError(
            [f"execution_manifest_forbidden_field:{path}" for path in forbidden]
        )
    return manifest


def get_sealed_scoring_manifest(
    release: EvaluationRelease,
    *,
    require_frozen: bool = True,
) -> SealedScoringManifest:
    if require_frozen and release.status != "frozen":
        raise EvaluationReleaseValidationError(["evaluation_release_must_be_frozen"])
    try:
        manifest = SealedScoringManifest.model_validate_json(
            release.scoring_manifest_json
        )
    except ValidationError as exc:
        raise EvaluationReleaseValidationError(["scoring_manifest_invalid"]) from exc
    if manifest_hash(manifest) != release.scoring_manifest_hash:
        raise EvaluationReleaseValidationError(["scoring_manifest_hash_mismatch"])
    return manifest


def assert_release_environment(release: EvaluationRelease) -> None:
    try:
        preregistration = EvaluationPreregistration.model_validate_json(
            release.preregistration_json
        )
    except ValidationError as exc:
        raise EvaluationReleaseValidationError(["preregistration_invalid"]) from exc
    reasons = preregistration_environment_reasons(preregistration)
    if reasons:
        raise EvaluationReleaseValidationError(reasons)


def serialize_evaluation_release(
    release: EvaluationRelease,
    *,
    include_questions: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": release.id,
        "name": release.name,
        "version": release.version,
        "policy_version": release.policy_version,
        "status": release.status,
        "development_dataset_id": release.development_dataset_id,
        "validation_dataset_id": release.validation_dataset_id,
        "test_dataset_id": release.test_dataset_id,
        "created_at": as_utc(release.created_at).isoformat(),
        "reviewed_at": as_utc(release.reviewed_at).isoformat() if release.reviewed_at else None,
        "frozen_at": as_utc(release.frozen_at).isoformat() if release.frozen_at else None,
        "correction_of_release_id": release.correction_of_release_id,
        "correction_summary": release.correction_summary,
        "execution_manifest_hash": release.execution_manifest_hash,
        "scoring_manifest_hash": release.scoring_manifest_hash,
        "preregistration_hash": release.preregistration_hash,
        "release_label": PROCEDURAL_AI_RELEASE_LABEL,
        "procedural_review_policy_version": (
            release.procedural_review_policy_version
        ),
        "reserve_order_hash": release.reserve_order_hash,
        "reserve_order_frozen_at": (
            as_utc(release.reserve_order_frozen_at).isoformat()
            if release.reserve_order_frozen_at
            else None
        ),
        "review_manifest_hash": release.review_manifest_hash,
        "human_reviewed": False,
        "independently_validated": False,
        "publication_grade": False,
        "forecasting_quality_established": False,
        "calibration_established": False,
        "release_hash": release.release_hash,
        "structural_blinding_only": True,
    }
    if include_questions:
        payload["questions"] = sorted(
            [_release_question_metadata(item) for item in release.questions],
            key=lambda item: (item["split"], item["evaluation_question_id"]),
        )
    return payload


def release_audit(
    session: Session,
    release: EvaluationRelease,
) -> dict[str, Any]:
    rows = release.questions
    counts = {
        split: {
            "included": sum(
                item.split == split and item.inclusion_status == "included"
                for item in rows
            ),
            "excluded": sum(
                item.split == split and item.inclusion_status == "excluded"
                for item in rows
            ),
        }
        for split in SPLITS
    }
    payload = serialize_evaluation_release(release, include_questions=True)
    payload["counts"] = counts
    payload["reserve_order"] = (
        json.loads(release.reserve_order_json) if release.reserve_order_json else None
    )
    payload["procedural_ai_review_artifacts"] = [
        serialize_procedural_ai_review_artifact(item)
        for item in sorted(
            release.procedural_review_artifacts,
            key=lambda row: (
                row.evaluation_release_question_id,
                row.artifact_type,
            ),
        )
    ]
    payload["validation_reasons"] = validate_evaluation_release(
        session,
        release,
        validate_environment=False,
    )
    return payload

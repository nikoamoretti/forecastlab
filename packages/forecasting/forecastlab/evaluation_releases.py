from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.procedural_ai_review import (
    OUTCOME_ADJUDICATION_RUBRIC_HASH,
    OUTCOME_ADJUDICATION_RUBRIC_VERSION,
    PROCEDURAL_AI_RELEASE_LABEL,
    PROCEDURAL_AI_REVIEW_POLICY_V1,
    PROCEDURAL_AI_REVIEW_POLICY_V2,
    PROCEDURAL_AI_REVIEW_POLICY_VERSION,
    QUESTION_REVIEW_RUBRIC_HASH,
    QUESTION_REVIEW_RUBRIC_VERSION,
    RESERVE_ORDER_POLICY_VERSION,
)

POLICY_VERSION_V1 = "private_v1_real_evaluation_release_v1"
POLICY_VERSION_V2 = "private_v1_real_evaluation_release_v2"
# Preserve the public V1 constant for existing snapshots and callers.
POLICY_VERSION = POLICY_VERSION_V1

EvaluationSplit = Literal["development", "validation", "test"]
InclusionStatus = Literal["included", "excluded"]
SourceLicenseStatus = Literal[
    "public_domain",
    "licensed",
    "metadata_use_permitted",
    "unknown",
]


class EvaluationReleasePolicy(BaseModel):
    """Immutable production policy for a real-evaluation release."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[
        "private_v1_real_evaluation_release_v1",
        "private_v1_real_evaluation_release_v2",
    ] = POLICY_VERSION_V1
    required_included_counts: dict[EvaluationSplit, int] = Field(
        default_factory=lambda: {
            "development": 60,
            "validation": 40,
            "test": 100,
        }
    )
    dataset_status_required: Literal["frozen"] = "frozen"
    synthetic_questions_allowed: bool = False
    fixture_questions_allowed: bool = False
    duplicate_question_ids_allowed: bool = False
    duplicate_normalized_questions_allowed: bool = False
    duplicate_contracts_allowed: bool = False
    cross_split_event_families_allowed: bool = False
    cross_split_leakage_groups_allowed: bool = False
    independent_reviewer_and_adjudicator_required: bool = True
    known_source_license_required: bool = True
    evidence_cutoff_rule: Literal["forecast_date"] = "forecast_date"
    structural_blinding_only: bool = True
    procedural_review_policy_version: Literal[
        "private_v1_procedural_ai_review_v1",
        "private_v1_procedural_ai_review_v2",
    ] = PROCEDURAL_AI_REVIEW_POLICY_V1
    required_question_review_artifacts_per_included_question: Literal[1] = 1
    required_outcome_adjudication_artifacts_per_included_question: Literal[1] = 1
    role_separated_codex_runs_required: Literal[True] = True
    deterministic_reserve_order_required: Literal[True] = True
    test_split_one_shot_required: Literal[True] = True
    release_label: Literal[
        "Procedurally AI-reviewed private-V1 evaluation release"
    ] = "Procedurally AI-reviewed private-V1 evaluation release"
    human_review_claim_allowed: Literal[False] = False
    independent_validation_claim_allowed: Literal[False] = False


PRIVATE_V1_REAL_EVALUATION_RELEASE_V1 = EvaluationReleasePolicy()
PRIVATE_V1_REAL_EVALUATION_RELEASE_V2 = EvaluationReleasePolicy(
    version=POLICY_VERSION_V2,
    known_source_license_required=False,
    procedural_review_policy_version=PROCEDURAL_AI_REVIEW_POLICY_V2,
)


def evaluation_release_policy_for_version(version: str) -> EvaluationReleasePolicy:
    """Resolve only a registered immutable real-evaluation release policy."""

    policies = {
        POLICY_VERSION_V1: PRIVATE_V1_REAL_EVALUATION_RELEASE_V1,
        POLICY_VERSION_V2: PRIVATE_V1_REAL_EVALUATION_RELEASE_V2,
    }
    try:
        return policies[version]
    except KeyError as exc:
        raise ValueError("unsupported_evaluation_release_policy") from exc


def licensing_audit_metadata_required(policy: EvaluationReleasePolicy) -> bool:
    """Keep V1's licensing gates while leaving V2 metadata audit-only."""

    return policy.version == POLICY_VERSION_V1


class BlindedResolutionContract(BaseModel):
    """Resolution fields safe for forecast construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    forecast_type: Literal["binary"] = "binary"
    yes_condition: str
    no_condition: str
    resolution_date: datetime
    authoritative_resolver: str
    fallback_resolver_identities: list[str] = Field(default_factory=list)
    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""


class BlindedEvaluationQuestion(BaseModel):
    """Worker-facing question DTO that cannot represent an outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_question_id: str
    split: EvaluationSplit
    question: str
    normalized_question_hash: str
    contract_hash: str
    resolution_contract: BlindedResolutionContract
    forecast_date: datetime
    resolution_date: datetime
    domain: str
    category: str | None = None
    authoritative_resolver: str
    evidence_cutoff: datetime
    preregistration_hash: str


class EvaluationReleaseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    version: str
    policy_version: str


class BlindedExecutionManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    release: EvaluationReleaseIdentity
    dataset_hashes: dict[EvaluationSplit, str]
    preregistration_hash: str
    questions: list[BlindedEvaluationQuestion]


class SealedScoringQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_question_id: str
    split: EvaluationSplit
    outcome: Literal[0, 1]
    post_resolution_source: str
    outcome_known_at: datetime
    adjudication_record_hash: str
    scoring_contract_hash: str


class SealedScoringManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    release: EvaluationReleaseIdentity
    questions: list[SealedScoringQuestion]


class PreregisteredProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    version: int
    profile_hash: str
    source_profile: dict[str, Any]
    budget_ceiling: dict[str, int | float]


class EvaluationProviderIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_provider: str
    model: str
    search_provider: str


class EvaluationReleaseQuestionInput(BaseModel):
    """Release metadata; procedural artifacts, not these IDs, prove review."""

    model_config = ConfigDict(extra="forbid")

    evaluation_question_id: str
    split: EvaluationSplit
    event_family_id: str
    leakage_group_id: str
    inclusion_status: InclusionStatus = "included"
    exclusion_reason: str | None = None
    question_author_id: str | None = None
    question_reviewer_id: str | None = None
    outcome_adjudicator_id: str | None = None
    review_completed_at: datetime | None = None
    outcome_known_at: datetime | None = None
    source_license_status: SourceLicenseStatus = "unknown"
    source_use_basis: str | None = None
    redistribution_allowed: bool | None = None
    adjudication_notes: str | None = None
    adjudication_record_hash: str | None = None


class EvaluationPreregistration(BaseModel):
    """Complete immutable analysis and execution plan for a release."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    release_policy_version: str
    registered_at: datetime
    dataset_ids: dict[EvaluationSplit, str]
    dataset_hashes: dict[EvaluationSplit, str]
    split_sizes: dict[EvaluationSplit, int]
    profiles: list[PreregisteredProfile]
    prompt_versions: dict[str, str]
    prompt_hashes: dict[str, str]
    source_code_sha: str
    tracked_source_hash: str
    pyproject_hash: str
    dependency_lock_hash: str
    package_lock_hash: str
    provider_identity: EvaluationProviderIdentity
    procedural_review_policy_version: str = PROCEDURAL_AI_REVIEW_POLICY_VERSION
    procedural_review_release_label: str = PROCEDURAL_AI_RELEASE_LABEL
    question_review_rubric_version: str = QUESTION_REVIEW_RUBRIC_VERSION
    question_review_rubric_hash: str = QUESTION_REVIEW_RUBRIC_HASH
    outcome_adjudication_rubric_version: str = (
        OUTCOME_ADJUDICATION_RUBRIC_VERSION
    )
    outcome_adjudication_rubric_hash: str = OUTCOME_ADJUDICATION_RUBRIC_HASH
    reserve_order_policy_version: str = RESERVE_ORDER_POLICY_VERSION
    reserve_order_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_cutoff_rule: Literal["forecast_date"] = "forecast_date"
    primary_metric: Literal["brier_score"] = "brier_score"
    secondary_metrics: tuple[Literal["log_loss"], ...] = ("log_loss",)
    operational_metrics: tuple[
        Literal[
            "completion",
            "failure",
            "cost",
            "latency",
            "evidence_coverage",
        ],
        ...,
    ] = (
        "completion",
        "failure",
        "cost",
        "latency",
        "evidence_coverage",
    )
    paired_comparison_method: Literal["paired_by_evaluation_question_id"] = (
        "paired_by_evaluation_question_id"
    )
    bootstrap_method: Literal["paired_percentile"] = "paired_percentile"
    bootstrap_samples: int = 2_000
    bootstrap_seed: int = 20_260_823
    confidence_interval: str = "95%"
    calibration_minimum_sample: int = 20
    failure_handling: str = (
        "Failed runs receive no invented probability and no forecast score."
    )
    exclusion_rules: tuple[str, ...] = (
        "Exclusions are frozen before execution and cannot depend on profile performance.",
        "Excluded questions remain auditable but are neither executed nor scored.",
    )
    split_permitted_uses: dict[EvaluationSplit, str] = Field(
        default_factory=lambda: {
            "development": "debugging_instrumentation_and_future_method_development",
            "validation": "select_and_freeze_one_candidate_configuration",
            "test": "one_shot_held_out_measurement_only",
        }
    )
    test_set_one_shot_rule: str = (
        "The frozen test split may be executed once for the preregistered comparison."
    )
    no_tuning_rule: str = (
        "No prompts, profiles, thresholds, exclusions, or methods may be tuned from test results."
    )
    allowed_conclusion_language: tuple[str, ...] = (
        "observed difference",
        "bounded retrospective measurement",
        "insufficient evidence",
    )
    prohibited_claim_language: tuple[str, ...] = (
        "winner",
        "best",
        "superior",
        "calibrated",
    )


def manifest_hash(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return sha256_text(canonical_json(payload))

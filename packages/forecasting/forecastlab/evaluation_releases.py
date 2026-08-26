from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forecastlab.hashing import canonical_json, sha256_text

POLICY_VERSION = "private_v1_real_evaluation_release_v1"

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

    version: Literal["private_v1_real_evaluation_release_v1"] = POLICY_VERSION
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


PRIVATE_V1_REAL_EVALUATION_RELEASE_V1 = EvaluationReleasePolicy()


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
    """Human review and provenance metadata attached to one dataset question."""

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

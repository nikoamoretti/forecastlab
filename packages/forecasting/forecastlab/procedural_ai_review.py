from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, model_validator

from forecastlab.hashing import canonical_json, sha256_text

PROCEDURAL_AI_REVIEW_POLICY_VERSION = "private_v1_procedural_ai_review_v1"
QUESTION_REVIEW_RUBRIC_VERSION = "private_v1_question_review_rubric_v1"
OUTCOME_ADJUDICATION_RUBRIC_VERSION = (
    "private_v1_outcome_adjudication_rubric_v1"
)
RESERVE_ORDER_POLICY_VERSION = "private_v1_deterministic_reserve_order_v1"
RESERVE_ORDER_SEED = 20_260_831
PROCEDURAL_AI_RELEASE_LABEL = (
    "Procedurally AI-reviewed private-V1 evaluation release"
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"
ReviewStatus = Literal["pass", "fail", "uncertain"]


class ProceduralAIReviewRubric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    role: Literal["question_review", "outcome_adjudication"]
    required_finding_codes: tuple[str, ...]
    source_only_citations: Literal[True] = True
    uncertainty_fails_closed: Literal[True] = True
    conflicts_fail_closed: Literal[True] = True
    licensing_mismatch_fails_closed: Literal[True] = True
    rubric_mismatch_fails_closed: Literal[True] = True
    hidden_reasoning_forbidden: Literal[True] = True
    external_knowledge_citations_forbidden: Literal[True] = True


QUESTION_REVIEW_RUBRIC = ProceduralAIReviewRubric(
    version=QUESTION_REVIEW_RUBRIC_VERSION,
    role="question_review",
    required_finding_codes=(
        "binary_contract",
        "resolution_objectivity",
        "pre_outcome_origin",
        "authoritative_resolver",
        "event_family",
        "leakage_group",
        "licensing_metadata",
    ),
)
OUTCOME_ADJUDICATION_RUBRIC = ProceduralAIReviewRubric(
    version=OUTCOME_ADJUDICATION_RUBRIC_VERSION,
    role="outcome_adjudication",
    required_finding_codes=(
        "resolver_authority",
        "outcome_matches_contract",
        "temporal_order",
        "source_consistency",
    ),
)


def rubric_hash(rubric: ProceduralAIReviewRubric) -> str:
    return sha256_text(canonical_json(rubric.model_dump(mode="json")))


QUESTION_REVIEW_RUBRIC_HASH = rubric_hash(QUESTION_REVIEW_RUBRIC)
OUTCOME_ADJUDICATION_RUBRIC_HASH = rubric_hash(OUTCOME_ADJUDICATION_RUBRIC)


class CodexReviewRunIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["question_review", "outcome_adjudication"]
    model_provider: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=255)
    model_version: str = Field(min_length=1, max_length=128)
    tool_name: Literal["codex"] = "codex"
    tool_version: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=8, max_length=255)


class QuestionReviewSource(BaseModel):
    """Pre-outcome source supplied to the question-review role only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl
    title: str = Field(min_length=1, max_length=512)
    source_role: Literal[
        "pre_outcome_origin",
        "contract_terms",
        "authoritative_resolver_definition",
        "licensing_metadata",
    ]
    source_available_at: datetime
    temporal_basis: Literal["snapshot_date", "immutable_version"]
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    source_license_status: Literal[
        "public_domain", "licensed", "metadata_use_permitted", "unknown"
    ]
    source_use_basis: str = Field(min_length=1, max_length=1000)
    redistribution_allowed: bool


class OutcomeAdjudicationSource(BaseModel):
    """Post-resolution source supplied to the outcome role only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl
    title: str = Field(min_length=1, max_length=512)
    source_role: Literal["authoritative_resolution", "supporting_resolution"]
    source_available_at: datetime
    temporal_basis: Literal[
        "publication_date", "snapshot_date", "immutable_version"
    ]
    content_sha256: str = Field(pattern=SHA256_PATTERN)


class QuestionReviewManifest(BaseModel):
    """Outcome-blind input. This type cannot represent resolution results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_type: Literal["question_review_manifest"] = "question_review_manifest"
    evaluation_release_id: str
    evaluation_release_question_id: str
    evaluation_question_id: str
    normalized_question_hash: str = Field(pattern=SHA256_PATTERN)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    question: str = Field(min_length=1)
    yes_condition: str = Field(min_length=1)
    no_condition: str = Field(min_length=1)
    forecast_date: datetime
    resolution_date: datetime
    authoritative_resolver: str = Field(min_length=1)
    event_family_id: str = Field(min_length=1, max_length=128)
    leakage_group_id: str = Field(min_length=1, max_length=128)
    inclusion_status: Literal["included"] = "included"
    declared_source_license_status: Literal[
        "public_domain", "licensed", "metadata_use_permitted", "unknown"
    ]
    declared_source_use_basis: str = Field(min_length=1)
    declared_redistribution_allowed: bool
    sources: list[QuestionReviewSource] = Field(min_length=1)
    rubric_version: Literal["private_v1_question_review_rubric_v1"] = (
        QUESTION_REVIEW_RUBRIC_VERSION
    )
    rubric_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def unique_source_ids(self) -> QuestionReviewManifest:
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate_question_review_source_id")
        return self


class OutcomeAdjudicationManifest(BaseModel):
    """Review-output-blind sealed input for the outcome role only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_type: Literal["outcome_adjudication_manifest"] = (
        "outcome_adjudication_manifest"
    )
    evaluation_release_id: str
    evaluation_release_question_id: str
    evaluation_question_id: str
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    yes_condition: str = Field(min_length=1)
    no_condition: str = Field(min_length=1)
    resolution_date: datetime
    candidate_outcome: Literal[0, 1]
    outcome_known_at: datetime
    sources: list[OutcomeAdjudicationSource] = Field(min_length=1)
    rubric_version: Literal["private_v1_outcome_adjudication_rubric_v1"] = (
        OUTCOME_ADJUDICATION_RUBRIC_VERSION
    )
    rubric_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def unique_source_ids(self) -> OutcomeAdjudicationManifest:
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate_outcome_adjudication_source_id")
        return self


class SourceGroundedFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    status: ReviewStatus
    conclusion: str = Field(min_length=1, max_length=1200)
    source_ids: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_citations(self) -> SourceGroundedFinding:
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("duplicate_source_citation")
        return self


class QuestionReviewOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    output_type: Literal["question_review_output"] = "question_review_output"
    decision: Literal["accepted", "rejected", "uncertain"]
    findings: list[SourceGroundedFinding] = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list, max_length=32)
    conflicts: list[str] = Field(default_factory=list, max_length=32)


class OutcomeAdjudicationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    output_type: Literal["outcome_adjudication_output"] = (
        "outcome_adjudication_output"
    )
    decision: Literal["confirmed", "rejected", "uncertain", "conflict"]
    adjudicated_outcome: Literal[0, 1] | None
    findings: list[SourceGroundedFinding] = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list, max_length=32)
    conflicts: list[str] = Field(default_factory=list, max_length=32)


class QuestionReviewArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_type: Literal["question_review"] = "question_review"
    policy_version: Literal["private_v1_procedural_ai_review_v1"] = (
        PROCEDURAL_AI_REVIEW_POLICY_VERSION
    )
    rubric_version: Literal["private_v1_question_review_rubric_v1"] = (
        QUESTION_REVIEW_RUBRIC_VERSION
    )
    rubric_hash: str = Field(pattern=SHA256_PATTERN)
    run_identity: CodexReviewRunIdentity
    started_at: datetime
    completed_at: datetime
    input_manifest: QuestionReviewManifest
    input_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    output: QuestionReviewOutput
    output_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def role_matches(self) -> QuestionReviewArtifact:
        if self.run_identity.role != "question_review":
            raise ValueError("question_review_role_mismatch")
        return self


class OutcomeAdjudicationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_type: Literal["outcome_adjudication"] = "outcome_adjudication"
    policy_version: Literal["private_v1_procedural_ai_review_v1"] = (
        PROCEDURAL_AI_REVIEW_POLICY_VERSION
    )
    rubric_version: Literal["private_v1_outcome_adjudication_rubric_v1"] = (
        OUTCOME_ADJUDICATION_RUBRIC_VERSION
    )
    rubric_hash: str = Field(pattern=SHA256_PATTERN)
    run_identity: CodexReviewRunIdentity
    started_at: datetime
    completed_at: datetime
    input_manifest: OutcomeAdjudicationManifest
    input_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    output: OutcomeAdjudicationOutput
    output_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def role_matches(self) -> OutcomeAdjudicationArtifact:
        if self.run_identity.role != "outcome_adjudication":
            raise ValueError("outcome_adjudication_role_mismatch")
        return self


ProceduralAIReviewArtifact = QuestionReviewArtifact | OutcomeAdjudicationArtifact


class ProceduralAIArtifactIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    evaluation_release_question_id: str
    evaluation_question_id: str
    artifact_type: Literal["question_review", "outcome_adjudication"]
    run_id: str
    rubric_version: str
    rubric_hash: str = Field(pattern=SHA256_PATTERN)
    input_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    output_hash: str = Field(pattern=SHA256_PATTERN)
    gate_status: Literal["passed", "failed"]
    completed_at: datetime


class ProceduralAIReviewGateManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    label: Literal[
        "Procedurally AI-reviewed private-V1 evaluation release"
    ] = PROCEDURAL_AI_RELEASE_LABEL
    policy_version: Literal["private_v1_procedural_ai_review_v1"] = (
        PROCEDURAL_AI_REVIEW_POLICY_VERSION
    )
    question_review_rubric_version: str = QUESTION_REVIEW_RUBRIC_VERSION
    question_review_rubric_hash: str = QUESTION_REVIEW_RUBRIC_HASH
    outcome_adjudication_rubric_version: str = OUTCOME_ADJUDICATION_RUBRIC_VERSION
    outcome_adjudication_rubric_hash: str = OUTCOME_ADJUDICATION_RUBRIC_HASH
    reserve_order_policy_version: str = RESERVE_ORDER_POLICY_VERSION
    reserve_order_hash: str = Field(pattern=SHA256_PATTERN)
    artifacts: list[ProceduralAIArtifactIdentity]
    human_review_claimed: Literal[False] = False
    independent_validation_claimed: Literal[False] = False
    publication_grade_claimed: Literal[False] = False
    forecasting_quality_claimed: Literal[False] = False
    calibration_claimed: Literal[False] = False


QUESTION_REVIEW_FORBIDDEN_KEYS = frozenset(
    {
        "outcome",
        "candidate_outcome",
        "adjudicated_outcome",
        "outcome_known_at",
        "resolution_source",
        "post_resolution_source",
        "adjudication_notes",
        "adjudication_record_hash",
        "brier_score",
        "log_loss",
        "forecast_probability",
        "score",
    }
)
OUTCOME_ADJUDICATION_FORBIDDEN_KEYS = frozenset(
    {
        "question",
        "question_text",
        "normalized_question_hash",
        "event_family_id",
        "leakage_group_id",
        "split",
        "reserve_order",
        "reserve_rank",
        "question_review_artifact",
        "question_review_output",
        "question_reviewer_id",
        "review_completed_at",
        "forecast_probability",
        "brier_score",
        "log_loss",
        "score",
    }
)


def _forbidden_paths(value: Any, forbidden: frozenset[str], path: str = "") -> list[str]:
    reasons: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if key.casefold() in forbidden:
                reasons.append(f"disallowed_data:{child_path}")
            reasons.extend(_forbidden_paths(child, forbidden, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reasons.extend(_forbidden_paths(child, forbidden, f"{path}[{index}]"))
    return reasons


def review_artifact_payload_errors(
    payload: dict[str, Any],
    *,
    artifact_type: Literal["question_review", "outcome_adjudication"],
) -> list[str]:
    """Validate required shape and role-specific data exclusion before persistence."""

    reasons: list[str] = []
    if payload.get("artifact_type") != artifact_type:
        reasons.append("artifact_type_mismatch")
    forbidden = (
        QUESTION_REVIEW_FORBIDDEN_KEYS
        if artifact_type == "question_review"
        else OUTCOME_ADJUDICATION_FORBIDDEN_KEYS
    )
    reasons.extend(_forbidden_paths(payload, forbidden))
    artifact_model = (
        QuestionReviewArtifact
        if artifact_type == "question_review"
        else OutcomeAdjudicationArtifact
    )
    try:
        artifact_model.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors(include_url=False, include_context=False):
            location = ".".join(str(item) for item in error["loc"])
            reasons.append(f"required_or_schema_invalid:{location}:{error['type']}")
    return list(dict.fromkeys(reasons))


def parse_review_artifact(
    payload: dict[str, Any],
    *,
    artifact_type: Literal["question_review", "outcome_adjudication"],
) -> ProceduralAIReviewArtifact:
    reasons = review_artifact_payload_errors(payload, artifact_type=artifact_type)
    if reasons:
        raise ValueError(";".join(reasons))
    if artifact_type == "question_review":
        return QuestionReviewArtifact.model_validate(payload)
    return OutcomeAdjudicationArtifact.model_validate(payload)


def artifact_integrity_reasons(artifact: ProceduralAIReviewArtifact) -> list[str]:
    reasons: list[str] = []
    expected_rubric = (
        QUESTION_REVIEW_RUBRIC
        if artifact.artifact_type == "question_review"
        else OUTCOME_ADJUDICATION_RUBRIC
    )
    expected_rubric_hash = rubric_hash(expected_rubric)
    if artifact.rubric_version != expected_rubric.version:
        reasons.append("rubric_version_mismatch")
    if artifact.rubric_hash != expected_rubric_hash:
        reasons.append("rubric_hash_mismatch")
    if artifact.input_manifest.rubric_version != expected_rubric.version:
        reasons.append("input_manifest_rubric_version_mismatch")
    if artifact.input_manifest.rubric_hash != expected_rubric_hash:
        reasons.append("input_manifest_rubric_hash_mismatch")
    expected_input_hash = sha256_text(
        canonical_json(artifact.input_manifest.model_dump(mode="json"))
    )
    expected_output_hash = sha256_text(
        canonical_json(artifact.output.model_dump(mode="json"))
    )
    if artifact.input_manifest_hash != expected_input_hash:
        reasons.append("input_manifest_hash_mismatch")
    if artifact.output_hash != expected_output_hash:
        reasons.append("output_hash_mismatch")
    if artifact.completed_at < artifact.started_at:
        reasons.append("artifact_completion_precedes_start")

    allowed_source_ids = {item.source_id for item in artifact.input_manifest.sources}
    findings_by_code = {item.code: item for item in artifact.output.findings}
    if len(findings_by_code) != len(artifact.output.findings):
        reasons.append("duplicate_finding_code")
    for finding in artifact.output.findings:
        unknown = sorted(set(finding.source_ids) - allowed_source_ids)
        if unknown:
            reasons.append(
                f"citation_not_in_input_manifest:{finding.code}:{','.join(unknown)}"
            )
    for code in expected_rubric.required_finding_codes:
        finding = findings_by_code.get(code)
        if finding is None:
            reasons.append(f"required_finding_missing:{code}")
        elif finding.status != "pass":
            reasons.append(f"required_finding_not_passed:{code}:{finding.status}")
    if artifact.output.uncertainties:
        reasons.append("artifact_uncertainty_present")
    if artifact.output.conflicts:
        reasons.append("artifact_conflict_present")
    if isinstance(artifact, QuestionReviewArtifact):
        if artifact.output.decision != "accepted":
            reasons.append(f"question_review_not_accepted:{artifact.output.decision}")
        if artifact.input_manifest.declared_source_license_status == "unknown":
            reasons.append("question_review_license_unknown")
    else:
        if artifact.output.decision != "confirmed":
            reasons.append(
                f"outcome_adjudication_not_confirmed:{artifact.output.decision}"
            )
        if artifact.output.adjudicated_outcome is None:
            reasons.append("adjudicated_outcome_missing")
        elif artifact.output.adjudicated_outcome != artifact.input_manifest.candidate_outcome:
            reasons.append("adjudicated_outcome_conflicts_with_manifest")
    return list(dict.fromkeys(reasons))


def artifact_input_hash(artifact: ProceduralAIReviewArtifact) -> str:
    return sha256_text(canonical_json(artifact.input_manifest.model_dump(mode="json")))


def artifact_output_hash(artifact: ProceduralAIReviewArtifact) -> str:
    return sha256_text(canonical_json(artifact.output.model_dump(mode="json")))

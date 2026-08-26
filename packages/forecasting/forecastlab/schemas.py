from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forecastlab.timeutil import as_utc

QuestionType = Literal["binary"]
RunMode = Literal["live", "backtest", "demo"]
TrackType = Literal["base_rate", "current_evidence", "skeptic", "single_agent"]
ForecastExecutionStrategy = Literal["legacy_tracks", "graph_nodes", "single_model"]
JobStatus = Literal["pending", "running", "completed", "failed"]
SourceClass = Literal["primary", "secondary"]
EvidenceClaimSourceClass = Literal["primary", "secondary", "unknown_legacy"]
EvidenceExtractionMethod = Literal[
    "structured_full_document",
    "structured_smaller_chunk",
    "document_fallback",
    "mock_structured",
    "unknown_legacy",
]
WatchKind = Literal["html", "json"]
ForecastContractStatus = Literal["draft", "approved", "superseded"]
ForecastGraphStatus = Literal["draft", "approved", "superseded"]
ForecastNodeType = Literal[
    "base_rate",
    "trend",
    "driver",
    "dependency",
    "scenario",
    "adversarial",
    "resolver",
]
ForecastNodeStatus = Literal["pending", "completed", "failed"]
EvidenceStance = Literal["supports", "refutes"]
TemporalBasis = Literal["publication_date", "snapshot_date", "retrieval_date"]


class ResolutionContract(BaseModel):
    exact_yes: str
    exact_no: str
    resolution_deadline: datetime
    authoritative_source: str
    fallback_sources: list[str] = Field(default_factory=list)
    geography: str | None = None
    units: str | None = None
    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""


class ForecastContract(BaseModel):
    id: str
    question_id: str
    version: int = Field(default=1, ge=1)
    created_at: datetime
    created_by: str

    original_question: str
    normalized_question: str

    yes_condition: str = ""
    no_condition: str = ""

    resolution_date: datetime | None = None
    authoritative_source: str = ""
    fallback_sources: list[str] = Field(default_factory=list)
    resolution_method: str = ""

    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""

    forecast_type: str = "binary"
    geography: str | None = None
    units: str | None = None
    domain: str | None = None

    initial_reference_class: str = ""
    suggested_drivers: list[str] = Field(default_factory=list)
    known_dependencies: list[str] = Field(default_factory=list)

    status: ForecastContractStatus = "draft"

    def approval_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.yes_condition.strip():
            errors.append("yes_condition_required")
        if not self.no_condition.strip():
            errors.append("no_condition_required")
        if self.resolution_date is None:
            errors.append("resolution_date_required")
        if not self.authoritative_source.strip():
            errors.append("authoritative_source_required")
        if not self.resolution_method.strip():
            errors.append("resolution_method_required")
        if self.yes_condition.strip().casefold() == self.no_condition.strip().casefold() and self.yes_condition.strip():
            errors.append("outcome_conditions_must_differ")
        return errors

    def to_resolution_contract(self) -> ResolutionContract:
        if self.resolution_date is None:
            raise ValueError("resolution_date_required")
        return ResolutionContract(
            exact_yes=self.yes_condition,
            exact_no=self.no_condition,
            resolution_deadline=self.resolution_date,
            authoritative_source=self.authoritative_source,
            fallback_sources=self.fallback_sources,
            geography=self.geography,
            units=self.units,
            ambiguity_notes=self.ambiguity_notes,
            cancellation_conditions=self.cancellation_conditions,
            resolver_risk_notes=self.resolver_risk_notes,
        )


class ForecastNode(BaseModel):
    id: str
    graph_id: str
    parent_node_id: str | None = None
    question: str
    node_type: ForecastNodeType
    importance_weight: float = Field(ge=0.0, le=1.0)
    dependencies: list[str] = Field(default_factory=list)
    preferred_sources: list[str] = Field(default_factory=list)
    required_output_type: str
    status: ForecastNodeStatus = "pending"


class ForecastGraphGenerationAudit(BaseModel):
    """Sanitized graph-generation diagnostics safe for durable audit storage."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    schema_name: str = "forecast_graph"
    transport: str = "canonical_v1"
    transport_character_count: int | None = Field(default=None, ge=0)
    transport_max_characters: int | None = Field(default=None, gt=0)
    provider_request_id: str | None = None
    requested_max_output_tokens: int = Field(gt=0)
    requested_max_completion_tokens: int | None = Field(default=None, gt=0)
    requested_max_visible_output_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: str | None = None
    verbosity: str | None = None
    finish_reason: str | None = None
    refusal_present: bool = False
    refusal_category: str | None = None
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    visible_output_tokens: int | None = Field(default=None, ge=0)
    token_split_available: bool | None = None
    token_split_interpretation: str | None = None
    content_character_count: int = Field(default=0, ge=0)
    json_parsing_succeeded: bool
    schema_validation_succeeded: bool
    strict_schema_validation_succeeded: bool | None = None
    schema_validation_errors: list[dict[str, str]] = Field(default_factory=list)
    domain_validation_succeeded: bool
    domain_validation_errors: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    prompt_version: str
    generated_at: datetime


class ForecastGraph(BaseModel):
    id: str
    contract_id: str
    version: int = Field(default=1, ge=1)
    status: ForecastGraphStatus = "draft"
    created_at: datetime
    generation_model: str
    root_question: str
    nodes: list[ForecastNode] = Field(default_factory=list)
    generation_audit: ForecastGraphGenerationAudit | None = None


class ForecastGraphGenerationResult(BaseModel):
    graph: ForecastGraph
    generation_audit: ForecastGraphGenerationAudit


class SubquestionPlan(BaseModel):
    text: str
    purpose: str
    preferred_source_types: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    expected_output: str
    relationship_to_forecast: str


class ResearchPlan(BaseModel):
    objective: str
    approach: str
    subquestions: list[SubquestionPlan]


class Driver(BaseModel):
    factor: str
    direction: Literal["up", "down", "unclear"]
    importance: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    inference: bool = False


class TrackForecastOutput(BaseModel):
    probability: float = Field(ge=0.01, le=0.99)
    prior_probability: float | None = Field(default=None, ge=0.01, le=0.99)
    key_drivers: list[Driver] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    unresolved_uncertainties: list[str] = Field(default_factory=list)
    resolver_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_quality: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning_summary: str

    @field_validator("key_drivers")
    @classmethod
    def factual_drivers_need_evidence(cls, drivers: list[Driver]) -> list[Driver]:
        cleaned: list[Driver] = []
        for driver in drivers:
            if not driver.inference and not driver.evidence_ids:
                continue
            cleaned.append(driver)
        return cleaned


class SingleModelForecastOutput(BaseModel):
    """One direct forecast authored from an approved contract and evidence packet."""

    model_config = ConfigDict(extra="forbid")

    probability: float = Field(ge=0.01, le=0.99)
    reasoning: str = Field(min_length=1)
    uncertainty: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reasoning_required")
        return normalized

    @field_validator("uncertainty", "evidence_ids")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list_items_must_not_be_blank")
        return list(dict.fromkeys(normalized))


class ModelUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""
    provider: str = ""
    request_id: str | None = None
    cost_source: str = "estimated"


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    published_at: datetime | None = None
    published_at_source: str | None = None
    score: float = 0.0
    source_class: SourceClass = "secondary"


class FetchedDocument(BaseModel):
    url: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    source_available_at: datetime
    temporal_basis: TemporalBasis
    publication_date_source: str | None = None
    publication_date_verified: bool = False
    publication_date_hint: datetime | None = None
    publication_date_hint_source: str | None = None
    modified_at: datetime | None = None
    modified_date_source: str | None = None
    text: str
    content_hash: str
    snapshot_url: str | None = None
    snapshot_at: datetime | None = None
    requested_snapshot_url: str | None = None
    requested_snapshot_at: datetime | None = None
    final_snapshot_url: str | None = None
    final_snapshot_at: datetime | None = None
    archived_original_url: str | None = None
    snapshot_verification_status: str | None = None
    status_code: int = 200
    rejected: bool = False
    rejection_reason: str | None = None
    as_of_eligible: bool = True
    published_at_unknown: bool = False


class EvidenceClaim(BaseModel):
    id: str
    evidence_item_id: str
    forecast_node_id: str

    claim: str
    excerpt: str

    source_url: str
    source_title: str
    publisher: str
    publication_date: datetime | None = None
    publication_date_source: str | None = None
    publication_date_verified: bool = False
    retrieval_date: datetime
    source_available_at: datetime
    temporal_basis: TemporalBasis

    supports_or_refutes: EvidenceStance
    confidence: float = Field(ge=0.0, le=1.0)
    source_quality: float = Field(ge=0.0, le=1.0)
    primary_source: bool

    as_of_eligible: bool
    cutoff_verified: bool
    source_class: EvidenceClaimSourceClass = "unknown_legacy"
    extraction_method: EvidenceExtractionMethod = "unknown_legacy"
    source_host: str = ""

    def forecasting_errors(
        self,
        *,
        mode: RunMode | None = None,
        cutoff: datetime | None = None,
        run_completion_time: datetime | None = None,
    ) -> list[str]:
        errors: list[str] = []
        required_text = {
            "id_required": self.id,
            "evidence_item_id_required": self.evidence_item_id,
            "forecast_node_id_required": self.forecast_node_id,
            "claim_required": self.claim,
            "excerpt_required": self.excerpt,
            "source_url_required": self.source_url,
            "source_title_required": self.source_title,
            "publisher_required": self.publisher,
        }
        errors.extend(reason for reason, value in required_text.items() if not value.strip())
        if not self.as_of_eligible:
            errors.append("claim_not_as_of_eligible")
        if not self.cutoff_verified:
            errors.append("claim_cutoff_not_verified")
        if self.publication_date is None and self.publication_date_verified:
            errors.append("verified_publication_date_required")
        if self.publication_date is not None:
            if as_utc(self.publication_date) > as_utc(self.retrieval_date):
                errors.append("publication_after_retrieval")
            if cutoff is not None and as_utc(self.publication_date) > as_utc(cutoff):
                errors.append("claim_after_cutoff")
        if self.temporal_basis == "publication_date":
            if self.publication_date is None:
                errors.append("publication_basis_date_required")
            elif as_utc(self.source_available_at) != as_utc(self.publication_date):
                errors.append("publication_basis_timestamp_mismatch")
        if self.temporal_basis == "retrieval_date" and as_utc(self.source_available_at) != as_utc(
            self.retrieval_date
        ):
            errors.append("retrieval_basis_timestamp_mismatch")

        effective_mode = mode or ("backtest" if cutoff is not None else "live")
        if effective_mode == "backtest":
            if cutoff is None:
                errors.append("historical_cutoff_required")
            else:
                if as_utc(self.source_available_at) > as_utc(cutoff):
                    errors.append("claim_after_cutoff")
            if self.temporal_basis == "retrieval_date":
                errors.append("historical_retrieval_basis_forbidden")
        else:
            completion = as_utc(run_completion_time or self.retrieval_date)
            if as_utc(self.source_available_at) > completion:
                errors.append("source_available_after_run_completion")
        return errors


class ForecastNodeOutput(BaseModel):
    """Strict model-authored probability and audit trail for one graph node."""

    model_config = ConfigDict(extra="forbid")

    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    opposing_claim_ids: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reasoning_required")
        return normalized

    @field_validator("uncertainty_notes")
    @classmethod
    def validate_uncertainty_notes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("uncertainty_notes_must_not_be_blank")
        return list(dict.fromkeys(normalized))


class NodeForecast(BaseModel):
    node_id: str
    probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    opposing_claim_ids: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    model_used: str = Field(min_length=1)
    # Retained for existing reports and stored rows. It is derived from cited-claim
    # confidence, not authored by the model or used by graph aggregation.
    uncertainty: float = Field(ge=0.0, le=1.0)


class ForecastNodeRun(NodeForecast):
    id: str
    run_id: str
    raw_importance_weight: float = Field(default=0.0, ge=0.0)
    dependency_factor: float = Field(default=1.0, ge=0.0, le=1.0)
    normalized_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    probability_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime


class ForecastNodeContribution(BaseModel):
    """One deterministic weighted-log-odds term in a graph aggregation."""

    node_id: str = Field(min_length=1)
    node_question: str = Field(min_length=1)
    input_probability: float = Field(gt=0.0, lt=1.0)
    raw_importance_weight: float = Field(ge=0.0)
    normalized_weight: float = Field(ge=0.0, le=1.0)
    log_odds: float
    weighted_log_odds_contribution: float


class ForecastAggregation(BaseModel):
    """Persistable audit record produced by deterministic graph aggregation."""

    id: str = Field(min_length=1)
    forecast_run_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    final_probability: float = Field(ge=0.0, le=1.0)
    calculation_trace: list[dict[str, Any]] = Field(default_factory=list)
    node_contributions: list[ForecastNodeContribution] = Field(default_factory=list)
    created_at: datetime


class ForecastProfile(BaseModel):
    id: str
    version: int = 1
    label: str
    description: str
    execution_strategy: ForecastExecutionStrategy = "legacy_tracks"
    graph_generation_enabled: bool = False
    evidence_claims_enabled: bool = False
    node_forecasting_enabled: bool = False
    graph_aggregation_enabled: bool = False
    tracks: list[TrackType]
    subquestions_per_track: int = 4
    search_results_per_subquestion: int = 3
    fetches_per_subquestion: int = 2
    aggregation_method: str = "equal_weight_logit_shrinkage"
    shrinkage: float = 0.10
    max_model_calls: int = 40
    max_search_calls: int = 36
    max_fetched_documents: int = 24
    max_tokens: int = 200_000
    max_output_tokens_per_call: int = 4096
    graph_generation_max_completion_tokens: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_max_visible_output_tokens: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_reasoning_effort: Literal[
        "none",
        "minimal",
        "low",
        "medium",
        "high",
    ] | None = None
    graph_generation_verbosity: Literal["low", "medium", "high"] | None = None
    graph_generation_transport: Literal["compact_indexed_v1"] | None = None
    graph_generation_transport_max_characters: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_node_question_max_characters: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_local_id_max_characters: int | None = Field(
        default=None,
        ge=2,
    )
    graph_generation_max_dependencies_per_node: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_max_preferred_sources_per_node: int | None = Field(
        default=None,
        ge=1,
    )
    graph_generation_preferred_source_max_characters: int | None = Field(
        default=None,
        ge=1,
    )
    max_candidate_fetch_attempts_per_node: int | None = Field(
        default=None,
        ge=1,
    )
    search_candidate_pool_per_node: int | None = Field(default=None, ge=1)
    prefer_distinct_candidate_hosts: bool = False
    evidence_sufficiency_policy: Literal["private_v1_evidence_gate_v1"] | None = None
    material_node_policy: Literal[
        "none",
        "private_v1_material_node_gate_v1",
    ] = "none"
    max_estimated_cost_usd: float = 5.0
    max_wall_clock_seconds: int = 300
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph_execution_limits(self) -> ForecastProfile:
        completion = self.graph_generation_max_completion_tokens
        visible = self.graph_generation_max_visible_output_tokens
        if completion is not None and visible is not None and visible > completion:
            raise ValueError(
                "graph_generation_visible_output_exceeds_completion_envelope"
            )
        attempts = self.max_candidate_fetch_attempts_per_node
        if attempts is not None and attempts < self.fetches_per_subquestion:
            raise ValueError(
                "candidate_fetch_attempts_below_successful_document_target"
            )
        pool = self.search_candidate_pool_per_node
        effective_attempts = attempts or self.fetches_per_subquestion
        if pool is not None and pool < effective_attempts:
            raise ValueError("candidate_pool_below_fetch_attempt_ceiling")
        transport_fields = {
            "graph_generation_transport_max_characters": (
                self.graph_generation_transport_max_characters
            ),
            "graph_generation_node_question_max_characters": (
                self.graph_generation_node_question_max_characters
            ),
            "graph_generation_local_id_max_characters": (
                self.graph_generation_local_id_max_characters
            ),
            "graph_generation_max_dependencies_per_node": (
                self.graph_generation_max_dependencies_per_node
            ),
            "graph_generation_max_preferred_sources_per_node": (
                self.graph_generation_max_preferred_sources_per_node
            ),
            "graph_generation_preferred_source_max_characters": (
                self.graph_generation_preferred_source_max_characters
            ),
        }
        if self.graph_generation_transport is not None:
            missing = [name for name, value in transport_fields.items() if value is None]
            if missing:
                raise ValueError(
                    "compact_graph_transport_limits_required:"
                    + ",".join(sorted(missing))
                )
        elif any(value is not None for value in transport_fields.values()):
            raise ValueError("graph_transport_required_for_transport_limits")
        if self.prefer_distinct_candidate_hosts and pool is None:
            raise ValueError("candidate_pool_required_for_host_diversity")
        return self


class BudgetState(BaseModel):
    model_calls: int = 0
    search_calls: int = 0
    fetches: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model_cost_usd: float = 0.0
    search_cost_usd: float = 0.0
    failed_attempt_cost_usd: float = 0.0
    reserved_tokens: int = 0
    reserved_cost_usd: float = 0.0
    cost_is_estimated: bool = False
    cost_label: str = "estimated"
    provider_request_count: int = 0
    started_monotonic: float = 0.0
    stopped: bool = False
    stop_reason: str | None = None
    stop_stage: str | None = None


class ProviderConfig(BaseModel):
    model_provider: str = "mock"
    model_base_url: str | None = None
    model_name: str = "mock-forecast-v1"
    model_timeout_seconds: float = 60.0
    search_provider: str = "mock"
    max_cost_usd: float = 5.0
    allow_local_fixtures: bool = True


class SettingsPublic(BaseModel):
    model_provider: str
    model_base_url: str | None
    model_name: str
    model_api_key_set: bool
    search_provider: str
    search_api_key_set: bool
    max_cost_usd: float
    model_timeout_seconds: float
    mode: str


class SettingsUpdate(BaseModel):
    model_provider: str | None = None
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: str | None = None
    search_provider: str | None = None
    search_api_key: str | None = None
    max_cost_usd: float | None = None
    model_timeout_seconds: float | None = None


class BenchmarkImportRow(BaseModel):
    question: str
    forecast_date: datetime
    resolution_date: datetime
    outcome: int
    resolution_source: str
    category: str
    provenance: str = "user_import"
    is_synthetic: bool = False
    exact_yes: str | None = None
    exact_no: str | None = None
    resolution_deadline: datetime | None = None
    authoritative_source: str | None = None
    fallback_sources: list[str] = Field(default_factory=list)
    geography: str | None = None
    units: str | None = None
    ambiguity_notes: str = ""
    cancellation_conditions: str = ""
    resolver_risk_notes: str = ""

    @field_validator("outcome")
    @classmethod
    def binary_outcome(cls, value: int) -> int:
        if value not in (0, 1):
            raise ValueError("outcome must be 0 or 1")
        return value


PublicSettings = SettingsPublic
SettingsPatch = SettingsUpdate

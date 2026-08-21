from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

QuestionType = Literal["binary"]
RunMode = Literal["live", "backtest", "demo"]
TrackType = Literal["base_rate", "current_evidence", "skeptic", "single_agent"]
JobStatus = Literal["pending", "running", "completed", "failed"]
SourceClass = Literal["primary", "secondary"]
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


class ForecastGraph(BaseModel):
    id: str
    contract_id: str
    version: int = Field(default=1, ge=1)
    status: ForecastGraphStatus = "draft"
    created_at: datetime
    generation_model: str
    root_question: str
    nodes: list[ForecastNode] = Field(default_factory=list)


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
    score: float = 0.0
    source_class: SourceClass = "secondary"


class FetchedDocument(BaseModel):
    url: str
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
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


class ForecastProfile(BaseModel):
    id: str
    version: int = 1
    label: str
    description: str
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
    max_estimated_cost_usd: float = 5.0
    max_wall_clock_seconds: int = 300
    prompt_versions: dict[str, str] = Field(default_factory=dict)


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

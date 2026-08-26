from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    inspect,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column, relationship

from forecastlab.timeutil import utcnow
from forecastlab_api.db import Base


def _now() -> datetime:
    return utcnow()


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str] = mapped_column(String(32), default="binary")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    forecast_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_mode: Mapped[str] = mapped_column(String(32), default="demo")
    requested_profile_id: Mapped[str] = mapped_column(String(64), default="three_track_ensemble")
    requested_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False)

    contract: Mapped[ResolutionContractRow | None] = relationship(back_populates="question", uselist=False)
    forecast_contracts: Mapped[list[ForecastContractRow]] = relationship(
        back_populates="question",
        order_by="ForecastContractRow.version",
    )
    runs: Mapped[list[ForecastRun]] = relationship(back_populates="question")
    versions: Mapped[list[ForecastVersion]] = relationship(back_populates="question")
    watches: Mapped[list[Watch]] = relationship(back_populates="question")


class ResolutionContractRow(Base):
    __tablename__ = "resolution_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    exact_yes: Mapped[str] = mapped_column(Text)
    exact_no: Mapped[str] = mapped_column(Text)
    resolution_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authoritative_source: Mapped[str] = mapped_column(Text)
    fallback_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    geography: Mapped[str | None] = mapped_column(String(128), nullable=True)
    units: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ambiguity_notes: Mapped[str] = mapped_column(Text, default="")
    cancellation_conditions: Mapped[str] = mapped_column(Text, default="")
    resolver_risk_notes: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    question: Mapped[Question] = relationship(back_populates="contract")


class ForecastContractRow(Base):
    __tablename__ = "forecast_contracts"
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_forecast_contract_question_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_by: Mapped[str] = mapped_column(String(128), default="user")

    original_question: Mapped[str] = mapped_column(Text)
    normalized_question: Mapped[str] = mapped_column(Text)

    yes_condition: Mapped[str] = mapped_column(Text, default="")
    no_condition: Mapped[str] = mapped_column(Text, default="")

    resolution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authoritative_source: Mapped[str] = mapped_column(Text, default="")
    fallback_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    resolution_method: Mapped[str] = mapped_column(Text, default="")

    ambiguity_notes: Mapped[str] = mapped_column(Text, default="")
    cancellation_conditions: Mapped[str] = mapped_column(Text, default="")
    resolver_risk_notes: Mapped[str] = mapped_column(Text, default="")

    forecast_type: Mapped[str] = mapped_column(String(32), default="binary")
    geography: Mapped[str | None] = mapped_column(String(128), nullable=True)
    units: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)

    initial_reference_class: Mapped[str] = mapped_column(Text, default="")
    suggested_drivers_json: Mapped[str] = mapped_column(Text, default="[]")
    known_dependencies_json: Mapped[str] = mapped_column(Text, default="[]")

    status: Mapped[str] = mapped_column(String(32), default="draft")

    question: Mapped[Question] = relationship(back_populates="forecast_contracts")
    forecast_graphs: Mapped[list[ForecastGraphRow]] = relationship(
        back_populates="contract",
        order_by="ForecastGraphRow.version",
    )


class ForecastGraphRow(Base):
    __tablename__ = "forecast_graphs"
    __table_args__ = (UniqueConstraint("contract_id", "version", name="uq_forecast_graph_contract_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("forecast_contracts.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    generation_model: Mapped[str] = mapped_column(String(256))
    root_question: Mapped[str] = mapped_column(Text)
    generation_audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped[ForecastContractRow] = relationship(back_populates="forecast_graphs")
    nodes: Mapped[list[ForecastNodeRow]] = relationship(back_populates="graph")


class ForecastNodeRow(Base):
    __tablename__ = "forecast_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_id: Mapped[str] = mapped_column(ForeignKey("forecast_graphs.id"))
    parent_node_id: Mapped[str | None] = mapped_column(ForeignKey("forecast_nodes.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    node_type: Mapped[str] = mapped_column(String(32))
    importance_weight: Mapped[float] = mapped_column(Float)
    dependencies_json: Mapped[str] = mapped_column(Text, default="[]")
    preferred_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    required_output_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")

    graph: Mapped[ForecastGraphRow] = relationship(back_populates="nodes")
    evidence_claims: Mapped[list[EvidenceClaimRow]] = relationship(back_populates="forecast_node")
    runs: Mapped[list[ForecastNodeRunRow]] = relationship(back_populates="node")
    execution_failures: Mapped[list[GraphExecutionFailureRow]] = relationship(back_populates="node")


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (UniqueConstraint("benchmark_task_id", name="uq_forecast_run_benchmark_task"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    profile_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # total lifetime cost, including failed attempts and search
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    search_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    failed_attempt_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_count: Mapped[int] = mapped_column(Integer, default=0)
    run_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_source: Mapped[str] = mapped_column(String(32), default="estimated")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_versions_json: Mapped[str] = mapped_column(Text, default="{}")
    budget_json: Mapped[str] = mapped_column(Text, default="{}")
    aggregation_json: Mapped[str] = mapped_column(Text, default="{}")
    disagreement_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    progress_stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress_message: Mapped[str] = mapped_column(Text, default="Waiting for worker")
    execution_context_json: Mapped[str] = mapped_column(Text, default="{}")
    configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_policy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixture_evidence_used: Mapped[bool] = mapped_column(Boolean, default=False)
    code_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synthetic_fixture_run: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark_task_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_tasks.id"), nullable=True)

    question: Mapped[Question] = relationship(back_populates="runs")
    tracks: Mapped[list[ResearchTrack]] = relationship(back_populates="run")
    evidence: Mapped[list[EvidenceItem]] = relationship(back_populates="run")
    node_runs: Mapped[list[ForecastNodeRunRow]] = relationship(back_populates="forecast_run")
    aggregation: Mapped[ForecastAggregationRow | None] = relationship(
        back_populates="forecast_run",
        uselist=False,
    )
    evidence_sufficiency_assessment: Mapped[EvidenceSufficiencyAssessmentRow | None] = relationship(
        back_populates="forecast_run",
        uselist=False,
    )
    material_node_coverage_assessment: Mapped[MaterialNodeCoverageAssessmentRow | None] = relationship(
        back_populates="forecast_run",
        uselist=False,
    )
    scenario_synthesis: Mapped[ScenarioSynthesisRow | None] = relationship(
        back_populates="forecast_run",
        uselist=False,
    )
    graph_execution_failures: Mapped[list[GraphExecutionFailureRow]] = relationship(
        back_populates="forecast_run"
    )
    research_plan: Mapped[ResearchPlanRow | None] = relationship(
        back_populates="forecast_run",
        uselist=False,
    )
    versions: Mapped[list[ForecastVersion]] = relationship(back_populates="run")


class ResearchTrack(Base):
    __tablename__ = "research_tracks"
    __table_args__ = (UniqueConstraint("run_id", "track_type", name="uq_track_run_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    track_type: Mapped[str] = mapped_column(String(32))
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_drivers_json: Mapped[str] = mapped_column(Text, default="[]")
    counterarguments_json: Mapped[str] = mapped_column(Text, default="[]")
    unresolved_json: Mapped[str] = mapped_column(Text, default="[]")
    resolver_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    independent: Mapped[bool] = mapped_column(Boolean, default=True)

    run: Mapped[ForecastRun] = relationship(back_populates="tracks")
    subquestions: Mapped[list[Subquestion]] = relationship(back_populates="track")


class Subquestion(Base):
    __tablename__ = "subquestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("research_tracks.id"))
    text: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, default="")
    preferred_source_types_json: Mapped[str] = mapped_column(Text, default="[]")
    search_queries_json: Mapped[str] = mapped_column(Text, default="[]")
    expected_output: Mapped[str] = mapped_column(Text, default="")
    relationship_to_forecast: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    track: Mapped[ResearchTrack] = relationship(back_populates="subquestions")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    track_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subquestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str | None] = mapped_column(String(256), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    temporal_basis: Mapped[str] = mapped_column(String(32), default="retrieval_date")
    publication_date_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_date_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    publication_date_hint: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_date_hint_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    modified_date_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    source_class: Mapped[str] = mapped_column(String(32), default="secondary")
    as_of_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_original_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_verification_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    published_at_unknown: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[ForecastRun] = relationship(back_populates="evidence")
    claims: Mapped[list[EvidenceClaimRow]] = relationship(back_populates="evidence_item")


class EvidenceClaimRow(Base):
    __tablename__ = "evidence_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_item_id: Mapped[str] = mapped_column(ForeignKey("evidence_items.id"))
    forecast_node_id: Mapped[str] = mapped_column(ForeignKey("forecast_nodes.id"))

    claim: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text)

    source_url: Mapped[str] = mapped_column(Text)
    source_title: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(String(256))
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_date_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_date_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temporal_basis: Mapped[str] = mapped_column(String(32))

    supports_or_refutes: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    source_quality: Mapped[float] = mapped_column(Float)
    primary_source: Mapped[bool] = mapped_column(Boolean, default=False)

    as_of_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    cutoff_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_class: Mapped[str] = mapped_column(
        String(32), default="unknown_legacy", server_default="unknown_legacy"
    )
    extraction_method: Mapped[str] = mapped_column(
        String(48), default="unknown_legacy", server_default="unknown_legacy"
    )
    source_host: Mapped[str] = mapped_column(String(255), default="", server_default="")

    evidence_item: Mapped[EvidenceItem] = relationship(back_populates="claims")
    forecast_node: Mapped[ForecastNodeRow] = relationship(back_populates="evidence_claims")


class ForecastNodeRunRow(Base):
    __tablename__ = "forecast_node_runs"
    __table_args__ = (UniqueConstraint("forecast_run_id", "node_id", name="uq_forecast_node_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("forecast_nodes.id"))
    probability: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    reasoning: Mapped[str] = mapped_column(Text)
    supporting_claim_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    opposing_claim_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    uncertainty: Mapped[float] = mapped_column(Float)
    uncertainty_notes_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    model_used: Mapped[str] = mapped_column(
        String(255),
        default="legacy:deterministic-node-v1",
        server_default="legacy:deterministic-node-v1",
    )
    raw_importance_weight: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    dependency_factor: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    normalized_weight: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    probability_contribution: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="node_runs")
    node: Mapped[ForecastNodeRow] = relationship(back_populates="runs")


class ForecastAggregationRow(Base):
    __tablename__ = "forecast_aggregations"
    __table_args__ = (UniqueConstraint("forecast_run_id", name="uq_forecast_aggregation_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    method: Mapped[str] = mapped_column(String(128))
    final_probability: Mapped[float] = mapped_column(Float)
    calculation_trace_json: Mapped[str] = mapped_column(Text, default="[]")
    node_contributions_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="aggregation")


class EvidenceSufficiencyAssessmentRow(Base):
    """One immutable deterministic private-V1 evidence assessment per run."""

    __tablename__ = "evidence_sufficiency_assessments"
    __table_args__ = (
        UniqueConstraint("forecast_run_id", name="uq_evidence_sufficiency_assessment_run"),
        CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_evidence_sufficiency_assessment_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    policy_version: Mapped[str] = mapped_column(String(64))
    policy_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_node_count: Mapped[int] = mapped_column(Integer)
    included_node_count: Mapped[int] = mapped_column(Integer)
    critical_node_count: Mapped[int] = mapped_column(Integer)
    selected_coverage_numerator: Mapped[int] = mapped_column(Integer)
    selected_coverage_denominator: Mapped[int] = mapped_column(Integer)
    selected_node_coverage: Mapped[float] = mapped_column(Float)
    graph_coverage_numerator: Mapped[int] = mapped_column(Integer)
    graph_coverage_denominator: Mapped[int] = mapped_column(Integer)
    graph_node_coverage: Mapped[float] = mapped_column(Float)
    included_graph_weight: Mapped[float] = mapped_column(Float)
    total_graph_weight: Mapped[float] = mapped_column(Float)
    graph_weight_coverage: Mapped[float] = mapped_column(Float)
    cited_claim_count: Mapped[int] = mapped_column(Integer)
    cited_item_count: Mapped[int] = mapped_column(Integer)
    cited_source_count: Mapped[int] = mapped_column(Integer)
    distinct_host_count: Mapped[int] = mapped_column(Integer)
    distinct_hosts_json: Mapped[str] = mapped_column(Text, default="[]")
    primary_claim_count: Mapped[int] = mapped_column(Integer)
    primary_node_count: Mapped[int] = mapped_column(Integer)
    structured_claim_count: Mapped[int] = mapped_column(Integer)
    fallback_claim_count: Mapped[int] = mapped_column(Integer)
    included_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    excluded_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    insufficient_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    per_node_json: Mapped[str] = mapped_column(Text, default="[]")
    assessment_input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="evidence_sufficiency_assessment")


class MaterialNodeCoverageAssessmentRow(Base):
    """One immutable deterministic material-node assessment per forecast run."""

    __tablename__ = "material_node_coverage_assessments"
    __table_args__ = (
        UniqueConstraint(
            "forecast_run_id",
            name="uq_material_node_coverage_assessment_run",
        ),
        CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_material_node_coverage_assessment_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    policy_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    graph_id: Mapped[str] = mapped_column(ForeignKey("forecast_graphs.id"))
    graph_version: Mapped[int] = mapped_column(Integer)
    research_plan_id: Mapped[str] = mapped_column(ForeignKey("research_plans.id"))
    evidence_sufficiency_assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence_sufficiency_assessments.id"),
        nullable=True,
    )
    evidence_sufficiency_assessment_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    graph_node_count: Mapped[int] = mapped_column(Integer)
    selected_node_count: Mapped[int] = mapped_column(Integer)
    included_node_count: Mapped[int] = mapped_column(Integer)
    excluded_node_count: Mapped[int] = mapped_column(Integer)
    total_graph_weight: Mapped[str] = mapped_column(String(64))
    included_graph_weight: Mapped[str] = mapped_column(String(64))
    excluded_graph_weight: Mapped[str] = mapped_column(String(64))
    included_frontier_weight: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    maximum_excluded_weight: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    selected_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    included_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    excluded_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    higher_importance_excluded_node_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    frontier_tie_excluded_node_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    missing_parent_relationships_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    missing_dependency_relationships_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    policy_snapshot_json: Mapped[str] = mapped_column(Text)
    assessment_input_hash: Mapped[str] = mapped_column(String(64))

    forecast_run: Mapped[ForecastRun] = relationship(
        back_populates="material_node_coverage_assessment"
    )


class ScenarioSynthesisRow(Base):
    """One immutable grounded scenario-synthesis artifact per forecast run."""

    __tablename__ = "scenario_syntheses"
    __table_args__ = (
        UniqueConstraint(
            "forecast_run_id",
            name="uq_scenario_synthesis_forecast_run",
        ),
        CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_scenario_synthesis_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    policy_version: Mapped[str] = mapped_column(String(64))
    policy_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prompt_version: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenarios_json: Mapped[str] = mapped_column(Text, default="[]")
    coverage_audit_json: Mapped[str] = mapped_column(Text, default="{}")
    failure_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    diagnostics_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_sufficiency_assessment_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_sufficiency_assessments.id")
    )
    evidence_sufficiency_assessment_hash: Mapped[str] = mapped_column(String(64))
    material_node_coverage_assessment_id: Mapped[str] = mapped_column(
        ForeignKey("material_node_coverage_assessments.id")
    )
    material_node_coverage_assessment_hash: Mapped[str] = mapped_column(String(64))

    forecast_run: Mapped[ForecastRun] = relationship(
        back_populates="scenario_synthesis"
    )


class GraphExecutionFailureRow(Base):
    """A durable graph-stage failure and its effect on forecast completion."""

    __tablename__ = "graph_execution_failures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("forecast_nodes.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str] = mapped_column(String(128))
    error_message: Mapped[str] = mapped_column(Text)
    research_plan_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    queries_attempted_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    sources_checked_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    critical_node: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    impact: Mapped[str] = mapped_column(
        String(64),
        default="forecast_failed",
        server_default="forecast_failed",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="graph_execution_failures")
    node: Mapped[ForecastNodeRow | None] = relationship(back_populates="execution_failures")


class ResearchPlanRow(Base):
    """The immutable graph-node selection and budget allocation for one forecast run."""

    __tablename__ = "research_plans"
    __table_args__ = (
        UniqueConstraint("forecast_run_id", name="uq_research_plan_forecast_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    selected_nodes_json: Mapped[str] = mapped_column(Text, default="[]")
    skipped_nodes_json: Mapped[str] = mapped_column(Text, default="[]")
    priority_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    budget_allocation_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    forecast_run: Mapped[ForecastRun] = relationship(back_populates="research_plan")


class ForecastVersion(Base):
    __tablename__ = "forecast_versions"
    __table_args__ = (UniqueConstraint("run_id", name="uq_forecast_version_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    raw_track_probabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    ensemble_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    aggregation_json: Mapped[str] = mapped_column(Text, default="{}")
    shrinkage: Mapped[float] = mapped_column(Float, default=0.1)
    track_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_drivers_json: Mapped[str] = mapped_column(Text, default="[]")
    counterarguments_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    trigger_event: Mapped[str] = mapped_column(String(64), default="run")
    previous_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    question: Mapped[Question] = relationship(back_populates="versions")
    run: Mapped[ForecastRun] = relationship(back_populates="versions")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_jobs_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress_message: Mapped[str] = mapped_column(Text, default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_history_json: Mapped[str] = mapped_column(Text, default="[]")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    stage: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class FrozenEvaluationDatasetError(ValueError):
    """Raised when application code attempts to alter a frozen evaluation release."""


class FrozenForecastExperimentError(ValueError):
    """Raised when application code attempts to alter frozen experiment inputs."""


class EvaluationDataset(Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluation_dataset_name_version"),
        CheckConstraint(
            "status IN ('draft', 'reviewed', 'frozen')",
            name="ck_evaluation_dataset_status",
        ),
        CheckConstraint("question_count >= 0", name="ck_evaluation_dataset_question_count"),
        CheckConstraint(
            "status != 'frozen' OR frozen_at IS NOT NULL",
            name="ck_evaluation_dataset_frozen_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    question_count: Mapped[int] = mapped_column(Integer, default=0)

    questions: Mapped[list[EvaluationQuestion]] = relationship(
        back_populates="dataset",
        order_by="EvaluationQuestion.question_hash",
        cascade="all, delete-orphan",
    )


class EvaluationQuestion(Base):
    __tablename__ = "evaluation_questions"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "question_hash",
            name="uq_evaluation_dataset_question_hash",
        ),
        CheckConstraint("outcome IN (0, 1)", name="ck_evaluation_question_binary_outcome"),
        CheckConstraint(
            "forecast_date < resolution_date",
            name="ck_evaluation_question_date_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE")
    )
    question: Mapped[str] = mapped_column(Text)
    resolution_contract: Mapped[str] = mapped_column(Text)
    forecast_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[int] = mapped_column(Integer)
    resolution_source: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question_hash: Mapped[str] = mapped_column(String(64))

    dataset: Mapped[EvaluationDataset] = relationship(back_populates="questions")


class ForecastExperiment(Base):
    """A controlled comparison over one frozen real-evaluation dataset."""

    __tablename__ = "forecast_experiments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'completed_with_failures', 'failed')",
            name="ck_forecast_experiment_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("evaluation_datasets.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    profiles_json: Mapped[str] = mapped_column(Text)
    configuration_hash: Mapped[str] = mapped_column(String(64))
    configuration_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list[ForecastExperimentRun]] = relationship(back_populates="experiment")
    failures: Mapped[list[ForecastFailure]] = relationship(back_populates="experiment")


class ForecastExperimentRun(Base):
    """One immutable question/profile assignment in a controlled experiment."""

    __tablename__ = "forecast_experiment_runs"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "evaluation_question_id",
            "profile_id",
            name="uq_forecast_experiment_run_cell",
        ),
        UniqueConstraint("forecast_run_id", name="uq_forecast_experiment_run_forecast_run"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
            name="ck_forecast_experiment_run_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("forecast_experiments.id"))
    evaluation_question_id: Mapped[str] = mapped_column(ForeignKey("evaluation_questions.id"))
    profile_id: Mapped[str] = mapped_column(String(64))
    forecast_run_id: Mapped[str | None] = mapped_column(ForeignKey("forecast_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    experiment: Mapped[ForecastExperiment] = relationship(back_populates="runs")
    result: Mapped[ForecastExperimentResult | None] = relationship(
        back_populates="experiment_run",
        uselist=False,
    )
    failures: Mapped[list[ForecastFailure]] = relationship(back_populates="experiment_run")


class ForecastExperimentResult(Base):
    """Persisted probability and measurements for one experiment cell."""

    __tablename__ = "forecast_experiment_results"
    __table_args__ = (
        UniqueConstraint("experiment_run_id", name="uq_forecast_experiment_result_run"),
        CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="ck_forecast_experiment_result_probability",
        ),
        CheckConstraint("outcome IN (0, 1)", name="ck_forecast_experiment_result_outcome"),
        CheckConstraint(
            "completion_status IN ('completed', 'partial', 'failed')",
            name="ck_forecast_experiment_result_completion_status",
        ),
        CheckConstraint("cost_usd >= 0", name="ck_forecast_experiment_result_cost"),
        CheckConstraint("latency_ms >= 0", name="ck_forecast_experiment_result_latency"),
        CheckConstraint(
            "evidence_coverage IS NULL OR (evidence_coverage >= 0 AND evidence_coverage <= 1)",
            name="ck_forecast_experiment_result_evidence_coverage",
        ),
        CheckConstraint(
            "evidence_covered_units >= 0 AND evidence_total_units >= 0",
            name="ck_forecast_experiment_result_evidence_units",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_experiment_runs.id"))
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[int] = mapped_column(Integer)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_covered_units: Mapped[int] = mapped_column(Integer, default=0)
    evidence_total_units: Mapped[int] = mapped_column(Integer, default=0)
    completion_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    experiment_run: Mapped[ForecastExperimentRun] = relationship(back_populates="result")


class ForecastFailure(Base):
    """Internal classification explaining a controlled forecast run's failure mode."""

    __tablename__ = "forecast_failures"
    __table_args__ = (
        UniqueConstraint(
            "forecast_experiment_run_id",
            "category",
            name="uq_forecast_failure_run_category",
        ),
        CheckConstraint(
            "category IN ("
            "'bad_contract', 'bad_evidence', 'bad_decomposition', "
            "'bad_node_forecast', 'bad_aggregation', 'operational_failure'"
            ")",
            name="ck_forecast_failure_category",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("forecast_experiments.id"))
    forecast_experiment_run_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_experiment_runs.id")
    )
    category: Mapped[str] = mapped_column(String(64))
    annotation: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), default="internal_reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    experiment: Mapped[ForecastExperiment] = relationship(back_populates="failures")
    experiment_run: Mapped[ForecastExperimentRun] = relationship(back_populates="failures")


def _frozen_forecast_experiment_configuration_guard(
    _mapper: object,
    _connection: Connection,
    target: ForecastExperiment,
) -> None:
    state = inspect(target)
    frozen_fields = (
        "dataset_id",
        "profiles_json",
        "configuration_hash",
        "configuration_json",
        "created_at",
    )
    if any(state.attrs[field].history.has_changes() for field in frozen_fields):
        raise FrozenForecastExperimentError("forecast_experiment_configuration_immutable")


def _frozen_evaluation_dataset_before_update(
    _mapper: object,
    _connection: Connection,
    target: EvaluationDataset,
) -> None:
    status_history = inspect(target).attrs.status.history
    prior_status = status_history.deleted[0] if status_history.deleted else target.status
    if prior_status == "frozen":
        raise FrozenEvaluationDatasetError("frozen_evaluation_dataset_immutable")


def _frozen_evaluation_dataset_before_delete(
    _mapper: object,
    _connection: Connection,
    target: EvaluationDataset,
) -> None:
    if target.status == "frozen":
        raise FrozenEvaluationDatasetError("frozen_evaluation_dataset_immutable")


def _frozen_evaluation_question_guard(
    _mapper: object,
    connection: Connection,
    target: EvaluationQuestion,
) -> None:
    status = connection.execute(
        select(EvaluationDataset.status).where(EvaluationDataset.id == target.dataset_id)
    ).scalar_one_or_none()
    if status == "frozen":
        raise FrozenEvaluationDatasetError("frozen_evaluation_dataset_immutable")


event.listen(EvaluationDataset, "before_update", _frozen_evaluation_dataset_before_update)
event.listen(EvaluationDataset, "before_delete", _frozen_evaluation_dataset_before_delete)
event.listen(EvaluationQuestion, "before_insert", _frozen_evaluation_question_guard)
event.listen(EvaluationQuestion, "before_update", _frozen_evaluation_question_guard)
event.listen(EvaluationQuestion, "before_delete", _frozen_evaluation_question_guard)
event.listen(
    ForecastExperiment,
    "before_update",
    _frozen_forecast_experiment_configuration_guard,
)


class BenchmarkDataset(Base):
    __tablename__ = "benchmark_datasets"
    __table_args__ = (UniqueConstraint("builtin_key", "builtin_version", name="uq_benchmark_dataset_builtin"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    dataset_hash: Mapped[str] = mapped_column(String(64), unique=True)
    provenance: Mapped[str] = mapped_column(String(128), default="user_import")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    builtin_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    builtin_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BenchmarkQuestion(Base):
    __tablename__ = "benchmark_questions"
    __table_args__ = (UniqueConstraint("dataset_id", "import_hash", name="uq_benchmark_dataset_import"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_datasets.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    forecast_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[int] = mapped_column(Integer)
    resolution_source: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(128), default="user_import")
    import_hash: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    exact_yes: Mapped[str] = mapped_column(Text, default="")
    exact_no: Mapped[str] = mapped_column(Text, default="")
    resolution_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authoritative_source: Mapped[str] = mapped_column(Text, default="")
    fallback_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    geography: Mapped[str | None] = mapped_column(String(128), nullable=True)
    units: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ambiguity_notes: Mapped[str] = mapped_column(Text, default="")
    cancellation_conditions: Mapped[str] = mapped_column(Text, default="")
    resolver_risk_notes: Mapped[str] = mapped_column(Text, default="")


class BenchmarkExperiment(Base):
    __tablename__ = "benchmark_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("benchmark_datasets.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_context_json: Mapped[str] = mapped_column(Text, default="{}")
    model_provider: Mapped[str] = mapped_column(String(64), default="mock")
    model_name: Mapped[str] = mapped_column(String(128), default="mock-forecast-v1")
    model_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    search_provider: Mapped[str] = mapped_column(String(64), default="mock")
    profile_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    profile_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_policy: Mapped[str] = mapped_column(String(64), default="synthetic_historical_fixtures")
    experiment_hash: Mapped[str] = mapped_column(String(64), default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    environment_identity_json: Mapped[str] = mapped_column(Text, default="{}")
    tracked_source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pyproject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dependency_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    package_lock_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    working_tree_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    application_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    container_image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)

    snapshots: Mapped[list[BenchmarkProfileSnapshot]] = relationship(back_populates="experiment")


class BenchmarkProfileSnapshot(Base):
    __tablename__ = "benchmark_profile_snapshots"
    __table_args__ = (UniqueConstraint("experiment_id", "profile_id", name="uq_benchmark_profile_snapshot"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("benchmark_experiments.id"))
    profile_id: Mapped[str] = mapped_column(String(64))
    source_profile_json: Mapped[str] = mapped_column(Text)
    effective_profile_json: Mapped[str] = mapped_column(Text)
    profile_hash: Mapped[str] = mapped_column(String(64))
    prompt_bundle_json: Mapped[str] = mapped_column(Text)
    prompt_versions_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    model_provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(128))
    model_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_provider: Mapped[str] = mapped_column(String(64))
    model_timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    evidence_policy: Mapped[str] = mapped_column(String(64))
    effective_max_cost_usd: Mapped[float] = mapped_column(Float)
    effective_max_tokens: Mapped[int] = mapped_column(Integer)
    effective_max_model_calls: Mapped[int] = mapped_column(Integer)
    effective_max_search_calls: Mapped[int] = mapped_column(Integer)
    effective_max_fetched_documents: Mapped[int] = mapped_column(Integer)
    effective_max_wall_clock_seconds: Mapped[int] = mapped_column(Integer)
    pricing_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    pricing_hash: Mapped[str] = mapped_column(String(64), default="")
    execution_context_json: Mapped[str] = mapped_column(Text)
    configuration_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    experiment: Mapped[BenchmarkExperiment] = relationship(back_populates="snapshots")


class BenchmarkTask(Base):
    __tablename__ = "benchmark_tasks"
    __table_args__ = (
        UniqueConstraint("experiment_id", "benchmark_question_id", "profile_id", name="uq_benchmark_task"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("benchmark_experiments.id"))
    benchmark_question_id: Mapped[str] = mapped_column(ForeignKey("benchmark_questions.id"))
    profile_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (UniqueConstraint("benchmark_task_id", name="uq_benchmark_result_task"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_experiments.id"), nullable=True)
    benchmark_task_id: Mapped[str | None] = mapped_column(ForeignKey("benchmark_tasks.id"), nullable=True)
    benchmark_question_id: Mapped[str] = mapped_column(ForeignKey("benchmark_questions.id"))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(64))
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_covered_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    evidence_total_units: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[bool] = mapped_column(Boolean, default=False)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    endpoint_url: Mapped[str] = mapped_column(Text)
    endpoint_type: Mapped[str] = mapped_column(String(16))
    json_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    poll_seconds: Mapped[int] = mapped_column(Integer, default=300)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    auto_rerun: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    question: Mapped[Question] = relationship(back_populates="watches")
    events: Mapped[list[WatchEvent]] = relationship(back_populates="watch")


class WatchEvent(Base):
    __tablename__ = "watch_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watch_id: Mapped[str] = mapped_column(ForeignKey("watches.id"))
    old_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    material: Mapped[bool] = mapped_column(Boolean, default=True)
    resulting_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(32), default="ok")

    watch: Mapped[Watch] = relationship(back_populates="events")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(32), default="idle")


class ForecastRunAttempt(Base):
    __tablename__ = "forecast_run_attempts"
    __table_args__ = (UniqueConstraint("run_id", "attempt_number", name="uq_forecast_run_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    search_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_count: Mapped[int] = mapped_column(Integer, default=0)


class ProviderCallLedger(Base):
    __tablename__ = "provider_call_ledger"
    __table_args__ = (
        UniqueConstraint("run_id", "logical_call_id", "physical_attempt_number", name="uq_provider_call_physical"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    run_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("forecast_run_attempts.id"), nullable=True)
    logical_call_id: Mapped[str] = mapped_column(String(36))
    physical_attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(64))
    provider_type: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    request_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    reserved_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    actual_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(32), default="reserved")
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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

    contract: Mapped[ResolutionContractRow | None] = relationship(back_populates="question", uselist=False)
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


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    profile_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
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

    question: Mapped[Question] = relationship(back_populates="runs")
    tracks: Mapped[list[ResearchTrack]] = relationship(back_populates="run")
    evidence: Mapped[list[EvidenceItem]] = relationship(back_populates="run")
    versions: Mapped[list[ForecastVersion]] = relationship(back_populates="run")


class ResearchTrack(Base):
    __tablename__ = "research_tracks"

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
    excerpt: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    source_class: Mapped[str] = mapped_column(String(32), default="secondary")
    as_of_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, default=200)

    run: Mapped[ForecastRun] = relationship(back_populates="evidence")


class ForecastVersion(Base):
    __tablename__ = "forecast_versions"

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


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    stage: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class BenchmarkQuestion(Base):
    __tablename__ = "benchmark_questions"
    __table_args__ = (UniqueConstraint("import_hash", name="uq_benchmark_import_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    forecast_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[int] = mapped_column(Integer)
    resolution_source: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(128), default="user_import")
    import_hash: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    benchmark_question_id: Mapped[str] = mapped_column(ForeignKey("benchmark_questions.id"))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(64))
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)
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

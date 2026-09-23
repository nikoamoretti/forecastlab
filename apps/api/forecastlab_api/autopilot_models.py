"""Additive cloud and Autopilot records; ordinary runs remain the execution ledger."""
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
)
from sqlalchemy.orm import Mapped, mapped_column

from forecastlab.timeutil import utcnow
from forecastlab_api.db import Base


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AutopilotPolicy(Base):
    __tablename__ = "autopilot_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, unique=True)
    config_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutopilotState(Base):
    __tablename__ = "autopilot_state"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="personal")
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("autopilot_policies.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    qualification_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_reason: Mapped[str] = mapped_column(Text, default="Not enabled")
    provider_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_receipt_json: Mapped[str] = mapped_column(Text, default="{}")


class ExecutionLease(Base):
    __tablename__ = "execution_leases"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedQuestion(Base):
    __tablename__ = "autopilot_questions"
    __table_args__ = (UniqueConstraint("indicator", "period", name="uq_autopilot_event_question"),)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("autopilot_policies.id"))
    indicator: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(7))
    release_event: Mapped[str] = mapped_column(String(128))
    macro_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    initial_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"))
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("autopilot_policies.id"))
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    kind: Mapped[str] = mapped_column(String(32))
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stop_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approval_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("kind IN ('initial', 'refresh', 'qualification')", name="ck_auto_run_kind"),)


class AutopilotDispatch(Base):
    __tablename__ = "autopilot_dispatches"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WeeklyBudget(Base):
    __tablename__ = "autopilot_weekly_budgets"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    limit_usd: Mapped[float] = mapped_column(Float, default=25)


class AutopilotSpend(Base):
    __tablename__ = "autopilot_spend"
    ledger_id: Mapped[str] = mapped_column(ForeignKey("provider_call_ledger.id"), primary_key=True)
    week_id: Mapped[str] = mapped_column(ForeignKey("autopilot_weekly_budgets.id"), index=True)


class ExecutionCheckpoint(Base):
    __tablename__ = "execution_checkpoints"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="started")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinalEstimateHold(Base):
    __tablename__ = "final_estimate_holds"
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    cost_usd: Mapped[float] = mapped_column(Float)
    tokens: Mapped[int] = mapped_column(Integer)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class OutcomeProposal(Base):
    __tablename__ = "outcome_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    contract_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuestionAdjudication(Base):
    __tablename__ = "question_adjudications"
    __table_args__ = (
        UniqueConstraint("question_id", "revision", name="uq_question_adjudication_revision"),
        CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_question_adjudication_binary"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("outcome_proposals.id"))
    contract_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[int | None] = mapped_column(Integer)
    evidence_json: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OfficialMacroOutcomeAmendment(Base):
    """Append-only post-freeze evidence for a deterministic macro outcome."""

    __tablename__ = "official_macro_outcome_amendments"
    __table_args__ = (
        UniqueConstraint("question_id", "revision", name="uq_official_macro_outcome_amendment_revision"),
        CheckConstraint("status IN ('ready', 'exception')", name="ck_official_macro_outcome_amendment_status"),
        CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_official_macro_outcome_amendment_binary"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    prospective_entry_id: Mapped[str | None] = mapped_column(ForeignKey("prospective_entries.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(String(64))
    contract_hash: Mapped[str] = mapped_column(String(64))
    release_event: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_identity_json: Mapped[str] = mapped_column(Text, default="{}")
    artifact_json: Mapped[str] = mapped_column(Text, default="{}")
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measurement_json: Mapped[str] = mapped_column(Text, default="{}")
    outcome: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_known_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exception_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exception_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxEvent(Base):
    __tablename__ = "inbox_events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str] = mapped_column(Text)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("questions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


TABLES = [AppSetting, AuthSession, AutopilotPolicy, AutopilotState, ExecutionLease, ManagedQuestion,
          AutopilotRun, AutopilotDispatch, WeeklyBudget, AutopilotSpend, ExecutionCheckpoint,
          FinalEstimateHold, OutcomeProposal, QuestionAdjudication, InboxEvent]


def _immutable(_mapper, _connection, _target):
    raise ValueError("append_only_record")


for _model in (AutopilotPolicy, AutopilotRun, OutcomeProposal, QuestionAdjudication, OfficialMacroOutcomeAmendment):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)

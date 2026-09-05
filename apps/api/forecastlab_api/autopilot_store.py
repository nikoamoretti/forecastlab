"""Database arbitration shared by cron, user requests, and provider calls."""
from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from forecastlab.errors import BudgetExceeded, PermanentProviderError
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.autopilot_models import (
    AppSetting,
    AutopilotPolicy,
    AutopilotRun,
    AutopilotSpend,
    AutopilotState,
    ExecutionLease,
    FinalEstimateHold,
    InboxEvent,
    ManagedQuestion,
    WeeklyBudget,
)
from forecastlab_api.config import settings
from forecastlab_api.models import Job, ProviderCallLedger

worker_ticket: ContextVar[tuple[str, int] | None] = ContextVar("forecastlab_worker_ticket", default=None)
paid_stage: ContextVar[str | None] = ContextVar("forecastlab_paid_stage", default=None)


def insert_once(session, table, values: dict):
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    session.execute(insert(table).values(**values).on_conflict_do_nothing())


def state(session, *, lock=False) -> AutopilotState:
    insert_once(session, AutopilotState, {"id": "personal", "enabled": False,
        "qualification_enabled": False, "pause_reason": "Not enabled", "provider_failures": 0, "restore_receipt_json": "{}"})
    query = select(AutopilotState).where(AutopilotState.id == "personal")
    return session.scalar(query.with_for_update() if lock else query)


def week_bounds(now: datetime | None = None):
    local = as_utc(now or utcnow()).astimezone(ZoneInfo("America/Los_Angeles"))
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.date().isoformat(), start.astimezone(UTC), (start + timedelta(days=7)).astimezone(UTC)


def weekly_budget(session, *, now=None, lock=False) -> WeeklyBudget:
    key, start, end = week_bounds(now)
    insert_once(session, WeeklyBudget, {"id": key, "starts_at": start, "ends_at": end, "limit_usd": 25.0})
    query = select(WeeklyBudget).where(WeeklyBudget.id == key)
    return session.scalar(query.with_for_update() if lock else query)


def budget_totals(session, week_id: str) -> dict:
    entries = session.scalars(select(ProviderCallLedger).join(AutopilotSpend,
        AutopilotSpend.ledger_id == ProviderCallLedger.id).where(AutopilotSpend.week_id == week_id)).all()
    used = sum(float(r.actual_cost_usd if r.actual_cost_usd is not None else r.reserved_cost_usd)
               for r in entries if r.status not in {"reserved", "released"})
    reserved = sum(float(r.reserved_cost_usd) for r in entries if r.status == "reserved")
    return {"used_usd": used, "reserved_usd": reserved, "remaining_usd": max(0.0, 25 - used - reserved)}


def remaining_holds(session, run_id: str, stage: str = "") -> tuple[float, int]:
    holds = session.scalars(select(FinalEstimateHold).where(FinalEstimateHold.run_id == run_id,
        FinalEstimateHold.consumed.is_(False))).all()
    return (sum(h.cost_usd for h in holds if stage != "root_estimate:" + h.role),
            sum(h.tokens for h in holds if stage != "root_estimate:" + h.role))


def reserve_automation(session, run_id: str, extra_cost: float):
    if settings.cloud and not settings.production:
        raise BudgetExceeded("environment", "preview_provider_calls_disabled")
    auto = session.get(AutopilotRun, run_id)
    if auto is None:
        return None
    current = state(session, lock=True)
    if not (current.qualification_enabled if auto.kind == "qualification" else current.enabled):
        raise BudgetExceeded("autopilot", "autopilot_paused")
    managed = session.get(ManagedQuestion, auto.question_id)
    if not managed or managed.status != "active":
        raise BudgetExceeded("autopilot", "question_suspended")
    if utcnow() >= as_utc(auto.stop_at):
        raise BudgetExceeded("autopilot", "release_forecast_cutoff_reached")
    week = weekly_budget(session, lock=True)
    policy = session.get(AutopilotPolicy, current.policy_id)
    limit = min(25, json.loads(policy.config_json)["policy"]["weekly_usd"]) if policy else 0
    totals = budget_totals(session, week.id)
    if extra_cost + totals["used_usd"] + totals["reserved_usd"] > limit + 1e-12:
        raise BudgetExceeded("autopilot", "weekly_budget_exhausted")
    if auto.kind == "qualification":
        entries = session.scalars(select(ProviderCallLedger).join(AutopilotRun,
            AutopilotRun.run_id == ProviderCallLedger.run_id).where(AutopilotRun.kind == "qualification")).all()
        spent = sum((r.actual_cost_usd if r.actual_cost_usd is not None else r.reserved_cost_usd)
                    for r in entries if r.status != "released")
        if spent + extra_cost > 15 + 1e-12:
            raise BudgetExceeded("autopilot", "qualification_budget_exhausted")
    return week.id


def claim_lease(key: str, *, seconds=660) -> tuple[str, int] | None:
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        insert_once(session, ExecutionLease, {"key": key, "generation": 0})
        session.scalar(select(ExecutionLease).where(ExecutionLease.key == key).with_for_update())
        control = session.get(AppSetting, "release_control") if key == "paid_worker" else None
        if control and json.loads(control.value_json).get("dispatch_paused"):
            session.commit()
            return None
        now, owner = utcnow(), str(uuid.uuid4())
        generation = session.execute(update(ExecutionLease).where(ExecutionLease.key == key,
            (ExecutionLease.expires_at.is_(None)) | (ExecutionLease.expires_at <= now))
            .values(owner=owner, generation=ExecutionLease.generation + 1, expires_at=now + timedelta(seconds=seconds))
            .returning(ExecutionLease.generation)).scalar_one_or_none()
        session.commit()
        return (owner, generation) if generation is not None else None


def release_lease(key: str, ticket: tuple[str, int]) -> None:
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        session.execute(update(ExecutionLease).where(ExecutionLease.key == key,
            ExecutionLease.owner == ticket[0], ExecutionLease.generation == ticket[1]).values(owner=None, expires_at=None))
        session.commit()


def assert_job_fence(session, job_id: str, lease_owner: str | None) -> None:
    row = session.execute(select(Job.status, Job.lease_owner, Job.lease_expires_at).where(Job.id == job_id).with_for_update()).one_or_none()
    if row is None or row.status != "running" or row.lease_owner != lease_owner or not row.lease_expires_at or as_utc(row.lease_expires_at) <= utcnow():
        raise PermanentProviderError("job_lease_lost")


def assert_worker_fence(session, run_id: str) -> None:
    ticket = worker_ticket.get()
    if ticket is None:
        return  # Local synchronous tools/tests have no worker lease.
    lease = session.scalar(select(ExecutionLease).where(ExecutionLease.key == "paid_worker").with_for_update().execution_options(populate_existing=True))
    if not lease or lease.owner != ticket[0] or lease.generation != ticket[1] or not lease.expires_at or as_utc(lease.expires_at) <= utcnow():
        raise PermanentProviderError("worker_fence_lost")
    from forecastlab_api.models import ForecastRun
    job_id = session.scalar(select(ForecastRun.job_id).where(ForecastRun.id == run_id))
    if job_id:
        assert_job_fence(session, job_id, f"{ticket[0]}:{ticket[1]}")


def notify(session, key: str, kind: str, title: str, detail: str, question_id=None):
    insert_once(session, InboxEvent, {"id": key, "kind": kind, "title": title, "detail": detail,
                                    "question_id": question_id, "created_at": utcnow()})


def record_provider_result(session, run_id: str, *, succeeded: bool):
    if not session.get(AutopilotRun, run_id):
        return
    current = state(session, lock=True)
    current.provider_failures = 0 if succeeded else current.provider_failures + 1
    if current.provider_failures == 3:
        current.enabled = current.qualification_enabled = False
        current.pause_reason = "Three consecutive provider failures"
        notify(session, "provider-pause:" + run_id, "incident", "Automatic forecasting paused", current.pause_reason)


def record_run_finished(run_id: str):
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, PersonalForecast
    with SessionLocal() as session:
        auto = session.get(AutopilotRun, run_id)
        if not auto:
            return
        run, personal = session.get(ForecastRun, run_id), session.get(PersonalForecast, run_id)
        if run is None or run.status not in {"completed", "failed"}:
            return
        key = "run:" + run.id
        if session.get(InboxEvent, key):
            return
        result = json.loads(personal.result_json or "{}") if personal else {}
        detail = run.error_message if run.status == "failed" else (
            "Probability: " + str(round(result["probability"] * 100, 1)) + "%" if result.get("probability") is not None
            else "; ".join(result.get("evidence_gaps", [])))
        notify(session, key, "forecast", "Forecast " + ("failed" if run.status == "failed" else "updated"), detail or "Review execution details", run.question_id)
        session.commit()

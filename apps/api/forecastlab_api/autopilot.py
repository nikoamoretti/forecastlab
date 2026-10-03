"""Persisted, bounded decisions. Cron delivery itself is never the work record."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from forecastlab.macro import MacroSnapshot, MacroSpec
from forecastlab.profiles import load_profile
from forecastlab.prompts import load_prompt_bundle
from forecastlab.question_selection import QuestionSuggestion, choose_questions
from forecastlab.root_event import contract_hash, digest
from forecastlab.schemas import ForecastContract, ForecastProfile
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.autopilot_models import (
    AutopilotDispatch,
    AutopilotPolicy,
    AutopilotRun,
    ExecutionCheckpoint,
    InboxEvent,
    ManagedQuestion,
)
from forecastlab_api.autopilot_store import (
    budget_totals,
    claim_lease,
    insert_once,
    notify,
    release_lease,
    state,
    weekly_budget,
)
from forecastlab_api.config import settings
from forecastlab_api.contracts import store_forecast_contract
from forecastlab_api.models import ForecastRun, Job, PersonalForecast, Question
from forecastlab_api.personal_forecasts import PROFILE, envelope
from forecastlab_api.pipeline import create_run_record, resolve_for_question
from forecastlab_api.question_suggestions import (
    SelectionSources,
    database_selection_sources,
    schedule_resolution_evidence,
)
from forecastlab_api.secrets import load_secrets


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["autopilot_policy_v1"] = "autopilot_policy_v1"
    method: Literal["root_event_ensemble_v1"] = "root_event_ensemble_v1"
    weekly_usd: float = Field(default=25, gt=0, le=25)
    forecast_usd: float = Field(default=5, gt=0, le=5)
    new_per_week: int = Field(default=3, ge=0, le=3)
    refreshes_per_week: int = Field(default=2, ge=0, le=2)
    horizon_days: Literal[7] = 7
    cooldown_hours: Literal[24] = 24
    stop_minutes: Literal[15] = 15
    indicators: tuple[Literal["unemployment"], Literal["payrolls"], Literal["cpi"]] = ("unemployment", "payrolls", "cpi")
    outcome_confirmation: Literal[True] = True


def method_signature() -> str:
    configuration = load_secrets()
    return digest({"profile": load_profile(PROFILE).model_dump(mode="json"),
        "prompts": load_prompt_bundle().hashes(),
        "settings": {k: configuration.get(k) for k in ("model_provider", "model_name", "model_base_url",
            "model_timeout_seconds", "search_provider", "max_cost_usd")}})


def policy_config(row: AutopilotPolicy) -> PolicyConfig:
    return PolicyConfig.model_validate(json.loads(row.config_json)["policy"])


def approve_policy(session, config: PolicyConfig, *, approved_by: str) -> AutopilotPolicy:
    current = state(session, lock=True)
    snapshot = {"policy": config.model_dump(mode="json"), "method_signature": method_signature()}
    fingerprint = digest(snapshot)
    existing = session.get(AutopilotPolicy, current.policy_id) if current.policy_id else None
    if existing and existing.fingerprint == fingerprint:
        return existing
    revision = (session.scalar(select(func.max(AutopilotPolicy.revision))) or 0) + 1
    policy = AutopilotPolicy(id=str(uuid.uuid4()), revision=revision, config_json=json.dumps(snapshot),
        fingerprint=fingerprint, approved_by=approved_by)
    session.add(policy)
    session.flush()
    current.policy_id, current.enabled, current.qualification_enabled = policy.id, False, False
    current.pause_reason = "Policy approved; awaiting release validation and enablement"
    notify(session, "policy:" + policy.id, "policy", "Autopilot policy approved", f"Revision {revision}; automatic spending remains disabled")
    session.commit()
    return policy


def qualification_status(session, policy: AutopilotPolicy | None) -> dict:
    signature = json.loads(policy.config_json)["method_signature"] if policy else None
    rows = session.execute(select(AutopilotRun, ForecastRun, PersonalForecast)
        .join(ForecastRun, ForecastRun.id == AutopilotRun.run_id)
        .join(PersonalForecast, PersonalForecast.run_id == ForecastRun.id)
        .where(AutopilotRun.kind == "qualification")).all()
    qualified, attempts = set(), []
    for auto, run, personal in rows:
        approval = json.loads(auto.approval_json)
        macro = MacroSpec.model_validate_json(personal.macro_json)
        valid = (run.mode == "live" and not run.synthetic_fixture_run and run.status == "completed"
            and personal.outcome_status == "forecasted" and run.total_cost_usd <= 5
            and approval["method_signature"] == signature and run.finished_at is not None
            and as_utc(run.finished_at) < as_utc(auto.stop_at))
        if valid:
            qualified.add(macro.indicator)
        attempts.append({"run_id": run.id, "indicator": macro.indicator, "status": run.status,
            "outcome": personal.outcome_status, "cost_usd": run.total_cost_usd, "qualified": valid})
    return {"complete": qualified == {"unemployment", "payrolls", "cpi"}, "attempts": attempts,
            "qualified_indicators": sorted(qualified), "ceiling_usd": 15}


def enable_gaps(session) -> list[str]:
    current = state(session)
    policy = session.get(AutopilotPolicy, current.policy_id) if current.policy_id else None
    gaps = []
    if not policy:
        gaps.append("Approve the automation policy")
    elif json.loads(policy.config_json)["method_signature"] != method_signature():
        gaps.append("Method or model settings changed; approve and qualify the new revision")
    if not json.loads(current.restore_receipt_json).get("verified"):
        gaps.append("Verify a database backup restore")
    if not qualification_status(session, policy)["complete"]:
        gaps.append("Complete three successful live qualification forecasts, one per indicator")
    if settings.cloud and not settings.production:
        gaps.append("Automatic spending is disabled in previews")
    if settings.cloud and not (settings.github_client_id and settings.github_client_secret):
        gaps.append("Configure owner GitHub login before unattended operation")
    return gaps


def set_enabled(session, enabled: bool):
    current = state(session, lock=True)
    if enabled:
        gaps = enable_gaps(session)
        if gaps:
            raise HTTPException(409, {"code": "autopilot_not_qualified", "gaps": gaps})
    current.enabled = enabled
    current.qualification_enabled = False
    current.pause_reason = "" if enabled else "Paused by owner"
    if enabled:
        current.provider_failures = 0
    notify(session, "control:" + str(uuid.uuid4()), "policy", "Autopilot enabled" if enabled else "Autopilot paused",
           "Applies before the next paid provider attempt")
    session.commit()


def _decision(session, key, action, reason, **payload):
    insert_once(session, AutopilotDispatch, {"id": key, "action": action, "reason": reason,
        "payload_json": json.dumps(payload), "created_at": utcnow()})


def source_fingerprint(snapshot: MacroSnapshot) -> str:
    # Retrieval times and decorative HTML do not make new evidence.
    return digest({"indicator": snapshot.indicator, "values": [
        [o.period, o.value, o.units, o.seasonal_adjustment] for o in snapshot.observations]})


def _resolution_evidence(candidate: QuestionSuggestion, sources: SelectionSources) -> list[dict]:
    return schedule_resolution_evidence(candidate.schedule, sources)


def dispatch(session, candidate: QuestionSuggestion, sources: SelectionSources, *, kind: str,
             question_id: str | None = None) -> ForecastRun | None:
    """One transaction freezes approval, identity, quota consumption and queued job."""
    current = state(session, lock=True)
    policy = session.get(AutopilotPolicy, current.policy_id) if current.policy_id else None
    if not policy or not (current.qualification_enabled if kind == "qualification" else current.enabled):
        return None
    config = policy_config(policy)
    now = utcnow()
    # Qualification is an explicitly requested, bounded release-validation
    # batch. It can test the next release of all three indicators even when
    # their calendars do not overlap. Ordinary discovery remains seven days.
    horizon_days = 90 if kind == "qualification" else config.horizon_days
    if not now + timedelta(minutes=config.stop_minutes) < as_utc(candidate.macro.release_at) <= now + timedelta(days=horizon_days):
        return None
    if json.loads(policy.config_json)["method_signature"] != method_signature():
        current.enabled = current.qualification_enabled = False
        current.pause_reason = "Method settings changed; a new policy revision and qualification are required"
        notify(session, "method-changed:" + policy.id, "incident", "Autopilot paused", current.pause_reason)
        session.commit()
        return None
    week = weekly_budget(session, lock=True)
    count = session.scalar(select(func.count()).select_from(AutopilotRun).where(
        AutopilotRun.created_at >= week.starts_at, AutopilotRun.created_at < week.ends_at,
        AutopilotRun.kind.in_(["refresh"] if kind == "refresh" else ["initial", "qualification"]))) or 0
    limit = config.refreshes_per_week if kind == "refresh" else config.new_per_week
    totals = budget_totals(session, week.id)
    if count >= limit or totals["used_usd"] + totals["reserved_usd"] >= config.weekly_usd:
        _decision(session, f"limit:{week.id}:{kind}", "skip", "Weekly volume or spending limit reached")
        session.commit()
        return None
    existing = session.scalar(select(ManagedQuestion).where(ManagedQuestion.indicator == candidate.macro.indicator,
        ManagedQuestion.period == candidate.macro.observation_period).with_for_update())
    if kind != "refresh" and existing:
        return None
    if kind == "refresh":
        if not existing or existing.question_id != question_id or existing.status != "active":
            return None
        latest_run_at = session.scalar(select(func.max(func.coalesce(
            AutopilotRun.cutoff, PersonalForecast.created_at, Job.created_at, ForecastRun.started_at,
        ))).select_from(ForecastRun)
            .outerjoin(AutopilotRun, AutopilotRun.run_id == ForecastRun.id)
            .outerjoin(PersonalForecast, PersonalForecast.run_id == ForecastRun.id)
            .outerjoin(Job, Job.id == ForecastRun.job_id)
            .where(ForecastRun.question_id == question_id))
        cooldown_from = max((as_utc(t) for t in (existing.last_refresh_at, latest_run_at) if t is not None), default=None)
        if cooldown_from and now - cooldown_from < timedelta(hours=24):
            return None
        active = session.scalar(select(ForecastRun.id).where(ForecastRun.question_id == question_id,
            ForecastRun.status.in_(["pending", "running", "preparing"])))
        if active:
            return None
        question = session.get(Question, question_id)
        initial = session.get(PersonalForecast, existing.initial_run_id)
        contract = ForecastContract.model_validate_json(initial.contract_json)
        macro = MacroSpec.model_validate_json(existing.macro_json)  # Keep original threshold and target.
    else:
        question = Question(id=str(uuid.uuid4()), original_text=candidate.question, normalized_text=candidate.question,
            requested_mode="live", requested_profile_id=PROFILE, forecast_deadline=candidate.macro.release_at)
        session.add(question)
        session.flush()
        macro = candidate.macro
        contract = macro.template(question.id, str(uuid.uuid4())).model_copy(update={"status": "approved"})
        store_forecast_contract(session, contract)
    resolution = _resolution_evidence(candidate, sources)
    snapshot = sources.snapshots[macro.indicator]
    from forecastlab.macro_evidence import validate_snapshot
    validate_snapshot(snapshot, macro, now)
    context = resolve_for_question(question, profile_id=PROFILE, mode="live")
    if context.effective_mode != "live" or context.model_is_mock or context.search_provider == "mock":
        raise HTTPException(409, "Live model and search settings are required")
    run = create_run_record(session, question=question, context=context, as_of=None)
    personal = envelope(session, run, request_key="autopilot:" + run.id, request_hash=contract_hash(contract), contract=contract, macro=macro)
    profile = ForecastProfile.model_validate_json(personal.profile_json)
    personal.profile_json = profile.model_copy(update={"max_estimated_cost_usd": min(profile.max_estimated_cost_usd, config.forecast_usd)}).model_dump_json()
    cutoff = utcnow()  # All cached official inputs precede this frozen forecast version.
    personal.result_json = json.dumps({"forecast_cutoff": cutoff.isoformat(), "macro_snapshot": snapshot.model_dump(mode="json"),
        "resolution_evidence": resolution, "question_selection": candidate.model_dump(mode="json")})
    session.flush()
    session.add(AutopilotRun(run_id=run.id, policy_id=policy.id, question_id=question.id, kind=kind,
        cutoff=cutoff, stop_at=macro.release_at - timedelta(minutes=15), approval_json=json.dumps({
            "policy_revision": policy.revision, "policy_fingerprint": policy.fingerprint, "selection_horizon_days": horizon_days,
            "method_signature": json.loads(policy.config_json)["method_signature"], "contract_hash": contract_hash(contract)})))
    if existing:
        existing.last_refresh_at = cutoff
        existing.fingerprint = source_fingerprint(snapshot)
    else:
        session.add(ManagedQuestion(question_id=question.id, policy_id=policy.id, indicator=macro.indicator,
            period=macro.observation_period, release_event=candidate.release_event, macro_json=macro.model_dump_json(),
            initial_run_id=run.id, fingerprint=source_fingerprint(snapshot), last_checked_at=sources.checked_at))
    _decision(session, "dispatch:" + run.id, kind, candidate.reason if kind != "refresh" else "Accepted evidence changed",
              run_id=run.id, question_id=question.id, policy_revision=policy.revision, week=week.id)
    session.commit()
    return run


def _calendar_matches(managed: ManagedQuestion, sources: SelectionSources) -> bool:
    macro = MacroSpec.model_validate_json(managed.macro_json)
    releases = [r for r in sources.releases if r.event_id == managed.release_event]
    return len(releases) == 1 and as_utc(releases[0].release_at) == as_utc(macro.release_at)


def qualification_dispatch(session):
    current = state(session, lock=True)
    if not current.policy_id:
        raise HTTPException(409, "Approve a policy before qualification")
    if not json.loads(current.restore_receipt_json).get("verified"):
        raise HTTPException(409, "A verified database restore is required before live qualification")
    if settings.cloud and not settings.production:
        raise HTTPException(409, "Previews cannot call paid providers")
    current.qualification_enabled = True
    session.commit()
    return reconcile(qualification=True)


def reconcile(*, qualification=False) -> dict:
    """Independent source/outcome work proceeds even while a paid worker holds its lease."""
    from forecastlab_api.db import SessionLocal
    ticket = claim_lease("scheduler", seconds=240)
    if ticket is None:
        return {"status": "already_running"}
    queued = []
    try:
        with SessionLocal() as session:
            current = state(session)
            current.last_tick_at = utcnow()
            should_discover = current.enabled or qualification
            managed_ids = list(session.scalars(select(ManagedQuestion.question_id).where(
                ManagedQuestion.status.in_(["active", "awaiting_outcome"]),
            ).order_by(ManagedQuestion.last_checked_at.asc().nulls_first(), ManagedQuestion.created_at)))
            session.commit()
            # Official outcomes do not depend on the discovery-source index or
            # paid forecasting. Resolve due frozen entries even when source
            # discovery is paused or temporarily unavailable.
            from forecastlab_api.official_macro_outcomes import process_due_prospective_entries
            process_due_prospective_entries(session)
        if not should_discover and not managed_ids:
            return {"status": "paused", "queued": []}
        try:
            sources = database_selection_sources()
        except Exception as exc:
            # Discovery is independent of adjudicating an already frozen
            # question. The exact dated first-release document can still
            # establish its outcome while the rolling source index is down.
            from forecastlab_api.official_macro_outcomes import process_managed_question
            with SessionLocal() as session:
                checks = 0
                for question_id in managed_ids:
                    if checks >= 2:
                        break
                    managed = session.get(ManagedQuestion, question_id)
                    if not managed or utcnow() < MacroSpec.model_validate_json(managed.macro_json).release_at:
                        continue
                    checks += 1
                    try:
                        managed.status = "awaiting_outcome"
                        session.commit()
                        process_managed_question(session, managed)
                    except Exception as outcome_exc:
                        session.rollback()
                        notify(session, "outcome-check:" + question_id + ":" + utcnow().strftime("%Y-%m-%d"),
                               "incident", "Official outcome check needs attention",
                               type(outcome_exc).__name__, question_id)
                        session.commit()
                notify(session, "selection-sources:" + utcnow().strftime("%Y-%m-%d"), "incident",
                       "Public data check needs attention", type(exc).__name__)
                session.commit()
            return {"status": "selection_sources_unavailable", "queued": [],
                    "gaps": ["selection_sources_" + type(exc).__name__]}
        candidates, gaps = choose_questions(sources.releases, sources.snapshots, now=utcnow())
        with SessionLocal() as session:
            if sources.gaps:
                bucket = utcnow().strftime("%Y-%m-%d")
                notify(session, "sources:" + bucket, "incident", "Public data check needs attention", "; ".join(sources.gaps))
            # Source/outcome work is bounded separately from the single paid
            # forecast. Oldest unchecked questions catch up on subsequent ticks.
            checks = 0
            for question_id in managed_ids:
                managed = session.get(ManagedQuestion, question_id)
                if not managed:
                    continue
                macro = MacroSpec.model_validate_json(managed.macro_json)
                known_schedule = any(r.event_id == managed.release_event for r in sources.releases)
                # A rolling index eventually omits an old event. After its
                # deadline, the dated original document can still establish an
                # outcome. Known conflicting dates always suspend the question.
                if not _calendar_matches(managed, sources) and (utcnow() < macro.release_at or known_schedule):
                    managed.status = "schedule_review"
                    notify(session, "schedule:" + question_id, "incident", "Release schedule needs review",
                           "Forecasting suspended because the official calendar changed or could not be verified", question_id)
                    continue
                if utcnow() >= macro.release_at:
                    if checks >= 2:
                        continue
                    managed.status = "awaiting_outcome"
                    session.commit()
                    from forecastlab_api.official_macro_outcomes import process_managed_question
                    process_managed_question(session, managed)
                    checks += 1
                    continue
                if not should_discover or qualification or utcnow() >= macro.release_at - timedelta(minutes=15):
                    continue
                if managed.last_checked_at and utcnow() - as_utc(managed.last_checked_at) < timedelta(hours=12):
                    continue
                if checks >= 2:
                    continue
                snapshot = sources.snapshots.get(macro.indicator)
                candidate = next((c for c in candidates if c.macro.indicator == macro.indicator and c.macro.observation_period == macro.observation_period), None)
                if snapshot and candidate:
                    changed = source_fingerprint(snapshot) != managed.fingerprint
                    from forecastlab_api.source_monitor import accepted_sources_changed
                    changed = accepted_sources_changed(session, managed) or changed
                    checks += 1
                    managed.last_checked_at = utcnow()
                    session.commit()
                    if changed:
                        run = dispatch(session, candidate, sources, kind="refresh", question_id=question_id)
                        if run:
                            queued.append(run.id)
            session.commit()
            if should_discover:
                for candidate in candidates:
                    run = dispatch(session, candidate, sources, kind="qualification" if qualification else "initial")
                    if run:
                        queued.append(run.id)
            _weekly_summary(session)
            session.commit()
        return {"status": "checked", "queued": queued, "gaps": sources.gaps + gaps}
    finally:
        release_lease("scheduler", ticket)


def _weekly_summary(session):
    from forecastlab_api.autopilot_outcomes import metrics
    previous = weekly_budget(session, now=utcnow() - timedelta(days=7))
    key = "weekly:" + previous.id
    if session.get(InboxEvent, key):
        return
    runs = session.scalar(select(func.count()).select_from(AutopilotRun).where(
        AutopilotRun.created_at >= previous.starts_at, AutopilotRun.created_at < previous.ends_at)) or 0
    if runs:
        totals = budget_totals(session, previous.id)
        scores = metrics(session)
        notify(session, key, "summary", "Weekly forecasting summary",
            f"Week of {previous.id}: {runs} versions; ${totals['used_usd']:.2f} used, ${totals['reserved_usd']:.2f} unresolved reservations. "
            f"Track record: {scores['resolved_questions']} confirmed resolved questions.")


def dashboard(session) -> dict:
    from forecastlab_api.autopilot_outcomes import metrics, proposal_list
    current = state(session)
    policy = session.get(AutopilotPolicy, current.policy_id) if current.policy_id else None
    week = weekly_budget(session)
    pending = session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["pending", "running"]))) or 0
    rows = session.scalars(select(ManagedQuestion).order_by(ManagedQuestion.created_at.desc()).limit(100)).all()
    activities = session.scalars(select(AutopilotDispatch).order_by(AutopilotDispatch.created_at.desc()).limit(30)).all()
    inbox = session.scalars(select(InboxEvent).order_by(InboxEvent.created_at.desc()).limit(50)).all()
    unknown = session.scalars(select(ExecutionCheckpoint).where(ExecutionCheckpoint.status == "started").order_by(ExecutionCheckpoint.created_at).limit(50)).all()
    totals = budget_totals(session, week.id)
    limit = policy_config(policy).weekly_usd if policy else 25
    from forecastlab_api.autopilot_models import FinalEstimateHold
    holds = session.scalar(select(func.coalesce(func.sum(FinalEstimateHold.cost_usd), 0)).join(
        ForecastRun, ForecastRun.id == FinalEstimateHold.run_id).join(AutopilotRun, AutopilotRun.run_id == ForecastRun.id).where(
        FinalEstimateHold.consumed.is_(False), ForecastRun.status.in_(["pending", "running"]))) or 0
    totals.update(limit_usd=limit, final_estimate_capacity_usd=float(holds),
        remaining_usd=max(0.0, limit - totals["used_usd"] - totals["reserved_usd"] - float(holds)))
    return {"schema_version": "autopilot_dashboard_v1", "enabled": current.enabled,
        "qualification_enabled": current.qualification_enabled, "pause_reason": current.pause_reason,
        "policy": policy_config(policy).model_dump(mode="json") if policy else PolicyConfig().model_dump(mode="json"),
        "policy_revision": policy.revision if policy else None, "last_tick_at": current.last_tick_at,
        "next_tick_at": as_utc(current.last_tick_at) + timedelta(minutes=15) if current.last_tick_at else None,
        "queue_count": pending, "budget": {**totals, "week": week.id, "resets_at": week.ends_at},
        "enable_gaps": enable_gaps(session), "qualification": qualification_status(session, policy),
        "managed_questions": [{"question_id": m.question_id, "indicator": m.indicator, "period": m.period,
            "status": m.status, "release_event": m.release_event, "release_at": json.loads(m.macro_json)["release_at"]} for m in rows],
        "activity": [{"id": a.id, "action": a.action, "reason": a.reason, "created_at": a.created_at,
            **json.loads(a.payload_json)} for a in activities],
        "inbox": [{"id": e.id, "kind": e.kind, "title": e.title, "detail": e.detail, "question_id": e.question_id,
            "created_at": e.created_at, "read_at": e.read_at} for e in inbox],
        "unresolved_calls": [{"id": c.id, "run_id": c.run_id, "stage": c.stage, "started_at": c.created_at} for c in unknown],
        "outcomes": proposal_list(session), "metrics": metrics(session)}

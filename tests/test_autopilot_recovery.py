from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from tests.test_autopilot import auto_run
from tests.test_autopilot import ready as _ready
from tests.test_question_selection import NOW

from forecastlab.macro import MacroSpec
from forecastlab_api.autopilot_models import AutopilotRun, ExecutionCheckpoint, ManagedQuestion, QuestionAdjudication
from forecastlab_api.autopilot_store import claim_lease, release_lease, state
from forecastlab_api.models import ForecastRun, PersonalForecast

ready = _ready


def complete(session, run_id, probability=.6, *, status="completed"):
    run = session.get(ForecastRun, run_id)
    run.status, run.finished_at = status, NOW + timedelta(seconds=90)
    personal = session.get(PersonalForecast, run_id)
    personal.outcome_status = "forecasted" if probability is not None else "insufficient_evidence"
    personal.result_json = json.dumps({"probability": probability})
    session.commit()


def test_refresh_freezes_threshold_cooldown_and_latest_failure(ready, monkeypatch):
    from forecastlab_api.autopilot_outcomes import metrics
    autopilot, sessions, sources, candidates, _ = ready
    first_id = auto_run(ready)
    candidate = next(c for c in candidates if c.macro.indicator == "cpi")
    changed = candidate.model_copy(update={"macro": candidate.macro.model_copy(update={"threshold": 99})})
    with sessions() as session:
        complete(session, first_id)
        first = session.get(AutopilotRun, first_id)
        assert autopilot.dispatch(session, changed, sources, kind="refresh", question_id=first.question_id) is None
        monkeypatch.setattr(autopilot, "utcnow", lambda: NOW + timedelta(days=2))
        refreshed = autopilot.dispatch(session, changed, sources, kind="refresh", question_id=first.question_id)
        assert refreshed is not None
        assert MacroSpec.model_validate_json(session.get(PersonalForecast, refreshed.id).macro_json).threshold != 99
        complete(session, refreshed.id, None, status="failed")
        assert autopilot.dispatch(session, changed, sources, kind="refresh", question_id=first.question_id) is None
        # Scoring never silently uses an older probability as the latest version.
        session.add(QuestionAdjudication(id="adjudication", question_id=first.question_id, revision=1,
            proposal_id=None, contract_hash="fixture", outcome=1, evidence_json="{}", confirmed_by="owner"))
        session.commit()
        result = metrics(session)
        assert result["initial"]["scored_questions"] == 1
        assert result["latest_prerelease"]["scored_questions"] == 0
        assert result["matched"]["questions"] == 0
        assert result["resolved_questions"] == 1


def test_outcomes_require_archive_confirmation_and_append_corrections(ready, monkeypatch):
    from forecastlab.http_client import SafeResponse
    from forecastlab_api import autopilot_outcomes as outcomes
    from forecastlab_api.models import ProspectiveOutcome
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    html = '''<pre>8:30 a.m. (ET) Friday, September 11, 2026
CONSUMER PRICE INDEX - AUGUST 2026
Over the last 12 months, the all items index increased 3.2 percent before seasonal adjustment.</pre>'''
    future = NOW + timedelta(days=8)
    monkeypatch.setattr(outcomes, "utcnow", lambda: future)
    monkeypatch.setattr(outcomes, "safe_get", lambda url, **_: SafeResponse(url=url, status_code=200, final_url=url,
        content=html.encode(), content_type="text/html"))
    with sessions() as session:
        complete(session, run_id)
        run = session.get(ForecastRun, run_id)
        managed = session.get(ManagedQuestion, run.question_id)
        proposal = outcomes.collect_outcome(session, managed)
        assert proposal is not None
        assert outcomes.metrics(session)["resolved_questions"] == 0
        original = outcomes.confirm(session, proposal.id, confirmed_by="146488758")
        assert original.revision == 1
        assert outcomes.confirm(session, proposal.id, confirmed_by="146488758").id == original.id
        correction = outcomes.confirm(session, proposal.id, confirmed_by="146488758", correction={
            "outcome": 0, "reason": "Reviewed official first-release rounding", "evidence_url": "https://www.bls.gov/news.release/cpi.nr0.htm"})
        assert correction.revision == 2 and correction.id != original.id
        assert len(session.scalars(select(QuestionAdjudication)).all()) == 2
        assert session.scalars(select(ProspectiveOutcome)).all() == []
        correction.outcome = 1
        with pytest.raises(ValueError, match="append_only"):
            session.commit()
        session.rollback()
        if session.get_bind().dialect.name == "postgresql":
            with pytest.raises(Exception, match="append.only"):
                session.execute(text("DELETE FROM question_adjudications"))
                session.commit()
            session.rollback()


def test_changed_calendar_suspends_without_dispatch(ready, monkeypatch):
    autopilot, sessions, sources, *_ = ready
    run_id = auto_run(ready)
    sources.releases = [r.model_copy(update={"release_at": r.release_at + timedelta(hours=1)}) for r in sources.releases]
    monkeypatch.setattr(autopilot, "database_selection_sources", lambda: sources)
    result = autopilot.reconcile()
    assert result["queued"] == []
    with sessions() as session:
        run = session.get(ForecastRun, run_id)
        assert session.get(ManagedQuestion, run.question_id).status == "schedule_review"


@pytest.mark.parametrize("known_conflict", [False, True])
def test_due_release_outcome_survives_rolling_index_but_not_known_conflict(ready, monkeypatch, known_conflict):
    from forecastlab_api import official_macro_outcomes
    autopilot, sessions, sources, *_ = ready
    run_id = auto_run(ready)
    with sessions() as session:
        complete(session, run_id)
        state(session).enabled = False
        session.commit()
    if known_conflict:
        sources.releases = [r.model_copy(update={"release_at": r.release_at + timedelta(hours=1)}) for r in sources.releases]
    else:
        sources.releases = []
    checked = []
    monkeypatch.setattr(autopilot, "database_selection_sources", lambda: sources)
    monkeypatch.setattr(autopilot, "utcnow", lambda: NOW + timedelta(days=8))
    monkeypatch.setattr(official_macro_outcomes, "process_managed_question", lambda session, managed: checked.append(managed.question_id))
    assert autopilot.reconcile()["queued"] == []
    assert bool(checked) is not known_conflict


def test_existing_proposal_does_not_block_automatic_official_adjudication(ready, monkeypatch):
    from forecastlab.http_client import SafeResponse
    from forecastlab_api import official_macro_outcomes
    from forecastlab_api.autopilot_models import OutcomeProposal

    autopilot, sessions, sources, *_ = ready
    run_id = auto_run(ready)
    future = NOW + timedelta(days=8)
    html = b"""<pre>8:30 a.m. (ET) Friday, September 11, 2026
CONSUMER PRICE INDEX - AUGUST 2026
Over the last 12 months, the all items index increased 3.4 percent before seasonal adjustment.</pre>"""
    monkeypatch.setattr(autopilot, "utcnow", lambda: future)
    monkeypatch.setattr(official_macro_outcomes, "utcnow", lambda: future)
    monkeypatch.setattr(official_macro_outcomes, "safe_get", lambda url, **_: SafeResponse(
        url=url, final_url=url, status_code=200, content=html,
        content_type="text/html", bytes_read=len(html)))
    sources.releases = []
    monkeypatch.setattr(autopilot, "database_selection_sources", lambda: sources)
    with sessions() as session:
        complete(session, run_id)
        managed = session.scalar(select(ManagedQuestion).where(ManagedQuestion.initial_run_id == run_id))
        session.add(OutcomeProposal(id="existing-proposal", question_id=managed.question_id,
            contract_hash="prior-proposal", payload_json="{}"))
        state(session).enabled = False
        session.commit()

    assert autopilot.reconcile()["queued"] == []
    with sessions() as session:
        managed = session.scalar(select(ManagedQuestion).where(ManagedQuestion.initial_run_id == run_id))
        adjudication = session.scalar(select(QuestionAdjudication).where(
            QuestionAdjudication.question_id == managed.question_id))
        assert managed.status == "resolved"
        assert adjudication is not None
        assert adjudication.confirmed_by == official_macro_outcomes.SYSTEM_ATTRIBUTION
        assert adjudication.outcome in (0, 1)


def test_due_official_outcome_proceeds_when_discovery_sources_fail(ready, monkeypatch):
    from forecastlab_api import official_macro_outcomes

    autopilot, sessions, *_ = ready
    run_id = auto_run(ready)
    with sessions() as session:
        complete(session, run_id)
        state(session).enabled = False
        session.commit()
    monkeypatch.setattr(autopilot, "utcnow", lambda: NOW + timedelta(days=8))
    monkeypatch.setattr(autopilot, "database_selection_sources", lambda: (_ for _ in ()).throw(TimeoutError()))
    checked = []
    monkeypatch.setattr(official_macro_outcomes, "process_managed_question",
        lambda session, managed: checked.append(managed.question_id))

    result = autopilot.reconcile()
    assert result["status"] == "selection_sources_unavailable"
    assert len(checked) == 1
    assert result["queued"] == []


def test_release_drain_lock_blocks_new_workers(client, monkeypatch):
    from forecastlab_api.config import settings
    monkeypatch.setattr(settings, "internal_secret", "test-release-secret")
    headers = {"authorization": "Bearer test-release-secret"}
    ticket = claim_lease("paid_worker")
    assert ticket
    result = client.post("/internal/release/pause", headers=headers).json()
    assert result["paused"] and not result["drained"]
    assert client.post("/internal/release/complete", headers=headers).status_code == 409
    release_lease("paid_worker", ticket)
    assert claim_lease("paid_worker") is None
    assert client.post("/internal/release/pause", headers=headers).json()["drained"]
    assert client.post("/internal/release/complete", headers=headers).status_code == 200
    final = claim_lease("paid_worker")
    assert final
    release_lease("paid_worker", final)


def test_completed_provider_call_survives_orchestration_restart(ready):
    from forecastlab.providers.base import ChatResult
    from forecastlab.schemas import ModelUsage
    from forecastlab_api.durable_execution import DurableModel
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    class Provider:
        name = model = "stub"
        def complete_json(self, **_):
            return ChatResult(content='{"result":1}', parsed={"result": 1}, usage=ModelUsage(cost_usd=.1))
    model = DurableModel(Provider(), run_id)
    request = {"schema_name": "test", "system": "s", "user": "u", "max_output_tokens": 100}
    model.complete_json(**request)
    with sessions() as session:
        assert session.scalar(select(ExecutionCheckpoint.status).where(ExecutionCheckpoint.run_id == run_id)) == "completed"
    class Unavailable(Provider):
        def complete_json(self, **_):
            raise AssertionError("A completed call must not reach the provider")
    restored = DurableModel(Unavailable(), run_id).complete_json(**{**request, "max_output_tokens": 200})
    assert restored.parsed == {"result": 1}


def test_weekly_limit_enforced_across_questions(ready):
    from tests.test_autopilot import reserve

    from forecastlab.errors import BudgetExceeded
    from forecastlab_api.autopilot import PolicyConfig
    from forecastlab_api.usage_ledger import PersistentUsageLedger
    autopilot, sessions, *_ = ready
    with sessions() as session:
        autopilot.approve_policy(session, PolicyConfig(weekly_usd=1), approved_by="owner")
        state(session).enabled = True
        session.commit()
    run_id = auto_run(ready)
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    reserve(ledger, run_id, "first", .6)
    with pytest.raises(BudgetExceeded, match="weekly_budget_exhausted"):
        reserve(ledger, run_id, "second", .5)


def test_interrupted_attempt_counts_active_time_not_queue_delay(ready, monkeypatch):
    from forecastlab_api.durable_execution import elapsed_execution
    from forecastlab_api.jobs import recover_stale_jobs
    from forecastlab_api.models import ForecastRunAttempt, Job
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    with sessions() as session:
        run = session.get(ForecastRun, run_id)
        job = session.get(Job, run.job_id)
        job.status, job.attempts = "running", 1
        job.heartbeat_at = NOW + timedelta(seconds=40)
        job.lease_expires_at = NOW + timedelta(seconds=45)
        session.add(ForecastRunAttempt(id="interrupted", run_id=run_id, job_id=job.id, attempt_number=1,
            status="running", started_at=NOW))
        session.commit()
        monkeypatch.setattr("forecastlab_api.jobs.utcnow", lambda: NOW + timedelta(days=2))
        assert recover_stale_jobs(session) == 1
        session.commit()
        assert elapsed_execution(session, run_id) == 48


def test_suspended_schedule_stops_already_queued_paid_calls(ready):
    from tests.test_autopilot import reserve

    from forecastlab.errors import BudgetExceeded
    from forecastlab_api.usage_ledger import PersistentUsageLedger
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    with sessions() as session:
        run = session.get(ForecastRun, run_id)
        session.get(ManagedQuestion, run.question_id).status = "schedule_review"
        session.commit()
    with pytest.raises(BudgetExceeded, match="question_suspended"):
        reserve(PersistentUsageLedger(sessions, max_cost_usd=5), run_id, "late", .1)


def test_empty_search_response_is_recovered_without_a_new_physical_call(ready):
    from forecastlab_api.durable_execution import DurableSearch
    run_id = auto_run(ready)
    class Search:
        name = "stub"
        calls = 0
        def search(self, query, *, max_results=5):
            self.calls += 1
            return []
    provider = Search()
    durable = DurableSearch(provider, run_id)
    assert durable.get_cached_result("unchanged", max_results=3) is None
    assert durable.search("unchanged", max_results=3) == []
    restarted = DurableSearch(provider, run_id)
    assert restarted.get_cached_result("unchanged", max_results=3) == []
    assert restarted.search("unchanged", max_results=3) == []
    assert provider.calls == 1

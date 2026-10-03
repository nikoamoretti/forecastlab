from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from forecastlab.macro import MacroDataError, MacroSpec, fetch_macro, normalize_observations
from forecastlab.root_event import RootEstimate, aggregate_root_estimates, contract_hash, digest, source_lineage


def spec():
    return MacroSpec(indicator="unemployment", observation_period="2040-01", threshold=5,
                     release_at=datetime(2040, 2, 5, 13, 30, tzinfo=UTC))


def test_same_event_aggregation_rejects_wrong_target_missing_role_and_bad_evidence():
    contract = spec().template("q", "c")
    packet = [{"claim_id": "e1", "usable": True}]
    estimates = [RootEstimate(contract_hash=contract_hash(contract), evidence_packet_hash=digest(packet),
        target_question=contract.normalized_question, resolution_date=contract.resolution_date.isoformat(),
        as_of=None, role=role, probability=p, reasoning="Evidence", evidence_ids=["e1"],
        uncertainties=[], developments_to_watch=[])
        for role, p in [("base_rate", .2), ("current_evidence", .6), ("skeptic", .4)]]
    result = aggregate_root_estimates(contract, packet, estimates, as_of=None)
    assert .3 < result["probability"] < .5
    assert result["shared_evidence"]
    for replacement in ({"target_question": "Will a driver matter?"}, {"as_of": "2030-01-01"},
                        {"evidence_ids": ["unknown"]}, {"contract_hash": "different"}):
        with pytest.raises(ValueError):
            aggregate_root_estimates(contract, packet, [estimates[0].model_copy(update=replacement), *estimates[1:]], as_of=None)
    with pytest.raises(ValueError):
        aggregate_root_estimates(contract, packet, estimates[:2], as_of=None)
    with pytest.raises(ValueError):
        RootEstimate.model_validate({**estimates[0].model_dump(), "probability": float("nan")})
    for probability in (float("nan"), float("inf"), -0.1, 1.1):
        with pytest.raises(ValueError, match="invalid_root_probability"):
            aggregate_root_estimates(contract, packet, [estimates[0].model_copy(update={"probability": probability}), *estimates[1:]], as_of=None)
    assert result == aggregate_root_estimates(contract, packet, list(reversed(estimates)), as_of=None)


@pytest.mark.parametrize("schema_name", ["root_event", "root_evidence", "graph_research_plan", "evidence_claims"])
def test_personal_schemas_reach_the_actual_provider_transport(monkeypatch, schema_name):
    from tests.test_openai_compatible import _stub_client

    from forecastlab.providers.openai_compatible import OpenAICompatibleProvider
    from forecastlab.root_event import strict_schema
    sent = _stub_client(monkeypatch)
    provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.openai.com/v1",
        model="gpt-5-mini", provider_id="openai")
    schema = strict_schema(RootEstimate)
    provider.complete_json(system="Return JSON", user="Test", schema_name=schema_name,
        max_output_tokens=4096, json_schema=schema,
        reasoning_effort="low" if schema_name == "root_event" else "minimal")
    assert sent[0]["json"]["response_format"] == {"type": "json_schema", "json_schema": {
        "name": schema_name, "strict": True, "schema": schema}}
    assert sent[0]["json"]["reasoning_effort"] == ("low" if schema_name == "root_event" else "minimal")


def test_macro_units_and_vintage_boundary():
    now = datetime(2026, 9, 4, tzinfo=UTC)
    args = dict(available_at=now, vintage="2026-09-03", source_url="https://bls.gov", revision_basis="test")
    payroll = normalize_observations("payrolls", {"2026-06": 150000, "2026-07": 150123}, **args)
    assert payroll[0].value == 123000
    assert payroll[0].units == "jobs"
    cpi = normalize_observations("cpi", {"2025-07": 100, "2026-07": 103}, **args)
    assert cpi[0].value == pytest.approx(3)
    assert cpi[0].seasonal_adjustment == "not_seasonally_adjusted"
    assert normalize_observations("cpi", {"2025-07": 100, "2026-07": 103.04}, **args)[0].value == 3.0
    with pytest.raises(ValueError, match="observation_period_must_precede_release"):
        MacroSpec.model_validate({**spec().model_dump(), "release_at": "2040-01-20T13:30:00Z"})
    with pytest.raises(MacroDataError, match="vintage_key_required"):
        fetch_macro(spec(), as_of=now)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"observations": [{"date": "2026-07-01", "value": "4.2"}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = fetch_macro(spec(), as_of=now, fred_api_key="test-private", client=client)
    assert seen[0].url.params["realtime_end"] == "2026-09-03"
    assert snapshot.observations[0].available_at < now
    assert "test-private" not in snapshot.model_dump_json()
    assert source_lineage("https://fred.stlouisfed.org/series/UNRATE", text="BLS UNRATE") == source_lineage("https://www.bls.gov/news.release/empsit.htm")
    assert source_lineage("https://notbls.gov/") != "agency:bls"


def test_draft_one_review_same_run_and_abstention(client):
    body = {"mode": "demo", "request_key": "personal-test-001", "macro": spec().model_dump(mode="json")}
    response = client.post("/api/forecast-drafts", json=body)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "awaiting_review"
    assert draft["cost_usd"] == 0
    assert client.post("/api/forecast-drafts", json=body).json()["run_id"] == draft["run_id"]
    conflict = client.post("/api/forecast-drafts", json={**body, "question": "Different question"})
    assert conflict.status_code == 409
    launched = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    assert launched.status_code == 200, launched.text
    assert launched.json()["run_id"] == draft["run_id"]
    assert launched.json()["result"]["probability"] is None
    assert launched.json()["outcome_status"] == "insufficient_evidence"
    repeat = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    assert repeat.status_code == 200, repeat.text
    report = client.get(f"/api/questions/{draft['question_id']}/report").json()
    assert report["version_count"] == 1
    assert report["latest_probability"] is None
    assert report["latest_run"]["node_runs"] == []
    assert client.get("/api/forecast-summaries").json()["total"] == 0
    assert any(row["id"] == draft["question_id"] for row in client.get("/api/forecast-summaries?include_fixtures=true").json()["items"])


def test_general_question_review_accepts_iso_timestamp_and_freezes_contract(client):
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import PersonalForecast
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "general-review-test",
        "question": "Will unemployment exceed 5% by 2040?"}).json()
    launched = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {
        "resolution_date": "2040-02-05T13:30:00Z", "normalized_question": "Will unemployment exceed 5% in January 2040?"}})
    assert launched.status_code == 200, launched.text
    assert launched.json()["result"]["contract"]["resolution_date"] == "2040-02-05T13:30:00Z"
    with SessionLocal() as session:
        record = session.get(PersonalForecast, draft["run_id"])
        record.contract_json = "{}"
        with pytest.raises(ValueError, match="contract_is_frozen"):
            session.commit()


@pytest.mark.parametrize("method", ["single_model_forecaster_v1", "three_track_forecaster"])
def test_personal_baseline_outcome_matches_its_persisted_probability(client, method):

    from sqlalchemy import select

    from forecastlab.schemas import ForecastContract
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastVersion, PersonalForecast, Question
    from forecastlab_api.personal_forecasts import envelope
    from forecastlab_api.pipeline import create_run_record, execute_run, resolve_for_question

    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "baseline-review",
        "macro": spec().model_dump(mode="json")}).json()
    reviewed = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}}).json()
    contract = ForecastContract.model_validate(reviewed["result"]["contract"])
    with SessionLocal() as session:
        question = session.get(Question, draft["question_id"])
        context = resolve_for_question(question, profile_id=method, mode="demo")
        run = create_run_record(session, question=question, context=context, as_of=None, enqueue=False)
        envelope(session, run, request_key="baseline-run", request_hash="baseline", contract=contract, macro=spec())
        session.commit()
        execute_run(session, run)
        version = session.scalar(select(ForecastVersion).where(ForecastVersion.run_id == run.id))
        personal = session.get(PersonalForecast, run.id)
        assert version.ensemble_probability is not None
        assert personal.outcome_status == "forecasted"
        assert json.loads(personal.result_json)["probability"] == version.ensemble_probability


def test_atomic_cost_reservations(client):
    from forecastlab.errors import BudgetExceeded
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.usage_ledger import PersistentUsageLedger
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "concurrency-test", "macro": spec().model_dump(mode="json")}).json()
    ledger = PersistentUsageLedger(SessionLocal, max_cost_usd=1)
    def reserve(index):
        try:
            ledger.reserve(run_id=draft["run_id"], run_attempt_id=None, logical_call_id=str(index),
                physical_attempt_number=1, stage="test", provider_type="model", provider="test", model="test",
                reserved_input_tokens=1, reserved_output_tokens=1, reserved_cost_usd=.6)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        successes = list(pool.map(reserve, range(4)))
    assert sum(successes) == 1
    assert ledger.totals(draft["run_id"]).total_cost_usd == pytest.approx(.6)


@pytest.mark.parametrize("late_response", ["success", "retryable_failure"])
def test_execution_deadline_retains_cost_and_prevents_a_paid_retry(monkeypatch, late_response):
    from forecastlab import deadline
    from forecastlab.errors import BudgetExceeded, TransientProviderError
    from forecastlab.ledger import InMemoryUsageLedger
    from forecastlab.physical import run_physical_attempts
    from forecastlab.schemas import ModelUsage

    clock = [100.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: clock[0])
    ledger = InMemoryUsageLedger()
    ledger.deadline = deadline.ExecutionDeadline.after(5)
    sent = []
    def send(attempt):
        sent.append(attempt)
        assert deadline.request_timeout(ledger, 60, "test") == 5
        clock[0] += 6
        if late_response == "retryable_failure":
            raise TransientProviderError("Timed out")
        return {"probability": .7}, ModelUsage(model="test", prompt_tokens=1, completion_tokens=1, cost_usd=.1)

    with pytest.raises(BudgetExceeded, match="max_wall_clock_seconds"):
        run_physical_attempts(ledger=ledger, run_id="run", run_attempt_id=None, logical_call_id="call",
            stage="forecast", provider_type="model", provider="test", model="test",
            reserved_input_tokens=1, reserved_output_tokens=1, reserved_cost_usd=.2, send=send,
            sleep=lambda _: pytest.fail("No retry sleep after the deadline"))
    assert sent == [1]
    assert len(ledger.entries("run")) == 1
    assert ledger.totals("run").total_cost_usd == pytest.approx(.1 if late_response == "success" else .2)


def test_prospective_creation_does_not_require_or_accept_an_outcome(client):
    body = {"name": "Pilot", "questions": [{"macro": spec().model_dump(mode="json"),
        "cutoff": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "release_event": "employment-2040-02"}]}
    response = client.post("/api/prospective/cohorts", json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["matched_resolved_count"] == 0
    assert data["questions"][0]["outcome"] is None
    assert all(row["brier_score"] is None for row in data["methods"])
    denied = client.post(f"/api/prospective/entries/{data['questions'][0]['id']}/outcomes", json={
        "outcome": 1, "source_url": "https://bls.gov", "evidence": "Not released", "confirmed_by": "Tester"})
    assert denied.status_code == 409


def test_evidence_gate_excludes_fallback_and_unrelated_claims():
    from tests.test_evidence_sufficiency import _claim

    from forecastlab.root_event import EvidenceAssessment, assess_packet
    fallback, _ = _claim("fallback", "n", "bls.gov", source_class="primary", extraction_method="document_fallback")
    unrelated, _ = _claim("unrelated", "n", "apple.com", source_class="primary")
    packet, gaps = assess_packet([fallback, unrelated], [
        EvidenceAssessment(claim_id="fallback", relevant=True, classification="supporting",
            required_sections=["resolution", "reference_class", "current_conditions"], reason="Only an excerpt"),
        EvidenceAssessment(claim_id="unrelated", relevant=False, classification="unusable",
            required_sections=[], reason="App store navigation does not evidence a hardware launch"),
    ])
    assert all(not item["usable"] for item in packet)
    assert len(gaps) == 4


def test_copied_quotes_are_not_independent_evidence():
    from tests.test_evidence_sufficiency import _claim

    from forecastlab.root_event import EvidenceAssessment, assess_packet
    original, _ = _claim("original", "n", "bls.gov", source_class="primary")
    copy = original.model_copy(update={"id": "copy", "source_url": "https://aggregator.example/news"})
    packet, _ = assess_packet([original, copy], [EvidenceAssessment(claim_id=c.id, relevant=True,
        classification="background", required_sections=["current_conditions"], reason="The same quotation") for c in [original, copy]])
    assert packet[0]["usable"] and not packet[1]["usable"]
    assert packet[1]["duplicate_quote"]
    assert packet[0]["corroboration_group"] == packet[1]["corroboration_group"]


def test_bls_missing_month_is_retained_without_imputation_and_wrong_series_rejected(monkeypatch):
    import forecastlab.macro as macro
    monkeypatch.setattr(macro, "utcnow", lambda: datetime(2026, 9, 4, tzinfo=UTC))
    rows = [{"year": "2026", "period": "M08", "value": "4.1"},
            {"year": "2025", "period": "M10", "value": "-", "footnotes": [{"text": "Unavailable"}]}]
    def transport(series):
        return httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"seriesID": series, "data": rows}]}}))
    with httpx.Client(transport=transport("LNS14000000")) as http:
        result = fetch_macro(spec(), client=http)
    assert len(result.observations) == 1
    assert result.raw_payload["Results"]["series"][0]["data"][1]["value"] == "-"
    with httpx.Client(transport=transport("wrong-series")) as http, pytest.raises(MacroDataError, match="series_mismatch"):
        fetch_macro(spec(), client=http)
    future = httpx.MockTransport(lambda r: httpx.Response(200, json={"observations": [
        {"date": "2026-07-01", "value": "4.1", "realtime_start": "2026-09-04"}]}))
    with httpx.Client(transport=future) as http, pytest.raises(MacroDataError, match="vintage_after_cutoff"):
        fetch_macro(spec(), as_of=datetime(2026, 9, 4, tzinfo=UTC), fred_api_key="test", client=http)
    with pytest.raises(MacroDataError, match="intraday_publication_time_evidence_required"):
        fetch_macro(spec(), as_of=datetime(2026, 9, 4, 15, tzinfo=UTC), fred_api_key="test")


def test_preparation_failures_and_retries_remain_in_the_original_budget(client, monkeypatch):
    from types import SimpleNamespace

    from forecastlab_api import personal_forecasts
    from forecastlab_api.config import settings
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, PersonalForecast
    monkeypatch.setattr(settings, "embedded_worker", False)
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "paid-preparation-test",
        "question": "Will unemployment exceed 5% by 2040?"}).json()
    def builder(**kwargs):
        class FailingModel:
            name = "mock"
            model = "mock-forecast-v1"
            def complete_json(self, **request):
                with SessionLocal() as session:
                    assert session.get(ForecastRun, kwargs["run_id"])
                    assert session.get(PersonalForecast, kwargs["run_id"])
                ledger = kwargs["ledger"]
                row = ledger.reserve(run_id=kwargs["run_id"], run_attempt_id=kwargs["run_attempt_id"],
                    logical_call_id=kwargs["run_attempt_id"], physical_attempt_number=1, stage="prepare_contract",
                    provider_type="model", provider="test", model="test", reserved_input_tokens=10,
                    reserved_output_tokens=10, reserved_cost_usd=.1)
                ledger.fail(row.id, error_category="provider_failed", error_message="Recorded failure")
                raise RuntimeError("Recorded provider failure")
        return FailingModel()
    monkeypatch.setattr(personal_forecasts, "build_model_provider", builder)
    for attempt in (1, 2):
        with SessionLocal() as session, pytest.raises(RuntimeError):
            personal_forecasts.prepare_draft(session, session.get(ForecastRun, draft["run_id"]), job=SimpleNamespace(attempts=attempt))
    receipt = client.get(f"/api/forecast-drafts/{draft['run_id']}").json()
    assert receipt["cost_usd"] == pytest.approx(.2)
    assert receipt["status"] == "failed" and receipt["outcome_status"] == "execution_failed"
    assert receipt["contract"] is None


def test_research_provider_failure_is_a_failed_execution_not_an_abstention(client, monkeypatch):
    from forecastlab.errors import PermanentProviderError
    from forecastlab_api import pipeline
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "provider-failure-test",
        "question": "Will unemployment exceed 5% by 2040?"}).json()
    class FailingModel:
        name = "scripted"
        model = "mock-forecast-v1"
        def complete_json(self, **kwargs):
            raise PermanentProviderError("Provider unavailable")
    monkeypatch.setattr(pipeline, "build_model_provider", lambda **kwargs: FailingModel())
    with pytest.raises(PermanentProviderError):
        client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    result = client.get(f"/api/forecast-drafts/{draft['run_id']}").json()
    assert result["status"] == "failed"
    assert result["outcome_status"] == "execution_failed"
    assert result["result"].get("probability") is None


def test_version_selection_and_cohort_rerun_budget_isolation(client):
    draft = client.post("/api/forecast-drafts", json={"mode": "demo", "request_key": "history-selection-test",
        "macro": spec().model_dump(mode="json")}).json()
    client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    rerun = client.post(f"/api/questions/{draft['question_id']}/runs", json={"mode": "demo", "profile_id": "root_event_ensemble_v1"})
    assert rerun.status_code == 200, rerun.text
    old = client.get(f"/api/questions/{draft['question_id']}/report?run_id={draft['run_id']}").json()
    assert old["is_historical_view"]
    assert old["latest_run"]["id"] == draft["run_id"]
    assert client.get(f"/api/questions/{draft['question_id']}/report?run_id=unknown").status_code == 404
    cohort = client.post("/api/prospective/cohorts", json={"name": "No escaped retries", "questions": [{
        "macro": spec().model_dump(mode="json"), "cutoff": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "release_event": "release"}]}).json()
    qid = cohort["questions"][0]["question_id"]
    assert client.post(f"/api/questions/{qid}/runs", json={"mode": "live", "profile_id": "root_event_ensemble_v1"}).status_code == 409


def test_successful_root_pipeline_uses_only_same_event_estimates(client, monkeypatch):
    import uuid

    from tests.test_evidence_sufficiency import _claim

    from forecastlab.graph_research import GraphResearchResult, NodeResearchPlan
    from forecastlab.providers.base import ChatResult
    from forecastlab.schemas import ModelUsage
    from forecastlab_api import pipeline, root_executor

    calls = []
    class Model:
        name = "scripted"
        model = "mock-forecast-v1"
        def complete_json(self, **kwargs):
            assert kwargs["json_schema"]["additionalProperties"] is False
            assert kwargs["reasoning_effort"] == ("minimal" if kwargs["schema_name"] == "root_evidence" else "low")
            body = json.loads(kwargs["user"])
            calls.append(body)
            if kwargs["schema_name"] == "root_evidence":
                parsed = {"assessments": [{"claim_id": c["id"], "classification": "background", "relevant": True,
                    "required_sections": ["resolution", "reference_class", "current_conditions"],
                    "reason": "Test source has all three factual sections"} for c in body["claims"]]}
            else:
                assert "estimates" not in body and "node_probability" not in body
                parsed = {k: body[k] for k in ("contract_hash", "evidence_packet_hash", "target_question", "resolution_date", "as_of", "role")}
                parsed.update(probability={"base_rate": .2, "current_evidence": .6, "skeptic": .4}[body["role"]],
                    reasoning="Cited test finding", evidence_ids=[body["evidence"][0]["claim_id"]],
                    uncertainties=["Future release"], developments_to_watch=["Next release"])
            return ChatResult(content=json.dumps(parsed), parsed=parsed, usage=ModelUsage(model="mock-forecast-v1"))

    def research(self, node):
        claim, _ = _claim(str(uuid.uuid4()), node.id, "bls.gov", source_class="primary")
        evidence = {"id": claim.evidence_item_id, "url": claim.source_url, "title": claim.source_title,
            "publisher": claim.publisher, "excerpt": claim.excerpt, "content_hash": "test", "source_class": "primary",
            "source_available_at": claim.source_available_at.isoformat(), "retrieved_at": claim.retrieval_date.isoformat(),
            "published_at": claim.publication_date.isoformat(), "publication_date_verified": True,
            "publication_date_source": "fixture",
            "temporal_basis": "publication_date"}
        return GraphResearchResult(node_id=node.id, plan=NodeResearchPlan(primary_research_question=node.question,
            supporting_search_queries=[node.question], preferred_sources=[], required_evidence_types=["facts"]),
            queries_attempted=[node.question], sources_checked=[], evidence=[evidence], claims=[claim])
    draft = client.post("/api/forecast-drafts", json={"question": "Will the unemployment rate exceed 5% by 2040?",
        "mode": "demo", "request_key": "success-pipeline-test"}).json()
    monkeypatch.setattr(pipeline, "build_model_provider", lambda **kwargs: Model())
    monkeypatch.setattr(root_executor.GraphResearchExecutor, "execute", research)
    response = client.post(f"/api/forecast-drafts/{draft['run_id']}/launch", json={"review": {}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["outcome_status"] == "forecasted", result.get("evidence_gaps")
    assert len(result["estimates"]) == 3
    assert .3 < result["probability"] < .5
    assert len(calls) == 4


def test_cohort_freeze_budget_and_append_only_outcomes(client, monkeypatch):
    from sqlalchemy import select

    from forecastlab.errors import BudgetExceeded
    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import prospective
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, ForecastVersion, PersonalForecast, ProspectiveOutcome
    from forecastlab_api.usage_ledger import PersistentUsageLedger
    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings={
            "model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5})
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)
    now = datetime.now(UTC)
    cohort = client.post("/api/prospective/cohorts", json={"name": "Bounded", "budget_usd": 1,
        "questions": [{"macro": spec().model_dump(mode="json"), "cutoff": (now + timedelta(days=1)).isoformat(), "release_event": "release"}]}).json()
    frozen = client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"})
    assert frozen.status_code == 200, frozen.text
    data = frozen.json()
    cells = data["questions"][0]["cells"]
    assert len(cells) == len(prospective.METHODS) == 4
    repeated = client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"}).json()
    assert repeated["manifest_hash"] == data["manifest_hash"]
    from forecastlab_api.models import ProspectiveCohort, ProspectiveEntry
    with SessionLocal() as session:
        session.get(ProspectiveCohort, data["id"]).status = "running"
        session.commit()
        run = session.get(ForecastRun, cells[0]["run_id"])
        prospective.check_assignment(session, run)
        entry = session.get(ProspectiveEntry, data["questions"][0]["id"])
        entry.cutoff += timedelta(hours=1)
        with pytest.raises(ValueError, match="frozen_assignment_changed"):
            prospective.check_assignment(session, run)
        session.rollback()
        monkeypatch.setattr(prospective, "forecasting_source_hash", lambda: "changed-source")
        with pytest.raises(ValueError, match="frozen_code_or_manifest_changed"):
            prospective.check_assignment(session, run)
    ledger = PersistentUsageLedger(SessionLocal, max_cost_usd=5)
    args = dict(run_attempt_id=None, logical_call_id="reservation", physical_attempt_number=1, stage="test",
        provider_type="model", provider="test", model="test", reserved_input_tokens=1, reserved_output_tokens=1, reserved_cost_usd=.7)
    ledger.reserve(run_id=cells[0]["run_id"], **args)
    with pytest.raises(BudgetExceeded, match="cohort_budget_exhausted"):
        ledger.reserve(run_id=cells[1]["run_id"], **args)
    with SessionLocal() as session:
        for cell in cells:
            run = session.get(ForecastRun, cell["run_id"])
            run.status = "completed"
            run.finished_at = now
            session.get(PersonalForecast, run.id).outcome_status = "forecasted"
            session.add(ForecastVersion(id=cell["run_id"], question_id=run.question_id, run_id=run.id, ensemble_probability=.7))
        session.commit()
    monkeypatch.setattr(prospective, "utcnow", lambda: datetime(2041, 1, 1, tzinfo=UTC))
    entry_id = data["questions"][0]["id"]
    for value in (1, 0):
        recorded = client.post(f"/api/prospective/entries/{entry_id}/outcomes", json={"outcome": value,
            "source_url": "https://www.bls.gov/dated-release", "evidence": "Dated source, correction explained", "confirmed_by": "Test reviewer"})
        assert recorded.status_code == 201, recorded.text
    scored = client.post(f"/api/prospective/cohorts/{cohort['id']}/score").json()
    assert scored["matched_resolved_count"] == 1
    assert all(m["brier_score"] == pytest.approx(.49) for m in scored["methods"])
    assert scored["questions"][0]["outcome"]["revision"] == 2
    with SessionLocal() as session:
        outcome = session.scalar(select(ProspectiveOutcome))
        outcome.evidence = "Overwrite"
        with pytest.raises(ValueError, match="append_only"):
            session.commit()


def test_new_cohorts_use_strict_three_track_and_reports_follow_frozen_methods(client, monkeypatch):
    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import prospective
    from forecastlab_api.db import SessionLocal

    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings={
            "model_provider": "openrouter", "model_name": "openai/gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5})
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)
    now = datetime.now(UTC)

    def freeze(name: str) -> dict:
        cohort = client.post("/api/prospective/cohorts", json={"name": name, "budget_usd": 1,
            "questions": [{"macro": spec().model_dump(mode="json"), "cutoff": (now + timedelta(days=1)).isoformat(),
                           "release_event": "release"}]}).json()
        return client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"}).json()

    current = prospective.METHODS
    assert current == ("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_strict_forecaster_v1",
                       "statistical_baseline_v1")
    legacy = ("root_event_ensemble_v1", "single_model_forecaster_v1", "three_track_forecaster")
    monkeypatch.setattr(prospective, "METHODS", legacy)
    earlier = freeze("Earlier")
    monkeypatch.setattr(prospective, "METHODS", current)
    data = freeze("Strict")

    assert [m["method"] for m in data["methods"]] == list(current)
    assert {c["method"] for c in data["questions"][0]["cells"]} == set(current)
    with SessionLocal() as session:
        report = prospective.cohort_report(session, earlier["id"])
    assert [m["method"] for m in report["methods"]] == list(legacy)
    assert {m["method"]: m["assigned"] for m in report["methods"]} == dict.fromkeys(legacy, 1)


def _schedule_sources(*, release_at, documents_ok=True):
    from forecastlab.question_selection import ScheduledRelease
    from forecastlab_api.question_suggestions import SelectionSources

    checked = datetime.now(UTC)
    url, confirm = "https://www.dol.gov/newsroom/economicdata/empsit_test.pdf", "https://www.newyorkfed.org/test.html"
    release = ScheduledRelease(family="empsit", observation_period="2040-01", release_at=release_at, source_url=url,
        source_hash="a" * 64, checked_at=checked, schedule_basis="dol_fed_schedule_v1",
        quote="The Employment Situation for January 2040 is scheduled to be released on February 5, 2040.",
        verification_sources=[{"url": confirm, "source_hash": "b" * 64, "checked_at": checked.isoformat(),
                               "role": "current_release_time_confirmation", "source_lineage": "agency:bls"}])
    documents = {url: {"sha256": "a" * 64, "path": "calendar.pdf"}, confirm: {"sha256": "b" * 64 if documents_ok else "c" * 64}}
    return SelectionSources(checked_at=checked, releases=[release], documents=documents)


def _freeze_with_sources(client, monkeypatch, sources_fn):
    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import prospective
    from forecastlab_api.config import settings

    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings={
            "model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5})
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)
    monkeypatch.setattr(settings, "cohort_schedule_evidence", True)
    monkeypatch.setattr(prospective, "selection_sources", sources_fn)
    now = datetime.now(UTC)
    cohort = client.post("/api/prospective/cohorts", json={"name": "Schedule", "budget_usd": 1,
        "questions": [{"macro": spec().model_dump(mode="json"), "cutoff": (now + timedelta(days=1)).isoformat(),
                       "release_event": "release"}]}).json()
    frozen = client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"})
    assert frozen.status_code == 200, frozen.text
    return frozen.json()


def _frozen_state(cohort_id):
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import PersonalForecast, ProspectiveAssignment, ProspectiveCohort

    with SessionLocal() as session:
        manifest = json.loads(session.get(ProspectiveCohort, cohort_id).manifest_json)
        run_ids = [row.run_id for row in session.scalars(select(ProspectiveAssignment).where(
            ProspectiveAssignment.cohort_id == cohort_id))]
        results = [json.loads(session.get(PersonalForecast, run_id).result_json) for run_id in run_ids]
    return manifest, results


def test_cohort_freeze_attaches_verified_official_schedule_as_resolution_evidence(client, monkeypatch):
    sources = _schedule_sources(release_at=spec().release_at)
    data = _freeze_with_sources(client, monkeypatch, lambda: sources)
    manifest, results = _frozen_state(data["id"])

    official = manifest["entries"][0]["official_schedule"]
    assert official["source_urls"] == [sources.releases[0].source_url]
    assert official["source_sha256"] == ["a" * 64]
    assert len(results) == 4
    for result in results:
        (item,) = result["resolution_evidence"]
        assert item["required_sections"] == ["resolution"] and item["usable"] and item["primary_source"]
        assert item["claim_id"] == official["claim_ids"][0]
        assert item["quote"].startswith("The Employment Situation for January 2040")


@pytest.mark.parametrize(("case", "gap"), [
    ("other_release", "official_schedule_release_not_verified"),
    ("bad_hash", "official_schedule_documents_unverified"),
    ("network", "official_schedule_sources_unavailable"),
])
def test_cohort_freeze_records_schedule_gap_without_evidence(client, monkeypatch, case, gap):
    if case == "network":
        def sources_fn():
            raise httpx.ConnectError("offline")
    else:
        release_at = spec().release_at + (timedelta(days=1) if case == "other_release" else timedelta())
        sources = _schedule_sources(release_at=release_at, documents_ok=case != "bad_hash")
        def sources_fn():
            return sources
    data = _freeze_with_sources(client, monkeypatch, sources_fn)
    manifest, results = _frozen_state(data["id"])

    assert manifest["entries"][0]["official_schedule"] == {"gap": gap}
    assert all("resolution_evidence" not in result for result in results)


def test_cohort_freeze_skips_schedule_lookup_when_disabled(client, monkeypatch):
    from forecastlab_api.config import settings

    def forbidden():
        raise AssertionError("schedule sources must not be fetched when disabled")

    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import prospective

    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings={
            "model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5})
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)
    monkeypatch.setattr(prospective, "selection_sources", forbidden)
    assert settings.cohort_schedule_evidence is False
    now = datetime.now(UTC)
    cohort = client.post("/api/prospective/cohorts", json={"name": "Disabled", "budget_usd": 1,
        "questions": [{"macro": spec().model_dump(mode="json"), "cutoff": (now + timedelta(days=1)).isoformat(),
                       "release_event": "release"}]}).json()
    frozen = client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"})
    assert frozen.status_code == 200, frozen.text
    manifest, results = _frozen_state(frozen.json()["id"])
    assert manifest["entries"][0]["official_schedule"] == {"gap": "official_schedule_lookup_disabled"}
    assert all("resolution_evidence" not in result for result in results)

from __future__ import annotations

import json as json_lib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from forecastlab.hashing import content_hash
from forecastlab.profiles import load_profile
from forecastlab.schemas import FetchedDocument
from forecastlab.timeutil import utcnow
from forecastlab_api.models import EvidenceItem, ForecastRun, ForecastVersion, ProviderCallLedger, Question
from forecastlab_api.paid_smoke import (
    ABSENT_MESSAGE,
    CREDENTIALS_MISSING_MESSAGE,
    SMOKE_PROFILE_ID,
    _audit,
    _require_valid_probability,
    credentials_ready,
    execute_live_smoke,
    main,
    opted_in,
    print_result,
)

LIVE_SECRETS = {
    "model_provider": "xai",
    "model_name": "grok-test",
    "model_base_url": "https://api.x.ai/v1",
    "model_api_key": "xai-test-key",
    "search_provider": "tavily",
    "search_api_key": "tvly-test-key",
    "max_cost_usd": 0.25,
    "model_timeout_seconds": 15,
}

PLAN = {
    "objective": "Assess whether U-3 reaches 5%",
    "approach": "Read official BLS releases",
    "subquestions": [
        {
            "text": "What is the latest official U-3 rate?",
            "purpose": "Current evidence",
            "preferred_source_types": ["official"],
            "search_queries": ["US civilian unemployment rate U-3 BLS"],
            "expected_output": "Latest official monthly U-3",
            "relationship_to_forecast": "direct",
        }
    ],
}

FORECAST = {
    "probability": 0.22,
    "reasoning_summary": "The latest official U-3 reading remains below 5.0 percent.",
    "key_drivers": [
        {
            "factor": "current official U-3",
            "direction": "down",
            "importance": 0.8,
            "evidence_ids": [],
            "inference": True,
        }
    ],
    "counterarguments": ["A sharp labor-market break could still occur before mid-2027."],
    "unresolved_uncertainties": ["Future labor demand"],
    "resolver_risk": 0.1,
    "evidence_quality": 0.7,
}


class _Elapsed:
    def total_seconds(self) -> float:
        return 0.02


class _HttpResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = {"x-request-id": "stub-rid"}
        self.elapsed = _Elapsed()
        self.text = json_lib.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _StubClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def post(self, url: str, headers=None, json=None):
        if "tavily" in url:
            return _HttpResponse(
                {
                    "results": [
                        {
                            "title": "Employment Situation",
                            "url": "https://www.bls.gov/news.release/empsit.nr0.htm",
                            "content": "The unemployment rate was 4.1 percent.",
                            "score": 0.9,
                        }
                    ]
                }
            )
        messages = (json or {}).get("messages") or []
        user = messages[-1]["content"] if messages else ""
        payload = PLAN if "subquestions_needed" in user or "research_plan" in user else FORECAST
        return _HttpResponse(
            {
                "id": "req-stub",
                "choices": [{"message": {"content": json_lib.dumps(payload)}}],
                "usage": {"prompt_tokens": 24, "completion_tokens": 12},
            }
        )


def _live_secrets_env(monkeypatch) -> None:
    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.working_tree_dirty", lambda: False)
    monkeypatch.setattr("forecastlab_api.paid_smoke.load_secrets", lambda: LIVE_SECRETS)
    monkeypatch.setattr("forecastlab_api.pipeline.load_secrets", lambda: LIVE_SECRETS)
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _StubClient)
    monkeypatch.setattr("forecastlab.providers.search.httpx.Client", _StubClient)
    monkeypatch.setattr("forecastlab.run_cache.fetch_document", _live_document)
    monkeypatch.setattr("forecastlab.fetch.fetch_document", _live_document)
    monkeypatch.setattr("forecastlab_api.paid_smoke.apply_schema", lambda: None)


def _live_document(url: str, **_kwargs) -> FetchedDocument:
    text = "The seasonally adjusted U-3 unemployment rate was 4.1 percent."
    return FetchedDocument(
        url=url,
        title="Employment Situation",
        publisher="www.bls.gov",
        published_at=datetime(2026, 1, 10, tzinfo=UTC),
        retrieved_at=utcnow(),
        text=text,
        content_hash=content_hash(text),
        snapshot_verification_status="live",
        rejected=False,
        as_of_eligible=True,
    )


def _live_context() -> str:
    return json_lib.dumps(
        {
            "model_provider": "xai",
            "search_provider": "tavily",
            "model_is_mock": False,
            "search_is_mock": False,
            "fixture_evidence_used": False,
        }
    )


def _default_ledger() -> list[dict[str, Any]]:
    return [
        {
            "logical_call_id": "model-1",
            "physical_attempt_number": 1,
            "stage": "forecast",
            "provider_type": "model",
            "provider": "xai",
            "status": "succeeded",
        },
        {
            "logical_call_id": "search-1",
            "physical_attempt_number": 1,
            "stage": "search",
            "provider_type": "search",
            "provider": "tavily",
            "status": "succeeded",
        },
    ]


def _seed_smoke_run(
    session,
    *,
    status: str = "completed",
    probability: float | None = 0.22,
    include_version: bool = True,
    include_evidence: bool = True,
    evidence_url: str = "https://www.bls.gov/news.release/empsit.nr0.htm",
    evidence_rejected: bool = False,
    evidence_eligible: bool = True,
    evidence_rows: list[dict[str, Any]] | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
    fixture_evidence_used: bool = False,
    total_cost: float = 0.01,
) -> ForecastRun:
    question = Question(
        id=str(uuid.uuid4()),
        original_text="Will U-3 reach 5.0 percent before mid-2027?",
        requested_mode="live",
        requested_profile_id=SMOKE_PROFILE_ID,
    )
    session.add(question)
    session.flush()
    run = ForecastRun(
        id=str(uuid.uuid4()),
        question_id=question.id,
        profile_id=SMOKE_PROFILE_ID,
        mode="live",
        status=status,
        fixture_evidence_used=fixture_evidence_used,
        execution_context_json=_live_context(),
        total_cost_usd=total_cost,
        cost_usd=total_cost,
    )
    session.add(run)
    session.flush()
    if include_version:
        session.add(
            ForecastVersion(
                id=str(uuid.uuid4()),
                question_id=question.id,
                run_id=run.id,
                ensemble_probability=probability,
            )
        )
    rows = evidence_rows
    if rows is None and include_evidence:
        rows = [{"url": evidence_url, "rejected": evidence_rejected, "as_of_eligible": evidence_eligible}]
    for item in rows or []:
        session.add(
            EvidenceItem(
                id=str(uuid.uuid4()),
                run_id=run.id,
                url=str(item["url"]),
                rejected=bool(item.get("rejected", False)),
                as_of_eligible=bool(item.get("as_of_eligible", True)),
            )
        )
    for row in ledger_rows if ledger_rows is not None else _default_ledger():
        session.add(
            ProviderCallLedger(
                id=str(uuid.uuid4()),
                run_id=run.id,
                logical_call_id=str(row["logical_call_id"]),
                physical_attempt_number=int(row["physical_attempt_number"]),
                stage=str(row["stage"]),
                provider_type=str(row["provider_type"]),
                provider=str(row["provider"]),
                status=str(row["status"]),
            )
        )
    session.flush()
    return run


def _audit_seeded(session, **kwargs) -> dict[str, Any]:
    run = _seed_smoke_run(session, **kwargs)
    session.commit()
    return _audit(session, run, expected_model="xai", expected_search="tavily", profile=load_profile(SMOKE_PROFILE_ID))


def test_paid_smoke_does_nothing_without_opt_in(monkeypatch, capsys) -> None:
    provider_calls = {"count": 0}

    class _ForbiddenClient(_StubClient):
        def post(self, url: str, headers=None, json=None):
            provider_calls["count"] += 1
            raise AssertionError("provider_called")

    monkeypatch.delenv("FORECASTLAB_RUN_PAID_SMOKE", raising=False)
    monkeypatch.setattr("forecastlab_api.paid_smoke.credentials_ready", lambda: True)
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _ForbiddenClient)
    monkeypatch.setattr("forecastlab.providers.search.httpx.Client", _ForbiddenClient)
    monkeypatch.setattr(
        "forecastlab_api.paid_smoke.execute_live_smoke",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("smoke_executed")),
    )
    assert opted_in() is False
    assert main() == 0
    assert ABSENT_MESSAGE in capsys.readouterr().out
    assert provider_calls["count"] == 0


def test_paid_smoke_refuses_missing_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.load_secrets", lambda: {"model_provider": "mock"})
    monkeypatch.setattr(
        "forecastlab_api.paid_smoke.execute_live_smoke",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("smoke_executed")),
    )
    assert credentials_ready({"model_provider": "mock"}) is False
    assert main() == 2
    assert CREDENTIALS_MISSING_MESSAGE in capsys.readouterr().out


def test_stubbed_paid_smoke_completes_without_mock_or_fixture_evidence(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal

    _live_secrets_env(monkeypatch)
    payload = execute_live_smoke(session_factory=SessionLocal)
    assert payload["status"] == "completed"
    assert payload["probability"] is not None
    assert 0.01 <= float(payload["probability"]) <= 0.99
    assert payload["model_provider"] == "xai"
    assert payload["search_provider"] == "tavily"
    assert payload["ledger"]
    assert any(row["provider"] == "xai" for row in payload["ledger"])
    assert any(row["provider"] == "tavily" for row in payload["ledger"])
    assert all(row["provider"] != "openai_compatible" for row in payload["ledger"])
    assert payload["total_cost_usd"] <= 0.25 + 1e-9

    with SessionLocal() as session:
        evidence = session.scalars(select(EvidenceItem).where(EvidenceItem.run_id == payload["run_id"])).all()
        ledger = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == payload["run_id"])).all()
    assert evidence
    assert all("fixtures.forecastlab.local" not in (item.url or "") for item in evidence)
    assert all(item.provider != "mock" for item in ledger)
    assert any(item.provider_type == "model" and item.status == "succeeded" for item in ledger)
    assert any(item.provider_type == "search" and item.status == "succeeded" for item in ledger)


def test_completed_run_with_probability_passes(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        payload = _audit_seeded(session)
    assert payload["status"] == "completed"
    assert payload["probability"] == 0.22
    assert payload["accepted_external_evidence_count"] == 1
    assert payload["accepted_evidence_urls"] == ["https://www.bls.gov/news.release/empsit.nr0.htm"]


def test_failed_run_returns_nonzero(client, monkeypatch, capsys) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="smoke_forecast_failed"):
            _audit_seeded(session, status="failed")
    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.credentials_ready", lambda: True)
    monkeypatch.setattr(
        "forecastlab_api.paid_smoke.execute_live_smoke",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("smoke_forecast_failed")),
    )
    assert main() == 1
    assert "paid_smoke_failed:smoke_forecast_failed" in capsys.readouterr().err


def test_completed_run_without_forecast_version_fails(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_forecast_version_missing"):
        _audit_seeded(session, include_version=False)


def test_version_without_probability_fails(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_probability_missing"):
        _audit_seeded(session, probability=None)


def test_no_accepted_external_evidence_fails(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_no_accepted_external_evidence"):
        _audit_seeded(session, evidence_rejected=True, evidence_eligible=False)


def test_fixture_only_evidence_fails(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session, pytest.raises(RuntimeError, match="mock_or_fixture_evidence_used"):
        _audit_seeded(session, evidence_url="https://fixtures.forecastlab.local/unrate.json")


def test_only_failed_model_ledger_rows_fail(client) -> None:
    from forecastlab_api.db import SessionLocal

    rows = _default_ledger()
    rows[0]["status"] = "failed"
    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_no_successful_model_request"):
        _audit_seeded(session, ledger_rows=rows)


def test_only_failed_search_ledger_rows_fail(client) -> None:
    from forecastlab_api.db import SessionLocal

    rows = _default_ledger()
    rows[1]["status"] = "failed"
    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_no_successful_search_request"):
        _audit_seeded(session, ledger_rows=rows)


def test_failed_retry_then_successful_request_passes(client, capsys) -> None:
    from forecastlab_api.db import SessionLocal

    rows = [
        {
            "logical_call_id": "model-1",
            "physical_attempt_number": 1,
            "stage": "forecast",
            "provider_type": "model",
            "provider": "xai",
            "status": "failed",
        },
        {
            "logical_call_id": "model-1",
            "physical_attempt_number": 2,
            "stage": "forecast",
            "provider_type": "model",
            "provider": "xai",
            "status": "succeeded",
        },
        {
            "logical_call_id": "search-1",
            "physical_attempt_number": 1,
            "stage": "search",
            "provider_type": "search",
            "provider": "tavily",
            "status": "succeeded",
        },
    ]
    with SessionLocal() as session:
        payload = _audit_seeded(session, ledger_rows=rows)
    assert payload["status"] == "completed"
    assert payload["probability"] == 0.22
    assert any(row["status"] == "failed" for row in payload["ledger"])
    assert any(row["provider_type"] == "model" and row["status"] == "succeeded" for row in payload["ledger"])
    print_result(payload)
    printed = capsys.readouterr().out
    parsed = [_parse_ledger_json(line) for line in _ledger_lines(printed)]
    assert any(row["status"] == "failed" for row in parsed)
    assert any(row["provider_type"] == "model" and row["status"] == "succeeded" for row in parsed)


def test_released_ledger_entry_fails(client) -> None:
    from forecastlab_api.db import SessionLocal

    rows = _default_ledger()
    rows.append(
        {
            "logical_call_id": "model-2",
            "physical_attempt_number": 1,
            "stage": "forecast",
            "provider_type": "model",
            "provider": "xai",
            "status": "released",
        }
    )
    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_released_ledger_entry"):
        _audit_seeded(session, ledger_rows=rows)


@pytest.mark.parametrize("status", ["reserved", "running", "pending", "unknown"])
def test_nonterminal_ledger_row_fails(client, status: str) -> None:
    from forecastlab_api.db import SessionLocal

    rows = _default_ledger()
    rows.append(
        {
            "logical_call_id": "model-2",
            "physical_attempt_number": 1,
            "stage": "forecast",
            "provider_type": "model",
            "provider": "xai",
            "status": status,
        }
    )
    with SessionLocal() as session, pytest.raises(RuntimeError, match="smoke_nonterminal_ledger_entry"):
        _audit_seeded(session, ledger_rows=rows)


def test_migrations_applied_before_smoke_database_access(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr("forecastlab_api.paid_smoke.working_tree_dirty", lambda: False)
    monkeypatch.setattr("forecastlab_api.paid_smoke.load_secrets", lambda: LIVE_SECRETS)
    monkeypatch.setattr("forecastlab_api.paid_smoke.apply_schema", lambda: events.append("apply_schema"))

    def factory():
        events.append("session")
        raise RuntimeError("session_opened")

    with pytest.raises(RuntimeError, match="session_opened"):
        execute_live_smoke(session_factory=factory)
    assert events == ["apply_schema", "session"]


def test_successful_stubbed_live_smoke_main_returns_zero(client, monkeypatch, capsys) -> None:
    from forecastlab_api.db import SessionLocal

    _live_secrets_env(monkeypatch)
    monkeypatch.setattr("forecastlab_api.paid_smoke._session_factory", lambda: SessionLocal)
    assert main() == 0
    captured = capsys.readouterr()
    out = captured.out
    for marker in (
        "run_id=",
        "status=completed",
        "probability=",
        "model_provider=xai",
        "search_provider=tavily",
        "model_cost_usd=",
        "search_cost_usd=",
        "failed_attempt_cost_usd=",
        "total_cost_usd=",
        "cost_source=",
        "run_attempt_count=",
        "provider_request_count=",
        "accepted_external_evidence_count=",
        "accepted_evidence_url=https://www.bls.gov/",
        "checked_at=",
        "ledger:",
    ):
        assert marker in out
    for line in _ledger_lines(out):
        _parse_ledger_json(line)
    assert "paid_smoke_failed:" not in captured.err


def test_accepted_evidence_payload_deduplicates_and_excludes_rejected(client) -> None:
    from forecastlab_api.db import SessionLocal

    rows = [
        {"url": "https://www.bls.gov/news.release/empsit.nr0.htm", "rejected": False, "as_of_eligible": True},
        {"url": "https://www.bls.gov/news.release/empsit.nr0.htm", "rejected": False, "as_of_eligible": True},
        {"url": "https://fred.stlouisfed.org/series/UNRATE", "rejected": True, "as_of_eligible": True},
        {"url": "https://www.bls.gov/cps/", "rejected": False, "as_of_eligible": False},
    ]
    with SessionLocal() as session:
        payload = _audit_seeded(session, evidence_rows=rows)
    assert payload["accepted_external_evidence_count"] == 2
    assert payload["accepted_evidence_urls"] == ["https://www.bls.gov/news.release/empsit.nr0.htm"]


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.1, float("nan"), float("inf"), float("-inf")])
def test_invalid_probability_fails(client, probability: float) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        run = _seed_smoke_run(session, probability=0.22)
        session.commit()
        version = session.scalars(select(ForecastVersion).where(ForecastVersion.run_id == run.id)).one()
        version.ensemble_probability = probability
        with pytest.raises(RuntimeError, match="smoke_probability_invalid"):
            _audit(session, run, expected_model="xai", expected_search="tavily", profile=load_profile(SMOKE_PROFILE_ID))
    with pytest.raises(RuntimeError, match="smoke_probability_invalid"):
        _require_valid_probability(probability)


def test_non_numeric_probability_fails() -> None:
    with pytest.raises(RuntimeError, match="smoke_probability_invalid"):
        _require_valid_probability("not-a-number")


def test_secret_redaction_in_failure_output(monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.credentials_ready", lambda: True)
    secrets = (
        "xai-abcdefghijklmnopqrstuvwxyz",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "tvly-abcdefghijklmnopqrstuvwxyz",
    )
    def _raise_with(secret: str):
        def _fail(**_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError(f"provider failed with {secret}")

        return _fail

    for key in secrets:
        monkeypatch.setattr("forecastlab_api.paid_smoke.execute_live_smoke", _raise_with(key))
        assert main() == 1
        err = capsys.readouterr().err
        assert key not in err
        assert "paid_smoke_failed:" in err
        assert "REDACTED" in err


def _ledger_lines(output: str) -> list[str]:
    after = output.split("ledger:\n", 1)
    assert len(after) == 2
    return [line for line in after[1].splitlines() if line.strip()]


def _parse_ledger_json(line: str) -> dict[str, Any]:
    parsed = json_lib.loads(line)
    assert isinstance(parsed, dict)
    return parsed

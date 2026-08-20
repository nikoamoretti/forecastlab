from __future__ import annotations

import json as json_lib
from datetime import UTC, datetime

from sqlalchemy import select

from forecastlab.hashing import content_hash
from forecastlab.schemas import FetchedDocument
from forecastlab.timeutil import utcnow
from forecastlab_api.paid_smoke import (
    ABSENT_MESSAGE,
    CREDENTIALS_MISSING_MESSAGE,
    credentials_ready,
    execute_live_smoke,
    main,
    opted_in,
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


def test_paid_smoke_does_nothing_without_opt_in(monkeypatch, capsys) -> None:
    monkeypatch.delenv("FORECASTLAB_RUN_PAID_SMOKE", raising=False)
    monkeypatch.setattr("forecastlab_api.paid_smoke.credentials_ready", lambda: True)
    assert opted_in() is False
    assert main() == 0
    assert ABSENT_MESSAGE in capsys.readouterr().out


def test_paid_smoke_refuses_missing_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.load_secrets", lambda: {"model_provider": "mock"})
    assert credentials_ready({"model_provider": "mock"}) is False
    assert main() == 2
    assert CREDENTIALS_MISSING_MESSAGE in capsys.readouterr().out


def test_stubbed_paid_smoke_completes_without_mock_or_fixture_evidence(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal

    monkeypatch.setenv("FORECASTLAB_RUN_PAID_SMOKE", "1")
    monkeypatch.setattr("forecastlab_api.paid_smoke.working_tree_dirty", lambda: False)
    monkeypatch.setattr("forecastlab_api.paid_smoke.load_secrets", lambda: LIVE_SECRETS)
    monkeypatch.setattr("forecastlab_api.pipeline.load_secrets", lambda: LIVE_SECRETS)
    monkeypatch.setattr("forecastlab.providers.openai_compatible.httpx.Client", _StubClient)
    monkeypatch.setattr("forecastlab.providers.search.httpx.Client", _StubClient)
    monkeypatch.setattr("forecastlab.run_cache.fetch_document", _live_document)
    monkeypatch.setattr("forecastlab.fetch.fetch_document", _live_document)
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
    from forecastlab_api.models import EvidenceItem, ProviderCallLedger

    with SessionLocal() as session:
        evidence = session.scalars(select(EvidenceItem).where(EvidenceItem.run_id == payload["run_id"])).all()
        ledger = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == payload["run_id"])).all()
    assert evidence
    assert all("fixtures.forecastlab.local" not in (item.url or "") for item in evidence)
    assert all(item.provider != "mock" for item in ledger)
    assert any(item.provider_type == "model" and item.status == "succeeded" for item in ledger)
    assert any(item.provider_type == "search" and item.status == "succeeded" for item in ledger)

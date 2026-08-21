from __future__ import annotations

from typing import Any

import pytest

from forecastlab.contracts import ForecastContractError, QuestionCompiler
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import ModelUsage


class StubContractModel:
    name = "stub"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def complete_json(self, **_kwargs: Any) -> ChatResult:
        self.calls += 1
        return ChatResult(
            content="{}",
            parsed=self.payload,
            usage=ModelUsage(model="stub", provider="stub"),
        )


def clear_contract_payload() -> dict[str, Any]:
    return {
        "normalized_question": (
            "Will Tesla common stock close above $500 on 31 December 2027 according to the Nasdaq official close?"
        ),
        "yes_condition": "Nasdaq reports a TSLA official closing price strictly above $500 on 31 December 2027.",
        "no_condition": "Nasdaq reports a TSLA official closing price at or below $500 on 31 December 2027.",
        "resolution_date": "2027-12-31T21:00:00Z",
        "authoritative_source": "Nasdaq TSLA historical closing-price record",
        "fallback_sources": ["Tesla investor relations historical-price record"],
        "resolution_method": "Use the unadjusted official Nasdaq close for TSLA on the named trading date.",
        "ambiguity_notes": "If the date is not a trading day, use the final trading day of 2027.",
        "cancellation_conditions": "Cancel if TSLA has no comparable publicly traded common stock at resolution.",
        "resolver_risk_notes": "Adjusted and unadjusted historical prices can differ after a split.",
        "forecast_type": "binary",
        "geography": "United States",
        "units": "US dollars per share",
        "domain": "markets",
        "initial_reference_class": "Large-cap equities attempting a specified price threshold over a two-year horizon.",
        "suggested_drivers": ["revenue growth", "valuation multiple", "share splits"],
        "known_dependencies": ["the closing-price convention depends on the trading calendar"],
        "rejection_reasons": [],
    }


def test_generate_contract_from_clear_binary_question() -> None:
    model = StubContractModel(clear_contract_payload())
    contract = QuestionCompiler(model).compile(
        "  Will Tesla stock close above $500 on December 31 2027?  ",
        question_id="question-1",
        created_by="test-user",
    )

    assert model.calls == 1
    assert contract.status == "draft"
    assert contract.question_id == "question-1"
    assert contract.original_question == "Will Tesla stock close above $500 on December 31 2027?"
    assert contract.normalized_question.startswith("Will Tesla common stock")
    assert contract.yes_condition.startswith("Nasdaq reports")
    assert contract.no_condition
    assert contract.resolution_date is not None
    assert contract.authoritative_source == "Nasdaq TSLA historical closing-price record"
    assert contract.initial_reference_class


def test_reject_vague_question_before_model_call() -> None:
    model = StubContractModel(clear_contract_payload())
    with pytest.raises(ForecastContractError) as exc_info:
        QuestionCompiler(model).compile("Will AI change everything?", question_id="question-1")
    assert "vague_outcome" in exc_info.value.reasons
    assert model.calls == 0


def test_forecast_contract_api_generate_get_approve_and_run(client) -> None:
    generated = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    )
    assert generated.status_code == 200
    draft = generated.json()
    assert {
        "id",
        "question_id",
        "version",
        "created_at",
        "created_by",
        "original_question",
        "normalized_question",
        "yes_condition",
        "no_condition",
        "resolution_date",
        "authoritative_source",
        "fallback_sources",
        "resolution_method",
        "ambiguity_notes",
        "cancellation_conditions",
        "resolver_risk_notes",
        "forecast_type",
        "geography",
        "units",
        "domain",
        "initial_reference_class",
        "suggested_drivers",
        "known_dependencies",
        "status",
    } <= draft.keys()
    assert draft["status"] == "draft"
    assert draft["yes_condition"]
    assert draft["no_condition"]
    assert draft["resolution_date"]
    assert draft["authoritative_source"]
    assert draft["resolution_method"]

    fetched = client.get(f"/api/contracts/{draft['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == draft["id"]
    assert fetched.json()["normalized_question"] == draft["normalized_question"]

    blocked_run = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    )
    assert blocked_run.status_code == 422
    assert "approved_forecast_contract_required" in blocked_run.json()["reasons"]

    approved = client.post(
        f"/api/contracts/{draft['id']}/approve",
        json={"ambiguity_notes": "Reviewed before research."},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["ambiguity_notes"] == "Reviewed before research."

    question = client.get(f"/api/questions/{draft['question_id']}").json()
    assert question["contract"]["exact_yes"] == draft["yes_condition"]
    assert question["contract"]["exact_no"] == draft["no_condition"]
    assert question["normalized_text"] == draft["normalized_question"]

    run = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"


def test_vague_question_api_is_rejected_without_creating_a_contract(client) -> None:
    response = client.post("/api/contracts/generate", json={"question": "Will AI change everything?"})
    assert response.status_code == 422
    assert "vague_outcome" in response.json()["reasons"]
    questions = client.get("/api/dashboard").json()["questions"]
    assert all(item["original_text"] != "Will AI change everything?" for item in questions)


@pytest.mark.parametrize(
    ("field", "replacement", "expected_reason"),
    [
        ("yes_condition", "", "yes_condition_required"),
        ("no_condition", "", "no_condition_required"),
        ("authoritative_source", "", "authoritative_source_required"),
        ("resolution_date", None, "resolution_date_required"),
        ("resolution_method", "", "resolution_method_required"),
    ],
)
def test_approval_rejects_missing_required_resolution_field(
    client,
    field: str,
    replacement: object,
    expected_reason: str,
) -> None:
    generated = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastContractRow

    with SessionLocal() as session:
        row = session.get(ForecastContractRow, generated["id"])
        assert row is not None
        setattr(row, field, replacement)
        session.commit()

    response = client.post(f"/api/contracts/{generated['id']}/approve")
    assert response.status_code == 422
    assert expected_reason in response.json()["reasons"]
    assert client.get(f"/api/contracts/{generated['id']}").json()["status"] == "draft"

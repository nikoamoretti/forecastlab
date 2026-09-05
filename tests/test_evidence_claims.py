from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from forecastlab.evidence_claims import EvidenceClaimError, EvidenceExtractor, eligible_claims_for_forecasting
from forecastlab.prompts import PromptBundle, PromptRecord
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import EvidenceClaim, FetchedDocument, ModelUsage
from forecastlab.timeutil import as_utc
from forecastlab_api.evidence_claims import store_evidence_claims
from forecastlab_api.models import EvidenceClaimRow, EvidenceItem, ForecastNodeRow


class StubEvidenceModel:
    name = "stub"
    model = "stub-evidence-v1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_request: dict[str, Any] | None = None

    def complete_json(self, **kwargs: Any) -> ChatResult:
        self.calls += 1
        self.last_request = kwargs
        return ChatResult(
            content="{}",
            parsed=self.payload,
            usage=ModelUsage(model=self.model, provider=self.name),
        )


def fetched_document(**updates: Any) -> FetchedDocument:
    published_at = updates.get(
        "published_at",
        datetime(2026, 7, 3, 12, 30, tzinfo=UTC),
    )
    retrieved_at = updates.get(
        "retrieved_at",
        datetime(2026, 7, 3, 13, 0, tzinfo=UTC),
    )
    values: dict[str, Any] = {
        "url": "https://www.bls.gov/news.release/empsit.nr0.htm",
        "title": "The Employment Situation",
        "publisher": "Bureau of Labor Statistics",
        "published_at": published_at,
        "publication_date_source": "test_fixture_metadata" if published_at else None,
        "publication_date_verified": published_at is not None,
        "retrieved_at": retrieved_at,
        "source_available_at": published_at or retrieved_at,
        "temporal_basis": "publication_date" if published_at else "retrieval_date",
        "text": "The unemployment rate was 4.7 percent in June. Payroll employment changed little.",
        "content_hash": "a" * 64,
        "as_of_eligible": True,
        "rejected": False,
        "published_at_unknown": published_at is None,
        "snapshot_verification_status": "fixture",
    }
    values.update(updates)
    return FetchedDocument.model_validate(values)


def extraction_payload(**updates: Any) -> dict[str, Any]:
    claim: dict[str, Any] = {
        "claim": "The June unemployment rate was 4.7 percent.",
        "excerpt": "The unemployment rate was 4.7 percent in June.",
        "supports_or_refutes": "supports",
        "confidence": 0.96,
        "source_quality": 0.98,
        "primary_source": True,
    }
    claim.update(updates)
    return {"claims": [claim]}


def test_evidence_extraction_preserves_provenance_and_node_linkage() -> None:
    document = fetched_document()
    model = StubEvidenceModel(extraction_payload())

    claims = EvidenceExtractor(model).extract(
        document,
        evidence_item_id="evidence-1",
        forecast_node_id="node-1",
        forecast_node_question="What is the current unemployment trend?",
        as_of=datetime(2026, 7, 4, tzinfo=UTC),
    )

    assert model.calls == 1
    assert model.last_request is not None
    assert model.last_request["schema_name"] == "evidence_claims"
    assert len(claims) == 1
    claim = claims[0]
    assert claim.evidence_item_id == "evidence-1"
    assert claim.forecast_node_id == "node-1"
    assert claim.source_url == document.url
    assert claim.source_title == document.title
    assert claim.publisher == document.publisher
    assert claim.publication_date == document.published_at
    assert claim.publication_date_source == document.publication_date_source
    assert claim.publication_date_verified is True
    assert claim.retrieval_date == document.retrieved_at
    assert claim.source_available_at == document.source_available_at
    assert claim.temporal_basis == document.temporal_basis
    assert claim.excerpt in document.text
    assert claim.as_of_eligible is True
    assert claim.cutoff_verified is True
    assert claim.source_class == "primary"
    assert claim.extraction_method == "structured_full_document"
    assert claim.source_host == "bls.gov"


def test_evidence_extraction_uses_frozen_experiment_prompt_bundle() -> None:
    model = StubEvidenceModel(extraction_payload())
    bundle = PromptBundle(
        prompts={
            "evidence_claims": PromptRecord(
                name="evidence_claims",
                text="FROZEN EVIDENCE CLAIMS PROMPT",
                version="test-frozen",
                sha256="b" * 64,
            )
        }
    )

    EvidenceExtractor(model, prompt_bundle=bundle).extract(
        fetched_document(),
        evidence_item_id="evidence-frozen",
        forecast_node_id="node-frozen",
    )

    assert model.last_request is not None
    assert model.last_request["system"] == "FROZEN EVIDENCE CLAIMS PROMPT"


def test_extractor_rejects_missing_excerpt() -> None:
    model = StubEvidenceModel(extraction_payload(excerpt=""))

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            fetched_document(),
            evidence_item_id="evidence-1",
            forecast_node_id="node-1",
        )

    assert "excerpt_required" in exc_info.value.reasons


def test_extractor_rejects_unsupported_summary() -> None:
    model = StubEvidenceModel(extraction_payload(excerpt="This sentence does not occur in the document."))

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            fetched_document(),
            evidence_item_id="evidence-1",
            forecast_node_id="node-1",
        )

    assert "excerpt_not_in_document" in exc_info.value.reasons


def test_extractor_rejects_after_cutoff_before_model_call() -> None:
    model = StubEvidenceModel(extraction_payload())

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            fetched_document(),
            evidence_item_id="evidence-1",
            forecast_node_id="node-1",
            as_of=datetime(2026, 7, 1, tzinfo=UTC),
        )

    assert "claim_after_cutoff" in exc_info.value.reasons
    assert model.calls == 0


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (fetched_document(publisher=None), "publisher_required"),
        (fetched_document(rejected=True, rejection_reason="blocked"), "document_rejected"),
    ],
)
def test_extractor_rejects_missing_or_ineligible_provenance_before_model_call(
    document: FetchedDocument,
    reason: str,
) -> None:
    model = StubEvidenceModel(extraction_payload())

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            document,
            evidence_item_id="evidence-1",
            forecast_node_id="node-1",
        )

    assert reason in exc_info.value.reasons
    assert model.calls == 0


def test_live_undated_document_creates_retrieval_basis_claim_without_faking_publication() -> None:
    retrieved_at = datetime(2026, 7, 3, 13, 0, tzinfo=UTC)
    document = fetched_document(
        published_at=None,
        publication_date_source=None,
        publication_date_verified=False,
        published_at_unknown=True,
        retrieved_at=retrieved_at,
        source_available_at=retrieved_at,
        temporal_basis="retrieval_date",
        snapshot_verification_status="live",
    )

    claims = EvidenceExtractor(StubEvidenceModel(extraction_payload())).extract(
        document,
        evidence_item_id="evidence-live-undated",
        forecast_node_id="node-live-undated",
        mode="live",
        run_completion_time=datetime(2026, 7, 3, 13, 1, tzinfo=UTC),
    )

    assert len(claims) == 1
    claim = claims[0]
    assert claim.publication_date is None
    assert claim.publication_date_verified is False
    assert claim.temporal_basis == "retrieval_date"
    assert claim.source_available_at == retrieved_at
    assert claim.retrieval_date == retrieved_at
    assert claim.cutoff_verified is True


def test_current_undated_document_is_rejected_for_backtest() -> None:
    document = fetched_document(
        published_at=None,
        publication_date_source=None,
        publication_date_verified=False,
        published_at_unknown=True,
        source_available_at=datetime(2026, 7, 3, 13, 0, tzinfo=UTC),
        temporal_basis="retrieval_date",
        snapshot_verification_status="live",
    )
    model = StubEvidenceModel(extraction_payload())

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            document,
            evidence_item_id="evidence-current-undated",
            forecast_node_id="node-current-undated",
            mode="backtest",
            as_of=datetime(2026, 7, 4, tzinfo=UTC),
        )

    assert "historical_retrieval_basis_forbidden" in exc_info.value.reasons
    assert model.calls == 0


def test_verified_wayback_snapshot_allows_unknown_publication_date_before_cutoff() -> None:
    snapshot_at = datetime(2026, 7, 1, tzinfo=UTC)
    document = fetched_document(
        published_at=None,
        publication_date_source=None,
        publication_date_verified=False,
        published_at_unknown=True,
        source_available_at=snapshot_at,
        temporal_basis="snapshot_date",
        snapshot_at=snapshot_at,
        final_snapshot_at=snapshot_at,
        snapshot_verification_status="verified",
    )

    claims = EvidenceExtractor(StubEvidenceModel(extraction_payload())).extract(
        document,
        evidence_item_id="evidence-wayback-undated",
        forecast_node_id="node-wayback-undated",
        mode="backtest",
        as_of=datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert claims[0].publication_date is None
    assert claims[0].temporal_basis == "snapshot_date"
    assert claims[0].source_available_at == snapshot_at
    assert claims[0].cutoff_verified is True


def test_snapshot_after_cutoff_is_rejected_before_model_call() -> None:
    snapshot_at = datetime(2026, 7, 5, tzinfo=UTC)
    document = fetched_document(
        published_at=None,
        publication_date_source=None,
        publication_date_verified=False,
        published_at_unknown=True,
        source_available_at=snapshot_at,
        temporal_basis="snapshot_date",
        snapshot_at=snapshot_at,
        final_snapshot_at=snapshot_at,
        snapshot_verification_status="verified",
    )
    model = StubEvidenceModel(extraction_payload())

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            document,
            evidence_item_id="evidence-late-snapshot",
            forecast_node_id="node-late-snapshot",
            mode="backtest",
            as_of=datetime(2026, 7, 4, tzinfo=UTC),
        )

    assert "claim_after_cutoff" in exc_info.value.reasons
    assert model.calls == 0


def test_document_fallback_claim_uses_same_live_and_historical_temporal_rules() -> None:
    retrieved_at = datetime(2026, 7, 3, 13, 0, tzinfo=UTC)
    document = fetched_document(
        published_at=None,
        publication_date_source=None,
        publication_date_verified=False,
        published_at_unknown=True,
        retrieved_at=retrieved_at,
        source_available_at=retrieved_at,
        temporal_basis="retrieval_date",
        snapshot_verification_status="live",
    )
    extractor = EvidenceExtractor(StubEvidenceModel(extraction_payload()))

    claim = extractor.document_fallback_claim(
        document,
        evidence_item_id="fallback-live",
        forecast_node_id="node-fallback-live",
        mode="live",
        run_completion_time=datetime(2026, 7, 3, 13, 1, tzinfo=UTC),
    )
    assert claim.publication_date is None
    assert claim.temporal_basis == "retrieval_date"

    with pytest.raises(EvidenceClaimError) as exc_info:
        extractor.document_fallback_claim(
            document,
            evidence_item_id="fallback-backtest",
            forecast_node_id="node-fallback-backtest",
            mode="backtest",
            as_of=datetime(2026, 7, 4, tzinfo=UTC),
        )
    assert "historical_retrieval_basis_forbidden" in exc_info.value.reasons


def test_extractor_requires_forecast_node_linkage() -> None:
    model = StubEvidenceModel(extraction_payload())

    with pytest.raises(EvidenceClaimError) as exc_info:
        EvidenceExtractor(model).extract(
            fetched_document(),
            evidence_item_id="evidence-1",
            forecast_node_id="",
        )

    assert "forecast_node_id_required" in exc_info.value.reasons
    assert model.calls == 0


def test_rejected_claim_cannot_enter_forecasting_context() -> None:
    valid = EvidenceClaim(
        id="claim-valid",
        evidence_item_id="evidence-1",
        forecast_node_id="node-1",
        claim="The June unemployment rate was 4.7 percent.",
        excerpt="The unemployment rate was 4.7 percent in June.",
        source_url="https://www.bls.gov/news.release/empsit.nr0.htm",
        source_title="The Employment Situation",
        publisher="Bureau of Labor Statistics",
        publication_date=datetime(2026, 7, 3, tzinfo=UTC),
        publication_date_source="test_fixture_metadata",
        publication_date_verified=True,
        retrieval_date=datetime(2026, 7, 3, 13, 0, tzinfo=UTC),
        source_available_at=datetime(2026, 7, 3, 13, 0, tzinfo=UTC),
        temporal_basis="retrieval_date",
        supports_or_refutes="supports",
        confidence=0.96,
        source_quality=0.98,
        primary_source=True,
        as_of_eligible=True,
        cutoff_verified=True,
    )
    rejected = valid.model_copy(
        update={
            "id": "claim-rejected",
            "as_of_eligible": False,
            "cutoff_verified": False,
        }
    )

    assert eligible_claims_for_forecasting([valid, rejected]) == [valid]
    assert eligible_claims_for_forecasting([valid], mode="live") == [valid]
    assert (
        eligible_claims_for_forecasting(
            [valid],
            mode="backtest",
            cutoff=datetime(2026, 7, 4, tzinfo=UTC),
        )
        == []
    )


def test_evidence_claim_persistence_and_read_apis(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()
    graph = client.post(f"/api/contracts/{draft['id']}/graph").json()
    run = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    ).json()

    from forecastlab_api import main as main_mod

    published_at = datetime(2026, 7, 3, 12, 30, tzinfo=UTC)
    retrieved_at = datetime(2026, 7, 3, 13, 0, tzinfo=UTC)
    node_id = graph["nodes"][0]["id"]
    claim_id = "claim-api-1"
    with main_mod.SessionLocal() as session:
        item = EvidenceItem(
            id="evidence-api-1",
            run_id=run["id"],
            url="https://www.bls.gov/news.release/empsit.nr0.htm",
            title="The Employment Situation",
            publisher="Bureau of Labor Statistics",
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_available_at=published_at,
            temporal_basis="publication_date",
            publication_date_source="test_fixture_metadata",
            publication_date_verified=True,
            excerpt="The unemployment rate was 4.7 percent in June.",
            content_hash="b" * 64,
            source_class="primary",
            as_of_eligible=True,
            rejected=False,
            published_at_unknown=False,
        )
        session.add(item)
        session.flush()
        claim = EvidenceClaim(
            id=claim_id,
            evidence_item_id=item.id,
            forecast_node_id=node_id,
            claim="The June unemployment rate was 4.7 percent.",
            excerpt="The unemployment rate was 4.7 percent in June.",
            source_url=item.url,
            source_title=item.title,
            publisher=item.publisher or "",
            publication_date=published_at,
            publication_date_source="test_fixture_metadata",
            publication_date_verified=True,
            retrieval_date=retrieved_at,
            source_available_at=published_at,
            temporal_basis="publication_date",
            supports_or_refutes="supports",
            confidence=0.96,
            source_quality=0.98,
            primary_source=True,
            as_of_eligible=True,
            cutoff_verified=True,
        )
        rows = store_evidence_claims(session, [claim], cutoff=datetime(2026, 7, 4, tzinfo=UTC))
        assert rows[0].forecast_node_id == node_id
        session.commit()

    node_response = client.get(f"/api/nodes/{node_id}/evidence")
    assert node_response.status_code == 200
    payload = node_response.json()
    assert payload["node"]["id"] == node_id
    assert payload["claims"][0]["id"] == claim_id
    assert payload["claims"][0]["source_url"] == "https://www.bls.gov/news.release/empsit.nr0.htm"

    claim_response = client.get(f"/api/evidence/{claim_id}")
    assert claim_response.status_code == 200
    assert claim_response.json() == payload["claims"][0]
    assert client.get("/api/nodes/missing/evidence").status_code == 404
    assert client.get("/api/evidence/missing").status_code == 404


def test_rejected_evidence_item_cannot_be_persisted_as_a_claim(client) -> None:
    draft = client.post(
        "/api/contracts/generate",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?"},
    ).json()
    client.post(f"/api/contracts/{draft['id']}/approve").raise_for_status()
    graph = client.post(f"/api/contracts/{draft['id']}/graph").json()
    run = client.post(
        f"/api/questions/{draft['question_id']}/runs",
        json={"profile_id": "three_track_ensemble", "mode": "demo"},
    ).json()

    from forecastlab_api import main as main_mod

    published_at = datetime(2026, 7, 3, 12, 30, tzinfo=UTC)
    retrieved_at = datetime(2026, 7, 3, 13, 0, tzinfo=UTC)
    with main_mod.SessionLocal() as session:
        item = EvidenceItem(
            id="evidence-rejected-1",
            run_id=run["id"],
            url="https://example.com/rejected",
            title="Rejected source",
            publisher="Example Publisher",
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_available_at=published_at,
            temporal_basis="publication_date",
            publication_date_source="test_fixture_metadata",
            publication_date_verified=True,
            excerpt="This evidence was rejected.",
            content_hash="c" * 64,
            as_of_eligible=False,
            rejected=True,
            rejection_reason="after_cutoff",
            published_at_unknown=False,
        )
        session.add(item)
        session.flush()
        claim = EvidenceClaim(
            id="claim-rejected-1",
            evidence_item_id=item.id,
            forecast_node_id=graph["nodes"][0]["id"],
            claim="This evidence was rejected.",
            excerpt="This evidence was rejected.",
            source_url=item.url,
            source_title=item.title,
            publisher=item.publisher or "",
            publication_date=published_at,
            publication_date_source="test_fixture_metadata",
            publication_date_verified=True,
            retrieval_date=retrieved_at,
            source_available_at=published_at,
            temporal_basis="publication_date",
            supports_or_refutes="supports",
            confidence=0.5,
            source_quality=0.5,
            primary_source=False,
            as_of_eligible=False,
            cutoff_verified=False,
        )

        with pytest.raises(EvidenceClaimError) as exc_info:
            store_evidence_claims(session, [claim])

        assert "evidence_item_rejected" in exc_info.value.reasons
        assert "evidence_item_not_as_of_eligible" in exc_info.value.reasons
        assert session.scalar(select(func.count()).select_from(EvidenceClaimRow)) == 0
        assert session.get(ForecastNodeRow, graph["nodes"][0]["id"]) is not None
        assert as_utc(item.retrieved_at) == retrieved_at

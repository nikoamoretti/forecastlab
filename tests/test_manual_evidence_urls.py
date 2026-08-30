from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from forecastlab import fetch as fetch_module
from forecastlab.budget import Budget
from forecastlab.graph_research import GraphResearchExecutor
from forecastlab.profiles import load_profile
from forecastlab.providers.mock import MockModelProvider, MockSearchProvider
from forecastlab.run_cache import RunCache
from forecastlab.schemas import AttachedEvidenceDocument, FetchedDocument, ForecastNode
from forecastlab.ssrf import UnsafeURLError
from forecastlab.wayback import WaybackSnapshot
from forecastlab_api import manual_evidence as manual_module
from forecastlab_api.manual_evidence import (
    attach_manual_evidence_to_run,
    manual_evidence_documents_for_run,
)
from forecastlab_api.models import (
    EvidenceClaimRow,
    ForecastContractRow,
    ForecastGraphRow,
    ForecastNodeRow,
    ForecastRun,
    ForecastVersion,
    Question,
    Watch,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _document(
    url: str = "https://www.bls.gov/news.release/empsit.toc.htm",
    *,
    mode: str = "live",
    rejected: bool = False,
    reason: str | None = None,
    source_available_at: datetime | None = None,
    snapshot_url: str | None = None,
    snapshot_at: datetime | None = None,
) -> FetchedDocument:
    text = "Official labor statistics show a measured unemployment rate."
    historical = mode == "backtest"
    return FetchedDocument(
        url=url,
        final_url=snapshot_url or url,
        title="Employment Situation",
        publisher="U.S. Bureau of Labor Statistics",
        published_at=None,
        retrieved_at=NOW,
        source_available_at=source_available_at or snapshot_at or NOW,
        temporal_basis="snapshot_date" if historical else "retrieval_date",
        publication_date_verified=False,
        text="" if rejected else text,
        content_hash="text-hash" if not rejected else "empty-hash",
        raw_content_hash="raw-hash" if not rejected else "empty-raw-hash",
        extracted_text_hash="text-hash" if not rejected else "empty-hash",
        content_type="text/html",
        byte_length=128 if not rejected else 0,
        requested_snapshot_url=snapshot_url,
        requested_snapshot_at=snapshot_at,
        final_snapshot_url=snapshot_url,
        final_snapshot_at=snapshot_at,
        archived_original_url=url if historical else None,
        snapshot_verification_status="verified" if historical and not rejected else reason,
        status_code=200 if not rejected else 0,
        rejected=rejected,
        rejection_reason=reason,
        as_of_eligible=not rejected,
        published_at_unknown=True,
    )


def _new_question(client, *, mode: str = "live") -> str:
    response = client.post(
        "/api/questions",
        json={"question": "Will the official threshold be reached?", "mode": mode},
    )
    assert response.status_code == 200
    return str(response.json()["id"])


def test_live_manual_url_is_accepted_with_full_provenance_and_no_hidden_work(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"fetch": 0}

    def fetched(url: str, **_kwargs) -> FetchedDocument:
        calls["fetch"] += 1
        return _document(url)

    monkeypatch.setattr(manual_module, "fetch_document", fetched)
    question_id = _new_question(client)

    response = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={
            "url": "https://www.bls.gov/news.release/empsit.toc.htm",
            "note": "Official source",
            "intended_use": "general_question_evidence",
            "mode": "live",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["publication_date"] is None
    assert payload["publication_date_verified"] is False
    assert payload["temporal_basis"] == "retrieval_date"
    assert payload["source_available_at"] == NOW.isoformat()
    assert payload["content_hash"] == "raw-hash"
    assert payload["extracted_text_hash"] == "text-hash"
    assert payload["claim_created_at_intake"] is False
    assert calls == {"fetch": 1}

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastRun)) == 0
        assert session.scalar(select(func.count()).select_from(EvidenceClaimRow)) == 0


def test_duplicate_url_and_duplicate_content_are_idempotent(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"fetch": 0}

    def fetched(url: str, **_kwargs) -> FetchedDocument:
        calls["fetch"] += 1
        return _document(url)

    monkeypatch.setattr(manual_module, "fetch_document", fetched)
    question_id = _new_question(client)
    first = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/a", "mode": "live"},
    ).json()
    same_url = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/a/", "mode": "live"},
    ).json()
    same_content = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/b", "mode": "live"},
    ).json()

    assert first["id"] == same_url["id"] == same_content["id"]
    assert first["created"] is True
    assert same_url["created"] is False
    assert same_content["created"] is False
    assert calls["fetch"] == 2
    listing = client.get(f"/api/questions/{question_id}/evidence-urls").json()
    assert len(listing["attachments"]) == 1


def test_unsafe_private_network_url_is_rejected_and_persisted(client) -> None:
    question_id = _new_question(client)
    response = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "http://127.0.0.1/private", "mode": "live"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["rejection_reason"].startswith("unsafe_url:")
    listing = client.get(f"/api/questions/{question_id}/evidence-urls").json()
    assert listing["attachments"][0]["status"] == "rejected"


@pytest.mark.parametrize(
    ("reason", "status_code"),
    [
        ("unsafe_url:Redirect target is private", 0),
        ("unsafe_url:Response exceeds byte limit", 0),
        ("unsafe_url:Unsafe content type: application/octet-stream", 0),
    ],
)
def test_fetch_safety_rejections_remain_audited(
    client,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    status_code: int,
) -> None:
    monkeypatch.setattr(fetch_module, "validate_url", lambda *_args, **_kwargs: None)

    def rejected_fetch(*_args, **_kwargs):
        raise UnsafeURLError(reason.removeprefix("unsafe_url:"))

    monkeypatch.setattr(fetch_module, "safe_get", rejected_fetch)
    question_id = _new_question(client)
    payload = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/unsafe", "mode": "live"},
    ).json()
    assert payload["accepted"] is False
    assert payload["rejection_reason"] == reason
    assert payload["status_code"] == status_code
    assert payload["claim_created_at_intake"] is False


def test_node_from_another_question_is_rejected_before_fetch(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api import main as main_mod

    question_a = _new_question(client)
    question_b = _new_question(client)
    with main_mod.SessionLocal() as session:
        contract = ForecastContractRow(
            id="contract-b",
            question_id=question_b,
            original_question="B",
            normalized_question="B?",
            status="approved",
        )
        graph = ForecastGraphRow(
            id="graph-b",
            contract_id=contract.id,
            status="approved",
            generation_model="test",
            root_question="B?",
        )
        node = ForecastNodeRow(
            id="node-b",
            graph_id=graph.id,
            question="B node?",
            node_type="driver",
            importance_weight=1.0,
            required_output_type="probability",
        )
        session.add_all([contract, graph, node])
        session.commit()

    monkeypatch.setattr(
        manual_module,
        "fetch_document",
        lambda *_args, **_kwargs: pytest.fail("fetch must not run"),
    )
    response = client.post(
        f"/api/questions/{question_a}/evidence-urls",
        json={
            "url": "https://example.org/source",
            "mode": "live",
            "intended_use": "forecast_node",
            "forecast_node_id": "node-b",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "forecast_node_question_mismatch"


def test_backtest_requires_verified_pre_cutoff_snapshot(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)
    before = cutoff - timedelta(days=3)
    after = cutoff + timedelta(days=3)
    question_id = _new_question(client, mode="backtest")

    monkeypatch.setattr(
        manual_module,
        "discover_snapshots",
        lambda _url, **_kwargs: [],
    )
    monkeypatch.setattr(
        manual_module,
        "fetch_document",
        lambda url, **_kwargs: _document(
            url,
            mode="backtest",
            rejected=True,
            reason="no_eligible_historical_snapshot",
        ),
    )
    current = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/current", "mode": "backtest", "as_of": cutoff.isoformat()},
    ).json()
    assert current["accepted"] is False
    assert current["rejection_reason"] == "no_eligible_historical_snapshot"

    monkeypatch.setattr(
        manual_module,
        "discover_snapshots",
        lambda url, **_kwargs: [
            WaybackSnapshot(
                url=url,
                timestamp=before,
                snapshot_url=f"https://web.archive.org/web/20240129000000/{url}",
            )
        ],
    )

    def archived(url: str, **kwargs) -> FetchedDocument:
        assert kwargs["snapshot_at"] == before
        return _document(
            url,
            mode="backtest",
            source_available_at=before,
            snapshot_url=kwargs["snapshot_url"],
            snapshot_at=before,
        )

    monkeypatch.setattr(manual_module, "fetch_document", archived)
    accepted = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/archived", "mode": "backtest", "as_of": cutoff.isoformat()},
    ).json()
    assert accepted["accepted"] is True
    assert accepted["temporal_basis"] == "snapshot_date"
    assert accepted["source_available_at"] == before.isoformat()

    monkeypatch.setattr(
        manual_module,
        "discover_snapshots",
        lambda url, **_kwargs: [
            WaybackSnapshot(
                url=url,
                timestamp=after,
                snapshot_url=f"https://web.archive.org/web/20240204000000/{url}",
            )
        ],
    )
    monkeypatch.setattr(
        manual_module,
        "fetch_document",
        lambda url, **_kwargs: _document(
            url,
            mode="backtest",
            rejected=True,
            reason="no_eligible_historical_snapshot",
        ),
    )
    too_new = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/too-new", "mode": "backtest", "as_of": cutoff.isoformat()},
    ).json()
    assert too_new["accepted"] is False
    assert too_new["rejection_reason"] == "no_eligible_historical_snapshot"


def test_accepted_attachment_marks_completed_question_stale_without_creating_run(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api import main as main_mod

    monkeypatch.setattr(manual_module, "fetch_document", lambda url, **_kwargs: _document(url))
    question_id = _new_question(client)
    with main_mod.SessionLocal() as session:
        run = ForecastRun(
            id="completed-run",
            question_id=question_id,
            profile_id="three_track_ensemble",
            mode="live",
            status="completed",
        )
        version = ForecastVersion(
            id="completed-version",
            question_id=question_id,
            run_id=run.id,
            ensemble_probability=0.5,
        )
        session.add_all([run, version])
        session.commit()
        before_runs = session.scalar(select(func.count()).select_from(ForecastRun))

    payload = client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://example.org/manual", "mode": "live"},
    ).json()
    assert payload["fresh_explicit_rerun_required"] is True
    question = client.get(f"/api/questions/{question_id}").json()
    assert question["stale"] is True
    assert question["status"] == "stale"
    with main_mod.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ForecastRun)) == before_runs


def test_explicit_run_attachment_creates_no_claim_until_existing_extractor_runs(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api import main as main_mod

    monkeypatch.setattr(manual_module, "fetch_document", lambda url, **_kwargs: _document(url))
    question_id = _new_question(client)
    client.post(
        f"/api/questions/{question_id}/evidence-urls",
        json={"url": "https://www.bls.gov/manual", "mode": "live"},
    )
    with main_mod.SessionLocal() as session:
        question = session.get(Question, question_id)
        assert question is not None
        run = ForecastRun(
            id="new-explicit-run",
            question_id=question_id,
            profile_id="graph_live_smoke_v1",
            mode="live",
            status="pending",
        )
        session.add(run)
        session.flush()
        items = attach_manual_evidence_to_run(session, run=run)
        session.commit()
        assert len(items) == 1
        assert items[0].manual_evidence_attachment_id is not None
        assert session.scalar(select(func.count()).select_from(EvidenceClaimRow)) == 0
        attached = manual_evidence_documents_for_run(session, run=run)
        assert len(attached) == 1
        assert attached[0].document.raw_content_hash == "raw-hash"


class _NoSearch(MockSearchProvider):
    def search(self, query: str, *, max_results: int = 5):
        raise AssertionError(f"manual evidence must be considered before search: {query}")


def test_attached_document_enters_existing_node_extractor_without_search_or_fetch() -> None:
    profile = load_profile("graph_live_smoke_v1")
    budget = Budget(profile)
    node = ForecastNode(
        id="node-1",
        graph_id="graph-1",
        question="What does the official unemployment record show?",
        node_type="driver",
        importance_weight=0.8,
        preferred_sources=["BLS"],
        required_output_type="probability",
    )
    attached = AttachedEvidenceDocument(
        attachment_id="manual-1",
        evidence_item_id="manual-item-1",
        document=_document(),
    )
    result = GraphResearchExecutor(
        model=MockModelProvider(),
        search=_NoSearch(),
        profile=profile,
        budget=budget,
        cache=RunCache.create(
            run_id="manual-run",
            model_provider="mock",
            search_provider="mock",
            mode="live",
            as_of=None,
            configuration_hash="manual-evidence-test",
        ),
        run_id="manual-run",
        mode="live",
        as_of=None,
        allow_local_fixtures=False,
        max_queries_per_node=1,
        max_fetches_per_node=1,
        target_successful_documents_per_node=1,
        max_candidate_fetch_attempts_per_node=1,
        max_research_plan_model_calls=1,
        max_primary_extraction_calls=1,
        max_extraction_retry_calls=1,
        attached_documents=[attached],
    ).execute(node)
    assert result.failure is None
    assert result.queries_attempted == []
    assert result.claims
    assert result.claims[0].evidence_item_id == "manual-item-1"
    assert result.sources_checked[0]["source_origin"] == "manual_evidence_url"
    assert budget.state.search_calls == 0
    assert budget.state.fetches == 0


def test_watcher_auto_rerun_is_disabled_at_api_and_orm_boundaries(client) -> None:
    question_id = _new_question(client)
    response = client.post(
        f"/api/questions/{question_id}/watches",
        json={
            "endpoint_url": "https://example.org/status.json",
            "endpoint_type": "json",
            "auto_rerun": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "watcher_auto_rerun_disabled"

    from forecastlab_api import main as main_mod

    with main_mod.SessionLocal() as session:
        session.add(
            Watch(
                id=str(uuid.uuid4()),
                question_id=question_id,
                endpoint_url="https://example.org/status.json",
                endpoint_type="json",
                auto_rerun=True,
            )
        )
        with pytest.raises(ValueError, match="watcher_auto_rerun_disabled"):
            session.commit()
        session.rollback()


def test_mac_startup_verifier_uses_isolated_mock_configuration(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import verify_mac_local_startup as verifier

    root = tmp_path / "repo"
    (root / "apps/web").mkdir(parents=True)
    (root / "scripts").mkdir()
    for path in (
        root / "uv.lock",
        root / "apps/web/package-lock.json",
        root / "Start ForecastLab.command",
        root / "Stop ForecastLab.command",
    ):
        path.write_text("tracked\n", encoding="utf-8")
    (root / "scripts/dev_up.sh").write_text(
        "uv sync --extra dev --frozen\nnpm ci\n/health/worker\n"
        "FORECASTLAB_STARTUP_EXIT_AFTER_READY\nFORECASTLAB_CREDENTIALS_PATH\n"
        "FORECASTLAB_API_ORIGIN\n",
        encoding="utf-8",
    )
    (root / "scripts/dev_down.sh").write_text("kill pid\n", encoding="utf-8")

    def completed(_args, *, env, **_kwargs):
        database_url = env["FORECASTLAB_DATABASE_URL"]
        database = database_url.removeprefix("sqlite:///")
        path = __import__("pathlib").Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        assert env["FORECASTLAB_MODEL_PROVIDER"] == "mock"
        assert env["FORECASTLAB_SEARCH_PROVIDER"] == "mock"
        assert env["FORECASTLAB_MODEL_API_KEY"] == ""
        assert env["FORECASTLAB_SEARCH_API_KEY"] == ""
        assert env["FORECASTLAB_NO_BROWSER"] == "1"
        return SimpleNamespace(
            returncode=0,
            stdout="ForecastLab isolated startup verification passed and shut down cleanly.\n",
            stderr="",
        )

    monkeypatch.setattr(verifier.subprocess, "run", completed)
    result = verifier.verify_isolated_mock_startup(root)
    assert result["status"] == "passed"
    assert result["provider_calls"] == 0
    assert result["residual_pid_files"] == []

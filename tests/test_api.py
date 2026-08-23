from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.3.1"
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert meta.json()["application_version"] == "0.3.1"
    worker = client.get("/health/worker")
    assert worker.status_code == 200
    assert "fresh" in worker.json()


def test_settings_never_return_key(client) -> None:
    saved = client.put(
        "/api/settings",
        json={"model_api_key": "sk-secret-should-not-echo", "model_provider": "mock"},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert "sk-secret-should-not-echo" not in str(body)
    assert body["model_api_key_set"] is True
    again = client.get("/api/settings")
    assert "sk-secret" not in str(again.json())
    assert again.json().get("model_api_key") is None


def test_full_mock_forecast_contract_edit_report_export(client) -> None:
    created = client.post(
        "/api/questions",
        json={
            "question": "Will the US unemployment rate exceed 5% before 30 June 2027?",
            "mode": "demo",
            "start": False,
        },
    )
    assert created.status_code == 200
    qid = created.json()["id"]
    operationalized = client.post(f"/api/questions/{qid}/operationalize")
    assert operationalized.status_code == 200
    contract = operationalized.json()["contract"]
    assert contract["exact_yes"]
    contract["ambiguity_notes"] = "Edited by the reviewer."
    saved = client.put(
        f"/api/questions/{qid}/contract",
        json={
            "exact_yes": contract["exact_yes"],
            "exact_no": contract["exact_no"],
            "resolution_deadline": contract["resolution_deadline"],
            "authoritative_source": contract["authoritative_source"],
            "fallback_sources": contract.get("fallback_sources") or [],
            "geography": contract.get("geography"),
            "units": contract.get("units"),
            "ambiguity_notes": contract["ambiguity_notes"],
            "cancellation_conditions": contract.get("cancellation_conditions") or "",
            "resolver_risk_notes": contract.get("resolver_risk_notes") or "",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["contract"]["ambiguity_notes"] == "Edited by the reviewer."

    run = client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    assert run.status_code == 200
    assert run.json()["status"] == "completed"

    report = client.get(f"/api/questions/{qid}/report")
    payload = report.json()
    latest = payload["latest_run"]
    context = latest["execution_context"]
    assert context["effective_mode"] == "demo"
    assert context["model_is_mock"] is True
    assert context["search_is_mock"] is True
    assert context["fixture_evidence_used"] is True
    assert latest["configuration_hash"]
    assert payload["latest_probability"] is not None
    assert 0.02 <= payload["latest_probability"] <= 0.98
    tracks = latest["tracks"]
    assert len(tracks) == 3
    types = {track["track_type"] for track in tracks}
    assert types == {"base_rate", "current_evidence", "skeptic"}
    assert latest["aggregation"]["method"] == "equal_weight_logit_shrinkage"
    evidence = latest["evidence"]
    assert evidence
    urls = {item["url"] for item in evidence}
    assert all(
        url.startswith("https://fixtures.forecastlab.local/") or item.get("rejected")
        for item in evidence
        for url in [item["url"]]
    )
    assert urls

    md = client.get(f"/api/questions/{qid}/export.md")
    assert md.status_code == 200
    assert "Ensemble probability" in md.text
    js = client.get(f"/api/questions/{qid}/export.json")
    assert js.status_code == 200
    assert js.json()["id"] == qid


def test_watcher_and_second_version(client) -> None:
    created = client.post(
        "/api/questions",
        json={
            "question": "Will the US unemployment rate exceed 5% before 30 June 2027?",
            "mode": "demo",
            "start": False,
        },
    )
    qid = created.json()["id"]
    client.post(f"/api/questions/{qid}/operationalize")
    client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    report = client.get(f"/api/questions/{qid}/report").json()
    assert report["version_count"] == 1
    watch = report["watches"][0]
    first = client.post(f"/api/watches/{watch['id']}/check")
    assert first.status_code == 200
    sim = client.post("/demo/indicators/unemployment/simulate", json={"value": 5.4})
    assert sim.status_code == 200
    second = client.post(f"/api/watches/{watch['id']}/check")
    assert second.json()["material"] is True
    stale = client.get(f"/api/questions/{qid}").json()
    assert stale["stale"] is True
    client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    again = client.get(f"/api/questions/{qid}/report").json()
    assert again["version_count"] == 2
    assert again["versions"][0]["previous_version_id"] == again["versions"][1]["id"]


def test_live_mode_without_keys_is_422(client) -> None:
    created = client.post(
        "/api/questions",
        json={"question": "Will live mode reject missing keys?", "mode": "live"},
    )
    qid = created.json()["id"]
    operationalized = client.post(f"/api/questions/{qid}/operationalize")
    assert operationalized.status_code == 422
    run = client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "live"})
    assert run.status_code == 422
    body = run.json()
    assert "reasons" in body
    again = client.get(f"/api/questions/{qid}").json()
    assert again["runs"] == []
    assert again["status"] == "draft"


def test_backtest_without_as_of_is_422(client) -> None:
    created = client.post(
        "/api/questions",
        json={"question": "Will backtest require as_of?", "mode": "backtest"},
    )
    qid = created.json()["id"]
    run = client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "backtest"})
    assert run.status_code == 422


def test_providers_readiness_and_mock_probe(client) -> None:
    health = client.get("/health/providers").json()
    assert health["demo"]["ready"] is True
    assert "reasons" in health["live"]
    assert "sk-" not in str(health)
    model = client.post("/api/settings/test-model").json()
    assert model["success"] is True
    assert model["provider"] == "mock"
    search = client.post("/api/settings/test-search").json()
    assert search["success"] is True


def test_persist_is_idempotent(client) -> None:
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import ForecastRun, ForecastVersion, ResearchTrack
    from forecastlab_api.pipeline import execute_run

    created = client.post(
        "/api/questions",
        json={"question": "Will the US unemployment rate exceed 5% before 30 June 2027?", "mode": "demo"},
    )
    qid = created.json()["id"]
    client.post(f"/api/questions/{qid}/operationalize")
    run = client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    run_id = run.json()["id"]
    with SessionLocal() as session:
        row = session.get(ForecastRun, run_id)
        assert row is not None
        execute_run(session, row)
        session.commit()
        versions = session.scalars(select(ForecastVersion).where(ForecastVersion.run_id == run_id)).all()
        tracks = session.scalars(select(ResearchTrack).where(ResearchTrack.run_id == run_id)).all()
        assert len(versions) == 1
        assert len(tracks) == 3


def test_benchmark_import_and_compare(client) -> None:
    from forecastlab_api.worker import drain_jobs

    csv = (
        "question,forecast_date,resolution_date,outcome,resolution_source,category,provenance,is_synthetic\n"
        "Will unique test series Z exceed threshold 9 before 2026-12-31?,2024-08-01,2026-01-01,1,https://fixtures.forecastlab.local/synthetic-z,test,api-test,true\n"
    )
    imported = client.post(
        "/api/benchmarks/import",
        files={"file": ("bench.csv", BytesIO(csv.encode("utf-8")), "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] >= 1
    assert imported.json()["is_synthetic"] is True
    assert imported.json()["dataset_hash"]
    dup = client.post(
        "/api/benchmarks/import",
        files={"file": ("bench.csv", BytesIO(csv.encode("utf-8")), "text/csv")},
    )
    assert dup.json()["duplicates"] >= 1
    mixed = (
        "question,forecast_date,resolution_date,outcome,resolution_source,category,provenance,is_synthetic\n"
        "Will mix A happen?,2024-08-01,2026-01-01,1,https://example.com/a,test,api-test,true\n"
        "Will mix B happen?,2024-08-01,2026-01-01,0,https://example.com/b,test,api-test,false\n"
    )
    rejected = client.post(
        "/api/benchmarks/import",
        files={"file": ("mix.csv", BytesIO(mixed.encode("utf-8")), "text/csv")},
    )
    assert rejected.status_code == 400
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item["name"] == "synthetic_fixtures_v1")
    created = client.post(
        "/api/experiments",
        json={"dataset_id": synth["id"], "profile_ids": ["single_agent_baseline", "three_track_ensemble"]},
    )
    assert created.status_code == 200
    assert created.json()["total_tasks"] == synth["question_count"] * 2
    first_id = created.json()["id"]
    drain_jobs(max_steps=80)
    summary = client.get(f"/api/experiments/{first_id}/summary").json()
    ids = {row["profile_id"] for row in summary["profiles"]}
    assert "single_agent_baseline" in ids
    assert "three_track_ensemble" in ids
    assert summary["synthetic"] is True
    assert summary["dataset_hash"]
    assert "Software-verification fixtures only" in summary["notice"]
    assert summary["reliability_by_profile"]
    assert all(item.get("available") is False for item in summary["reliability_by_profile"].values())
    second = client.post(
        "/api/experiments",
        json={"dataset_id": synth["id"], "profile_ids": ["single_agent_baseline"]},
    )
    assert second.json()["id"] != first_id
    board = client.get("/api/dashboard").json()
    assert all("synthetic series A" not in item["original_text"] for item in board["questions"])
    assert all(not item.get("is_benchmark") for item in board["questions"])


def test_json_benchmark_import(client) -> None:
    payload = [
        {
            "question": "Will unique JSON series Q exceed threshold 3 before 2026-12-31?",
            "forecast_date": "2024-08-01",
            "resolution_date": "2026-01-01",
            "outcome": 0,
            "resolution_source": "https://fixtures.forecastlab.local/synthetic-q",
            "category": "test",
            "provenance": "api-test",
            "is_synthetic": True,
        }
    ]
    imported = client.post(
        "/api/benchmarks/import",
        files={"file": ("bench.json", BytesIO(json.dumps(payload).encode("utf-8")), "application/json")},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] >= 1
    template = client.get("/api/benchmarks/template.csv")
    assert template.status_code == 200
    assert "question,forecast_date" in template.text
    assert "exact_yes,exact_no,resolution_deadline" in template.text


def test_report_run_order_normalizes_mixed_sqlite_timestamps() -> None:
    from forecastlab_api.main import _run_order_time

    runs = [
        SimpleNamespace(started_at=datetime(2026, 8, 22, 10, 0), finished_at=None),
        SimpleNamespace(started_at=datetime(2026, 8, 22, 11, 0, tzinfo=UTC), finished_at=None),
    ]

    ordered = sorted(runs, key=_run_order_time, reverse=True)

    assert ordered[0].started_at.hour == 11

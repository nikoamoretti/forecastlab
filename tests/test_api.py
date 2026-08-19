from __future__ import annotations

import json
from io import BytesIO


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
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


def test_benchmark_import_and_compare(client) -> None:
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
    dup = client.post(
        "/api/benchmarks/import",
        files={"file": ("bench.csv", BytesIO(csv.encode("utf-8")), "text/csv")},
    )
    assert dup.json()["duplicates"] >= 1
    ran = client.post("/api/benchmarks/run")
    assert ran.status_code == 200
    summary = client.get("/api/benchmarks/summary").json()
    ids = {row["profile_id"] for row in summary["profiles"]}
    assert "single_agent_baseline" in ids
    assert "three_track_ensemble" in ids
    assert summary["synthetic"] is True
    assert summary["reliability"]["available"] is False


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

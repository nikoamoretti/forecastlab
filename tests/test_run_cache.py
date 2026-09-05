from __future__ import annotations

import shutil
from datetime import UTC, datetime

from forecastlab.fetch import FIXTURES_DIR
from forecastlab.providers.mock import SAMPLE_QUESTION
from forecastlab.run_cache import RunCache


def test_run_cache_does_not_share_across_identities() -> None:
    left = RunCache.create(
        run_id="run-a",
        model_provider="mock",
        search_provider="mock",
        mode="demo",
        as_of=None,
        configuration_hash="one",
    )
    assert left.matches(
        run_id="run-a",
        model_provider="mock",
        search_provider="mock",
        mode="demo",
        as_of=None,
        configuration_hash="one",
    )
    assert left.matches(
        run_id="run-b",
        model_provider="mock",
        search_provider="mock",
        mode="demo",
        as_of=None,
        configuration_hash="one",
    ) is False
    assert left.matches(
        run_id="run-a",
        model_provider="openai_compatible",
        search_provider="mock",
        mode="demo",
        as_of=None,
        configuration_hash="one",
    ) is False
    assert left.matches(
        run_id="run-a",
        model_provider="mock",
        search_provider="tavily",
        mode="demo",
        as_of=None,
        configuration_hash="one",
    ) is False
    assert left.matches(
        run_id="run-a",
        model_provider="mock",
        search_provider="mock",
        mode="live",
        as_of=None,
        configuration_hash="one",
    ) is False
    assert left.matches(
        run_id="run-a",
        model_provider="mock",
        search_provider="mock",
        mode="demo",
        as_of=None,
        configuration_hash="two",
    ) is False
    assert left.matches(
        run_id="run-a",
        model_provider="mock",
        search_provider="mock",
        mode="demo",
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
        configuration_hash="one",
    ) is False


def test_watch_change_rerun_fetches_fresh_evidence(client, tmp_path, monkeypatch) -> None:
    from forecastlab import fetch as fetch_mod

    sources = tmp_path / "sources"
    shutil.copytree(FIXTURES_DIR, sources)
    page = sources / "bls-employment-situation.html"
    page.write_text(
        '<html><head><meta name="date" content="2024-06-07"></head><body>CONTENT_A unique-marker</body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(fetch_mod, "FIXTURES_DIR", sources)

    created = client.post(
        "/api/questions",
        json={"question": SAMPLE_QUESTION, "mode": "demo", "start": False},
    )
    qid = created.json()["id"]
    client.post(f"/api/questions/{qid}/operationalize")
    client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    first = client.get(f"/api/questions/{qid}/report").json()
    excerpts = " ".join(item.get("excerpt") or "" for item in first["latest_run"]["evidence"])
    assert "CONTENT_A unique-marker" in excerpts

    page.write_text(
        '<html><head><meta name="date" content="2024-06-07"></head><body>CONTENT_B unique-marker</body></html>',
        encoding="utf-8",
    )
    watch = first["watches"][0]
    client.post("/demo/indicators/unemployment/simulate", json={"value": 4.1})
    client.post(f"/api/watches/{watch['id']}/check")
    client.post("/demo/indicators/unemployment/simulate", json={"value": 8.8})
    changed = client.post(f"/api/watches/{watch['id']}/check").json()
    assert changed["material"] is True
    stale = client.get(f"/api/questions/{qid}").json()
    assert stale["stale"] is True

    client.post(f"/api/questions/{qid}/runs", json={"profile_id": "three_track_ensemble", "mode": "demo"})
    second = client.get(f"/api/questions/{qid}/report").json()
    excerpts_two = " ".join(item.get("excerpt") or "" for item in second["latest_run"]["evidence"])
    assert "CONTENT_B unique-marker" in excerpts_two
    assert "CONTENT_A unique-marker" not in excerpts_two
    assert second["version_count"] == 2


def test_user_cannot_create_internal_or_loopback_watch(client) -> None:
    created = client.post(
        "/api/questions",
        json={"question": SAMPLE_QUESTION, "mode": "demo", "start": False},
    )
    qid = created.json()["id"]
    blocked = [
        {"endpoint_url": "http://127.0.0.1/secret.json", "endpoint_type": "json"},
        {"endpoint_url": "http://localhost/page", "endpoint_type": "html"},
        {"endpoint_url": "demo:indicators/unemployment", "endpoint_type": "internal"},
        {"endpoint_url": "https://example.com/ok", "endpoint_type": "internal"},
    ]
    for body in blocked:
        response = client.post(f"/api/questions/{qid}/watches", json=body)
        assert response.status_code == 400, body

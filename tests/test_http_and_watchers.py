from __future__ import annotations

import httpx

from forecastlab.http_client import safe_get
from forecastlab.ranking import classify_source
from forecastlab.ssrf import UnsafeURLError, host_matches, validate_url
from forecastlab_api.watches import check_watch


def test_trusted_domain_uses_exact_boundaries() -> None:
    assert host_matches("bls.gov", "bls.gov")
    assert host_matches("www.bls.gov", "bls.gov")
    assert host_matches("bls.gov.attacker.example", "bls.gov") is False
    assert classify_source("https://bls.gov.attacker.example/data") == "secondary"
    assert classify_source("https://www.bls.gov/news") == "primary"


def test_embedded_credentials_rejected() -> None:
    try:
        validate_url("https://user:pass@example.com")
        raised = False
    except UnsafeURLError:
        raised = True
    assert raised is True


def test_redirect_to_private_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, text="nope")

    try:
        safe_get("https://example.com/start", transport=httpx.MockTransport(handler))
        raised = False
    except UnsafeURLError:
        raised = True
    assert raised is True


def test_oversized_response_stopped() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "99999999", "content-type": "text/plain"}, text="x")

    try:
        safe_get("https://example.com/big", transport=httpx.MockTransport(handler))
        raised = False
    except UnsafeURLError:
        raised = True
    assert raised is True


def test_unsafe_content_type_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"\x00\x01")

    try:
        safe_get("https://example.com/bin", transport=httpx.MockTransport(handler))
        raised = False
    except UnsafeURLError:
        raised = True
    assert raised is True


def test_json_watcher_rejects_loopback(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab_api.db import Base
    from forecastlab_api.models import Question, Watch

    engine = create_engine(f"sqlite:///{tmp_path}/watch.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        question = Question(id="q1", original_text="test", status="draft")
        watch = Watch(
            id="w1",
            question_id="q1",
            endpoint_url="http://127.0.0.1/secret.json",
            endpoint_type="json",
            json_path="$.value",
        )
        session.add(question)
        session.add(watch)
        session.commit()
        event = check_watch(session, watch)
        assert event.fetch_status.startswith("error:")
        assert event.material is False

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


def test_chunked_response_without_content_length_is_capped() -> None:
    served = {"chunks": 0}

    def body():
        for _ in range(40):
            served["chunks"] += 1
            yield b"x" * 80_000

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=body())

    try:
        result = safe_get("https://example.com/chunked", max_bytes=150_000, transport=httpx.MockTransport(handler))
        retained = len(result.content)
        raised = False
    except UnsafeURLError:
        retained = 0
        raised = True
    assert raised is True
    assert retained == 0
    assert served["chunks"] <= 3
    assert served["chunks"] >= 2


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


def test_html_loopback_watch_is_rejected(tmp_path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab_api.db import Base
    from forecastlab_api.models import Question, Watch

    engine = create_engine(f"sqlite:///{tmp_path}/html-watch.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        session.add(Question(id="q1", original_text="test", status="draft"))
        session.add(Watch(id="w1", question_id="q1", endpoint_url="http://localhost/secret", endpoint_type="html"))
        session.commit()
        event = check_watch(session, session.get(Watch, "w1"))
        assert event.fetch_status.startswith("error:")


def test_user_watch_validation_rejects_private_and_metadata() -> None:
    from forecastlab_api.watches import validate_user_watch

    cases = [
        ("http://127.0.0.1/secret.json", "json"),
        ("http://localhost/secret.html", "html"),
        ("http://10.1.2.3/status", "json"),
        ("http://[::1]/secret", "json"),
        ("http://[fd00::1]/secret", "html"),
        ("http://metadata.google.internal/latest", "json"),
        ("http://169.254.169.254/latest/meta-data", "json"),
        ("https://example.com/ok", "internal"),
    ]
    for url, kind in cases:
        try:
            validate_user_watch(endpoint_url=url, endpoint_type=kind)
            raised = False
        except UnsafeURLError:
            raised = True
        assert raised is True, url


def test_user_watch_validation_allows_external_hosts() -> None:
    from forecastlab_api.watches import validate_user_watch

    assert validate_user_watch(endpoint_url="https://example.com/status.json", endpoint_type="json") is None
    assert validate_user_watch(endpoint_url="https://example.com/page", endpoint_type="html") is None


def test_internal_demo_watch_does_not_use_network(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab_api import watches as watches_mod
    from forecastlab_api.db import Base
    from forecastlab_api.models import Question
    from forecastlab_api.watches import attach_demo_watch, check_watch

    def boom(*_args, **_kwargs):
        raise AssertionError("internal demo watch must not use the network")

    monkeypatch.setattr(watches_mod, "safe_get", boom)
    engine = create_engine(f"sqlite:///{tmp_path}/demo-watch.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        question = Question(id="q1", original_text="test", status="draft")
        session.add(question)
        watch = attach_demo_watch(session, question)
        session.commit()
        assert watch.endpoint_type == "internal"
        event = check_watch(session, watch)
        assert event.fetch_status == "ok"
        assert event.material is False


def test_json_watch_redirect_to_private_is_rejected(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab.http_client import safe_get
    from forecastlab_api import watches as watches_mod
    from forecastlab_api.db import Base
    from forecastlab_api.models import Question, Watch

    def redirecting_get(url, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://192.168.1.9/secret"})
            return httpx.Response(200, text="nope")

        return safe_get(url, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(watches_mod, "safe_get", redirecting_get)
    engine = create_engine(f"sqlite:///{tmp_path}/redir-watch.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        session.add(Question(id="q1", original_text="test", status="draft"))
        watch = Watch(id="w1", question_id="q1", endpoint_url="https://example.com/start", endpoint_type="json")
        session.add(watch)
        session.commit()
        event = check_watch(session, watch)
        assert event.fetch_status.startswith("error:")


def test_valid_external_json_and_html_watches(tmp_path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from forecastlab.http_client import SafeResponse
    from forecastlab.schemas import FetchedDocument
    from forecastlab.timeutil import utcnow
    from forecastlab_api import watches as watches_mod
    from forecastlab_api.db import Base
    from forecastlab_api.models import Question, Watch

    def fake_get(url, **kwargs):
        return SafeResponse(
            url=url,
            final_url=url,
            status_code=200,
            content=b'{"value": 4.2}',
            content_type="application/json",
        )

    def fake_fetch(url, **kwargs):
        now = utcnow()
        return FetchedDocument(
            url=url,
            title="ok",
            publisher="example.com",
            published_at=now,
            retrieved_at=now,
            text="external html body",
            content_hash="abc",
            as_of_eligible=True,
        )

    monkeypatch.setattr(watches_mod, "safe_get", fake_get)
    monkeypatch.setattr(watches_mod, "fetch_document", fake_fetch)
    engine = create_engine(f"sqlite:///{tmp_path}/ext-watch.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        session.add(Question(id="q1", original_text="test", status="draft"))
        json_watch = Watch(id="wj", question_id="q1", endpoint_url="https://example.com/status.json", endpoint_type="json", json_path="$.value")
        html_watch = Watch(id="wh", question_id="q1", endpoint_url="https://example.com/page", endpoint_type="html")
        session.add_all([json_watch, html_watch])
        session.commit()
        json_event = check_watch(session, json_watch)
        html_event = check_watch(session, html_watch)
        assert json_event.fetch_status == "ok"
        assert json_event.new_value == "4.2"
        assert html_event.fetch_status == "ok"
        assert "external html body" in (html_event.new_value or "")

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from forecastlab.errors import EvidenceIntegrityError
from forecastlab.http_client import SafeResponse
from forecastlab.wayback import canonicalize_url, discover_snapshots, parse_wayback_url, verify_final_capture


def test_parse_wayback_url_and_id_modifier() -> None:
    capture = parse_wayback_url("https://web.archive.org/web/20240601120000id_/https://example.org/path?q=1")
    assert capture is not None
    assert capture.timestamp == datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    assert capture.archived_original_url.startswith("https://example.org/path")
    assert capture.modifier == "id_"


def test_final_capture_before_cutoff_succeeds() -> None:
    response = SafeResponse(
        url="https://web.archive.org/web/20240101000000/https://example.org/a",
        final_url="https://web.archive.org/web/20240101000000id_/https://example.org/a",
        status_code=200,
        content=b"<html></html>",
        content_type="text/html",
    )
    capture, status = verify_final_capture(
        response,
        requested_url="https://example.org/a",
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
    )
    assert status == "verified"
    assert capture.timestamp.year == 2024


def test_final_capture_after_cutoff_fails() -> None:
    response = SafeResponse(
        url="https://web.archive.org/web/20250701000000/https://example.org/a",
        final_url="https://web.archive.org/web/20250701000000/https://example.org/a",
        status_code=200,
        content=b"<html></html>",
        content_type="text/html",
    )
    with pytest.raises(EvidenceIntegrityError, match="final_snapshot_after_as_of"):
        verify_final_capture(response, requested_url="https://example.org/a", as_of=datetime(2024, 6, 1, tzinfo=UTC))


def test_redirect_to_different_original_fails() -> None:
    response = SafeResponse(
        url="https://web.archive.org/web/20240101000000/https://example.org/a",
        final_url="https://web.archive.org/web/20240101000000/https://other.example/b",
        status_code=200,
        content=b"<html></html>",
        content_type="text/html",
    )
    with pytest.raises(EvidenceIntegrityError, match="final_snapshot_original_url_mismatch"):
        verify_final_capture(response, requested_url="https://example.org/a", as_of=datetime(2024, 6, 1, tzinfo=UTC))


def test_redirect_to_live_page_fails() -> None:
    response = SafeResponse(
        url="https://web.archive.org/web/20240101000000/https://example.org/a",
        final_url="https://example.org/a",
        status_code=200,
        content=b"<html></html>",
        content_type="text/html",
    )
    with pytest.raises(EvidenceIntegrityError, match="final_snapshot_not_wayback"):
        verify_final_capture(response, requested_url="https://example.org/a", as_of=datetime(2024, 6, 1, tzinfo=UTC))


def test_encoded_cdx_url_remains_valid(monkeypatch) -> None:
    from forecastlab import wayback

    captured: dict[str, str] = {}

    def fake_get(url: str, **kwargs):
        captured["url"] = url
        return SafeResponse(
            url=url,
            final_url=url,
            status_code=200,
            content=b'[["timestamp","original","statuscode"]]',
            content_type="application/json",
        )

    monkeypatch.setattr(wayback, "safe_get", fake_get)
    source = "https://exämple.org/path?a=1&b=two#frag"
    discover_snapshots(source, as_of=datetime(2024, 6, 1, tzinfo=UTC))
    parsed = urlparse(captured["url"])
    query = parse_qs(parsed.query)
    assert "a=1" in query["url"][0]
    assert "b=two" in query["url"][0]
    assert "#" not in query["url"][0]
    assert "&url=" in captured["url"] or "url=" in captured["url"]


def test_canonicalize_strips_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.org/a/?q=1#x") == "https://example.org/a?q=1"


def test_requested_and_final_metadata_in_evidence(client) -> None:
    from unittest.mock import patch

    from forecastlab.fetch import fetch_document
    from forecastlab.http_client import SafeResponse as SR

    response = SR(
        url="https://web.archive.org/web/20240101000000/https://example.org/report",
        final_url="https://web.archive.org/web/20240101000000id_/https://example.org/report",
        status_code=200,
        content=b'<html><meta property="article:published_time" content="2023-01-01T00:00:00Z"><body>ok</body></html>',
        content_type="text/html",
    )
    with patch("forecastlab.fetch.safe_get", return_value=response):
        doc = fetch_document(
            "https://example.org/report",
            as_of=datetime(2024, 6, 1, tzinfo=UTC),
            allow_local_fixtures=False,
            snapshot_url="https://web.archive.org/web/20240101000000/https://example.org/report",
            snapshot_at=datetime(2024, 1, 1, tzinfo=UTC),
            mode="backtest",
        )
    assert doc.rejected is False
    assert doc.requested_snapshot_url.endswith("/https://example.org/report")
    assert doc.final_snapshot_url is not None
    assert "id_" in doc.final_snapshot_url
    assert doc.archived_original_url.endswith("example.org/report")
    assert doc.snapshot_verification_status == "verified"


def test_rejected_wayback_preserves_actual_final_metadata() -> None:
    from unittest.mock import patch

    from forecastlab.fetch import fetch_document
    from forecastlab.http_client import SafeResponse as SR

    requested = "https://example.org/a"
    final = "https://web.archive.org/web/20240115120000/https://other.example/b?x=1"
    response = SR(
        url="https://web.archive.org/web/20240101000000/https://example.org/a",
        final_url=final,
        status_code=200,
        content=b"<html><body>mismatch</body></html>",
        content_type="text/html",
    )
    with patch("forecastlab.fetch.safe_get", return_value=response):
        doc = fetch_document(
            requested,
            as_of=datetime(2024, 6, 1, tzinfo=UTC),
            allow_local_fixtures=False,
            snapshot_url="https://web.archive.org/web/20240101000000/https://example.org/a",
            snapshot_at=datetime(2024, 1, 1, tzinfo=UTC),
            mode="backtest",
        )
    assert doc.rejected is True
    assert doc.rejection_reason == "final_snapshot_original_url_mismatch"
    assert doc.final_snapshot_url == final
    assert doc.final_snapshot_at == datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    assert doc.archived_original_url is not None
    assert "other.example/b" in doc.archived_original_url
    assert doc.archived_original_url != requested
    assert "example.org/a" not in (doc.archived_original_url or "")

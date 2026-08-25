from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfWriter

from forecastlab import fetch as fetch_mod
from forecastlab.fetch import (
    DocumentDateMetadata,
    _date_metadata_from_html,
    _extract_pdf,
    _with_search_hint,
    fetch_document,
)


def test_trafilatura_metadata_date_is_discovered(monkeypatch) -> None:
    monkeypatch.setattr(
        fetch_mod.trafilatura,
        "extract_metadata",
        lambda _raw: SimpleNamespace(date="2026-03-04", title="Fixture"),
    )

    metadata = _date_metadata_from_html("<html><body>No explicit date element.</body></html>")

    assert metadata.published_at == datetime(2026, 3, 4, tzinfo=UTC)
    assert metadata.publication_date_source == "trafilatura_metadata:date"
    assert metadata.publication_date_verified is True


def test_article_published_time_is_discovered_with_attribute_order_independence() -> None:
    metadata = _date_metadata_from_html(
        '<meta content="2026-02-03T14:15:00Z" property="article:published_time">'
    )

    assert metadata.published_at == datetime(2026, 2, 3, 14, 15, tzinfo=UTC)
    assert metadata.publication_date_source == "html_meta:article:published_time"


def test_json_ld_publication_and_modified_dates_are_kept_separate() -> None:
    metadata = _date_metadata_from_html(
        """
        <script type="application/ld+json">
        {"@type":"NewsArticle","datePublished":"2026-01-02","dateModified":"2026-01-05"}
        </script>
        """
    )

    assert metadata.published_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert metadata.publication_date_source == "json_ld:datePublished"
    assert metadata.modified_at == datetime(2026, 1, 5, tzinfo=UTC)
    assert metadata.modified_date_source == "json_ld:dateModified"


def test_time_datetime_is_discovered() -> None:
    metadata = _date_metadata_from_html(
        '<article><time class="published" datetime="2025-12-31T23:00:00Z">31 December</time></article>'
    )

    assert metadata.published_at == datetime(2025, 12, 31, 23, 0, tzinfo=UTC)
    assert metadata.publication_date_source == "html_time:datetime"


def test_pdf_creation_metadata_is_retained_as_unverified_publication_hint() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/CreationDate": "D:20260102123456Z", "/ModDate": "D:20260103120000Z"})
    buffer = BytesIO()
    writer.write(buffer)

    _text, metadata = _extract_pdf(buffer.getvalue())

    assert metadata.published_at == datetime(2026, 1, 2, 12, 34, 56, tzinfo=UTC)
    assert metadata.publication_date_source == "pdf_creation_metadata"
    assert metadata.publication_date_verified is False
    assert metadata.modified_at == datetime(2026, 1, 3, 12, 0, tzinfo=UTC)


def test_tavily_published_date_hint_is_retained_but_not_verified() -> None:
    hinted = datetime(2026, 4, 5, tzinfo=UTC)

    metadata, retained_hint, hint_source = _with_search_hint(
        DocumentDateMetadata(),
        hinted,
        "tavily_search_hit",
    )

    assert metadata.published_at == hinted
    assert metadata.publication_date_source == "tavily_search_hit"
    assert metadata.publication_date_verified is False
    assert retained_hint == hinted
    assert hint_source == "tavily_search_hit"


def test_tavily_hint_cannot_replace_historical_snapshot_proof() -> None:
    document = fetch_document(
        "https://example.org/current-page",
        mode="backtest",
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        allow_local_fixtures=False,
        publication_date_hint=datetime(2024, 1, 1, tzinfo=UTC),
        publication_date_hint_source="tavily_search_hit",
    )

    assert document.rejected is True
    assert document.rejection_reason == "no_eligible_historical_snapshot"
    assert document.temporal_basis == "retrieval_date"


def test_live_undated_fetch_never_copies_retrieval_time_into_publication_time() -> None:
    document = fetch_document(
        "https://fixtures.forecastlab.local/undated",
        mode="live",
        allow_local_fixtures=True,
    )

    assert document.rejected is False
    assert document.published_at is None
    assert document.publication_date_verified is False
    assert document.temporal_basis == "retrieval_date"
    assert document.source_available_at == document.retrieved_at

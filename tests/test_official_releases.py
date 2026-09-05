from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from tests.test_question_selection import bls_payload

from forecastlab.macro import MacroDataError, MacroSpec
from forecastlab.macro_evidence import parse_release_document, release_pdf_text
from forecastlab.official_releases import (
    DOL_INDEX,
    dol_release_links,
    fed_calendar_url,
    release_announcements,
    verify_fed_schedule,
)
from forecastlab.question_selection import choose_questions

FIXTURES = Path(__file__).parent / "fixtures" / "official_releases"
NOW = datetime(2026, 9, 5, 5, tzinfo=UTC)
FILES = {"empsit": "empsit_09042026.pdf", "cpi": "cpi_08122026.pdf"}


def fed_html(family, *, day=None, time="08:30"):
    label, month, default = ("Employment Situation", "October", 2) if family == "empsit" else ("Consumer Price Index", "September", 11)
    return (f"<nav>PREVIOUS MONTH {month} 2026 NEXT MONTH</nav><p>all Eastern Time</p><table><tr><td>{day or default:02d} "
        f'<a href="https://www.bls.gov/news.release/{family}.toc.htm">{label}</a><br>({time})</td></tr></table>')


def document(family):
    return (FIXTURES / FILES[family]).read_bytes()


def url(family):
    return DOL_INDEX + "/" + FILES[family]


@pytest.mark.parametrize("family,period,at", [
    ("empsit", "2026-09", "2026-10-02T12:30:00+00:00"),
    ("cpi", "2026-08", "2026-09-11T12:30:00+00:00"),
])
def test_original_pdf_explicit_period_and_independent_calendar_agree(family, period, at):
    current, upcoming = release_announcements(document(family), source_url=url(family), checked_at=NOW)
    verified = verify_fed_schedule(fed_html(family), upcoming, source_url=fed_calendar_url(upcoming))
    assert verified.observation_period == period
    assert verified.release_at.isoformat() == at
    assert current.release_at < NOW < verified.release_at
    assert verified.schedule_basis == "dol_fed_schedule_v1"
    assert len(verified.source_hash) == 64
    assert verified.verification_sources[0]["source_lineage"] == "agency:bls"
    assert "is scheduled to be published" in verified.quote


@pytest.mark.parametrize("indicator,value", [("unemployment", 4.1), ("payrolls", 162000), ("cpi", 3.4)])
def test_original_pdf_outcomes_preserve_units_and_lineage(indicator, value):
    family = "cpi" if indicator == "cpi" else "empsit"
    spec = MacroSpec(indicator=indicator, threshold=3, observation_period="2026-07" if family == "cpi" else "2026-08",
        release_at="2026-08-12T12:30:00Z" if family == "cpi" else "2026-09-04T12:30:00Z")
    result = parse_release_document(document(family), spec, source_url=url(family), retrieved_at=NOW)
    assert result["value"] == value and result["schema_version"] == "macro_first_release_v2"
    assert result["source_lineage"] == "agency:bls"
    assert result["source_url"] == url(family)
    assert result["original_source_url"].startswith("https://www.bls.gov/news.release/archives/")
    for bad in [spec.model_copy(update={"observation_period": "2026-06"}),
                spec.model_copy(update={"release_at": spec.release_at + timedelta(hours=1)}),
                spec.model_copy(update={"revision_policy": "as_of_resolution"})]:
        with pytest.raises(MacroDataError):
            parse_release_document(document(family), bad, source_url=url(family), retrieved_at=NOW)
    with pytest.raises(MacroDataError, match="wrong_release_time_or_future_information"):
        parse_release_document(document(family), spec, source_url=url(family), retrieved_at=spec.release_at - timedelta(seconds=1))


@pytest.mark.parametrize("change", ["day", "time", "timezone", "month", "link", "duplicate"])
def test_changed_or_ambiguous_fed_calendar_cannot_validate_schedule(change):
    _, upcoming = release_announcements(document("cpi"), source_url=url("cpi"), checked_at=NOW)
    original = fed_html("cpi")
    changed = {"day": fed_html("cpi", day=12), "time": fed_html("cpi", time="09:30"),
        "timezone": original.replace("all Eastern Time", "UTC"), "month": original.replace("September", "October"),
        "link": original.replace("www.bls.gov", "unrelated.example"), "duplicate": original + original}[change]
    with pytest.raises(MacroDataError):
        verify_fed_schedule(changed, upcoming, source_url=fed_calendar_url(upcoming))


def test_announcements_cannot_infer_month_or_accept_conflicting_text(monkeypatch):
    from forecastlab import official_releases as parser
    original = release_pdf_text(document("cpi"))
    for bad in [original.replace("for August 2026 is scheduled", "is scheduled"),
                original.replace("Friday, September 11", "Thursday, September 11"),
                original + " The Consumer Price Index news release for August 2026 is scheduled to be published on Monday, September 14, 2026, at 8:30 a.m. (ET)"]:
        monkeypatch.setattr(parser, "release_pdf_text", lambda _, bad=bad: bad)
        with pytest.raises(MacroDataError):
            release_announcements(document("cpi"), source_url=url("cpi"), checked_at=NOW)


def test_index_allowlist_pdf_errors_and_stale_announcements():
    assert dol_release_links('<a href="https://evil.example/cpi_08122026.pdf">CPI</a>') == {}
    with pytest.raises(MacroDataError, match="ambiguous"):
        dol_release_links(f'<a href="{url("cpi")}">CPI</a><a href="{DOL_INDEX}/cpi_09112026.pdf">CPI</a>')
    for bad in [b"<html>Access denied</html>", b"%PDF-" + b" " * 2_000_000, b"%PDF-invalid"]:
        with pytest.raises(MacroDataError):
            release_pdf_text(bad)
    with pytest.raises(MacroDataError, match="stale"):
        release_announcements(document("cpi"), source_url=url("cpi"), checked_at=NOW + timedelta(days=90))


@pytest.mark.parametrize("conflict", [False, True])
def test_blocked_bls_uses_retained_official_sources_without_models(client, monkeypatch, conflict):
    from fastapi import HTTPException

    from forecastlab_api import question_suggestions as service
    from forecastlab_api.artifact_store import get_bytes
    from forecastlab_api.autopilot import _resolution_evidence
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    requests = []
    original_client = httpx.Client

    def send(request):
        address = str(request.url)
        requests.append(address)
        if request.url.host == "www.bls.gov":
            return httpx.Response(403)
        if request.url.host == "api.bls.gov":
            return httpx.Response(200, json=bls_payload())
        if address == DOL_INDEX:
            return httpx.Response(200, text="".join(f'<a href="{url(f)}">{f}</a>' for f in FILES), headers={"content-type": "text/html"})
        for family in FILES:
            if address == url(family):
                return httpx.Response(200, content=document(family), headers={"content-type": "application/pdf"})
        family = "empsit" if "oct26" in address else "cpi"
        return httpx.Response(200, text=fed_html(family, day=3 if conflict and family == "empsit" else None), headers={"content-type": "text/html"})

    monkeypatch.setattr(service.httpx, "Client", lambda **kw: original_client(transport=httpx.MockTransport(send), **kw))
    sources = service._fetch_sources(NOW)
    candidates, _ = choose_questions(sources.releases, sources.snapshots, now=NOW)
    assert len(candidates) == (1 if conflict else 3)
    assert len([r for r in requests if r.startswith("https://www.bls.gov/")]) == 1
    assert len([r for r in requests if "api.bls.gov" in r]) == 1
    assert bool(sources.gaps) is conflict
    assert get_bytes(sources.documents[url("cpi")]) == document("cpi")
    assert all(c.schedule.verification_sources for c in candidates)
    evidence = _resolution_evidence(candidates[0], sources)[0]
    assert evidence["quote"] == candidates[0].schedule.quote
    assert evidence["corroboration"][0]["artifact"]["sha256"]
    sources.documents[candidates[0].schedule.source_url]["sha256"] = "tampered"
    with pytest.raises(HTTPException):
        _resolution_evidence(candidates[0], sources)

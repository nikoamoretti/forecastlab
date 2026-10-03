"""Offline tests: official publication documents as frozen ``resolution`` evidence for FRED cohort entries."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from forecastlab.fast_questions import dgs10_release_at
from forecastlab.fred_release_documents import (
    UNRECOGNIZED,
    document_text,
    parse_dol_weekly_claims,
    parse_h15_release,
    release_claim,
    verify_release_target,
)
from forecastlab.macro import MacroDataError, MacroSnapshot, MacroSpec, normalize_observations
from forecastlab.root_event import digest

FIXTURES = Path(__file__).parent / "fixtures" / "fred_release_documents"
DOL_PDF = FIXTURES / "dol_ui_data_10012026.pdf"
H15_HTML = FIXTURES / "frb_h15_10022026.html"
CHECKED = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)


def claims_spec(period="2026-10-03", release_at=datetime(2026, 10, 8, 12, 30, tzinfo=UTC)) -> MacroSpec:
    return MacroSpec(indicator="jobless_claims", observation_period=period, threshold=197000, release_at=release_at)


def dgs10_spec(period="2026-10-05", release_at=datetime(2026, 10, 6, 21, 0, tzinfo=UTC)) -> MacroSpec:
    return MacroSpec(indicator="treasury_10y", observation_period=period, threshold=5.24, release_at=release_at)


def h15_html(*, release: str = "January 6, 2040", columns=("2040 Jan 2", "2040 Jan 3", "2040 Jan 4", "2040 Jan 5"),
             note: str = "The release is posted daily Monday through Friday at 4:15pm.") -> bytes:
    """A minimal H.15 page with the official structure; hidden script text must never count."""
    headers = "".join("<th>" + "<br>".join(column.split()) + "</th>" for column in columns)
    return (f'<html><head><script>var fake = "The release is posted daily";</script></head><body>'
            f'<div class="note">\n      {note}\n      The release is not posted on holidays or in the event that the\n'
            f'      Board is closed.\n    </div><div class="dates">Release date: {release}</div>'
            f'<h5>Selected Interest Rates</h5><span class="tableunit">Yields in percent per annum</span>'
            f'<table><thead><tr><th>Instruments</th>{headers}</tr></thead><tbody>'
            f'<tr><th>Treasury constant maturities</th></tr><tr><th>Nominal 9</th></tr>'
            f'<tr><th>10-year</th><td>5.17</td></tr></tbody></table></body></html>').encode()


# --- Parsers on the original documents ---------------------------------------------------------

def test_dol_weekly_claims_release_is_parsed_with_a_verbatim_quote():
    content = DOL_PDF.read_bytes()
    document = parse_dol_weekly_claims(content)
    assert document.release_date.isoformat() == "2026-10-01"
    assert document.published_at == datetime(2026, 10, 1, 12, 30, tzinfo=UTC)  # 8:30 a.m. EDT
    assert document.latest_period == "2026-09-26" and document.reports_revision
    assert document.quote.startswith("TRANSMISSION OF MATERIALS IN THIS RELEASE IS EMBARGOED UNTIL 8:30 A.M. (Eastern) "
                                     "Thursday, October 1, 2026 UNEMPLOYMENT INSURANCE WEEKLY CLAIMS")
    assert "In the week ending September 26, the advance figure for seasonally adjusted initial claims was 197,000" in document.quote
    assert document.quote.endswith("The previous week's level was revised up by 1,000 from 197,000 to 198,000.")
    assert document.quote in document_text("jobless_claims", content)
    assert release_claim(document) == (
        "The U.S. Department of Labor Unemployment Insurance Weekly Claims news release, embargoed until 8:30 a.m. "
        "Eastern on Thursday, October 1, 2026, reports the advance figure for seasonally adjusted initial claims for "
        "the week ending Saturday, September 26, 2026, and revises the previous week's level.")


def test_h15_release_is_parsed_with_a_verbatim_quote():
    content = H15_HTML.read_bytes()
    document = parse_h15_release(content)
    assert document.release_date.isoformat() == "2026-10-02" and document.latest_period == "2026-10-01"
    assert document.posting_time == "4:15pm" and document.published_at is None
    assert document.quote.startswith("The release is posted daily Monday through Friday at 4:15pm. The release is not "
                                     "posted on holidays or in the event that the Board is closed. Release date: October 2, 2026")
    assert document.quote.endswith("Instruments 2026 Sep 25 2026 Sep 28 2026 Sep 29 2026 Sep 30 2026 Oct 1")
    assert document.quote in document_text("treasury_10y", content)
    assert release_claim(document) == (
        "The Federal Reserve Board H.15 Selected Interest Rates release is posted daily Monday through Friday at "
        "4:15pm, and not on holidays or when the Board is closed; the release dated Friday, October 2, 2026 reports "
        "selected interest rates for dates through October 1, 2026.")


@pytest.mark.parametrize(("parser", "content", "error"), [
    (parse_dol_weekly_claims, b"<html>Access Denied</html>", "official_release_pdf_invalid"),
    (parse_h15_release, b"<html><body>Access Denied</body></html>", UNRECOGNIZED),
    (parse_h15_release, h15_html(note="The release is posted weekly."), UNRECOGNIZED),
    (parse_h15_release, h15_html(columns=("2040 Jan 5", "2040 Jan 4")), UNRECOGNIZED),  # out of order
    (parse_h15_release, h15_html(columns=("2040 Jan 6",)), UNRECOGNIZED),  # not before the release date
    (parse_h15_release, h15_html(release="January 7, 2040", columns=("2040 Jan 6",)), UNRECOGNIZED),  # Saturday
    (parse_h15_release, h15_html().replace(b"10-year", b"20-year"), UNRECOGNIZED),
])
def test_unrecognized_documents_fail_closed(parser, content, error):
    with pytest.raises(MacroDataError, match=f"^{error}$"):
        parser(content)


# --- Publication-pattern checks ----------------------------------------------------------------

@pytest.mark.parametrize("spec", [
    claims_spec(), claims_spec("2026-10-10", datetime(2026, 10, 15, 12, 30, tzinfo=UTC)),
    *[dgs10_spec(f"2026-10-0{day}", dgs10_release_at(date(2026, 10, day))) for day in range(5, 10)],
])
def test_fast_batch_targets_follow_the_retained_publication_pattern(spec):
    path = DOL_PDF if spec.indicator == "jobless_claims" else H15_HTML
    parser = parse_dol_weekly_claims if spec.indicator == "jobless_claims" else parse_h15_release
    pattern = verify_release_target(parser(path.read_bytes()), spec, checked_at=CHECKED)
    assert pattern["target_release_stated_in_document"] is False
    assert pattern["target_observation_period"] == spec.observation_period


@pytest.mark.parametrize(("which", "spec", "checked", "gap"), [
    ("claims", claims_spec(), datetime(2026, 10, 12, 5, tzinfo=UTC), "official_release_document_stale"),
    ("claims", claims_spec(), datetime(2026, 10, 1, 12, 0, tzinfo=UTC), "official_release_document_date_invalid"),
    ("h15", dgs10_spec(), datetime(2026, 10, 8, 5, tzinfo=UTC), "official_release_document_stale"),
    ("claims", claims_spec("2026-09-26", datetime(2026, 10, 1, 12, 30, tzinfo=UTC)), CHECKED,
     "official_release_document_not_before_target"),
    ("claims", claims_spec(release_at=datetime(2026, 10, 7, 12, 30, tzinfo=UTC)), CHECKED,
     "official_release_time_not_verified"),
    ("h15", dgs10_spec(release_at=datetime(2026, 10, 7, 21, tzinfo=UTC)), CHECKED, "official_release_time_not_verified"),
    ("h15", claims_spec(), CHECKED, "official_release_document_series_mismatch"),
])
def test_pattern_checks_record_named_gaps(which, spec, checked, gap):
    document = (parse_dol_weekly_claims(DOL_PDF.read_bytes()) if which == "claims"
                else parse_h15_release(H15_HTML.read_bytes()))
    with pytest.raises(MacroDataError, match=f"^{gap}$"):
        verify_release_target(document, spec, checked_at=checked)


def test_a_holiday_shifted_release_is_a_gap_not_evidence():
    # A Monday holiday: the Tuesday release covers Friday, not the previous weekday.
    document = parse_h15_release(h15_html(release="January 10, 2040", columns=("2040 Jan 5", "2040 Jan 6")))
    spec = dgs10_spec("2040-01-11", datetime(2040, 1, 12, 21, tzinfo=UTC))
    with pytest.raises(MacroDataError, match="^official_release_pattern_unverified$"):
        verify_release_target(document, spec, checked_at=datetime(2040, 1, 10, 22, tzinfo=UTC))
    claims = parse_dol_weekly_claims(DOL_PDF.read_bytes())
    shifted = claims.model_copy(update={"release_date": claims.release_date - timedelta(days=1),
                                        "published_at": claims.published_at - timedelta(days=1)})
    with pytest.raises(MacroDataError, match="^official_release_pattern_unverified$"):
        verify_release_target(shifted, claims_spec(), checked_at=CHECKED)


# --- Retrieval, retention and the evidence item ------------------------------------------------

def _documents_client(responses: dict[str, httpx.Response], seen: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return responses[str(request.url)]
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_responses() -> dict[str, httpx.Response]:
    return {"https://www.dol.gov/ui/data.pdf": httpx.Response(200, content=DOL_PDF.read_bytes(),
                                                               headers={"content-type": "application/pdf"}),
            "https://www.federalreserve.gov/releases/h15/": httpx.Response(200, content=H15_HTML.read_bytes(),
                                                                            headers={"content-type": "text/html; charset=utf-8"})}


@pytest.fixture()
def local_artifacts(tmp_path, monkeypatch):
    from forecastlab_api import official_sources
    from forecastlab_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(official_sources, "utcnow", lambda: CHECKED)
    return tmp_path


def test_documents_are_retained_and_become_resolution_evidence(local_artifacts):
    from forecastlab_api.artifact_store import get_bytes
    from forecastlab_api.official_sources import fetch_fred_release_documents, release_document_evidence

    seen: list[str] = []
    with _documents_client(_ok_responses(), seen) as http:
        documents = fetch_fred_release_documents({"jobless_claims", "treasury_10y"}, client=http)
    assert sorted(seen) == ["https://www.dol.gov/ui/data.pdf", "https://www.federalreserve.gov/releases/h15/"]
    for spec, path, lineage in [(claims_spec(), DOL_PDF, "agency:dol"), (dgs10_spec(), H15_HTML, "agency:frb")]:
        retained = documents[spec.indicator]
        assert not isinstance(retained, str)
        assert retained.artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert get_bytes(retained.artifact) == path.read_bytes()
        (item,) = release_document_evidence(spec, retained)
        assert item["required_sections"] == ["resolution"] and item["usable"] and item["relevant"]
        assert item["primary_source"] and item["source_lineage"] == lineage
        assert item["classification"] == "background" and item["schema_version"] == "evidence_assessment_v2"
        assert item["claim_id"].startswith("release:") and item["url"] == retained.document.source_url
        assert item["quote"] == retained.document.quote and item["quote"] in document_text(spec.indicator, path.read_bytes())
        assert item["artifact"] == retained.artifact and item["source_available_at"] == CHECKED.isoformat()
        assert item["release_pattern"]["target_release_stated_in_document"] is False
    # Each target gets its own claim id over the same retained document.
    first = release_document_evidence(dgs10_spec(), documents["treasury_10y"])[0]["claim_id"]
    second = release_document_evidence(dgs10_spec("2026-10-06", datetime(2026, 10, 7, 21, tzinfo=UTC)),
                                       documents["treasury_10y"])[0]["claim_id"]
    assert first != second


def test_tampered_or_unquoted_documents_never_become_evidence(local_artifacts):
    from forecastlab_api.official_sources import fetch_fred_release_documents, release_document_evidence

    with _documents_client(_ok_responses()) as http:
        retained = fetch_fred_release_documents({"treasury_10y"}, client=http)["treasury_10y"]
    assert not isinstance(retained, str)
    with pytest.raises(MacroDataError, match="^official_release_document_hash_mismatch$"):
        release_document_evidence(dgs10_spec(), replace(retained, content=retained.content + b" "))
    invented = retained.document.model_copy(update={"quote": "The release is posted hourly."})
    with pytest.raises(MacroDataError, match="^official_release_quote_not_verbatim$"):
        release_document_evidence(dgs10_spec(), replace(retained, document=invented))
    with pytest.raises(MacroDataError, match="^official_release_document_series_mismatch$"):
        release_document_evidence(claims_spec(), retained)


@pytest.mark.parametrize(("response", "gap"), [
    (httpx.Response(403, content=b"Access Denied", headers={"content-type": "text/html"}), "official_release_document_unavailable"),
    (httpx.Response(301, headers={"location": "https://example.com/"}), "official_release_document_unavailable"),
    (httpx.Response(200, content=b"<html>Access Denied</html>", headers={"content-type": "text/html"}),
     "official_release_document_unavailable"),  # wrong content type for a PDF
    (httpx.Response(200, content=b"%PDF-1.7 broken", headers={"content-type": "application/pdf"}),
     "official_release_pdf_extraction_failed"),
])
def test_unavailable_or_unparseable_documents_are_recorded_as_gaps(local_artifacts, response, gap):
    from forecastlab_api.official_sources import fetch_fred_release_documents

    with _documents_client({"https://www.dol.gov/ui/data.pdf": response}) as http:
        assert fetch_fred_release_documents({"jobless_claims"}, client=http) == {"jobless_claims": gap}


def test_release_evidence_lets_the_root_gate_cover_resolution():
    """The root method's gate: every required section covered by usable evidence and a primary source."""
    from forecastlab_api.official_sources import RetainedReleaseDocument, release_document_evidence
    from forecastlab_api.root_executor import _macro_packet

    content = DOL_PDF.read_bytes()
    retained = RetainedReleaseDocument(indicator="jobless_claims", content=content, retrieved_at=CHECKED,
        artifact={"key": "sources/x", "sha256": hashlib.sha256(content).hexdigest()},
        document=parse_dol_weekly_claims(content))
    weeks = {(datetime(2026, 9, 26) - timedelta(days=7 * i)).date().isoformat(): 200000.0 + i for i in range(104)}
    observations = normalize_observations("jobless_claims", weeks, available_at=CHECKED, vintage=CHECKED.isoformat(),
        source_url="https://fred.stlouisfed.org/series/ICSA", revision_basis="latest_observed_revisions_not_first_release")
    payload = {"csv": "test"}
    snapshot = MacroSnapshot(indicator="jobless_claims", retrieved_at=CHECKED, observations=observations,
                             raw_payload=payload, raw_hash=digest(payload), source_lineage="agency:dol")
    without = _macro_packet(snapshot.model_dump(mode="json"))
    packet = without + release_document_evidence(claims_spec(), retained)

    def covered(items):
        return {section for item in items if item["usable"] for section in item["required_sections"]}
    assert "resolution" not in covered(without)
    assert covered(packet) == {"resolution", "reference_class", "current_conditions"}
    assert any(item["primary_source"] and item["usable"] for item in packet)


# --- Cohort freeze ------------------------------------------------------------------------------

def test_cohort_freeze_attaches_fred_release_evidence_and_records_gaps(client, monkeypatch):
    from sqlalchemy import select

    from forecastlab.execution import resolve_execution_context
    from forecastlab_api import official_sources, prospective
    from forecastlab_api.config import settings
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import PersonalForecast, ProspectiveAssignment, ProspectiveCohort

    def resolve(question, *, profile_id, mode):
        return resolve_execution_context(requested_mode="live", profile_id=profile_id, settings={
            "model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-not-real",
            "search_provider": "tavily", "search_api_key": "test-not-real", "max_cost_usd": 5})

    def forbidden():
        raise AssertionError("FRED-only cohorts must not read the BLS calendar")

    html = h15_html()
    responses = {"https://www.federalreserve.gov/releases/h15/": httpx.Response(200, content=html, headers={"content-type": "text/html"}),
                 "https://www.dol.gov/ui/data.pdf": httpx.Response(403, content=b"denied", headers={"content-type": "text/html"})}
    real_fetch = official_sources.fetch_fred_release_documents
    monkeypatch.setattr(prospective, "fetch_fred_release_documents",
                        lambda indicators: real_fetch(indicators, client=_documents_client(responses)))
    monkeypatch.setattr(official_sources, "utcnow", lambda: datetime(2040, 1, 7, 1, 0, tzinfo=UTC))  # Jan 6, 20:00 New York
    monkeypatch.setattr(prospective, "resolve_for_question", resolve)
    monkeypatch.setattr(prospective, "selection_sources", forbidden)
    monkeypatch.setattr(settings, "cohort_schedule_evidence", True)
    cutoff = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    yields = dgs10_spec("2040-01-09", datetime(2040, 1, 10, 21, tzinfo=UTC))
    claims = claims_spec("2040-01-07", datetime(2040, 1, 12, 13, 30, tzinfo=UTC))
    cohort = client.post("/api/prospective/cohorts", json={"name": "Fast", "budget_usd": 1, "questions": [
        {"macro": spec.model_dump(mode="json"), "cutoff": cutoff, "release_event": spec.indicator}
        for spec in (yields, claims)]}).json()
    frozen = client.post(f"/api/prospective/cohorts/{cohort['id']}/freeze", json={"reviewed_by": "Test reviewer"})
    assert frozen.status_code == 200, frozen.text

    with SessionLocal() as session:
        manifest = json.loads(session.get(ProspectiveCohort, cohort["id"]).manifest_json)
        by_entry = {}
        for row in session.scalars(select(ProspectiveAssignment).where(ProspectiveAssignment.cohort_id == cohort["id"])):
            by_entry.setdefault(row.entry_id, []).append(json.loads(session.get(PersonalForecast, row.run_id).result_json))
    official = {entry["macro"]["indicator"]: (entry["entry_id"], entry["official_schedule"]) for entry in manifest["entries"]}

    entry_id, record = official["treasury_10y"]
    assert record["source_urls"] == ["https://www.federalreserve.gov/releases/h15/"]
    assert record["source_sha256"] == [hashlib.sha256(html).hexdigest()]
    assert len(by_entry[entry_id]) == 4
    for result in by_entry[entry_id]:
        (item,) = result["resolution_evidence"]
        assert item["claim_id"] == record["claim_ids"][0] and item["required_sections"] == ["resolution"]
        assert item["quote"].startswith("The release is posted daily Monday through Friday at 4:15pm.")

    entry_id, record = official["jobless_claims"]
    assert record == {"gap": "official_release_document_unavailable"}
    assert all("resolution_evidence" not in result for result in by_entry[entry_id])

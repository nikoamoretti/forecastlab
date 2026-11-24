from __future__ import annotations

import pytest
from pydantic import ValidationError

from forecastlab.official_releases import official_correction_evidence_url
from forecastlab_api.autopilot_routes import Correction

DOL_RELEASE = "https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf"
BLS_RELEASE = "https://www.bls.gov/news.release/cpi.nr0.htm"


def _body(url: str) -> dict[str, object]:
    return {
        "outcome": 0,
        "reason": "Reviewed official first-release rounding",
        "evidence_url": url,
    }


@pytest.mark.parametrize(
    "url",
    [
        BLS_RELEASE,
        "https://www.bls.gov/",
        "https://www.BLS.gov/news.release/cpi.nr0.htm",
        DOL_RELEASE,
        "https://www.dol.gov/newsroom/economicdata/empsit_09042026.pdf",
        "https://www.DOL.gov/newsroom/economicdata/cpi_08122026.pdf",
    ],
)
def test_official_correction_urls_are_accepted(url: str) -> None:
    assert official_correction_evidence_url(url) is True
    Correction.model_validate(_body(url))


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bls.gov/news.release/cpi.nr0.htm",
        "http://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf",
        "https://user@www.bls.gov/news.release/cpi.nr0.htm",
        "https://user:pass@www.bls.gov/news.release/cpi.nr0.htm",
        "https://user@www.dol.gov/newsroom/economicdata/cpi_08122026.pdf",
        "https://www.bls.gov.evil.com/news.release/cpi.nr0.htm",
        "https://www.dol.gov.evil.com/newsroom/economicdata/cpi_08122026.pdf",
        "https://bls.gov/news.release/cpi.nr0.htm",
        "https://dol.gov/newsroom/economicdata/cpi_08122026.pdf",
        "https://www.dol.gov/newsroom/economicdata",
        "https://www.dol.gov/newsroom/economicdata/",
        "https://www.dol.gov/newsroom/economicdata/cpi_08122026.html",
        "https://www.dol.gov/agencies/bls",
        "https://www.dol.gov/newsroom/releases/osec/osec20260812",
        "https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf?download=1",
        "https://www.dol.gov/newsroom/economicdata/notcpi_08122026.pdf",
        "https://www.bls.gov",
        "https://www.bls.gov:443/news.release/cpi.nr0.htm",
        "ftp://www.bls.gov/news.release/cpi.nr0.htm",
    ],
)
def test_unofficial_or_lookalike_correction_urls_are_rejected(url: str) -> None:
    assert official_correction_evidence_url(url) is False
    with pytest.raises(ValidationError, match="official BLS page or DOL economic-data release"):
        Correction.model_validate(_body(url))


def test_correction_route_validates_evidence_url_before_lookup(client) -> None:
    accepted = client.post("/api/autopilot/outcomes/missing/corrections", json=_body(DOL_RELEASE))
    assert accepted.status_code == 404
    also_bls = client.post("/api/autopilot/outcomes/missing/corrections", json=_body(BLS_RELEASE))
    assert also_bls.status_code == 404
    rejected = client.post(
        "/api/autopilot/outcomes/missing/corrections",
        json=_body("https://www.dol.gov/newsroom/economicdata"),
    )
    assert rejected.status_code == 422
    lookalike = client.post(
        "/api/autopilot/outcomes/missing/corrections",
        json=_body("https://www.dol.gov.evil.com/newsroom/economicdata/cpi_08122026.pdf"),
    )
    assert lookalike.status_code == 422
    userinfo = client.post(
        "/api/autopilot/outcomes/missing/corrections",
        json=_body("https://user@www.bls.gov/news.release/cpi.nr0.htm"),
    )
    assert userinfo.status_code == 422
    plaintext = client.post(
        "/api/autopilot/outcomes/missing/corrections",
        json=_body("http://www.bls.gov/news.release/cpi.nr0.htm"),
    )
    assert plaintext.status_code == 422

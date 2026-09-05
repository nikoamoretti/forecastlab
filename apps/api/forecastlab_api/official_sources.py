"""Bounded retrieval of original DOL release PDFs and a current official calendar."""
from __future__ import annotations

from datetime import datetime

import httpx

from forecastlab.macro import MacroDataError
from forecastlab.official_releases import (
    DOL_INDEX,
    dol_release_links,
    fed_calendar_url,
    release_announcements,
    verify_fed_schedule,
)
from forecastlab_api.artifact_store import put_bytes


def fetch_dol_schedules(http: httpx.Client, now: datetime):
    documents: dict[str, dict] = {}
    content: dict[str, bytes] = {}

    def read(url: str) -> bytes:
        if url in content:
            return content[url]
        with http.stream("GET", url) as response:
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            expected = "application/pdf" if url.endswith(".pdf") else "text/html"
            if mime != expected:
                raise MacroDataError("official_source_content_type_mismatch")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise MacroDataError("official_source_response_too_large")
        content[url] = bytes(data)
        documents[url] = put_bytes(content[url], content_type=mime)
        return content[url]

    links = dol_release_links(read(DOL_INDEX).decode("utf-8"))
    releases, gaps = [], []
    for family in ("empsit", "cpi"):
        try:
            url = links.get(family)
            if url is None:
                raise MacroDataError("official_release_index_entry_missing")
            current, upcoming = release_announcements(read(url), source_url=url, checked_at=now)
            calendar_url = fed_calendar_url(upcoming)
            verified = verify_fed_schedule(read(calendar_url).decode("utf-8"), upcoming, source_url=calendar_url)
            releases.extend([current, verified])
        except (httpx.HTTPError, ValueError) as exc:
            reason = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                      else str(exc) if isinstance(exc, MacroDataError) else type(exc).__name__)
            gaps.append(f"{family}: Official release schedule unavailable ({reason}).")
    return releases, documents, gaps

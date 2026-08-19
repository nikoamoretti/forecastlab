from __future__ import annotations

from urllib.parse import urlparse

from forecastlab.schemas import SearchHit

PRIMARY_HOST_HINTS = (
    ".gov",
    ".gov.uk",
    ".europa.eu",
    "bls.gov",
    "bea.gov",
    "census.gov",
    "federalreserve.gov",
    "imf.org",
    "oecd.org",
    "worldbank.org",
    "who.int",
    "un.org",
    "sec.gov",
    "cbo.gov",
    "ons.gov.uk",
    "statcan.gc.ca",
    "ecb.europa.eu",
    "bis.org",
)

SOCIAL_HOSTS = (
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "youtube.com",
)


def classify_source(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host.endswith("forecastlab.local") and any(
        token in path for token in ("bls", "fred", "cbo", "nber", "jolts")
    ):
        return "primary"
    if any(host.endswith(hint.lstrip(".")) or hint in host for hint in PRIMARY_HOST_HINTS):
        return "primary"
    return "secondary"


def rank_hits(hits: list[SearchHit]) -> list[SearchHit]:
    def score(hit: SearchHit) -> tuple[float, str]:
        host = (urlparse(hit.url).hostname or "").lower()
        value = hit.score
        if classify_source(hit.url) == "primary":
            value += 3.0
        if urlparse(hit.url).scheme == "https":
            value += 0.2
        if hit.published_at is not None:
            value += 0.3
        if any(social in host for social in SOCIAL_HOSTS):
            value -= 2.0
        if host.endswith(".edu"):
            value += 0.5
        return (-value, hit.url)

    ordered = sorted(hits, key=score)
    for hit in ordered:
        hit.source_class = classify_source(hit.url)  # type: ignore[assignment]
    return ordered

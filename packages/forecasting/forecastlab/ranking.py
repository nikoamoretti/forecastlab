from __future__ import annotations

import re
from urllib.parse import urlparse

from forecastlab.schemas import SearchHit
from forecastlab.ssrf import host_matches

PRIMARY_DOMAINS = (
    "gov",
    "gov.uk",
    "europa.eu",
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
    "stlouisfed.org",
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
    if host_matches(host, "forecastlab.local") and any(
        token in path for token in ("bls", "fred", "cbo", "nber", "jolts")
    ):
        return "primary"
    if any(host_matches(host, domain) for domain in PRIMARY_DOMAINS):
        return "primary"
    return "secondary"


_AFFINITY_STOP_WORDS = {
    "about",
    "after",
    "before",
    "could",
    "evidence",
    "official",
    "primary",
    "source",
    "their",
    "there",
    "these",
    "which",
    "would",
}


def _affinity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 4 and token not in _AFFINITY_STOP_WORDS
    }


def _source_affinity(
    hit: SearchHit,
    *,
    preferred_sources: list[str],
    node_question: str | None,
) -> float:
    parsed = urlparse(hit.url)
    haystack = " ".join(
        (parsed.hostname or "", parsed.path, hit.title, hit.snippet)
    ).casefold()
    score = 0.0
    for index, source in enumerate(preferred_sources):
        parsed_source = urlparse(source if "://" in source else f"https://{source}")
        preferred_host = (parsed_source.hostname or "").casefold()
        if preferred_host and host_matches((parsed.hostname or "").casefold(), preferred_host):
            score += 2.5 if index == 0 else 1.5
        matches = len(_affinity_tokens(source) & _affinity_tokens(haystack))
        score += min(1.2, matches * (0.3 if index == 0 else 0.2))
    if node_question:
        score += min(
            0.8,
            len(_affinity_tokens(node_question) & _affinity_tokens(haystack))
            * 0.1,
        )
    return score


def rank_hits(
    hits: list[SearchHit],
    *,
    preferred_sources: list[str] | None = None,
    node_question: str | None = None,
) -> list[SearchHit]:
    source_preferences = preferred_sources or []

    def score(hit: SearchHit) -> tuple[float, str]:
        host = (urlparse(hit.url).hostname or "").lower()
        value = hit.score
        if classify_source(hit.url) == "primary":
            value += 3.0
        if urlparse(hit.url).scheme == "https":
            value += 0.2
        if hit.published_at is not None:
            value += 0.3
        if any(host_matches(host, social) for social in SOCIAL_HOSTS):
            value -= 2.0
        if host.endswith(".edu"):
            value += 0.5
        value += _source_affinity(
            hit,
            preferred_sources=source_preferences,
            node_question=node_question,
        )
        return (-value, hit.url)

    ordered = sorted(hits, key=score)
    for hit in ordered:
        hit.source_class = classify_source(hit.url)  # type: ignore[assignment]
    return ordered

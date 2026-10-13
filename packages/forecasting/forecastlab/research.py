"""Source documents for each forecast question, fetched before Claude writes its brief.

Claude plans the searches (``research_queries.json``: a few targeted queries per question,
aimed at primary sources such as official releases, filings and data). The research step
runs them through Tavily with the full page text, drops prediction-market sites and any
sentence that quotes market odds, and saves the result as ``research.json``. Claude then
writes its brief from those documents, and the judge panel reads the same excerpts, so
every forecaster works from the sources themselves rather than one short summary.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

TAVILY = "https://api.tavily.com/search"
QUERIES_FILE = "research_queries.json"
RESEARCH_FILE = "research.json"
MAX_QUERIES_PER_QUESTION = 5
MAX_QUERIES_PER_RUN = 120  # about 120 Tavily credits at most per day
RESULTS_PER_QUERY = 5
SOURCES_PER_QUESTION = 8
TEXT_CHARS = 8000  # kept per source in research.json

# Forecasters must never see market prices: no market sites, and no sentence that mentions one.
MARKET_DOMAINS = ("polymarket.com", "kalshi.com", "manifold.markets", "metaculus.com", "predictit.org",
                  "betfair.com", "smarkets.com", "oddschecker.com")
MARKET_WORDS = re.compile(r"polymarket|kalshi|manifold|metaculus|predictit|betting odds|implied probability",
                          re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

Search = Callable[[str], list[dict[str, Any]]]


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    published: str  # as Tavily reports it; often empty
    text: str


def is_market_site(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in MARKET_DOMAINS)


def clean(text: str) -> str:
    """The text without any sentence that names a prediction market or odds, whitespace collapsed."""
    kept = [s.strip() for s in SENTENCE.split(text or "") if s.strip() and not MARKET_WORDS.search(s)]
    return re.sub(r"[ \t]+", " ", "\n".join(kept)).strip()


def tavily_search(key: str, post: Callable[..., Any]) -> Search:
    def search(query: str) -> list[dict[str, Any]]:
        response = post(TAVILY, headers={"Authorization": f"Bearer {key}"}, json={
            "query": query, "search_depth": "basic", "max_results": RESULTS_PER_QUERY,
            "include_raw_content": True, "include_answer": False, "exclude_domains": list(MARKET_DOMAINS)})
        response.raise_for_status()
        return response.json().get("results", [])
    return search


def gather(queries: list[str], search: Search) -> list[Source]:
    """Run a question's queries and keep the first SOURCES_PER_QUESTION distinct usable pages."""
    sources: dict[str, Source] = {}
    for query in queries[:MAX_QUERIES_PER_QUESTION]:
        for hit in search(query):
            url = hit.get("url") or ""
            if not url or url in sources or is_market_site(url):
                continue
            text = clean(hit.get("raw_content") or hit.get("content") or "")[:TEXT_CHARS]
            if len(text) < 200:
                continue
            sources[url] = Source(title=clean(hit.get("title") or "")[:200], url=url,
                                  published=hit.get("published_date") or "", text=text)
            if len(sources) == SOURCES_PER_QUESTION:
                return list(sources.values())
    return list(sources.values())


def excerpts(sources: list[Source], *, per_source: int = 1800, total: int = 12000) -> str:
    """The sources as a prompt section, each cut to per_source characters, all within total."""
    parts, used = [], 0
    for n, source in enumerate(sources, 1):
        head = f"[{n}] {source.title} ({source.url}{', ' + source.published if source.published else ''})\n"
        body = source.text[:per_source]
        if used + len(head) + len(body) > total:
            break
        parts.append(head + body)
        used += len(head) + len(body)
    return "\n\n".join(parts)

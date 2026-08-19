from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from forecastlab.hashing import redact_secrets
from forecastlab.providers.mock import MockSearchProvider
from forecastlab.schemas import SearchHit
from forecastlab.timeutil import parse_datetime


class SearchProviderError(RuntimeError):
    pass


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        if not api_key:
            raise SearchProviderError("Search API key is not configured")
        self.api_key = api_key
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=8), reraise=True)
    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post("https://api.tavily.com/search", json=payload)
        except httpx.HTTPError as exc:
            raise SearchProviderError(redact_secrets(str(exc))) from exc
        if response.status_code >= 400:
            raise SearchProviderError(f"Tavily HTTP {response.status_code}: {redact_secrets(response.text[:300])}")
        data = response.json()
        hits: list[SearchHit] = []
        for item in data.get("results") or []:
            hits.append(
                SearchHit(
                    title=item.get("title") or item.get("url") or "Untitled",
                    url=item.get("url"),
                    snippet=item.get("content") or "",
                    published_at=parse_datetime(item.get("published_date")),
                    score=float(item.get("score") or 0.0),
                )
            )
        return hits


def build_search_provider(name: str, api_key: str | None) -> MockSearchProvider | TavilySearchProvider:
    if name == "tavily":
        if not api_key:
            return MockSearchProvider()
        return TavilySearchProvider(api_key)
    return MockSearchProvider()

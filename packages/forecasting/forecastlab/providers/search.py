from __future__ import annotations

import uuid
from typing import Any

import httpx

from forecastlab.errors import ConfigurationError, PermanentProviderError, TransientProviderError, classify_http_status
from forecastlab.execution import ExecutionContext
from forecastlab.hashing import redact_secrets
from forecastlab.ledger import UsageLedger
from forecastlab.physical import run_physical_attempts
from forecastlab.pricing import estimate_search_cost
from forecastlab.providers.mock import MockSearchProvider
from forecastlab.schemas import ModelUsage, SearchHit
from forecastlab.timeutil import parse_datetime


class SearchProviderError(PermanentProviderError):
    pass


class TavilySearchProvider:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        *,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
        pricing_catalog: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError(["search_api_key_missing"])
        self.api_key = api_key
        self.timeout = timeout
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id
        self.pricing_catalog = pricing_catalog

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
        }
        reserved_cost, label = estimate_search_cost("tavily", catalog=self.pricing_catalog)
        reserved = 0.0 if reserved_cost is None else reserved_cost
        logical_id = str(uuid.uuid4())

        def send(_physical: int) -> tuple[list[SearchHit], ModelUsage]:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post("https://api.tavily.com/search", json=payload)
            except httpx.TimeoutException as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            except httpx.HTTPError as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            if response.status_code >= 400:
                error_cls = classify_http_status(response.status_code)
                raise error_cls(f"Tavily HTTP {response.status_code}: {redact_secrets(response.text[:300])}")
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
            reported = data.get("usage", {}).get("cost") if isinstance(data.get("usage"), dict) else None
            cost, source = estimate_search_cost(
                "tavily",
                catalog=self.pricing_catalog,
                provider_reported=float(reported) if reported is not None else None,
            )
            usage = ModelUsage(
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0 if cost is None else cost,
                latency_ms=int(float(response.elapsed.total_seconds() * 1000)),
                model="tavily-search",
                provider=self.name,
                request_id=response.headers.get("x-request-id"),
                cost_source=source if cost is not None else "unavailable",
            )
            return hits, usage

        return run_physical_attempts(
            ledger=self.ledger,
            run_id=self.run_id,
            run_attempt_id=self.run_attempt_id,
            logical_call_id=logical_id,
            stage="search",
            provider_type="search",
            provider=self.name,
            model="tavily-search",
            reserved_input_tokens=0,
            reserved_output_tokens=0,
            reserved_cost_usd=reserved,
            send=send,
        )


def build_search_provider(
    name: str,
    api_key: str | None,
    *,
    execution: ExecutionContext | None = None,
    ledger: UsageLedger | None = None,
    run_id: str | None = None,
    run_attempt_id: str | None = None,
    pricing_catalog: dict[str, Any] | None = None,
) -> MockSearchProvider | TavilySearchProvider:
    if execution is not None:
        if execution.search_is_mock:
            return MockSearchProvider(ledger=ledger, run_id=run_id, run_attempt_id=run_attempt_id)
        if execution.search_provider == "tavily":
            if not api_key:
                raise ConfigurationError(["search_api_key_missing"])
            return TavilySearchProvider(
                api_key,
                ledger=ledger,
                run_id=run_id,
                run_attempt_id=run_attempt_id,
                pricing_catalog=pricing_catalog,
            )
        raise ConfigurationError(["unknown_search_provider"])
    if name in {"mock", "demo"}:
        return MockSearchProvider(ledger=ledger, run_id=run_id, run_attempt_id=run_attempt_id)
    if name == "tavily":
        if not api_key:
            raise ConfigurationError(["search_api_key_missing"])
        return TavilySearchProvider(
            api_key,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
        )
    raise ConfigurationError(["unknown_search_provider"])

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from forecastlab.deadline import request_timeout
from forecastlab.errors import ConfigurationError, PermanentProviderError, TransientProviderError, classify_http_status
from forecastlab.execution import ExecutionContext
from forecastlab.hashing import redact_secrets
from forecastlab.ledger import UsageLedger
from forecastlab.physical import run_physical_attempts
from forecastlab.pricing import estimate_call_cost, estimate_search_cost, lookup_search_rate
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
                with httpx.Client(timeout=request_timeout(self.ledger, self.timeout, "search")) as client:
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
                published_at = parse_datetime(item.get("published_date"))
                hits.append(
                    SearchHit(
                        title=item.get("title") or item.get("url") or "Untitled",
                        url=item.get("url"),
                        snippet=item.get("content") or "",
                        published_at=published_at,
                        published_at_source=(
                            "tavily_search_hit" if published_at is not None else None
                        ),
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


OPENAI_WEB_SEARCH = "openai_web_search"
DEFAULT_OPENAI_WEB_SEARCH_MODEL = "gpt-5-mini"
_OPENAI_WEB_SEARCH_INSTRUCTIONS = (
    "You are a research search engine. Use web search to find current source pages "
    "relevant to the query. Prefer primary sources such as government statistical "
    "agencies and central banks, then reputable news and research outlets. Reply with "
    "a short list, one line per page: the page title, then one sentence on what the "
    "page contains, citing the page. Cite specific article or data pages, not site "
    "home pages. Do not answer the query yourself."
)
# OpenAI appends this tracking parameter to cited URLs; it is not part of the source.
_OPENAI_TRACKING_PARAMS = {"utm_source"}


def _normalized_source_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if not (key in _OPENAI_TRACKING_PARAMS and value == "openai")]
    query = parsed.query if len(kept) == len(pairs) else urlencode(kept)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _line_around(text: str, index: int) -> str:
    start = text.rfind("\n", 0, max(0, index)) + 1
    end = text.find("\n", index)
    line = text[start : end if end >= 0 else len(text)]
    # Drop markdown link targets so the snippet is plain prose.
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    return line.strip(" -*\t")[:500]


def openai_web_search_hits(data: dict[str, Any], *, max_results: int) -> tuple[list[SearchHit], int]:
    """Return hits and the number of web search tool calls in a Responses payload.

    URLs come only from the tool's own citations and source lists, never from
    free text, so the model cannot introduce a page the search did not return.
    The model's one-line description is kept as the snippet. Publication dates
    are left unset: the fetch step reads them from the page itself.
    """

    cited: list[tuple[str, str, str]] = []
    listed: list[str] = []
    calls = 0
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            calls += 1
            action = item.get("action")
            for source in (action.get("sources") if isinstance(action, dict) else None) or []:
                if isinstance(source, dict):
                    listed.append(source.get("url"))
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") if isinstance(part.get("text"), str) else ""
                for note in part.get("annotations") or []:
                    if not isinstance(note, dict) or note.get("type") != "url_citation":
                        continue
                    index = note.get("start_index")
                    snippet = _line_around(text, index) if isinstance(index, int) else ""
                    cited.append((note.get("url"), str(note.get("title") or ""), snippet))
    hits: list[SearchHit] = []
    seen: set[str] = set()
    candidates = [*cited, *((url, "", "") for url in listed)]
    for raw_url, title, snippet in candidates:
        url = _normalized_source_url(raw_url)
        if url is None or url in seen:
            continue
        seen.add(url)
        hits.append(SearchHit(title=title.strip() or url, url=url, snippet=snippet))
        if len(hits) >= max_results:
            break
    total = len(hits)
    for position, hit in enumerate(hits):
        hit.score = round(1.0 - position / max(1, total), 6)
    return hits, calls


class OpenAIWebSearchProvider:
    """Search through OpenAI's built-in web search tool on the Responses API."""

    name = OPENAI_WEB_SEARCH

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_OPENAI_WEB_SEARCH_MODEL,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 90.0,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
        pricing_catalog: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError(["search_api_key_missing"])
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id
        self.pricing_catalog = pricing_catalog

    def _cost(self, data: dict[str, Any], calls: int) -> tuple[float, str]:
        """Tool fee per search call plus the search tokens at the catalog model rate."""

        rate = lookup_search_rate(self.name, catalog=self.pricing_catalog) or {}
        fee = rate.get("tool_call_fee_usd")
        if fee is None:
            estimate, label = estimate_search_cost(self.name, catalog=self.pricing_catalog)
            return (0.0 if estimate is None else estimate), label
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        tokens = estimate_call_cost(
            "openai",
            self.model,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            catalog=self.pricing_catalog,
        )
        return float(fee) * max(1, calls) + tokens, "estimated"

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        body = {
            "model": self.model,
            "instructions": _OPENAI_WEB_SEARCH_INSTRUCTIONS,
            "input": f"List at most {max_results} pages.\n\nQuery: {query}",
            "tools": [{"type": "web_search"}],
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "reasoning": {"effort": "low"},
            "store": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        reserved_cost, _label = estimate_search_cost(self.name, catalog=self.pricing_catalog)
        reserved = 0.0 if reserved_cost is None else reserved_cost
        logical_id = str(uuid.uuid4())

        def send(_physical: int) -> tuple[list[SearchHit], ModelUsage]:
            try:
                with httpx.Client(timeout=request_timeout(self.ledger, self.timeout, "search")) as client:
                    response = client.post(f"{self.base_url}/responses", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            except httpx.HTTPError as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            if response.status_code >= 400:
                error_cls = classify_http_status(response.status_code)
                raise error_cls(f"OpenAI web search HTTP {response.status_code}: {redact_secrets(response.text[:300])}")
            data = response.json()
            if data.get("status") == "failed":
                error = data.get("error") if isinstance(data.get("error"), dict) else {}
                raise TransientProviderError(f"OpenAI web search failed: {redact_secrets(str(error.get('code')))}")
            hits, calls = openai_web_search_hits(data, max_results=max_results)
            if calls == 0:
                raise SearchProviderError("openai_web_search_unsupported_response: no web search call")
            cost, source = self._cost(data, calls)
            # Search tokens are part of the search charge, not the forecasting token budget.
            usage = ModelUsage(
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=cost,
                latency_ms=int(float(response.elapsed.total_seconds() * 1000)),
                model=self.model,
                provider=self.name,
                request_id=response.headers.get("x-request-id"),
                cost_source=source,
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
            model=self.model,
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
) -> MockSearchProvider | TavilySearchProvider | OpenAIWebSearchProvider:
    if execution is not None:
        if execution.search_is_mock:
            return MockSearchProvider(ledger=ledger, run_id=run_id, run_attempt_id=run_attempt_id)
        name = execution.search_provider
        if name not in {"tavily", OPENAI_WEB_SEARCH}:
            raise ConfigurationError(["unknown_search_provider"])
    if name in {"mock", "demo"}:
        return MockSearchProvider(ledger=ledger, run_id=run_id, run_attempt_id=run_attempt_id)
    if name in {"tavily", OPENAI_WEB_SEARCH}:
        if not api_key:
            raise ConfigurationError(["search_api_key_missing"])
        provider_cls = TavilySearchProvider if name == "tavily" else OpenAIWebSearchProvider
        return provider_cls(
            api_key,
            ledger=ledger,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            pricing_catalog=pricing_catalog,
        )
    raise ConfigurationError(["unknown_search_provider"])

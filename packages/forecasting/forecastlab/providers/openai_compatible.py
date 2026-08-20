from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from forecastlab.errors import PermanentProviderError, TransientProviderError, classify_http_status
from forecastlab.hashing import redact_secrets
from forecastlab.ledger import UsageLedger
from forecastlab.physical import run_physical_attempts
from forecastlab.pricing import estimate_call_cost, lookup_rate
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import ModelUsage

DEFAULT_XAI_BASE = "https://api.x.ai/v1"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"


class ProviderError(PermanentProviderError):
    pass


def _usage_from_response(
    data: dict[str, Any],
    *,
    model: str,
    provider: str,
    latency_ms: int,
    catalog: dict[str, Any] | None,
    request_id: str | None,
) -> ModelUsage:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    rate = lookup_rate(provider, model, catalog=catalog)
    if rate:
        cost = (prompt / 1_000_000) * float(rate.get("input_per_million") or 0) + (
            completion / 1_000_000
        ) * float(rate.get("output_per_million") or 0)
        source = "estimated"
    else:
        cost = 0.0
        source = "unavailable"
    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        latency_ms=latency_ms,
        model=model,
        provider=provider,
        request_id=request_id,
        cost_source=source,
    )


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
        pricing_catalog: dict[str, Any] | None = None,
        stage: str = "model",
        provider_id: str = "openai_compatible",
    ) -> None:
        if not api_key:
            raise ProviderError("Model API key is not configured")
        self.provider_id = provider_id
        self.name = provider_id
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id
        self.pricing_catalog = pricing_catalog
        self.stage = stage

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        temperature: float = 0.2,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
    ) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if max_output_tokens is not None:
            body["max_tokens"] = max_output_tokens
        reserved_in = max(0, int(estimated_input_tokens or 0))
        reserved_out = max(0, int(max_output_tokens or 0))
        reserved_cost = estimate_call_cost(
            self.name,
            self.model,
            reserved_in,
            reserved_out,
            catalog=self.pricing_catalog,
        )
        logical_id = str(uuid.uuid4())

        def send(_physical: int) -> tuple[ChatResult, ModelUsage]:
            try:
                with httpx.Client(timeout=timeout or self.timeout) as client:
                    response = client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            except httpx.HTTPError as exc:
                raise TransientProviderError(redact_secrets(str(exc))) from exc
            if response.status_code >= 400:
                error_cls = classify_http_status(response.status_code)
                raise error_cls(f"Model provider HTTP {response.status_code}: {redact_secrets(response.text[:400])}")
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            request_id = response.headers.get("x-request-id") or data.get("id")
            usage = _usage_from_response(
                data,
                model=self.model,
                provider=self.name,
                latency_ms=int(float(response.elapsed.total_seconds() * 1000)),
                catalog=self.pricing_catalog,
                request_id=str(request_id) if request_id else None,
            )
            return ChatResult(content=content, parsed=parsed, usage=usage), usage

        return run_physical_attempts(
            ledger=self.ledger,
            run_id=self.run_id,
            run_attempt_id=self.run_attempt_id,
            logical_call_id=logical_id,
            stage=self.stage,
            provider_type="model",
            provider=self.name,
            model=self.model,
            reserved_input_tokens=reserved_in,
            reserved_output_tokens=reserved_out,
            reserved_cost_usd=reserved_cost,
            send=send,
        )

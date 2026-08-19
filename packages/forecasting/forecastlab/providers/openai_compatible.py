from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from forecastlab.errors import PermanentProviderError, TransientProviderError, classify_http_status
from forecastlab.hashing import redact_secrets
from forecastlab.pricing import lookup_rate
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import ModelUsage

DEFAULT_XAI_BASE = "https://api.x.ai/v1"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"


class ProviderError(PermanentProviderError):
    pass


def _usage_from_response(data: dict[str, Any], *, model: str, provider: str, latency_ms: int) -> ModelUsage:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    rate = lookup_rate(provider, model)
    if rate:
        cost = (prompt / 1_000_000) * float(rate.get("input_per_million") or 0) + (
            completion / 1_000_000
        ) * float(rate.get("output_per_million") or 0)
    else:
        cost = 0.0
    return ModelUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        latency_ms=latency_ms,
        model=model,
        provider=provider,
    )


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ProviderError("Model API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @retry(
        retry=retry_if_exception_type(TransientProviderError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        reraise=True,
    )
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
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
        usage = _usage_from_response(
            data,
            model=self.model,
            provider=self.name,
            latency_ms=int(float(response.elapsed.total_seconds() * 1000)),
        )
        return ChatResult(content=content, parsed=parsed, usage=usage)

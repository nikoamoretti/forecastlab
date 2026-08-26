from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

import httpx

from forecastlab.errors import PermanentProviderError, TransientProviderError, classify_http_status
from forecastlab.hashing import redact_secrets
from forecastlab.ledger import UsageLedger
from forecastlab.physical import run_physical_attempts
from forecastlab.pricing import estimate_call_cost, lookup_rate
from forecastlab.providers.base import ChatResult, StructuredOutputDiagnostics
from forecastlab.schemas import ModelUsage
from forecastlab.structured_outputs import validate_structured_output

DEFAULT_XAI_BASE = "https://api.x.ai/v1"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_OPENAI_MAX_COMPLETION_TOKEN_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_OPENAI_NO_TEMPERATURE_PREFIXES = _OPENAI_MAX_COMPLETION_TOKEN_PREFIXES
_OPENAI_STRICT_SCHEMA_PREFIXES = (
    "gpt-5",
    "gpt-4.1",
    "gpt-4o",
    "o1",
    "o3",
    "o4",
)
_KNOWN_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
_STRICT_STRUCTURED_TASK_SCHEMA_NAMES = {
    "forecast_graph",
    "forecast_graph_compact_indexed_v1",
    "scenario_synthesis",
}


class ProviderError(PermanentProviderError):
    pass


def _is_openai_model_family(provider_id: str, model: str, prefixes: tuple[str, ...]) -> bool:
    return provider_id.strip().lower() == "openai" and model.strip().lower().startswith(prefixes)


def _completion_limit_field(
    provider_id: str,
    model: str,
) -> Literal["max_tokens", "max_completion_tokens"]:
    if _is_openai_model_family(provider_id, model, _OPENAI_MAX_COMPLETION_TOKEN_PREFIXES):
        return "max_completion_tokens"
    return "max_tokens"


def _supports_temperature(provider_id: str, model: str) -> bool:
    return not _is_openai_model_family(provider_id, model, _OPENAI_NO_TEMPERATURE_PREFIXES)


def _supports_strict_json_schema(provider_id: str, model: str) -> bool:
    return _is_openai_model_family(provider_id, model, _OPENAI_STRICT_SCHEMA_PREFIXES)


def _supports_minimal_reasoning(provider_id: str, model: str) -> bool:
    return _is_openai_model_family(provider_id, model, ("gpt-5",))


def _supports_verbosity(provider_id: str, model: str) -> bool:
    return _is_openai_model_family(provider_id, model, ("gpt-5",))


def _is_strict_structured_task_schema(schema_name: str) -> bool:
    return schema_name in _STRICT_STRUCTURED_TASK_SCHEMA_NAMES


def _sanitized_finish_reason(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in _KNOWN_FINISH_REASONS else "other"


def _sanitized_request_id(value: Any) -> str | None:
    if value is None:
        return None
    request_id = str(value).strip()
    if not request_id or len(request_id) > 128:
        return "redacted"
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id) is None:
        return "redacted"
    return request_id


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
        cost = (prompt / 1_000_000) * float(rate.get("input_per_million") or 0) + (completion / 1_000_000) * float(
            rate.get("output_per_million") or 0
        )
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
        max_completion_tokens: int | None = None,
        max_visible_output_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None,
        verbosity: Literal["low", "medium", "high"] | None = None,
    ) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        strict_schema = bool(
            json_schema is not None
            and _is_strict_structured_task_schema(schema_name)
            and _supports_strict_json_schema(self.provider_id, self.model)
        )
        response_format: dict[str, Any]
        if strict_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        body: dict[str, Any] = {
            "model": self.model,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if (
            _is_strict_structured_task_schema(schema_name)
            and reasoning_effort is not None
            and _supports_minimal_reasoning(self.provider_id, self.model)
        ):
            body["reasoning_effort"] = reasoning_effort
        if (
            _is_strict_structured_task_schema(schema_name)
            and verbosity is not None
            and _supports_verbosity(self.provider_id, self.model)
        ):
            body["verbosity"] = verbosity
        if _supports_temperature(self.provider_id, self.model):
            body["temperature"] = temperature
        limit_field = _completion_limit_field(self.provider_id, self.model)
        request_output_limit = max_output_tokens
        if limit_field == "max_completion_tokens" and max_completion_tokens is not None:
            request_output_limit = max_completion_tokens
        if request_output_limit is not None:
            body[limit_field] = request_output_limit
        reserved_in = max(0, int(estimated_input_tokens or 0))
        reserved_out = max(0, int(request_output_limit or 0))
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
            choices = data.get("choices") or []
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice, dict) else {}
            if not isinstance(message, dict):
                message = {}
            raw_content = message.get("content")
            content = raw_content if isinstance(raw_content, str) else ""
            parsed: dict[str, Any] | None
            json_parsing_succeeded = False
            try:
                decoded = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            else:
                json_parsing_succeeded = True
                parsed = decoded if isinstance(decoded, dict) else None
            request_id = _sanitized_request_id(response.headers.get("x-request-id") or data.get("id"))
            usage = _usage_from_response(
                data,
                model=self.model,
                provider=self.name,
                latency_ms=int(float(response.elapsed.total_seconds() * 1000)),
                catalog=self.pricing_catalog,
                request_id=str(request_id) if request_id else None,
            )
            usage_payload = data.get("usage") or {}
            completion_details = usage_payload.get("completion_tokens_details") or {}
            reasoning_tokens_raw = completion_details.get("reasoning_tokens")
            reasoning_tokens = int(reasoning_tokens_raw) if reasoning_tokens_raw is not None else None
            completion_reported = usage_payload.get("completion_tokens") is not None
            token_split_available = completion_reported and reasoning_tokens is not None
            if token_split_available:
                visible_tokens = max(0, usage.completion_tokens - int(reasoning_tokens))
                token_split_interpretation = "provider_reported_reasoning_split"
            elif completion_reported:
                # Without a reported reasoning split, the total completion is
                # the only safe upper bound for visible output. This never
                # understates visible use and is explicitly identified below.
                visible_tokens = usage.completion_tokens
                token_split_interpretation = (
                    "completion_tokens_used_as_visible_upper_bound"
                )
            else:
                visible_tokens = None
                token_split_interpretation = "completion_token_usage_unavailable"
            schema_valid: bool | None = None
            schema_errors: list[dict[str, str]] = []
            if strict_schema and json_parsing_succeeded:
                validated, schema_errors = validate_structured_output(schema_name, decoded)
                schema_valid = validated is not None
                if validated is not None:
                    parsed = validated
            elif strict_schema:
                schema_valid = False
            refusal_present = bool(message.get("refusal"))
            diagnostics = StructuredOutputDiagnostics(
                schema_name=schema_name,
                provider_request_id=str(request_id) if request_id else None,
                finish_reason=_sanitized_finish_reason(choice.get("finish_reason")),
                refusal_present=refusal_present,
                refusal_category="provider_refusal" if refusal_present else None,
                requested_max_output_tokens=max_output_tokens,
                requested_max_completion_tokens=(
                    max_completion_tokens
                    if limit_field == "max_completion_tokens"
                    else None
                ),
                requested_max_visible_output_tokens=max_visible_output_tokens,
                reasoning_effort=(
                    reasoning_effort if "reasoning_effort" in body else None
                ),
                verbosity=verbosity if "verbosity" in body else None,
                completion_tokens=usage.completion_tokens,
                reasoning_tokens=reasoning_tokens,
                visible_output_tokens=visible_tokens,
                token_split_available=token_split_available,
                token_split_interpretation=token_split_interpretation,
                content_character_count=len(content),
                json_parsing_succeeded=json_parsing_succeeded,
                strict_schema_validation_succeeded=schema_valid,
                schema_validation_errors=schema_errors,
            )
            return ChatResult(
                content=content,
                parsed=parsed,
                usage=usage,
                diagnostics=diagnostics,
            ), usage

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

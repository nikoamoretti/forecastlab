from __future__ import annotations

import time
from typing import Any

from forecastlab.errors import PermanentProviderError, TransientProviderError
from forecastlab.execution import search_api_key_for
from forecastlab.hashing import redact_secrets
from forecastlab.providers.factory import build_model_provider
from forecastlab.providers.search import build_search_provider
from forecastlab_api.secrets import load_secrets


def _category(exc: Exception) -> str:
    if isinstance(exc, TransientProviderError):
        return "TransientProviderError"
    if isinstance(exc, PermanentProviderError):
        return "PermanentProviderError"
    return exc.__class__.__name__


def test_model_connection() -> dict[str, Any]:
    secrets = load_secrets()
    started = time.perf_counter()
    try:
        model = build_model_provider(
            provider=str(secrets.get("model_provider") or "mock"),
            api_key=secrets.get("model_api_key"),
            base_url=secrets.get("model_base_url"),
            model=str(secrets.get("model_name") or "mock-forecast-v1"),
            timeout=min(float(secrets.get("model_timeout_seconds") or 60), 20),
        )
        result = model.complete_json(
            system="PROMPT_ID: operationalize\nReturn a JSON object.",
            user='{"question":"connection test"}',
            schema_name="resolution_contract",
        )
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "provider": getattr(model, "name", secrets.get("model_provider")),
            "model": getattr(model, "model", secrets.get("model_name")),
            "success": True,
            "latency_ms": latency,
            "error_category": None,
            "parsed": bool(result.parsed),
        }
    except Exception as exc:
        return {
            "provider": secrets.get("model_provider"),
            "model": secrets.get("model_name"),
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error_category": _category(exc),
            "error": redact_secrets(str(exc))[:300],
        }


def test_search_connection() -> dict[str, Any]:
    secrets = load_secrets()
    started = time.perf_counter()
    try:
        search_provider = str(secrets.get("search_provider") or "mock")
        search_key = search_api_key_for(
            search_provider,
            secrets.get("search_api_key"),
            model_provider=secrets.get("model_provider"),
            model_api_key=secrets.get("model_api_key"),
        )
        search = build_search_provider(search_provider, search_key)
        hits = search.search("US unemployment rate", max_results=1)
        return {
            "provider": getattr(search, "name", secrets.get("search_provider")),
            "model": None,
            "success": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error_category": None,
            "hit_count": len(hits),
        }
    except Exception as exc:
        return {
            "provider": secrets.get("search_provider"),
            "model": None,
            "success": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error_category": _category(exc),
            "error": redact_secrets(str(exc))[:300],
        }

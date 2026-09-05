from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.paths import project_root

PRICING_PATH = project_root() / "configs" / "pricing" / "models.yaml"
COST_LABELS = ("provider_reported", "estimated", "mixed", "unavailable")


def load_pricing(*, path: Path | None = None, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    if catalog is not None:
        return catalog
    target = path or PRICING_PATH
    if not target.exists():
        return {"source": "missing", "last_updated": None, "estimated": True, "providers": {}, "search": {}}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def pricing_hash(*, path: Path | None = None, catalog: dict[str, Any] | None = None) -> str:
    return sha256_text(canonical_json(load_pricing(path=path, catalog=catalog)))


def lookup_rate(
    provider: str,
    model: str,
    *,
    path: Path | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = load_pricing(path=path, catalog=catalog)
    providers = data.get("providers") or {}
    models = providers.get(provider) or {}
    if model in models:
        return {**models[model], "provider": provider, "model": model, "catalog": data}
    unknown = models.get("unknown") or (providers.get("unknown") or {}).get("default")
    if unknown:
        return {**unknown, "provider": provider, "model": model, "catalog": data, "fallback": True}
    return None


def estimate_call_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    path: Path | None = None,
    catalog: dict[str, Any] | None = None,
) -> float:
    rate = lookup_rate(provider, model, path=path, catalog=catalog)
    if rate is None:
        return 0.0
    input_rate = float(rate.get("input_per_million") or 0)
    output_rate = float(rate.get("output_per_million") or 0)
    return (max(0, input_tokens) / 1_000_000) * input_rate + (max(0, output_tokens) / 1_000_000) * output_rate


def estimate_cost(provider: str, model: str, max_tokens: int, *, path: Path | None = None, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    rate = lookup_rate(provider, model, path=path, catalog=catalog)
    if rate is None:
        return {
            "estimated_cost_usd": None,
            "cost_available": False,
            "label": "unavailable",
            "source": "none",
        }
    input_rate = float(rate.get("input_per_million") or 0)
    output_rate = float(rate.get("output_per_million") or 0)
    input_tokens = max_tokens * 0.7
    output_tokens = max_tokens * 0.3
    cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
    catalog_data = rate.get("catalog") or {}
    return {
        "estimated_cost_usd": round(cost, 6),
        "cost_available": True,
        "label": "estimated",
        "source": catalog_data.get("source") or rate.get("source") or "manual",
        "last_updated": catalog_data.get("last_updated"),
        "fallback": bool(rate.get("fallback")),
    }


def lookup_search_rate(
    provider: str,
    *,
    path: Path | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = load_pricing(path=path, catalog=catalog)
    search = data.get("search") or {}
    row = search.get(provider) or search.get("unknown")
    if row is None:
        return None
    return {**row, "provider": provider, "catalog": data}


def estimate_search_cost(
    provider: str,
    *,
    path: Path | None = None,
    catalog: dict[str, Any] | None = None,
    provider_reported: float | None = None,
) -> tuple[float | None, str]:
    if provider_reported is not None:
        return float(provider_reported), "provider_reported"
    rate = lookup_search_rate(provider, path=path, catalog=catalog)
    if rate is None:
        return None, "unavailable"
    if rate.get("per_request") is not None:
        return float(rate["per_request"]), "estimated" if rate.get("estimated", True) else "provider_reported"
    if rate.get("estimated_per_request") is not None:
        return float(rate["estimated_per_request"]), "estimated"
    return None, "unavailable"


def combine_cost_labels(labels: list[str]) -> str:
    cleaned = [item for item in labels if item in COST_LABELS]
    if not cleaned:
        return "unavailable"
    unique = set(cleaned)
    if unique == {"unavailable"}:
        return "unavailable"
    unique.discard("unavailable")
    if len(unique) == 1:
        return next(iter(unique))
    return "mixed"

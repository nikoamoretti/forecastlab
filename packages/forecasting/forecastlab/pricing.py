from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PRICING_PATH = Path(__file__).resolve().parents[3] / "configs" / "pricing" / "models.yaml"


def load_pricing(*, path: Path | None = None) -> dict[str, Any]:
    target = path or PRICING_PATH
    if not target.exists():
        return {"source": "missing", "last_updated": None, "estimated": True, "providers": {}}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def lookup_rate(provider: str, model: str, *, path: Path | None = None) -> dict[str, Any] | None:
    data = load_pricing(path=path)
    providers = data.get("providers") or {}
    models = (providers.get(provider) or {})
    if model in models:
        return {**models[model], "provider": provider, "model": model, "catalog": data}
    unknown = models.get("unknown") or (providers.get("unknown") or {}).get("default")
    if unknown:
        return {**unknown, "provider": provider, "model": model, "catalog": data, "fallback": True}
    return None


def estimate_cost(provider: str, model: str, max_tokens: int, *, path: Path | None = None) -> dict[str, Any]:
    rate = lookup_rate(provider, model, path=path)
    if rate is None:
        return {
            "estimated_cost_usd": None,
            "cost_available": False,
            "label": "unavailable",
            "source": "none",
        }
    input_rate = float(rate.get("input_per_million") or 0)
    output_rate = float(rate.get("output_per_million") or 0)
    # Conservative: treat the token ceiling as all billed tokens, split 70/30.
    input_tokens = max_tokens * 0.7
    output_tokens = max_tokens * 0.3
    cost = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
    catalog = rate.get("catalog") or {}
    return {
        "estimated_cost_usd": round(cost, 6),
        "cost_available": True,
        "label": "estimated",
        "source": catalog.get("source") or rate.get("source") or "manual",
        "last_updated": catalog.get("last_updated"),
        "fallback": bool(rate.get("fallback")),
    }

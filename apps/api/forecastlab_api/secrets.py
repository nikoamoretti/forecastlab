from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forecastlab.hashing import mask_secret
from forecastlab.schemas import PublicSettings, SettingsPatch
from forecastlab_api.config import settings


def _path() -> Path:
    path = settings.credentials_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_secrets() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        data = {
            "model_provider": settings.model_provider,
            "model_base_url": settings.model_base_url,
            "model_name": settings.model_name,
            "model_api_key": settings.model_api_key,
            "model_timeout_seconds": settings.model_timeout_seconds,
            "max_cost_usd": settings.max_cost_usd,
            "search_provider": settings.search_provider,
            "search_api_key": settings.search_api_key,
        }
        save_secrets(data)
        return data
    return json.loads(path.read_text(encoding="utf-8"))


def save_secrets(data: dict[str, Any]) -> None:
    path = _path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)


def public_settings() -> PublicSettings:
    data = load_secrets()
    provider = data.get("model_provider") or "mock"
    search = data.get("search_provider") or "mock"
    key_set = bool(data.get("model_api_key"))
    mode = "demo" if provider == "mock" or not key_set else "live"
    return PublicSettings(
        model_provider=provider,
        model_base_url=data.get("model_base_url"),
        model_name=data.get("model_name") or "mock-forecast-v1",
        model_api_key_set=key_set,
        search_provider=search,
        search_api_key_set=bool(data.get("search_api_key")),
        max_cost_usd=float(data.get("max_cost_usd") or 5.0),
        model_timeout_seconds=float(data.get("model_timeout_seconds") or 60.0),
        mode=mode,
    )


def update_secrets(patch: SettingsPatch) -> PublicSettings:
    data = load_secrets()
    updates = patch.model_dump(exclude_none=True)
    for key, value in updates.items():
        if key in {"model_api_key", "search_api_key"} and value == "":
            data[key] = None
        else:
            data[key] = value
    save_secrets(data)
    return public_settings()


def redacted_dump(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in ("model_api_key", "search_api_key", "fred_api_key"):
        if out.get(key):
            out[key] = mask_secret(str(out[key]))
    return out


update_settings = update_secrets

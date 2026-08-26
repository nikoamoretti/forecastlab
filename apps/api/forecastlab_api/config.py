from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_prefix="FORECASTLAB_", extra="ignore")

    env: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    web_origin: str = "http://127.0.0.1:3000"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'forecastlab.db'}"
    data_dir: Path = ROOT / "data"
    credentials_path: Path = ROOT / "data" / "local" / "credentials.json"
    allow_local_fixtures: bool = True
    embedded_worker: bool = False
    log_level: str = "INFO"

    model_provider: str = "mock"
    model_base_url: str | None = None
    model_name: str = "mock-forecast-v1"
    model_api_key: str | None = None
    model_timeout_seconds: float = 60.0
    max_cost_usd: float = 5.0
    search_provider: str = "mock"
    search_api_key: str | None = None
    historical_evidence_bundle_root: Path | None = None


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "local").mkdir(parents=True, exist_ok=True)

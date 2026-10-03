from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from forecastlab.paths import project_root

ROOT = project_root()


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
    # Freezing a prospective cohort retrieves the official release calendar to
    # attach verified schedule evidence. Isolated tests disable this network step.
    cohort_schedule_evidence: bool = True
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
    database_direct_url: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    owner_github_id: str | None = None
    session_secret: str | None = None
    internal_secret: str | None = None
    blob_token: str | None = None
    blob_store_id: str | None = None
    deployment_revision: str = "local"
    fred_api_key: str | None = None

    @property
    def cloud(self) -> bool:
        return self.env != "local" or bool(os.environ.get("VERCEL"))

    @property
    def production(self) -> bool:
        return self.cloud and os.environ.get("VERCEL_ENV", self.env) == "production"


settings = Settings()
if settings.cloud and os.environ.get("DATABASE_URL") and not os.environ.get("FORECASTLAB_DATABASE_URL"):
    settings.database_url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
if settings.cloud:
    settings.database_direct_url = settings.database_direct_url or os.environ.get("DATABASE_URL_UNPOOLED")
if not settings.cloud:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "local").mkdir(parents=True, exist_ok=True)

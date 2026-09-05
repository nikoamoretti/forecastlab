"""Locate bundled read-only resources in checkouts and serverless installations."""
import os
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("FORECASTLAB_ROOT")
    if configured:
        return Path(configured).resolve()
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "configs" / "forecast_profiles").is_dir() and (candidate / "prompts").is_dir():
            return candidate
    raise RuntimeError("ForecastLab configuration and prompts are missing from the deployment bundle")

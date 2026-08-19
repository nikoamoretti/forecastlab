from __future__ import annotations

from pathlib import Path

import yaml

from forecastlab.schemas import ForecastProfile

PROFILES_DIR = Path(__file__).resolve().parents[3] / "configs" / "forecast_profiles"


def load_profile(profile_id: str, *, profiles_dir: Path | None = None) -> ForecastProfile:
    directory = profiles_dir or PROFILES_DIR
    path = directory / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Unknown forecast profile: {profile_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ForecastProfile.model_validate(data)


def list_profiles(*, profiles_dir: Path | None = None) -> list[ForecastProfile]:
    directory = profiles_dir or PROFILES_DIR
    profiles: list[ForecastProfile] = []
    for path in sorted(directory.glob("*.yaml")):
        profiles.append(ForecastProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))))
    return profiles

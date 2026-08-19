from __future__ import annotations

from pathlib import Path

import yaml

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.schemas import ForecastProfile

PROFILES_DIR = Path(__file__).resolve().parents[3] / "configs" / "forecast_profiles"


def profile_hash(profile: ForecastProfile) -> str:
    return sha256_text(canonical_json(profile.model_dump(mode="json")))


def effective_profile(profile: ForecastProfile, *, user_max_cost_usd: float) -> ForecastProfile:
    return profile.model_copy(
        update={"max_estimated_cost_usd": min(profile.max_estimated_cost_usd, float(user_max_cost_usd))}
    )


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

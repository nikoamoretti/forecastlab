from __future__ import annotations

from copy import deepcopy

from forecastlab.hashing import content_hash
from forecastlab.watchers import canonical_watch_value, extract_json_path

DEMO_INDICATORS = {
    "unemployment": {"value": 4.1, "unit": "percent", "source": "demo-fixture"},
    "inflation": {"value": 2.6, "unit": "percent", "source": "demo-fixture"},
    "policy_rate": {"value": 4.5, "unit": "percent", "source": "demo-fixture"},
}


def get_indicator(name: str) -> dict:
    if name not in DEMO_INDICATORS:
        raise KeyError(name)
    return deepcopy(DEMO_INDICATORS[name])


def simulate_indicator(name: str, value: float) -> dict:
    if name not in DEMO_INDICATORS:
        raise KeyError(name)
    DEMO_INDICATORS[name]["value"] = value
    return deepcopy(DEMO_INDICATORS[name])


def demo_payload_hash(name: str) -> str:
    payload = get_indicator(name)
    return content_hash(canonical_watch_value(extract_json_path(payload, "$.value")))


get_indicator = get_indicator
simulate_indicator = simulate_indicator
demo_payload_hash = demo_payload_hash
